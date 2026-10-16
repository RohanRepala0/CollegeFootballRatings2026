"""SP+ (Bill Connelly) season sheets, one tab per week (plan section 2.2).

Two stages:

  A. Sheet parse (no CFBD needed). Tabs become `SheetGame` records keyed by the
     sheet's own team names, plus the ratings side table, the postgame results
     tab and the `SP+ performance` tab. Parsing is by header name, never column
     letter. Header rows hold two tables side by side (ratings left of the games
     in 2024, right of them in 2025-2026); they are split at the blank column.
  B. CFBD join. Names resolve to team_ids, rows join to CFBD games and come out
     as standard ProjectionRow / LineRow / RatingRow.

Layout facts verified on the owner's 2024, 2025 and 2026 workbooks:
  - `Spread` names the favorite ("Georgia -7.5"). A bare number is logged as an
    issue and skipped rather than guessed.
  - `Win prob.` is a fraction (0.6); CSV exports may show "60%". Both parse.
  - Neutral sites are "A vs. B"; everything else is "Away at Home".
  - 2025 Week 0 is a short "Team 35.4, Team 21.2" list with no lines.
  - Excel turned some "W-L" and "12-10" strings into dates; those are undone.
  - `SP+ performance` reports ATS vs the early-week spread only (not the close),
    with weeks 0 and 1 combined as "0-1" and a single "Postseason" row.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd

from .. import config
from ..conventions import bet_outcome, sign_to_home, spread_to_home
from ..fetch import Fetcher
from ..teams import TeamResolver, normalize
from .base import GameIndex, LineRow, ParseResult, ProjectionRow, RatingRow

SOURCE = "sp_plus"
LINE_PROVIDER = "sp_sheet"

# ---------------------------------------------------------------- tabs

_WEEK_PATTERNS = [
    re.compile(r"^\s*fbs\s+week\s+(\d+)\s*$", re.I),
    re.compile(r"^\s*week\s+(\d+)\s+fbs\s*$", re.I),
]
_BOWL_PATTERN = re.compile(r"^\s*fbs\b.*\bbowl", re.I)
_EXCLUDE = re.compile(r"\b(fcs|d2|d3|div|naia|top|final|postgame)\b", re.I)


def classify_tab(title: str) -> tuple[str, int | None] | None:
    """('week', N) for an FBS week tab, ('bowl', None) for the bowl tab, else None."""
    if _EXCLUDE.search(title):
        return None
    for pat in _WEEK_PATTERNS:
        m = pat.match(title)
        if m:
            return ("week", int(m.group(1)))
    if _BOWL_PATTERN.search(title):
        return ("bowl", None)
    return None


def is_results_tab(title: str) -> bool:
    return bool(re.match(r"^\s*postgame win expectancy\s*$", title, re.I))


def is_performance_tab(title: str) -> bool:
    return bool(re.match(r"^\s*sp\+\s*performance\s*$", title, re.I))


def parse_pubhtml_tabs(html: str) -> list[dict]:
    """Tab names and gids from a published sheet's /pubhtml page."""
    import html as _html

    tabs: dict[str, str] = {}
    for gid, name in re.findall(r'id="sheet-button-(\d+)"[^>]*>\s*<a[^>]*>([^<]+)</a>', html):
        tabs[gid] = name
    for name, gid in re.findall(r'name:\s*"([^"]+)"[^}]*?gid:\s*"(\d+)"', html):
        tabs.setdefault(gid, name)
    return [{"title": _html.unescape(name).strip(), "gid": gid} for gid, name in tabs.items()]


# ---------------------------------------------------------------- cells


def cell_to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0) else v.isoformat()
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v).strip()


def rows_from_csv(text: str) -> list[list[str]]:
    return _pad([[c.strip() for c in r] for r in csv.reader(io.StringIO(text))])


def rows_from_xlsx(ws) -> list[list[str]]:
    return _pad([[cell_to_str(c) for c in r] for r in ws.iter_rows(values_only=True)])


def _pad(rows: list[list[str]]) -> list[list[str]]:
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def unmangle_pair(text: str) -> str:
    """Excel stores '4-5' typed into a cell as 2024-04-05. Undo that: '4-5'."""
    m = _ISO_DATE.match(text.strip())
    if m:
        return f"{int(m.group(2))}-{int(m.group(3))}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/\d{2,4})?$", text.strip())   # Sheets API formatted date
    return f"{int(m.group(1))}-{int(m.group(2))}" if m else text


def parse_number(text: str) -> float | None:
    t = text.strip().replace("%", "").replace("+", "")
    if re.fullmatch(r"(?i)pk|pick|pick'?em|even", t):
        return 0.0
    try:
        return float(t)
    except ValueError:
        return None


def parse_prob(text: str) -> float | None:
    v = parse_number(text)
    if v is None:
        return None
    return v / 100 if ("%" in text or v > 1) else v


def parse_spread_cell(text: str) -> tuple[str | None, float | None]:
    """('Georgia', -7.5) for 'Georgia -7.5'; (None, -7.5) for a bare '-7.5'."""
    t = text.strip()
    if not t:
        return None, None
    plain = parse_number(t)
    if plain is not None:
        return None, plain
    m = re.match(r"^(.+?)\s+([+-]?\d+(?:\.\d+)?|pk|pick)\s*$", t, re.I)
    if m:
        return m.group(1).strip(), parse_number(m.group(2))
    return None, None


def parse_score_total(text: str) -> float | None:
    nums = re.findall(r"\d+(?:\.\d+)?", unmangle_pair(text))
    return float(nums[0]) + float(nums[1]) if len(nums) == 2 else None


_RANK_PREFIX = re.compile(r"(?:^|(?<=\s))(?:no\.\s*|#)\d+\s+", re.I)


def parse_game(text: str) -> tuple[str, str, bool] | None:
    """('Away', 'Home', False) for 'Away at Home'; ('A', 'B', True) for 'A vs. B'."""
    t = _RANK_PREFIX.sub("", text.strip())
    parts = re.split(r"\s+vs\.?\s+", t, flags=re.I)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), True
    parts = re.split(r"\s+at\s+", t)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), False
    return None


def _parse_date(text: str) -> date | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text.strip())
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    ts = pd.to_datetime(text, errors="coerce") if re.search(r"\b(19|20)\d{2}\b", text) else pd.NaT
    return None if ts is pd.NaT else ts.date()


# ---------------------------------------------------------------- headers


def norm_header(h: str) -> str:
    s = h.strip().lower().replace("/", " ")
    s = re.sub(r"[.()%:]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


GAME_HEADERS = {
    "date": "date", "time et": "time", "time": "time",
    "game": "game", "matchup": "game",
    "proj winner": "proj_winner", "projected winner": "proj_winner",
    "proj margin": "proj_margin", "projected margin": "proj_margin",
    "win prob": "win_prob", "win probability": "win_prob",
    "proj score rounded": "proj_score", "proj score": "proj_score", "projected score": "proj_score",
    "spread": "spread", "line": "spread",
    "ats pick": "ats_pick", "spread diff": "spread_diff",
    "o u": "total", "ou": "total", "over under": "total",
    "o u pick": "ou_pick", "o u diff": "ou_diff",
}
SCORES_HEADERS = {"date": "date", "fbs vs fbs": "scores"}
RATING_HEADERS = {
    "team": "team", "conference": "conference", "conf": "conference",
    "rec": "record", "record": "record",
    "sp+": "sp_plus", "rk": "rank", "off sp+": "off", "def sp+": "def", "st sp+": "st",
    "lw": "last_week", "lw rk": "last_week_rank", "rk chg": "rank_change",
}
RESULTS_HEADERS = {
    "date": "date", "team": "team", "opponent": "opponent",
    "pts": "pts", "score": "pts", "opp pts": "opp_pts", "oppscore": "opp_pts",
    "h a": "home_away", "gametype": "game_type", "conf": "conf", "w l": "win", "win": "win",
    "margin": "margin", "pgwe": "pgwe", "adj mgn": "adj_margin",
}


@dataclass
class Table:
    header_row: int
    cols: dict[str, int]
    unknown: list[str]


def _segments(row: list[str]) -> list[tuple[int, int]]:
    """Contiguous runs of non-empty header cells, as [start, end) column ranges."""
    segs, start = [], None
    for c, cell in enumerate(row + [""]):
        if cell.strip() and start is None:
            start = c
        elif not cell.strip() and start is not None:
            segs.append((start, c))
            start = None
    return segs


def find_table(rows: list[list[str]], required: set[str], mapping: dict[str, str], max_scan: int = 10) -> Table | None:
    for r, row in enumerate(rows[:max_scan]):
        for start, end in _segments(row):
            names = [mapping.get(norm_header(c)) for c in row[start:end]]
            if required <= set(names):
                cols, unknown = {}, []
                for c in range(start, end):
                    key = mapping.get(norm_header(row[c]))
                    if key and key not in cols:
                        cols[key] = c
                    elif not key:
                        unknown.append(row[c])
                return Table(r, cols, unknown)
    return None


# ---------------------------------------------------------------- stage A: sheet records


@dataclass
class SheetGame:
    season: int
    week: int | None
    season_type: str
    tab: str
    row: int
    date: date | None
    team1: str                  # away team, or first-listed team at a neutral site
    team2: str                  # home team, or second-listed team at a neutral site
    neutral: bool
    proj_winner: str | None
    proj_margin: float | None   # unsigned, from the projected winner's side
    win_prob: float | None      # projected winner's win probability
    proj_total: float | None
    spread_team: str | None
    spread: float | None        # book spread for spread_team
    ats_pick: str | None
    total_line: float | None
    oriented: bool = True       # False when the sheet does not say who is home

    def margin_for(self, team: str) -> float | None:
        """Projected margin from `team`'s side."""
        if self.proj_margin is None:
            return None
        if self.proj_margin == 0:
            return 0.0
        if self.proj_winner is None:
            return None
        return self.proj_margin if _same(team, self.proj_winner) else -self.proj_margin

    def spread_for(self, team: str) -> float | None:
        if self.spread is None or self.spread_team is None:
            return None
        return self.spread if _same(team, self.spread_team) else -self.spread


def _same(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and normalize(a) == normalize(b)


def resolve_from_ats(team1: str, team2: str, winner: str | None, spread_team: str | None, spread: float | None,
                     margin: float | None, pick: str | None, diff: float | None) -> tuple[str, str] | None:
    """(winner, spread_team) as team1/team2 names, for rows that abbreviate them ('GSU', 'App. St. -5').

    The full-name ATS pick and the spread diff (model minus market margin, from the pick's side) fix
    both sides. `winner`/`spread_team` are passed only when already matched. None unless exactly one
    reading fits.
    """
    if margin is None or spread is None or diff is None or pick is None:
        return None
    if not (_same(pick, team1) or _same(pick, team2)):
        return None
    fits = {}
    for w in [winner] if winner else [team1, team2]:
        for s in [spread_team] if spread_team else [team1, team2]:
            model = margin if _same(w, pick) else -margin
            market = -spread if _same(s, pick) else spread
            if abs(model - market - diff) < 0.15:
                key = (margin if _same(w, team1) else -margin, spread if _same(s, team1) else -spread)
                fits[key] = (w, s)
    return next(iter(fits.values())) if len(fits) == 1 else None


@dataclass
class SheetRating:
    season: int
    week: int | None
    tab: str
    team: str
    rating: float


@dataclass
class SheetTab:
    games: list[SheetGame]
    ratings: list[SheetRating]
    issues: list[dict]


def parse_week_rows(rows: list[list[str]], season: int, week: int | None, season_type: str, tab: str) -> SheetTab:
    issues: list[dict] = []
    games: list[SheetGame] = []
    ratings: list[SheetRating] = []

    rt = find_table(rows, {"team", "sp_plus"}, RATING_HEADERS)
    if rt is not None:
        for r in range(rt.header_row + 1, len(rows)):
            team = rows[r][rt.cols["team"]].strip()
            if not team:
                break
            value = parse_number(rows[r][rt.cols["sp_plus"]])
            if value is not None:
                ratings.append(SheetRating(season, week, tab, team, value))

    gt = find_table(rows, {"game", "proj_margin"}, GAME_HEADERS)
    if gt is None:
        st = find_table(rows, {"scores"}, SCORES_HEADERS)
        if st is not None:
            games += _parse_score_list(rows, st, season, week, season_type, tab, issues)
        else:
            issues.append({"tab": tab, "issue": "no_game_table"})
        return SheetTab(games, ratings, issues)

    for u in gt.unknown:
        issues.append({"tab": tab, "issue": "unrecognized_header", "header": u})
    for r in range(gt.header_row + 1, len(rows)):
        row = rows[r]

        def cell(k: str) -> str:
            return row[gt.cols[k]].strip() if k in gt.cols else ""

        game_text = cell("game")
        if not game_text:
            continue
        parsed = parse_game(game_text)
        if parsed is None:
            issues.append({"tab": tab, "row": r, "issue": "unparsed_game", "text": game_text})
            continue
        team1, team2, neutral = parsed
        proj_margin = parse_number(cell("proj_margin")) if cell("proj_margin") else None
        ats = re.sub(r"\s+[+-]?\d+(?:\.\d+)?\s*$", "", cell("ats_pick")) or None
        winner = cell("proj_winner") or None
        winner_ok = winner is None or _same(winner, team1) or _same(winner, team2)
        spread_team, spread = parse_spread_cell(cell("spread"))
        if spread is not None and spread_team is None:
            issues.append({"tab": tab, "row": r, "issue": "bare_number_spread", "text": cell("spread")})
            spread = None
        spread_ok = spread_team is None or _same(spread_team, team1) or _same(spread_team, team2)
        if not (winner_ok and spread_ok):
            fixed = resolve_from_ats(team1, team2, winner if winner_ok else None, spread_team if spread_ok else None,
                                     spread, proj_margin, ats, parse_number(cell("spread_diff")))
            if fixed is not None:
                issues.append({"tab": tab, "row": r, "issue": "resolved_from_ats_pick", "text": game_text,
                               "winner": winner, "spread": cell("spread"), "resolved": f"{fixed[0]} | {fixed[1]}"})
                winner, spread_team = fixed
            else:
                if not winner_ok:
                    issues.append({"tab": tab, "row": r, "issue": "winner_not_in_game", "text": game_text,
                                   "winner": winner})
                    winner = None
                if not spread_ok:
                    issues.append({"tab": tab, "row": r, "issue": "spread_team_not_in_game", "text": cell("spread")})
                    spread_team, spread = None, None
        games.append(
            SheetGame(
                season=season, week=week, season_type=season_type, tab=tab, row=r,
                date=_parse_date(cell("date")) if cell("date") else None,
                team1=team1, team2=team2, neutral=neutral,
                proj_winner=winner,
                proj_margin=proj_margin,
                win_prob=parse_prob(cell("win_prob")) if cell("win_prob") else None,
                proj_total=parse_score_total(cell("proj_score")) if cell("proj_score") else None,
                spread_team=spread_team, spread=spread,
                ats_pick=ats,
                total_line=parse_number(cell("total")) if cell("total") else None,
            )
        )
    return SheetTab(games, ratings, issues)


_SCORE_ITEM = re.compile(r"^(?P<a>.+?)\s+(?P<pa>\d+(?:\.\d+)?),\s*(?P<b>.+?)\s+(?P<pb>\d+(?:\.\d+)?)$")


def _parse_score_list(rows, st: Table, season, week, season_type, tab, issues) -> list[SheetGame]:
    """2025 Week 0 style: 'Kansas 35.4, Fresno State 21.2'. Home/away comes from CFBD."""
    out = []
    for r in range(st.header_row + 1, len(rows)):
        text = rows[r][st.cols["scores"]].strip()
        if not text:
            continue
        m = _SCORE_ITEM.match(text)
        if not m:
            issues.append({"tab": tab, "row": r, "issue": "unparsed_score_item", "text": text})
            continue
        pa, pb = float(m["pa"]), float(m["pb"])
        out.append(
            SheetGame(
                season=season, week=week, season_type=season_type, tab=tab, row=r,
                date=_parse_date(rows[r][st.cols["date"]]) if "date" in st.cols else None,
                team1=m["a"].strip(), team2=m["b"].strip(), neutral=False,
                proj_winner=m["a"].strip() if pa >= pb else m["b"].strip(),
                proj_margin=round(abs(pa - pb), 1), win_prob=None, proj_total=pa + pb,
                spread_team=None, spread=None, ats_pick=None, total_line=None, oriented=False,
            )
        )
    if out:
        issues.append({"tab": tab, "issue": "score_list_format", "n": len(out),
                       "note": "no home/away in sheet; orientation comes from the CFBD join"})
    return out


def parse_results_rows(rows: list[list[str]]) -> pd.DataFrame:
    """Postgame results tab -> one row per game: date, team1, team2, pts1, pts2."""
    t = find_table(rows, {"date", "team", "opponent", "pts", "opp_pts"}, RESULTS_HEADERS)
    if t is None:
        return pd.DataFrame(columns=["date", "team1", "team2", "pts1", "pts2"])
    seen, out = set(), []
    for r in range(t.header_row + 1, len(rows)):
        row = rows[r]
        team, opp = row[t.cols["team"]].strip(), row[t.cols["opponent"]].strip()
        d = _parse_date(row[t.cols["date"]]) if row[t.cols["date"]] else None
        pts, opp_pts = parse_number(row[t.cols["pts"]]), parse_number(row[t.cols["opp_pts"]])
        if not team or not opp or d is None or pts is None or opp_pts is None:
            continue
        key = (d, frozenset((normalize(team), normalize(opp))))
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": d, "team1": team, "team2": opp, "pts1": pts, "pts2": opp_pts})
    return pd.DataFrame(out)


def parse_performance_rows(rows: list[list[str]]) -> pd.DataFrame:
    """Bill's weekly ATS record vs the early-week spread (FBS vs FBS), top block only."""
    t = None
    for r, row in enumerate(rows[:5]):
        cells = [norm_header(c) for c in row]
        if cells and cells[0] == "week" and any(c.startswith("ats record") for c in cells):
            t = (r, next(i for i, c in enumerate(cells) if c.startswith("ats record")),
                 next((i for i, c in enumerate(cells) if c.startswith("abs error")), None))
            break
    if t is None:
        return pd.DataFrame(columns=["week_label", "w", "l", "p", "abs_error"])
    hdr, ats_col, err_col = t
    out = []
    for row in rows[hdr + 1:]:
        label = row[0].strip()
        if label.upper() == "TOTAL" or label.upper().startswith("LAST YEAR"):
            break
        if not label:
            continue
        rec = re.match(r"^\s*(\d+)-(\d+)(?:-(\d+))?\s*$", unmangle_pair(row[ats_col]))
        if not rec:
            continue
        out.append({
            "week_label": label,
            "w": int(rec.group(1)), "l": int(rec.group(2)), "p": int(rec.group(3) or 0),
            "abs_error": parse_number(row[err_col]) if err_col is not None and row[err_col] else None,
        })
    return pd.DataFrame(out)


@dataclass
class SheetSeason:
    season: int
    games: list[SheetGame]
    ratings: list[SheetRating]
    results: pd.DataFrame
    performance: pd.DataFrame
    issues: list[dict]


def _tab_meta(title: str) -> tuple[int | None, str] | None:
    kind = classify_tab(title)
    if kind is None:
        return None
    return (kind[1], "regular") if kind[0] == "week" else (None, "postseason")


def read_workbook(path: Path, season: int) -> SheetSeason:
    """Stage A for an .xlsx download of a season sheet."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    games, ratings, issues = [], [], []
    results = performance = None
    for ws in wb.worksheets:
        meta = _tab_meta(ws.title)
        if meta is not None:
            tab = parse_week_rows(rows_from_xlsx(ws), season, meta[0], meta[1], ws.title)
            games += tab.games
            ratings += tab.ratings
            issues += tab.issues
        elif is_results_tab(ws.title):
            results = parse_results_rows(rows_from_xlsx(ws))
        elif is_performance_tab(ws.title):
            performance = parse_performance_rows(rows_from_xlsx(ws))
    if results is not None and len(results):
        # Bill reuses the tab: the 2024 workbook's copy holds 2025 games. Use it only
        # when most Aug-Dec dates fall in this season; January dates are too
        # unreliable to judge by (bowls are sometimes typed with last year's year).
        fall = [d for d in results.date if d.month >= 8]
        share = sum(d.year == season for d in fall) / len(fall) if fall else 0.0
        if share < 0.5:
            issues.append({"tab": "postgame win expectancy", "issue": "results_tab_other_season",
                           "note": f"only {share:.0%} of Aug-Dec results dated {season}; tab ignored"})
            results = parse_results_rows([])
        else:
            keep = [(d.year == season) or (d.year == season + 1 and d.month <= 1) for d in results.date]
            results = results[keep].reset_index(drop=True)
    return SheetSeason(season, games, ratings,
                       results if results is not None else parse_results_rows([]),
                       performance if performance is not None else parse_performance_rows([]),
                       issues)


# ---------------------------------------------------------------- acceptance test (plan section 2.2)


def week_label(g: SheetGame) -> str:
    if g.season_type == "postseason":
        return "Postseason"
    return "0-1" if g.week in (0, 1) else str(g.week)


def grade_against_results(season: SheetSeason) -> pd.DataFrame:
    """Every week-tab game with an early spread and a final score, graded on Bill's ATS pick.

    FBS membership comes from that week's ratings side table.
    """
    fbs_by_tab: dict[str, set[str]] = {}
    for r in season.ratings:
        fbs_by_tab.setdefault(r.tab, set()).add(normalize(r.team))
    res = {}
    for r in season.results.itertuples():
        res.setdefault(frozenset((normalize(r.team1), normalize(r.team2))), []).append(r)

    rows = []
    for g in season.games:
        key = frozenset((normalize(g.team1), normalize(g.team2)))
        cands = res.get(key, [])
        if len(cands) > 1 and g.date is not None:
            # Date only breaks ties (rematches). Bill's January bowl dates can carry the
            # previous year (typed in December without one), so retry a year later.
            for d in (g.date, g.date.replace(year=g.date.year + 1)):
                near = [c for c in cands if abs((c.date - d).days) <= 1]
                if near:
                    cands = near
                    break
        final = cands[0] if len(cands) == 1 else None
        fbs = fbs_by_tab.get(g.tab, set())
        row = {
            "week_label": week_label(g), "tab": g.tab, "team1": g.team1, "team2": g.team2,
            "fbs_vs_fbs": normalize(g.team1) in fbs and normalize(g.team2) in fbs,
            "has_spread": g.spread is not None, "has_final": final is not None, "result": None,
            "abs_error": None,
        }
        if final is not None:
            margin1 = final.pts1 - final.pts2 if _same(final.team1, g.team1) else final.pts2 - final.pts1
            proj1 = g.margin_for(g.team1)
            if proj1 is not None:
                row["abs_error"] = abs(proj1 - margin1)
            if g.spread is not None and g.ats_pick:
                pick_margin = margin1 if _same(g.ats_pick, g.team1) else -margin1
                row["result"] = bet_outcome(pick_margin, "home", g.spread_for(g.ats_pick))
        rows.append(row)
    return pd.DataFrame(rows)


def reproduce_performance(season: SheetSeason, tolerance: int = 2) -> pd.DataFrame:
    """Our weekly ATS record (FBS vs FBS, Bill's picks, early spread) next to Bill's."""
    graded = grade_against_results(season)
    fbs = graded[graded.fbs_vs_fbs & graded.has_spread & graded.has_final]
    ours = (fbs.groupby("week_label").result.value_counts().unstack(fill_value=0)
            .reindex(columns=["win", "loss", "push"], fill_value=0)
            .rename(columns={"win": "w", "loss": "l", "push": "p"}).reset_index())
    errs = graded[graded.fbs_vs_fbs & graded.has_final].groupby("week_label").abs_error.mean().round(1)
    ours = ours.merge(errs.rename("abs_error").reset_index(), on="week_label", how="outer")
    bills = season.performance.rename(columns={"w": "w_bill", "l": "l_bill", "p": "p_bill",
                                               "abs_error": "abs_error_bill"})
    m = bills.merge(ours, on="week_label", how="left")
    for c in ("w", "l", "p"):
        m[c] = m[c].fillna(0).astype(int)
    m["ok"] = ((m.w - m.w_bill).abs() <= tolerance) & ((m.l - m.l_bill).abs() <= tolerance)
    order = {"0-1": 1, "Postseason": 99}
    m["_o"] = m.week_label.map(lambda x: order.get(x, int(x) if str(x).isdigit() else 98))
    return m.sort_values("_o").drop(columns="_o").reset_index(drop=True)


# ---------------------------------------------------------------- stage B: CFBD join


def to_parse_result(
    sheet_games: list[SheetGame],
    sheet_ratings: list[SheetRating],
    games: GameIndex,
    resolver: TeamResolver,
    fetched_at: datetime,
    published_at: dict[tuple[int | None, str], datetime],
    published_inferred: bool,
) -> ParseResult:
    """published_at is keyed by (week, season_type) so archived tabs can carry inferred times."""
    out = ParseResult()
    suffix = "|published_at=inferred" if published_inferred else ""
    for g in sheet_games:
        pub = published_at.get((g.week, g.season_type))
        if pub is None:
            out.issues.append({"tab": g.tab, "row": g.row, "issue": "no_published_at"})
            continue
        id1, id2 = resolver.resolve(SOURCE, g.team1), resolver.resolve(SOURCE, g.team2)
        if id1 is None or id2 is None:
            continue
        when = datetime.combine(g.date, time(12)) if g.date else None
        game = games.find(g.season, id1, id2, week=g.week, date=when,
                          season_type=g.season_type if g.season_type == "postseason" else None)
        if game is None:
            out.issues.append({"tab": g.tab, "row": g.row, "issue": "no_cfbd_game", "text": f"{g.team1} / {g.team2}"})
            continue
        game_id, home_id = int(game.game_id), int(game.home_id)
        home_name = g.team1 if id1 == home_id else g.team2
        if g.oriented and not g.neutral and not game.neutral_site and id2 != home_id:
            out.issues.append({"tab": g.tab, "row": g.row, "issue": "home_away_disagrees", "game_id": game_id})
        if g.neutral and not game.neutral_site:
            out.issues.append({"tab": g.tab, "row": g.row, "issue": "vs_but_cfbd_not_neutral", "game_id": game_id})

        exp_margin = g.margin_for(home_name)
        if exp_margin is None:
            out.issues.append({"tab": g.tab, "row": g.row, "issue": "no_projection", "game_id": game_id})
            continue
        p = None
        if g.win_prob is not None:
            p = g.win_prob if _same(g.proj_winner, home_name) else 1 - g.win_prob
        out.projections.append(ProjectionRow(game_id, SOURCE, exp_margin, True, pub, fetched_at,
                                             home_win_prob=p, exp_total=g.proj_total,
                                             raw_ref=f"{g.tab}|row{g.row}{suffix}"))
        home_spread = g.spread_for(home_name)
        if home_spread is not None:
            out.lines.append(LineRow(game_id, LINE_PROVIDER, "early", home_spread, fetched_at, total=g.total_line))
    for r in sheet_ratings:
        if r.week is None:
            continue
        pub = published_at.get((r.week, "regular"))
        team_id = resolver.resolve(SOURCE, r.team)
        if team_id is not None and pub is not None:
            out.ratings.append(RatingRow("sp_plus_rating", r.season, r.week, team_id, r.rating, pub, fetched_at,
                                         f"{r.tab}{suffix}"))
    return out


def inferred_published_at(games: GameIndex, season: int, sheet_games: list[SheetGame]) -> dict:
    """One hour before each tab-week's first kickoff (archives carry no timestamp)."""
    out = {}
    for week, stype in {(g.week, g.season_type) for g in sheet_games}:
        if week is not None:
            out[(week, stype)] = games.inferred_published_at(season, week, stype)
        else:
            post = games.games[(games.games.season == season) & (games.games.season_type == "postseason")]
            if len(post):
                out[(None, stype)] = (post.start_date.min() - pd.Timedelta(hours=1)).to_pydatetime()
    return {k: v for k, v in out.items() if v is not None}


# ---------------------------------------------------------------- online fetching


def discover_tabs(fetcher: Fetcher, season: int, google_api_key: str | None, refresh: bool = False) -> list[dict]:
    cache = config.REFERENCE_DIR / f"sp_tabs_{season}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    spec = config.sources()["sp_plus"][season]
    if spec["type"] == "sheet":
        if not google_api_key:
            raise RuntimeError("GOOGLE_API_KEY is needed to list SP+ sheet tabs (plan section 2.2).")
        body = fetcher.get("sp_plus", f"https://sheets.googleapis.com/v4/spreadsheets/{spec['id']}",
                           params={"fields": "sheets.properties(sheetId,title)", "key": google_api_key},
                           fresh=refresh, ext="json")
        tabs = [{"title": s["properties"]["title"], "gid": str(s["properties"]["sheetId"])}
                for s in json.loads(body)["sheets"]]
    else:
        body = fetcher.get("sp_plus", f"https://docs.google.com/spreadsheets/d/e/{spec['pub_id']}/pubhtml",
                           fresh=refresh, ext="html")
        tabs = parse_pubhtml_tabs(body.decode("utf-8", "replace"))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(tabs, indent=2))
    return tabs


def tab_csv_url(season: int, gid: str) -> str:
    spec = config.sources()["sp_plus"][season]
    if spec["type"] == "sheet":
        return f"https://docs.google.com/spreadsheets/d/{spec['id']}/export?format=csv&gid={gid}"
    return f"https://docs.google.com/spreadsheets/d/e/{spec['pub_id']}/pub?output=csv&gid={gid}"


def _tab_rows(fetcher: Fetcher, season: int, tab: dict, google_api_key: str | None, fresh: bool):
    """One tab's cells. Regular sheets go through the Sheets API v4 values endpoint
    when a key is set (sheets.googleapis.com); published-to-web sheets, or no key,
    use the CSV export on docs.google.com."""
    spec = config.sources()["sp_plus"][season]
    if spec["type"] == "sheet" and google_api_key:
        from urllib.parse import quote

        rng = quote("'" + tab["title"].replace("'", "''") + "'", safe="")
        snap = fetcher.fetch("sp_plus", f"https://sheets.googleapis.com/v4/spreadsheets/{spec['id']}/values/{rng}",
                             params={"key": google_api_key, "valueRenderOption": "FORMATTED_VALUE"},
                             fresh=fresh, ext="json")
        values = json.loads(snap.body).get("values", [])
        return snap, _pad([[str(c).strip() for c in r] for r in values])
    snap = fetcher.fetch("sp_plus", tab_csv_url(season, tab["gid"]), fresh=fresh, ext="csv")
    return snap, rows_from_csv(snap.body.decode("utf-8", "replace"))


def fetch_season(fetcher: Fetcher, season: int, google_api_key: str | None,
                 live_week: int | None = None) -> tuple[SheetSeason, dict]:
    """Stage A over the live Google Sheet, tab by tab. Returns the season and,
    for a live week, {(week, 'regular'): fetched_at} to use as published_at."""
    games, ratings, issues, live_pub = [], [], [], {}
    results = performance = None
    for t in discover_tabs(fetcher, season, google_api_key, refresh=live_week is not None):
        meta = _tab_meta(t["title"])
        if meta is None and not is_results_tab(t["title"]) and not is_performance_tab(t["title"]):
            continue
        live = meta is not None and live_week is not None and meta[0] == live_week
        fresh = live or (meta is None and live_week is not None)
        snap, rows = _tab_rows(fetcher, season, t, google_api_key, fresh)
        if meta is not None:
            tab = parse_week_rows(rows, season, meta[0], meta[1], t["title"])
            games += tab.games
            ratings += tab.ratings
            issues += tab.issues
            if live:
                live_pub[(meta[0], meta[1])] = snap.fetched_at
        elif is_results_tab(t["title"]):
            results = parse_results_rows(rows)
        else:
            performance = parse_performance_rows(rows)
    return (SheetSeason(season, games, ratings,
                        results if results is not None else parse_results_rows([]),
                        performance if performance is not None else parse_performance_rows([]),
                        issues),
            live_pub)
