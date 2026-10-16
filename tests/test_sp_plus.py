"""SP+ sheet parsing. Rows mirror the real 2024-2026 layouts (header names, side tables,
'Team -X' spreads, 'vs.' neutral sites, the 2025 Week 0 score list)."""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from cfbmodel import db
from cfbmodel.sources import sp_plus
from cfbmodel.sources.base import GameIndex
from cfbmodel.teams import TeamResolver

GAME_HDR = ["Date", "Time (ET)", "Game", "Proj. winner", "Proj. margin", "Win prob.", "Proj. score (rounded)",
            "Spread", "ATS Pick", "Spread diff", "O/U", "O/U pick", "O/U diff"]
RATING_HDR = ["Team", "Conference", "Rec.", "SP+", "Rk", "Off. SP+", "Rk", "Def. SP+", "Rk", "ST SP+", "Rk"]


def games_right_of_ratings():   # 2024 layout
    return [RATING_HDR + [""] + GAME_HDR,
            ["Georgia", "SEC", "4-0", "29.9", "1", "40", "1", "11", "1", "0.4", "4", "",
             "2024-09-28", "12:00", "Auburn at Georgia", "Georgia", "10.5", "0.77", "31-21", "Georgia -7.5",
             "Georgia", "3", "48.5", "Over", "3.5"],
            ["Auburn", "SEC", "2-2", "15.0", "20", "30", "40", "15", "15", "0.1", "50", "",
             "2024-09-28", "15:30", "Army vs. Navy", "Navy", "2.0", "0.55", "21-19", "Army -1.5",
             "Navy", "3.5", "39.5", "Over", "1"]]


def games_left_of_ratings():    # 2025/2026 layout
    rows = games_right_of_ratings()
    return [r[12:] + [""] + r[:11] for r in rows]


@pytest.mark.parametrize("rows", [games_right_of_ratings(), games_left_of_ratings()])
def test_parse_week_rows_both_layouts(rows):
    tab = sp_plus.parse_week_rows(rows, 2024, 5, "regular", "FBS Week 5")
    assert tab.issues == []
    g1, g2 = tab.games
    assert (g1.team1, g1.team2, g1.neutral) == ("Auburn", "Georgia", False)
    assert g1.margin_for("Georgia") == 10.5 and g1.margin_for("Auburn") == -10.5
    assert g1.spread_for("Georgia") == -7.5 and g1.spread_for("Auburn") == 7.5
    assert g1.proj_total == 52 and g1.win_prob == 0.77 and g1.date == date(2024, 9, 28)
    assert g2.neutral and g2.margin_for("Army") == -2.0 and g2.spread_for("Army") == -1.5
    assert {r.team: r.rating for r in tab.ratings} == {"Georgia": 29.9, "Auburn": 15.0}


def test_score_list_week0():
    rows = [["Team", "Conference", "SP+", "", "date", "FBS vs. FBS"],
            ["Ohio State", "Big Ten", "26.1", "", "2025-08-23", "Kansas 35.4, Fresno State 21.2"]]
    tab = sp_plus.parse_week_rows(rows, 2025, 0, "regular", "FBS Week 0")
    g = tab.games[0]
    assert not g.oriented and g.margin_for("Fresno State") == pytest.approx(-14.2) and g.spread is None


def test_bare_number_spread_is_not_guessed():
    rows = [GAME_HDR, ["2024-09-28", "", "Auburn at Georgia", "Georgia", "10.5", "0.77", "31-21", "-7.5",
                       "Georgia", "", "", "", ""]]
    tab = sp_plus.parse_week_rows(rows, 2024, 5, "regular", "FBS Week 5")
    assert tab.games[0].spread is None
    assert [i["issue"] for i in tab.issues] == ["bare_number_spread"]


@pytest.mark.parametrize("title,expected", [
    ("FBS Week 0", ("week", 0)), ("Week 3 FBS", ("week", 3)), ("FBS BOWL PICKS", ("bowl", None)),
    ("FBS bowls (and Week 16)", ("bowl", None)), ("FCS Week 3", None), ("FBS FINAL", None),
    ("TOP 766 Week 15", None), ("SP+ performance", None), ("postgame win expectancy", None),
])
def test_classify_tab(title, expected):
    assert sp_plus.classify_tab(title) == expected


def test_excel_mangled_records():
    assert sp_plus.unmangle_pair("2024-04-05") == "4-5"
    assert sp_plus.parse_score_total("2026-12-10") == 22


def test_performance_top_block_only():
    rows = [["Week", "ATS record vs early-week spread (FBS vs FBS only)", "%", "Abs. error (FBS vs FBS only)"],
            ["0-1", "23-19", "0.55", "13.2"], ["15", "2024-04-05", "0.44", "21.3"], ["Postseason", "15-32", "", "11.8"],
            ["", "", "", ""], ["TOTAL", "403-389-9", "", ""], ["LAST YEAR (spreadsheet):"], ["0-1", "1-1", "", ""]]
    perf = sp_plus.parse_performance_rows(sp_plus._pad(rows))
    assert perf.week_label.tolist() == ["0-1", "15", "Postseason"]
    assert perf.iloc[1][["w", "l", "p"]].tolist() == [4, 5, 0]


def test_cfbd_join_signs_to_home(con):
    db.upsert_df(con, "teams", pd.DataFrame([
        {"team_id": 61, "school": "Georgia", "abbreviation": None, "conference": None, "classification": "fbs",
         "alternate_names": []},
        {"team_id": 2, "school": "Auburn", "abbreviation": None, "conference": None, "classification": "fbs",
         "alternate_names": []}]), "team_id")
    games = GameIndex(pd.DataFrame([{"game_id": 9, "season": 2024, "week": 5, "season_type": "regular",
                                     "start_date": "2024-09-28T16:00:00Z", "home_id": 61, "away_id": 2,
                                     "neutral_site": False, "home_points": 31, "away_points": 21}]))
    tab = sp_plus.parse_week_rows(games_left_of_ratings()[:2], 2024, 5, "regular", "FBS Week 5")
    resolver = TeamResolver.from_db(con)
    pub = sp_plus.inferred_published_at(games, 2024, tab.games)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    r = sp_plus.to_parse_result(tab.games, tab.ratings, games, resolver, now, pub, True)
    p, l = r.projections[0], r.lines[0]
    assert (p.game_id, p.exp_margin, p.home_win_prob, p.includes_hfa) == (9, 10.5, 0.77, True)
    assert p.published_at < pd.Timestamp("2024-09-28T16:00:00Z")
    assert (l.provider, l.snapshot, l.home_spread) == ("sp_sheet", "early", -7.5)


def test_validation_from_db(con):
    from cfbmodel.reports import sp_validation_from_db

    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    db.insert_df(con, "games", pd.DataFrame([
        {"game_id": i, "season": 2024, "week": 2, "season_type": "regular", "start_date": now, "home_id": 1,
         "away_id": 2, "neutral_site": False, "home_division": "fbs", "away_division": "fbs",
         "home_points": hp, "away_points": 20} for i, hp in [(1, 30), (2, 22), (3, 27)]]))
    # SP+ likes home by 10 at -7 in all three: covers, fails, pushes.
    db.insert_df(con, "source_projections", pd.DataFrame([
        {"game_id": i, "source": "sp_plus", "exp_margin": 10.0, "includes_hfa": True, "published_at": now,
         "fetched_at": now} for i in (1, 2, 3)]))
    db.insert_df(con, "lines", pd.DataFrame([
        {"game_id": i, "provider": "sp_sheet", "snapshot": "early", "home_spread": -7.0, "fetched_at": now}
        for i in (1, 2, 3)]))
    perf = pd.DataFrame([{"week_label": "2", "w": 1, "l": 1, "p": 1, "abs_error": None}])
    rep = sp_validation_from_db(con, 2024, perf)
    assert rep.iloc[0][["w", "l", "p", "ok"]].tolist() == [1, 1, 1, True]


def test_sheets_api_formatted_record():
    assert sp_plus.unmangle_pair("4/5/2024") == "4-5"
    assert sp_plus.unmangle_pair("23-19") == "23-19"


def test_2023_abbreviated_winner_and_spread_resolved_from_ats_pick():
    rows = [GAME_HDR,
            ["2023-10-24", "19:00", "New Mexico State at Louisiana Tech", "NMSU", "3.5", "0.58", "31-27", "LT -3",
             "New Mexico State", "6.5", "51.5", "Over", "6.8"],
            ["2023-10-25", "19:00", "Jacksonville State at Florida International", "Jax State", "7.3", "0.66",
             "29-21", "Jax State -8", "Florida International", "0.7", "44", "Over", "6.2"],
            ["2023-10-26", "19:30", "Georgia State at Georgia Southern", "GSU", "2.1", "0.55", "31-29", "GS -2.5",
             "Georgia State", "4.6", "62.5", "Under", "-2.4"],
            ["2023-10-27", "19:30", "Florida Atlantic at Charlotte", "FAU", "6", "0.63", "26-20", "FAU -6",
             "Charlotte", "0", "42", "Over", "3.9"]]
    tab = sp_plus.parse_week_rows(rows, 2023, 9, "regular", "FBS week 9")
    nmsu, jax, gsu, fau = tab.games
    assert nmsu.margin_for("Louisiana Tech") == -3.5 and nmsu.spread_for("Louisiana Tech") == -3
    assert jax.margin_for("Florida International") == -7.3 and jax.spread_for("Florida International") == 8
    assert gsu.margin_for("Georgia Southern") == -2.1 and gsu.spread_for("Georgia Southern") == -2.5
    # Model equals market (diff 0): either team could be "FAU", so nothing is guessed.
    assert fau.margin_for("Charlotte") is None and fau.spread is None
    assert [i["issue"] for i in tab.issues].count("resolved_from_ats_pick") == 3


def test_projected_total_uses_ou_diff_when_consistent():
    rows = [GAME_HDR,
            ["2023-10-24", "19:00", "New Mexico State at Louisiana Tech", "New Mexico State", "3.5", "0.58", "31-27",
             "Louisiana Tech -3", "New Mexico State", "6.5", "51.5", "Over", "6.8"],
            ["2023-10-24", "19:30", "Liberty at Western Kentucky", "Liberty", "6.7", "0.65", "32-25",
             "Liberty -3.5", "Liberty", "3.2", "61", "Under", "-4.6"],
            ["2023-10-25", "19:00", "UTEP at Sam Houston", "Sam Houston", "2.7", "0.56", "23-20", "Sam Houston -3.5",
             "UTEP", "0.8", "36", "Over", "9.9"]]
    tab = sp_plus.parse_week_rows(rows, 2023, 9, "regular", "FBS week 9")
    assert [g.proj_total for g in tab.games] == [58.3, 56.4, 43.0]   # last: 45.9 is > 1 from 43, keep rounded
    assert [i["issue"] for i in tab.issues] == ["ou_diff_disagrees_with_proj_score"]


def test_workbook_xlsx_url_needs_no_key():
    assert sp_plus.workbook_xlsx_url(2026).endswith("/export?format=xlsx")
    assert "/pub?output=xlsx" in sp_plus.workbook_xlsx_url(2023)     # 2023 is published-to-web
