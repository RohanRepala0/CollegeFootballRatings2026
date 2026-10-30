"""College Sports Commission compensation reports: NIL Go third-party deals and school revenue share.

https://www.collegesportscommission.org/nil-go-transparency-report/
https://www.collegesportscommission.org/school-revenue-share-compensation-report/

Neither page has an HTML table. Each embeds its data in a base64 `<script src="data:...">`. Checked
on the real pages (2026-10-30):

- NIL Go: `var B64="..."`, itself base64 JSON {percentileLabels, sports: [{name, grouped, horizons:
  [{label, start, end, thresholds, bands: [{cut, rows}] | rows}]}]}. Values are unrounded dollars.
  `thresholds` is the smallest n that publishes each percentile; smaller cohorts show "-" (None here).
- Revenue share: `const sportsData={mens | womens | mixed: {sport: {position: {combined | with |
  without: file}}}}` indexes `const tablesData={file: {basis, groups, columns, rows}}`, the table the
  page draws, and `const TABLE_SVGS={file: svg}`, the same table as a downloadable image. Values are
  text rounded to $500. "A4 Conferences" is a merged cell over one row or two (baseball's Non-A4 has
  only "Without $0"); tablesData gives each group's span. The SVG is parsed too, by geometry (rows
  belong to the band between full-width rules that holds them, never by order), and must agree.

A release is the sha256 of the decoded payload, not of the HTML, which also carries form nonces and
asset versions that can change while the data does not. The CSC says the data "may be updated" and
nothing archives old versions (the Wayback Machine has no captures), so each release is also written
to data/csc/releases/ and committed; `cfb ingest-csc --from-archive` rebuilds the tables from them.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from bisect import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from .. import config
from ..db import insert_df
from ..fetch import Fetcher

SOURCE = "csc"
REPORTS = {
    "nil_go": "https://www.collegesportscommission.org/nil-go-transparency-report/",
    "revenue_share": "https://www.collegesportscommission.org/school-revenue-share-compensation-report/",
}
ARCHIVE_DIR = config.DATA_DIR / "csc" / "releases"
PERCENTILES = (90, 75, 50, 25, 10)
# NIL Go publishes these thresholds; the revenue-share tables hide the same cells, so they are the
# expected rule there too (checked, not assumed: see `check`).
MIN_N = {90: 100, 75: 40, 50: 10, 25: 40, 10: 100}
FOOTBALL_POSITIONS = (
    "Quarterback", "Running Back", "Wide Receiver", "Tight End", "Offensive Lineman", "Defensive Lineman",
    "Linebacker", "Defensive Back", "Kicker", "Punter", "Long Snapper", "Position Unspecified",
)

# CFBD conference names, so cohorts join to `teams.conference`.
COHORTS = {
    "Atlantic Coast Conference": "ACC", "Big Ten Conference": "Big Ten", "Big 12 Conference": "Big 12",
    "Southeastern Conference": "SEC", "A4 Conferences": "A4", "Non-A4 Conferences": "Non-A4",
    "A4": "A4", "Non-A4": "Non-A4",
}
A4_COHORTS = {"A4", "ACC", "Big Ten", "Big 12", "SEC"}
# Sport names follow the revenue-share page; NIL Go's differ only here.
SPORT_ALIASES = {"Track, Outdoor": "Track & Field (Outdoor)", "Track, Indoor": "Track & Field (Indoor)"}
UNGENDERED = {"Football": "men", "Baseball": "men", "Softball": "women", "Field Hockey": "women"}
GENDER_KEYS = {"mens": "men", "womens": "women", "mixed": "mixed"}
POPULATIONS = {"With $0": "all_rostered", "Without $0": "paid", "with": "all_rostered", "without": "paid"}

_DATA_SCRIPT = re.compile(r'src="data:text/javascript;base64,([A-Za-z0-9+/=]+)"')
_ROW_LABEL = re.compile(r"^(?P<label>.+?) \(n=(?P<n>[\d,]+)\)$")
_MONTH_DATE = r"([A-Z][a-z]+ \d{1,2}, \d{4})"


@dataclass
class Cohort:
    """One published row: a cohort's size and its percentiles."""

    report: str
    gender: str
    sport: str
    position: str
    cohort: str              # A4 | Non-A4 | ACC | Big Ten | Big 12 | SEC
    population: str          # all_rostered ("With $0") | paid ("Without $0") | deal_holders (NIL Go)
    n: int | None
    values: dict[int, float | None]     # percentile -> dollars; None = suppressed
    window_label: str
    source_ref: str
    window_start: date | None = None
    window_end: date | None = None
    as_of: date | None = None
    min_n: dict[int, int] = field(default_factory=dict)   # published thresholds (NIL Go only)
    n_text: str | None = None           # the published count when it is not a number ("<10")


def _issue(check: str, severity: str, report: str, key: str, detail: str) -> dict:
    return {"check": check, "severity": severity, "report": report, "key": key, "detail": detail}


# ---------------------------------------------------------------- payload

def _js_object(js: str, marker: str):
    return json.JSONDecoder().raw_decode(js, js.index(marker) + len(marker))[0]


def extract_payload(html: str) -> tuple[str, dict]:
    """(report, payload) from a CSC page. Raises ValueError when no known payload is on it."""
    for b64 in _DATA_SCRIPT.findall(html):
        js = base64.b64decode(b64).decode("utf-8", "replace")
        m = re.search(r'var B64="([A-Za-z0-9+/=]+)"', js)
        if m:
            return "nil_go", json.loads(base64.b64decode(m.group(1)))
        if "const TABLE_SVGS=" in js:
            return "revenue_share", {k: _js_object(js, f"const {k}=")
                                     for k in ("tablesData", "sportsData", "TABLE_SVGS")}
    raise ValueError("no CSC data payload on the page; its layout changed")


def payload_sha256(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ---------------------------------------------------------------- names, dates, dollars

def normalize_sport(name: str) -> tuple[str, str] | None:
    """(gender, sport) for a NIL Go sport name, or None when the gender is not stated or known."""
    m = re.match(r"^(Men|Women)'s (.+)$", name)
    if m:
        return m.group(1).lower(), SPORT_ALIASES.get(m.group(2), m.group(2))
    if name in UNGENDERED:
        return UNGENDERED[name], name
    return None


def _cohort(label: str, report: str, issues: list[dict]) -> str:
    if label not in COHORTS:
        issues.append(_issue("unknown_cohort", "warning", report, label, "kept as published; a4 is null"))
    return COHORTS.get(label, label)


def _usd(text: str) -> float | None:
    t = text.strip()
    if t in ("-", "—", "–"):
        return None
    m = re.fullmatch(r"\$([\d,]+)", t)
    if not m:
        raise ValueError(f"not a dollar amount: {text!r}")
    return float(m.group(1).replace(",", ""))


def _long_date(text: str) -> date:
    return datetime.strptime(text, "%B %d, %Y").date()


def parse_window(text: str) -> tuple[date | None, date | None, date | None]:
    """(start, end, as_of) from a revenue-share subtitle; Nones when the wording is unknown."""
    as_of = re.search(r"agreements as of " + _MONTH_DATE, text)
    as_of = _long_date(as_of.group(1)) if as_of else None
    m = re.search(r"Payments in calendar year (\d{4})", text)
    if m:
        y = int(m.group(1))
        return date(y, 1, 1), date(y, 12, 31), as_of
    m = re.search(r"Payments from " + _MONTH_DATE + " to " + _MONTH_DATE, text)
    if m:
        return _long_date(m.group(1)), _long_date(m.group(2)), as_of
    m = re.search(r"Payments in the (\d{4})-(\d{2}) cap year", text)
    if m:
        y = int(m.group(1))
        return date(y, 7, 1), date(y + 1, 6, 30), as_of
    return None, None, as_of


# ---------------------------------------------------------------- NIL Go

def parse_nil_go(payload: dict) -> tuple[list[Cohort], list[dict]]:
    pcts = [int(re.match(r"(\d+)", lab).group(1)) for lab in payload["percentileLabels"]]
    cohorts: list[Cohort] = []
    issues: list[dict] = []
    for sport in payload["sports"]:
        gs = normalize_sport(sport["name"])
        if gs is None:
            issues.append(_issue("unknown_sport", "error", "nil_go", sport["name"], "gender unknown; skipped"))
            continue
        gender, name = gs
        for h in sport["horizons"]:
            if sport["grouped"]:
                rows = [(band["cut"], row["label"], row) for band in h["bands"] for row in band["rows"]]
            else:
                rows = [(row["label"], "All Positions", row) for row in h["rows"]]
            for label, position, row in rows:
                cohorts.append(Cohort(
                    report="nil_go", gender=gender, sport=name, position=position,
                    cohort=_cohort(label, "nil_go", issues), population="deal_holders",
                    n=row["count"] if isinstance(row["count"], int) else None,
                    n_text=None if isinstance(row["count"], int) else str(row["count"]),
                    values=dict(zip(pcts, row["values"])), window_label=h["label"],
                    source_ref=f'{sport["name"]} / {h["label"]} / {label} / {row["label"]}',
                    window_start=date.fromisoformat(h["start"]) if h.get("start") else None,
                    window_end=date.fromisoformat(h["end"]) if h.get("end") else None,
                    min_n=dict(zip(pcts, h.get("thresholds") or [])),
                ))
    return cohorts, issues


# ---------------------------------------------------------------- revenue share

@dataclass
class SvgTable:
    title: str
    above: list[str]                                   # text above the header, top to bottom
    percentiles: list[int]
    rows: list[tuple[str | None, str, list[str]]]      # (group label, row label, cells)
    problems: list[str]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_svg_table(svg: str) -> SvgTable:
    root = ET.fromstring(svg.encode("utf-8"))
    els = list(root.iter())
    title = next((e.text or "" for e in els if _local(e.tag) == "title"), "")
    texts = [(float(e.get("x")), round(float(e.get("y")), 1), (e.text or "").strip())
             for e in els if _local(e.tag) == "text"]
    rules = [tuple(float(e.get(k)) for k in ("x1", "y1", "x2", "y2")) for e in els if _local(e.tag) == "line"]

    header_y = next(y for _, y, t in texts if t.endswith(" Percentile"))
    header = [t for _, t in sorted((x, t) for x, y, t in texts if y == header_y)]
    percentiles = [int(m.group(1)) for t in header if (m := re.fullmatch(r"(\d+)th Percentile", t))]
    above = [t for _, y, t in sorted(texts, key=lambda r: r[1]) if y < header_y]
    body = [(x, y, t) for x, y, t in texts if y > header_y]
    problems: list[str] = []

    group_of: dict[int, str] = {}
    band = None
    if "$0 Amounts" in header:   # a group column: merged "A4 Conferences" cells
        divider = next(((x1, min(y1, y2), max(y1, y2)) for x1, y1, x2, y2 in rules if x1 == x2), None)
        if divider is None:
            return SvgTable(title, above, percentiles, [], ["group column but no divider rule"])
        dx, top, bottom = divider
        edges = sorted({top, bottom} | {y1 for x1, y1, x2, y2 in rules
                                        if y1 == y2 and x1 < dx - 1 and top < y1 < bottom})
        band = lambda y: bisect(edges, y)   # noqa: E731
        labels = defaultdict(list)
        for x, y, t in body:
            if x < dx:
                labels[band(y)].append(t)
        for b, ts in labels.items():
            if len(ts) > 1:
                problems.append(f"band {b} has {len(ts)} group labels: {ts}")
            group_of[b] = ts[0]
        body = [(x, y, t) for x, y, t in body if x >= dx]

    by_y: dict[float, list] = defaultdict(list)
    for x, y, t in body:
        by_y[y].append((x, t))
    rows = []
    for y in sorted(by_y):
        cells = [t for _, t in sorted(by_y[y])]
        group = group_of.get(band(y)) if band else None
        if band and group is None:
            problems.append(f"row {cells[0]!r} sits in no labeled group")
        rows.append((group, cells[0], cells[1:]))
    return SvgTable(title, above, percentiles, rows, problems)


def table_rows(table: dict) -> tuple[list[int], list[tuple[str | None, str, list[str]]], list[str]]:
    """(percentiles, rows, problems) from one `tablesData` entry, in the same shape as `parse_svg_table`."""
    percentiles = [int(m.group(1)) for c in table["columns"] if (m := re.fullmatch(r"(\d+)th Percentile", c))]
    groups = [g["label"] for g in table.get("groups") or [] for _ in range(g["span"])]
    rows, problems = table["rows"], []
    if groups and len(groups) != len(rows):
        problems.append(f"group spans cover {len(groups)} rows, table has {len(rows)}")
        groups = []
    return percentiles, [(groups[i] if groups else None, r[0], r[1:]) for i, r in enumerate(rows)], problems


def parse_revenue_share(payload: dict) -> tuple[list[Cohort], list[dict]]:
    """Cohorts from `tablesData` (explicit group spans); every SVG must say the same thing."""
    tables, index, svgs = payload["tablesData"], payload["sportsData"], payload["TABLE_SVGS"]
    cohorts: list[Cohort] = []
    issues: list[dict] = []
    indexed = set()
    for gkey, sports in index.items():
        gender = GENDER_KEYS.get(gkey)
        if gender is None:
            issues.append(_issue("unknown_gender", "error", "revenue_share", gkey, "skipped"))
            continue
        for sport, positions in sports.items():
            for position, files in positions.items():
                for kind, fname in files.items():
                    indexed.add(fname)
                    if fname not in tables:
                        issues.append(_issue("missing_table", "error", "revenue_share", fname, "indexed, no table data"))
                        continue
                    percentiles, rows, problems = table_rows(tables[fname])
                    issues += [_issue("table_layout", "error", "revenue_share", fname, p) for p in problems]
                    cohorts += _table_cohorts(rows, percentiles, tables[fname].get("basis", ""), fname, kind,
                                              gender, SPORT_ALIASES.get(sport, sport), position, issues)
                    if fname not in svgs:
                        issues.append(_issue("svg_mismatch", "warning", "revenue_share", fname, "no SVG"))
                        continue
                    svg = parse_svg_table(svgs[fname])
                    issues += [_issue("svg_layout", "warning", "revenue_share", fname, p) for p in svg.problems]
                    if (svg.percentiles, svg.rows) != (percentiles, rows):
                        issues.append(_issue("svg_mismatch", "warning", "revenue_share", fname,
                                             "the downloadable SVG and the table data disagree"))
    for fname in sorted(set(tables) - indexed):
        issues.append(_issue("unindexed_table", "warning", "revenue_share", fname, "embedded but not in sportsData"))
    return cohorts, issues


def _table_cohorts(rows: list[tuple[str | None, str, list[str]]], percentiles: list[int], window: str, fname: str,
                   kind: str, gender: str, sport: str, position: str, issues: list[dict]) -> list[Cohort]:
    start, end, as_of = parse_window(window)
    if start is None:
        issues.append(_issue("unknown_window", "warning", "revenue_share", fname, window or "no window text"))
    out = []
    for group, label, cells in rows:
        m = _ROW_LABEL.match(label)
        if not m or len(cells) != len(percentiles):
            issues.append(_issue("table_row", "error", "revenue_share", fname, f"unreadable row {label!r} {cells}"))
            continue
        row_label, n = m.group("label"), int(m.group("n").replace(",", ""))
        if row_label in POPULATIONS:            # combined table: the group is the cohort
            population, cohort = POPULATIONS[row_label], group
        else:                                   # with/without table: the row is the cohort
            population, cohort = POPULATIONS.get(kind), row_label
        if population is None or cohort is None:
            issues.append(_issue("table_row", "error", "revenue_share", fname, f"no population or cohort for {label!r}"))
            continue
        try:
            values = {p: _usd(c) for p, c in zip(percentiles, cells)}
        except ValueError as e:
            issues.append(_issue("table_row", "error", "revenue_share", fname, str(e)))
            continue
        out.append(Cohort(
            report="revenue_share", gender=gender, sport=sport, position=position,
            cohort=_cohort(cohort, "revenue_share", issues), population=population, n=n, values=values,
            window_label=window, source_ref=f"{fname} / {label}",
            window_start=start, window_end=end, as_of=as_of,
        ))
    return out


PARSERS = {"nil_go": parse_nil_go, "revenue_share": parse_revenue_share}


def to_frame(cohorts: list[Cohort], payload_sha: str) -> pd.DataFrame:
    rows = [{
        "report": c.report, "payload_sha256": payload_sha, "gender": c.gender, "sport": c.sport,
        "position": c.position, "cohort": c.cohort,
        "a4": c.cohort in A4_COHORTS if c.cohort in COHORTS.values() else None,
        "population": c.population, "n": c.n, "n_text": c.n_text, "percentile": p, "value_usd": v, "min_n": c.min_n.get(p),
        "window_start": c.window_start, "window_end": c.window_end, "as_of": c.as_of,
        "window_label": c.window_label, "source_ref": c.source_ref,
    } for c in cohorts for p, v in c.values.items()]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- quality checks

COHORT_KEY = ["report", "gender", "sport", "position", "cohort", "population", "window_label"]
ZERO_TOL = 0.02      # skip percentiles this close to the zero/paid boundary
ROUNDING = 500.0     # revenue-share cells are rounded to $500


def _key(row) -> str:
    return " / ".join(str(row[k]) for k in ("gender", "sport", "position", "cohort", "population"))


def check(df: pd.DataFrame) -> list[dict]:
    """Internal consistency of one release (or several). Returns issues; an empty list is clean.

    - monotone: p90 >= p75 >= p50 >= p25 >= p10.
    - suppression: a cell is hidden exactly when n is below the threshold for its percentile.
    - zero_mass: the "With $0" and "Without $0" tables describe one distribution. With a zero share
      z = 1 - n_paid / n_all, the all-rostered percentile p is $0 below z and above z must sit between
      the paid percentiles around q = (p - z) / (1 - z). This also catches rows parsed into the wrong
      group, since a misassigned row breaks n_paid <= n_all or the bracket.
    - football_coverage: every position x {A4, Non-A4} in both reports.
    - nil_vs_roster: NIL Go deal holders can't outnumber the rostered players of their cohort.
    """
    issues: list[dict] = []
    if df.empty:
        return issues
    for _, g in df.groupby(COHORT_KEY, dropna=False):
        g = g.sort_values("percentile", ascending=False)
        vals = g.value_usd.dropna().tolist()
        first = g.iloc[0]
        if any(a < b for a, b in zip(vals, vals[1:])):
            issues.append(_issue("monotone", "error", first.report, _key(first), str(vals)))
        for _, r in g.iterrows():
            limit = r.min_n if pd.notna(r.min_n) else MIN_N.get(r.percentile)
            if limit is None or pd.isna(r.n):
                continue
            if pd.isna(r.value_usd) != (r.n < limit):
                sev = "error" if pd.notna(r.min_n) else "warning"
                issues.append(_issue("suppression", sev, r.report, _key(r),
                                     f"p{r.percentile}: n={r.n}, threshold {limit}, value {r.value_usd}"))

    rs = df[df.report == "revenue_share"]
    keys = ["payload_sha256", "gender", "sport", "position", "cohort", "window_label"]
    for _, g in rs.groupby(keys, dropna=False):
        allr, paid = g[g.population == "all_rostered"], g[g.population == "paid"]
        if allr.empty or paid.empty:
            continue
        n_all, n_paid = allr.n.iloc[0], paid.n.iloc[0]
        key = _key(allr.iloc[0]).rsplit(" / ", 1)[0]
        if n_paid > n_all:
            issues.append(_issue("zero_mass", "error", "revenue_share", key, f"paid n={n_paid} > all n={n_all}"))
            continue
        z = 1 - n_paid / n_all
        paid_q = paid.dropna(subset=["value_usd"]).set_index("percentile").value_usd.sort_index()
        for _, r in allr.dropna(subset=["value_usd"]).iterrows():
            p = r.percentile / 100
            if abs(p - z) < ZERO_TOL:
                continue
            if p < z:
                ok, why = r.value_usd == 0, "expected $0"
            else:
                q = 100 * (p - z) / (1 - z)
                lo = paid_q[paid_q.index <= q]
                hi = paid_q[paid_q.index >= q]
                ok = (lo.empty or r.value_usd >= lo.iloc[-1] - ROUNDING) and \
                     (hi.empty or r.value_usd <= hi.iloc[0] + ROUNDING)
                why = (f"q={q:.1f} of paid, bracket {None if lo.empty else lo.iloc[-1]}"
                       f"-{None if hi.empty else hi.iloc[0]}")
            if not ok:
                issues.append(_issue("zero_mass", "error", "revenue_share", key,
                                     f"p{r.percentile}={r.value_usd} with zero share {z:.3f}: {why}"))

    fb = df[(df.sport == "Football") & (df.gender == "men")]
    have = set(fb[["report", "population", "position", "cohort"]].drop_duplicates().itertuples(index=False, name=None))
    for report, population in (("nil_go", "deal_holders"), ("revenue_share", "all_rostered"), ("revenue_share", "paid")):
        for pos in FOOTBALL_POSITIONS:
            for cut in ("A4", "Non-A4"):
                if (report, population, pos, cut) not in have:
                    issues.append(_issue("football_coverage", "error", report, f"{pos} / {cut} / {population}", "missing"))

    sizes = fb.drop_duplicates(["report", "population", "position", "cohort"]).set_index(
        ["report", "population", "position", "cohort"]).n
    for pos in FOOTBALL_POSITIONS:
        for cut in ("A4", "Non-A4"):
            nil, roster = sizes.get(("nil_go", "deal_holders", pos, cut)), sizes.get(("revenue_share", "all_rostered", pos, cut))
            if nil is not None and roster is not None and nil > roster:
                issues.append(_issue("nil_vs_roster", "warning", "nil_go", f"{pos} / {cut}",
                                     f"{nil} deal holders > {roster} rostered; the two systems label positions differently"))
    return issues


# ---------------------------------------------------------------- storage

def _archive_file(archive_dir: Path, report: str, sha: str) -> Path | None:
    return next(iter(sorted(archive_dir.glob(f"{report}_*_{sha[:12]}.json.gz"))), None)


def write_archive(archive_dir: Path, report: str, url: str, payload: dict, first_seen_at: datetime) -> Path:
    """Content-addressed and byte-stable (gzip mtime 0), so re-writing a release never changes git."""
    sha = payload_sha256(payload)
    existing = _archive_file(archive_dir, report, sha)
    if existing:
        return existing
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{report}_{first_seen_at:%Y-%m-%d}_{sha[:12]}.json.gz"
    envelope = {"report": report, "url": url, "first_seen_at": first_seen_at.isoformat(),
                "payload_sha256": sha, "payload": payload}
    path.write_bytes(gzip.compress(json.dumps(envelope, sort_keys=True, indent=1).encode(), mtime=0))
    return path


def read_archive(path: Path) -> dict:
    env = json.loads(gzip.decompress(path.read_bytes()))
    if payload_sha256(env["payload"]) != env["payload_sha256"]:
        raise ValueError(f"{path}: payload does not match its sha256")
    env["first_seen_at"] = datetime.fromisoformat(env["first_seen_at"])
    return env


def store_release(con: duckdb.DuckDBPyConnection, report: str, url: str, payload: dict, seen_at: datetime,
                  archive_dir: Path = ARCHIVE_DIR) -> dict:
    """Record a sighting of `payload`. A new payload is a new release; a known one only moves
    last_seen_at. Either way its rows are re-parsed, so parser fixes reach old releases."""
    sha = payload_sha256(payload)
    cohorts, issues = PARSERS[report](payload)
    prev = con.execute("SELECT first_seen_at, last_seen_at FROM csc_releases WHERE report = ? AND payload_sha256 = ?",
                       [report, sha]).fetchone()
    first, last = (min(prev[0], seen_at), max(prev[1], seen_at)) if prev else (seen_at, seen_at)
    path = write_archive(archive_dir, report, url, payload, first)
    con.execute("DELETE FROM csc_releases WHERE report = ? AND payload_sha256 = ?", [report, sha])
    con.execute("INSERT INTO csc_releases VALUES (?, ?, ?, ?, ?, ?, ?)",
                [report, sha, url, first, last, str(path), len(cohorts)])
    con.execute("DELETE FROM csc_percentiles WHERE report = ? AND payload_sha256 = ?", [report, sha])
    rows = insert_df(con, "csc_percentiles", to_frame(cohorts, sha))
    return {"report": report, "release": sha[:12], "new": prev is None, "cohorts": len(cohorts),
            "rows": rows, "archive": path, "issues": issues}


def ingest(con: duckdb.DuckDBPyConnection, fetcher: Fetcher, fresh: bool = False,
           archive_dir: Path = ARCHIVE_DIR) -> list[dict]:
    out = []
    for report, url in REPORTS.items():
        snap = fetcher.fetch(SOURCE, url, fresh=fresh, ext="html")
        found, payload = extract_payload(snap.body.decode("utf-8", "replace"))
        if found != report:
            raise ValueError(f"{url} carried a {found} payload, expected {report}")
        out.append(store_release(con, report, url, payload, snap.fetched_at, archive_dir))
    return out


def ingest_archive(con: duckdb.DuckDBPyConnection, archive_dir: Path = ARCHIVE_DIR) -> list[dict]:
    out = []
    for path in sorted(archive_dir.glob("*.json.gz")):
        env = read_archive(path)
        out.append(store_release(con, env["report"], env["url"], env["payload"], env["first_seen_at"], archive_dir))
    return out


def export_latest(con: duckdb.DuckDBPyConnection, path: Path | None = None) -> Path:
    """The newest release of each report, one row per cohort, for spreadsheets and git diffs."""
    path = path or config.DATA_DIR / "csc" / "percentiles.csv"
    cells = ", ".join(f"max(value_usd) FILTER (WHERE percentile = {p}) AS p{p}" for p in PERCENTILES)
    wide = con.execute(f"""
        SELECT report, gender, sport, position, cohort, a4, population, n, n_text, {cells},
               window_start, window_end, as_of, left(payload_sha256, 12) AS release
        FROM csc_latest GROUP BY ALL
        ORDER BY report, gender, sport, position, cohort, population""").df()
    path.parent.mkdir(parents=True, exist_ok=True)
    wide.to_csv(path, index=False)
    return path
