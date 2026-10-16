"""End to end on a synthetic league (made-up data, tests only)."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from cfbmodel import backtest as bt
from cfbmodel import db, market, tracking, weekly


@pytest.fixture
def league(con):
    rng = np.random.default_rng(7)
    n_teams, seasons = 24, [2019, 2021, 2022, 2023]
    db.upsert_df(con, "teams", pd.DataFrame([{"team_id": i, "school": f"T{i}", "abbreviation": None,
                                              "conference": None, "classification": "fbs", "alternate_names": []}
                                             for i in range(n_teams)]), "team_id")
    games, lines, projs = [], [], []
    gid = 0
    for s in seasons:
        strength = rng.normal(0, 10, n_teams)
        for week in range(1, 13):
            kickoff = datetime(s, 9, 1, 19, tzinfo=timezone.utc) + timedelta(weeks=week)
            order = rng.permutation(n_teams)
            for h, a in zip(order[::2], order[1::2]):
                gid += 1
                true = strength[h] - strength[a] + 2.5
                margin = int(round(rng.normal(true, 15)))
                margin = margin or 1
                games.append({"game_id": gid, "season": s, "week": week, "season_type": "regular",
                              "start_date": kickoff, "home_id": int(h), "away_id": int(a), "home_team": f"T{h}",
                              "away_team": f"T{a}", "neutral_site": False, "home_division": "fbs",
                              "away_division": "fbs", "home_points": 30 + max(margin, 0),
                              "away_points": 30 - min(margin, 0)})
                close = -round((true + rng.normal(0, 2)) * 2) / 2
                for prov in ("A", "B"):
                    lines.append({"game_id": gid, "provider": f"cfbd:{prov}", "snapshot": "close",
                                  "home_spread": close, "fetched_at": kickoff})
                lines.append({"game_id": gid, "provider": "sp_sheet", "snapshot": "early",
                              "home_spread": close + rng.choice([-1, 0, 1]), "fetched_at": kickoff})
                for src, noise in (("src_good", 3), ("src_bad", 8)):
                    projs.append({"game_id": gid, "source": src, "exp_margin": true + rng.normal(0, noise),
                                  "includes_hfa": True, "published_at": kickoff - timedelta(days=2),
                                  "fetched_at": kickoff - timedelta(days=2)})
    projs.append({**projs[-1], "source": "src_late", "published_at": games[-1]["start_date"] + timedelta(hours=1)})
    db.insert_df(con, "games", pd.DataFrame(games))
    db.insert_df(con, "lines", pd.DataFrame(lines))
    db.insert_df(con, "source_projections", pd.DataFrame(projs))
    return con


def cfg():
    return bt.BacktestConfig(seasons=[2021, 2022, 2023], distribution_seasons=[2019, 2021, 2022, 2023],
                             distribution="normal")


def test_backtest_runs_and_reports(league, tmp_path):
    result = bt.run(league, cfg())
    assert set(result.frame.season) == {2022, 2023}           # 2021 is training only
    assert "src_late" not in result.sources                    # published after kickoff
    late = league.execute("SELECT count(*) FROM exclusions WHERE stage = 'point_in_time'").fetchone()[0]
    assert late == 1
    w = result.season_params[-1]["weights"]
    assert w["src_good"] > w["src_bad"]
    errs = result.source_errors.set_index(["forecast", "sample"])
    assert errs.loc[("src_good", "common"), "rmse"] < errs.loc[("src_bad", "common"), "rmse"]
    assert {"src_good", "src_bad", "src_bad+src_good"} == set(result.combos.combo)
    path = bt.write_report(result, tmp_path / "r.md")
    text = path.read_text()
    assert "Reality check" in text and "52.38%" in text and "retail consensus close" in text


def test_retail_consensus_is_median(league):
    c = market.retail_consensus(league, "close").set_index("game_id")
    assert c.n_providers.eq(2).all()
    assert (c.mkt_margin == -c.home_spread).all()


def test_production_card_and_tracking(league, monkeypatch, tmp_path):
    monkeypatch.setattr(weekly, "PARAMS_PATH", tmp_path / "p.json")
    monkeypatch.setattr(weekly, "DIST_PATH", tmp_path / "d.json")
    params = weekly.fit_production(league, cfg(), distribution="normal")
    assert 0 <= params["alpha"] <= 1.5
    params, dist = weekly.load_production()
    gid = int(league.execute("SELECT min(game_id) FROM games WHERE season = 2023 AND week = 12").fetchone()[0])
    market.set_line(league, gid, "MyBook", "bet", -3.5, -115, -105)
    card = weekly.bet_card(league, 2023, 12, params, dist, book="mybook")
    assert len(card) == 2 * 12 and {"home", "away"} == set(card.side)
    row = card[(card.game_id == gid) & (card.side == "home")].iloc[0]
    assert (row.line, row.price, row.priced_at) == (-3.5, -115, "mybook")
    bet_id = tracking.log_bet(league, gid, "home", -3.5, -115, 1.0, "mybook", float(row.model_margin), dist)
    assert tracking.settle(league, dist) == 1
    b = league.execute("SELECT result, close_line, clv_points FROM bets WHERE bet_id = ?", [bet_id]).fetchone()
    assert b[0] in ("win", "loss", "push") and b[1] is not None
    assert tracking.summary(league)["settled"] == 1
