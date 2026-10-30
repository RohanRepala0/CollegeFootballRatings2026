import base64
import copy
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from cfbmodel import config, db
from cfbmodel.sources import csc

T0 = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)
URL = csc.REPORTS["nil_go"]

# NIL Go payload in the real page's shape (2026-10-30), trimmed to a few rows.
NIL = {
    "percentileLabels": ["90th Percentile", "75th Percentile", "50th Percentile", "25th Percentile", "10th Percentile"],
    "sports": [
        {"name": "Football", "grouped": True, "horizons": [{
            "label": "1/1/26 – 9/30/26", "start": "2026-01-01", "end": "2026-09-30",
            "thresholds": [100, 40, 10, 40, 100],
            "bands": [
                {"cut": "A4", "rows": [
                    {"label": "Quarterback", "count": 194, "values": [996048.5, 293020.0, 65550.0, 10722.49, 1570.0]},
                    {"label": "Punter", "count": 33, "values": [None, None, 1660.0, None, None]}]},
                {"cut": "Non-A4", "rows": [
                    {"label": "Quarterback", "count": 130, "values": [21805.0, 5388.0, 1570.0, 1500.0, 1155.0]}]}]}]},
        {"name": "Women's Track, Outdoor", "grouped": False, "horizons": [{
            "label": "6/11/25 – 6/30/26", "start": "2025-06-11", "end": "2026-06-30",
            "thresholds": [100, 40, 10, 40, 100],
            "rows": [{"label": "Southeastern Conference", "count": 50, "values": [None, 9000.0, 2000.0, 500.0, None]},
                     {"label": "Non-A4", "count": "<10", "values": [None] * 5}]}]},
        {"name": "Rifle", "grouped": False, "horizons": []},
    ],
}

# The real baseball revenue-share table: A4 has With $0 and Without $0, Non-A4 only Without $0,
# and each group label is drawn at the vertical center of its rows. Coordinates from the real SVG.
COLUMNS = ["Conference", "$0 Amounts", "90th Percentile", "75th Percentile", "50th Percentile",
           "25th Percentile", "10th Percentile"]
BASEBALL_ROWS = [["With $0 (n=2,498)", "$20,000", "$500", "$0", "$0", "$0"],
                 ["Without $0 (n=712)", "$62,000", "$30,000", "$12,000", "$5,000", "$500"],
                 ["Without $0 (n=747)", "$24,500", "$15,000", "$7,500", "$3,000", "$500"]]
BASEBALL_TABLE = {"title": "Men's Baseball", "subtitle": "All Positions", "basis": "Payments in the 2025-26 cap year",
                  "groups": [{"label": "A4 Conferences", "span": 2}, {"label": "Non-A4 Conferences", "span": 1}],
                  "columns": COLUMNS, "rows": BASEBALL_ROWS}
_XS = (551.7, 701.4, 851.0, 1000.7, 1150.4)
_HX = (133.0, 326.8, 494.4, 644.1, 793.8, 943.5, 1093.2)


def _text(x, y, t):
    return f'<text x="{x}" y="{y}">{t}</text>'


def _row(y, cells):
    return _text(251.6, y, cells[0]) + "".join(_text(x, y, c) for x, c in zip(_XS, cells[1:]))


def baseball_svg(rows=BASEBALL_ROWS):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg">'
            "<title>Men's Baseball · All Positions</title>"
            + _text(32, 54.0, "Men's Baseball") + _text(32, 93.4, "All Positions")
            + _text(32, 123.0, "Payments in the 2025-26 cap year")
            + "".join(_text(x, 178.6, c) for x, c in zip(_HX, COLUMNS))
            + '<line x1="234.0" y1="259.1" x2="1168.0" y2="259.1"/>' + _row(232.4, rows[0])
            + '<line x1="32.0" y1="313.5" x2="1168.0" y2="313.5"/>' + _row(286.8, rows[1]) + _row(341.2, rows[2])
            + _text(49.6, 259.6, "A4 Conferences") + _text(49.6, 341.2, "Non-A4 Conferences")
            + '<line x1="233.5" y1="205.2" x2="233.5" y2="368.4"/></svg>')


BASEBALL_FILE = "Mens_Baseball_All_Positions_Revenue_Share_Distribution.svg"
HOOPS_FILE = "Mens_Basketball_All_Positions_Revenue_Share_Distribution_Without_$0_Amounts.svg"
HOOPS_TABLE = {"title": "Men's Basketball", "subtitle": "All Positions · Without $0 Amounts",
               "basis": "Payments from July 1, 2025 to April 6, 2026 · payments due after April 6 are not included",
               "groups": None, "columns": ["Conference"] + COLUMNS[2:],
               "rows": [["Atlantic Coast Conference (n=169)", "$648,000", "$440,000", "$225,000", "$50,000", "$20,500"],
                        ["Non-A4 Conferences (n=2,303)", "$250,000", "$92,500", "$33,000", "$10,500", "$4,500"]]}


def hoops_svg():
    """No group column: each row is a conference, with a full-width rule under every row."""
    rows = HOOPS_TABLE["rows"]
    return ('<svg xmlns="http://www.w3.org/2000/svg">' + _text(32, 123.0, HOOPS_TABLE["basis"])
            + "".join(_text(x, 178.6, c) for x, c in zip((198.1,) + _HX[2:], HOOPS_TABLE["columns"]))
            + "".join(_text(49.6, y, r[0]) + "".join(_text(x, y, c) for x, c in zip(_XS, r[1:]))
                      + f'<line x1="32.0" y1="{y + 26.7}" x2="1168.0" y2="{y + 26.7}"/>'
                      for y, r in zip((232.4, 286.8), rows)) + "</svg>")


def rev_payload(svg=None):
    return {"tablesData": {BASEBALL_FILE: BASEBALL_TABLE, HOOPS_FILE: HOOPS_TABLE},
            "sportsData": {"mens": {"Baseball": {"All Positions": {"combined": BASEBALL_FILE}},
                                    "Basketball": {"All Positions": {"without": HOOPS_FILE}}}, "mixed": {}},
            "TABLE_SVGS": {BASEBALL_FILE: svg or baseball_svg(), HOOPS_FILE: hoops_svg()}}


def _page(js: str) -> str:
    noise = base64.b64encode(b"window.dataLayer=window.dataLayer||[];").decode()
    data = base64.b64encode(js.encode()).decode()
    return (f'<html><script src="data:text/javascript;base64,{noise}"></script>'
            f'<script src="data:text/javascript;base64,{data}"></script></html>')


def test_extract_payload_from_both_pages():
    b64 = base64.b64encode(json.dumps(NIL).encode()).decode()
    assert csc.extract_payload(_page(f'(function(){{"use strict";var B64="{b64}";var root=1}})();')) == ("nil_go", NIL)
    rev = rev_payload()
    js = "".join(f"const {k}={json.dumps(rev[k])};" for k in ("TABLE_SVGS", "sportsData", "tablesData"))
    assert csc.extract_payload(_page("(function(){" + js + "const positions=[];})();")) == ("revenue_share", rev)
    with pytest.raises(ValueError, match="layout changed"):
        csc.extract_payload(_page("var nothing=1;"))


def test_parse_nil_go():
    cohorts, issues = csc.parse_nil_go(NIL)
    by = {(c.sport, c.position, c.cohort): c for c in cohorts}
    qb = by[("Football", "Quarterback", "A4")]
    assert (qb.gender, qb.population, qb.n, qb.values[50], qb.window_end) == ("men", "deal_holders", 194, 65550.0,
                                                                             date(2026, 9, 30))
    assert qb.min_n == {90: 100, 75: 40, 50: 10, 25: 40, 10: 100}
    assert by[("Football", "Punter", "A4")].values[90] is None
    sec = by[("Track & Field (Outdoor)", "All Positions", "SEC")]       # NIL Go's name, CFBD's conference
    assert sec.gender == "women"
    tiny = by[("Track & Field (Outdoor)", "All Positions", "Non-A4")]
    assert (tiny.n, tiny.n_text) == (None, "<10")
    assert [(i["check"], i["key"]) for i in issues] == [("unknown_sport", "Rifle")]   # gender never guessed


def test_svg_rows_follow_group_rules_not_order():
    """A naive pairing of rows would put baseball's Non-A4 row under A4."""
    svg = csc.parse_svg_table(baseball_svg())
    pcts, rows, problems = csc.table_rows(BASEBALL_TABLE)
    assert svg.problems == problems == []
    assert (svg.percentiles, svg.rows) == (pcts, rows) == ([90, 75, 50, 25, 10], [
        ("A4 Conferences", "With $0 (n=2,498)", BASEBALL_ROWS[0][1:]),
        ("A4 Conferences", "Without $0 (n=712)", BASEBALL_ROWS[1][1:]),
        ("Non-A4 Conferences", "Without $0 (n=747)", BASEBALL_ROWS[2][1:])])


def test_parse_revenue_share():
    cohorts, issues = csc.parse_revenue_share(rev_payload())
    assert issues == []
    by = {(c.sport, c.cohort, c.population): c for c in cohorts}
    assert set(by) == {("Baseball", "A4", "all_rostered"), ("Baseball", "A4", "paid"), ("Baseball", "Non-A4", "paid"),
                       ("Basketball", "ACC", "paid"), ("Basketball", "Non-A4", "paid")}
    non_a4 = by[("Baseball", "Non-A4", "paid")]
    assert (non_a4.n, non_a4.values[50], non_a4.window_start, non_a4.window_end) == (747, 7500.0, date(2025, 7, 1),
                                                                                     date(2026, 6, 30))
    acc = by[("Basketball", "ACC", "paid")]
    assert (acc.n, acc.values[90], acc.window_end) == (169, 648000.0, date(2026, 4, 6))


def test_svg_that_disagrees_with_table_data_is_flagged():
    rows = copy.deepcopy(BASEBALL_ROWS)
    rows[2][3] = "$8,000"
    _, issues = csc.parse_revenue_share(rev_payload(baseball_svg(rows)))
    assert [(i["check"], i["key"]) for i in issues] == [("svg_mismatch", BASEBALL_FILE)]


@pytest.mark.parametrize("text, expected", [
    ("Payments in calendar year 2026 · agreements as of September 30, 2026",
     (date(2026, 1, 1), date(2026, 12, 31), date(2026, 9, 30))),
    ("Payments from July 1, 2025 to April 6, 2026 · payments due after April 6 are not included",
     (date(2025, 7, 1), date(2026, 4, 6), None)),
    ("Payments in the 2025-26 cap year", (date(2025, 7, 1), date(2026, 6, 30), None)),
    ("Something new", (None, None, None)),
])
def test_parse_window(text, expected):
    assert csc.parse_window(text) == expected


def _checks(df, ignore=("football_coverage",)):
    return [(i["check"], i["severity"]) for i in csc.check(df) if i["check"] not in ignore]


def test_check_passes_consistent_tables_and_catches_breaks():
    cohorts, _ = csc.parse_revenue_share(rev_payload())
    df = csc.to_frame(cohorts, "sha")
    assert _checks(df) == []

    allr = (df.population == "all_rostered") & (df.sport == "Baseball")
    bad = df.copy()
    bad.loc[allr & (bad.percentile == 90), "value_usd"] = 50000.0    # above paid p75 ($30,000) at q = 0.65
    assert _checks(bad) == [("zero_mass", "error")]
    bad = df.copy()
    bad.loc[allr & (bad.percentile == 50), "value_usd"] = 40000.0    # p50 above p75, and $0 expected below z
    assert sorted(_checks(bad)) == [("monotone", "error"), ("zero_mass", "error")]
    bad = df.copy()
    bad.loc[allr & (bad.percentile == 25), "value_usd"] = None       # n=2,498 publishes every percentile
    assert _checks(bad) == [("suppression", "warning")]


def test_check_reports_missing_football():
    cohorts, _ = csc.parse_nil_go(NIL)
    missing = [i["key"] for i in csc.check(csc.to_frame(cohorts, "sha")) if i["check"] == "football_coverage"]
    assert "Running Back / A4 / deal_holders" in missing
    assert "Quarterback / A4 / deal_holders" not in missing


def test_releases_are_versioned_and_archived(con, tmp_path):
    first = csc.store_release(con, "nil_go", URL, NIL, T0, tmp_path)
    again = csc.store_release(con, "nil_go", URL, NIL, T0 + timedelta(hours=6), tmp_path)
    assert first["new"] and not again["new"]
    assert con.execute("SELECT count(*), min(first_seen_at), max(last_seen_at) FROM csc_releases").fetchone() == (
        1, T0, T0 + timedelta(hours=6))
    assert con.execute("SELECT count(*) FROM csc_percentiles").fetchone()[0] == first["rows"]   # replaced, not doubled

    revised = copy.deepcopy(NIL)
    revised["sports"][0]["horizons"][0]["bands"][0]["rows"][0]["values"][2] = 70000.0
    assert csc.store_release(con, "nil_go", URL, revised, T0 + timedelta(days=1), tmp_path)["new"]
    latest = """SELECT value_usd FROM csc_latest
                WHERE sport = 'Football' AND position = 'Quarterback' AND cohort = 'A4' AND percentile = 50"""
    assert con.execute(latest).fetchone()[0] == 70000.0
    assert con.execute("SELECT count(DISTINCT payload_sha256) FROM csc_percentiles").fetchone()[0] == 2

    files = sorted(tmp_path.glob("nil_go_*.json.gz"))
    assert len(files) == 2 and csc.read_archive(files[0])["payload"] in (NIL, revised)
    rebuilt = db.connect(":memory:")
    db.init_db(rebuilt)
    csc.ingest_archive(rebuilt, tmp_path)
    assert rebuilt.execute(latest).fetchone()[0] == 70000.0
    assert sorted(tmp_path.glob("nil_go_*.json.gz")) == files                     # nothing rewritten


REAL = sorted((config.DATA_DIR / "csc" / "releases").glob("*_2026-10-30_*.json.gz"))


@pytest.mark.skipif(len(REAL) != 2, reason="the 2026-10-30 releases are not on disk")
def test_first_real_release(con, tmp_path):
    for path in REAL:
        env = csc.read_archive(path)
        assert csc.store_release(con, env["report"], env["url"], env["payload"], env["first_seen_at"],
                                 tmp_path)["issues"] == []
    issues = csc.check(con.execute("SELECT * FROM csc_latest").df())
    assert [(i["check"], i["key"]) for i in issues] == [("nil_vs_roster", "Position Unspecified / A4")]

    def cell(report, sport, position, cohort, population, col="n", percentile=50):
        return con.execute(f"""SELECT {col} FROM csc_latest WHERE report = ? AND sport = ? AND position = ?
                               AND cohort = ? AND population = ? AND percentile = ?""",
                           [report, sport, position, cohort, population, percentile]).fetchone()[0]

    assert cell("revenue_share", "Football", "Quarterback", "A4", "paid", "value_usd") == 230000
    assert cell("revenue_share", "Football", "Quarterback", "A4", "all_rostered") == 367
    assert cell("revenue_share", "Baseball", "All Positions", "Non-A4", "paid") == 747      # the one-row group
    assert cell("nil_go", "Football", "Quarterback", "A4", "deal_holders") == 194
    assert cell("nil_go", "Basketball", "All Positions", "SEC", "deal_holders") == 155
