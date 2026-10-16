import pandas as pd

from cfbmodel import db


def test_schema_created_and_idempotent(con):
    tables = db.init_db(con)
    for t in ("games", "lines", "source_projections", "raw_snapshots", "team_aliases", "bets", "exclusions"):
        assert t in tables


def test_lines_reject_unknown_snapshot(con):
    import duckdb
    import pytest

    with pytest.raises(duckdb.ConstraintException):
        con.execute("INSERT INTO lines VALUES (1, 'x', 'midweek', -3, NULL, NULL, NULL, now())")


def test_exclusions_logged(con):
    db.log_exclusions(con, [{"game_id": 1, "season": 2024, "week": 3, "stage": "t", "reason": "why"}])
    assert con.execute("SELECT reason FROM exclusions").fetchone()[0] == "why"


def test_upsert_replaces(con):
    teams = pd.DataFrame([{"team_id": 1, "school": "A", "abbreviation": None, "conference": None,
                           "classification": "fbs", "alternate_names": []}])
    db.upsert_df(con, "teams", teams, "team_id")
    db.upsert_df(con, "teams", teams.assign(school="B"), "team_id")
    assert con.execute("SELECT school FROM teams").fetchall() == [("B",)]
