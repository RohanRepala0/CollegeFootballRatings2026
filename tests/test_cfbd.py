from datetime import datetime, timezone

from cfbmodel.sources.cfbd import check_spread_sign, parse_games, parse_lines, parse_teams

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)

# Shapes follow CFBD API v2 field names; values are made up for the test.
GAMES = [{"id": 401, "season": 2025, "week": 3, "seasonType": "regular", "startDate": "2025-09-13T19:30:00.000Z",
          "neutralSite": False, "homeId": 61, "homeTeam": "Georgia", "homeClassification": "fbs", "homePoints": 31,
          "awayId": 2, "awayTeam": "Auburn", "awayClassification": "fbs", "awayPoints": 24,
          "homePregameElo": 1900, "awayPregameElo": 1600}]
LINES = [{"id": 401, "homeTeam": "Georgia", "awayTeam": "Auburn", "lines": [
    {"provider": "consensus", "spread": -7.5, "formattedSpread": "Georgia -7.5", "spreadOpen": -6.5,
     "overUnder": 48.5, "overUnderOpen": 47},
    {"provider": "Bovada", "spread": "-7", "formattedSpread": "Auburn 7"},
    {"provider": "Broken", "spread": 7, "formattedSpread": "Georgia -7"},
]}]


def test_parse_games_and_pregame_elo():
    df, elo = parse_games(GAMES)
    g = df.iloc[0]
    assert (g.home_id, g.away_id, g.home_points - g.away_points, g.neutral_site) == (61, 2, 7, False)
    assert {(r.team_id, r.rating) for r in elo} == {(61, 1900.0), (2, 1600.0)}
    assert all(r.published_at < g.start_date for r in elo)


def test_parse_lines_snapshots_and_sign_check():
    r = parse_lines(LINES, NOW)
    close = {l.provider: l.home_spread for l in r.lines if l.snapshot == "close"}
    assert close == {"cfbd:consensus": -7.5, "cfbd:Bovada": -7.0, "cfbd:Broken": 7.0}
    assert [l.home_spread for l in r.lines if l.snapshot == "open"] == [-6.5]
    assert [i["provider"] for i in r.issues] == ["cfbd:Broken"]


def test_check_spread_sign():
    assert check_spread_sign("Georgia", "Auburn", -7.5, "Georgia -7.5") is True
    assert check_spread_sign("Georgia", "Auburn", -7.5, "Auburn 7.5") is True
    assert check_spread_sign("Georgia", "Auburn", 7.5, "Georgia -7.5") is False
    assert check_spread_sign("Georgia", "Auburn", 0, "PK") is True
    assert check_spread_sign("Georgia", "Auburn", -3, None) is None


def test_parse_teams_v1_and_v2_alternates():
    df = parse_teams([{"id": 1, "school": "Miami", "alternateNames": ["Miami (FL)"]},
                      {"id": 2, "school": "Miami (OH)", "alt_name_1": "Miami-OH", "alt_name_2": None}])
    assert df.alternate_names.tolist() == [["Miami (FL)"], ["Miami-OH"]]
