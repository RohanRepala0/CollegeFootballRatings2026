"""Totals backtest on a synthetic league (made-up data, tests only)."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from cfbmodel import backtest as bt
from cfbmodel import db, totals


def test_total_probs_and_outcomes():
    pmf = np.zeros(totals.T_MAX + 1)
    pmf[[50, 51, 52]] = [0.25, 0.25, 0.5]
    assert totals.total_probs(pmf, "over", 50.5) == pytest.approx((0.75, 0.0, 0.25))
    assert totals.total_probs(pmf, "under", 51) == pytest.approx((0.25, 0.25, 0.5))
    assert totals.total_outcome(52, "over", 51.5) == "win"
    assert totals.total_outcome(52, "under", 51.5) == "loss"
    assert totals.total_outcome(51, "over", 51) == "push"


@pytest.mark.parametrize("name", list(totals.REGISTRY))
def test_distributions_are_valid_and_fit(name):
    rng = np.random.default_rng(0)
    centers = np.round(rng.normal(55, 8, 4000) * 2) / 2
    tot = np.clip(np.round(rng.normal(centers + 1.5, 14)), 0, None)
    m = totals.make(name).fit(centers, tot)
    p = m.pmf_matrix(np.array([30.5, 55.0, 80.5]))
    assert p.shape == (3, totals.T_MAX + 1)
    np.testing.assert_allclose(p.sum(axis=1), 1.0)
    assert (p >= 0).all()
    if name == "normal":
        assert m.sigma0 == pytest.approx(14, abs=0.7)
    if name == "normal_shift":
        assert m.shift == pytest.approx(1.5, abs=0.7)


def test_frequency_weights_find_common_totals():
    rng = np.random.default_rng(1)
    centers = np.round(rng.normal(55, 8, 6000) * 2) / 2
    tot = np.round(rng.normal(centers, 14))
    spike = rng.random(len(tot)) < 0.1
    tot[spike] = 45
    m = totals.TotalFrequencyWeighted().fit(centers, tot)
    assert m.weights[45] > 1.5 * m.weights[44]


@pytest.fixture
def league(con):
    rng = np.random.default_rng(11)
    n_teams, seasons = 24, [2019, 2021, 2022, 2023]
    db.upsert_df(con, "teams", pd.DataFrame([{"team_id": i, "school": f"T{i}", "abbreviation": None,
                                              "conference": None, "classification": "fbs", "alternate_names": []}
                                             for i in range(n_teams)]), "team_id")
    games, lines, projs = [], [], []
    gid = 0
    for s in seasons:
        for week in range(1, 13):
            kickoff = datetime(s, 9, 1, 19, tzinfo=timezone.utc) + timedelta(weeks=week)
            order = rng.permutation(n_teams)
            for h, a in zip(order[::2], order[1::2]):
                gid += 1
                true_total = rng.normal(55, 8)
                total = max(int(round(rng.normal(true_total, 14))), 3)
                home = int(total // 2 + rng.integers(0, 2))
                games.append({"game_id": gid, "season": s, "week": week, "season_type": "regular",
                              "start_date": kickoff, "home_id": int(h), "away_id": int(a), "home_team": f"T{h}",
                              "away_team": f"T{a}", "neutral_site": False, "home_division": "fbs",
                              "away_division": "fbs", "home_points": home, "away_points": total - home})
                close = round((true_total + rng.normal(0, 4)) * 2) / 2
                early = close + rng.choice([-1.5, 0, 1.5])
                for prov in ("A", "B"):
                    lines.append({"game_id": gid, "provider": f"cfbd:{prov}", "snapshot": "close",
                                  "home_spread": -3.0, "total": close, "fetched_at": kickoff})
                lines.append({"game_id": gid, "provider": "sp_sheet", "snapshot": "early",
                              "home_spread": -3.0, "total": early, "fetched_at": kickoff})
                for src, noise in (("src_good", 2), ("src_bad", 9)):
                    projs.append({"game_id": gid, "source": src, "exp_margin": 0.0,
                                  "exp_total": true_total + rng.normal(0, noise), "includes_hfa": True,
                                  "published_at": kickoff - timedelta(days=2),
                                  "fetched_at": kickoff - timedelta(days=2)})
    db.insert_df(con, "games", pd.DataFrame(games))
    db.insert_df(con, "lines", pd.DataFrame(lines))
    db.insert_df(con, "source_projections", pd.DataFrame(projs))
    return con


def cfg():
    return bt.BacktestConfig(seasons=[2021, 2022, 2023], distribution_seasons=[2019, 2021, 2022, 2023],
                             distribution="normal")


def test_totals_backtest_runs_and_reports(league, tmp_path):
    result = totals.run(league, cfg())
    assert set(result.frame.season) == {2022, 2023}
    assert set(result.sources) == {"src_good", "src_bad"}
    w = eval(result.season_params[-1]["weights"])
    assert w["src_good"] > w["src_bad"]
    # src_good knows the true total better than the market: the blend leans on it
    assert result.season_params[-1]["alpha"] < 0.8
    errs = result.errors.set_index(["forecast", "sample"])
    assert errs.loc[("src_good", "common"), "rmse"] < errs.loc[("market_close", "common"), "rmse"]
    bets = result.bets["blend_close"]
    assert len(bets) and set(bets.side) <= {"over", "under"} and (bets.clv_points == 0).all()
    text = totals.write_report(result, tmp_path / "t.md").read_text()
    assert "Totals backtest" in text and "Reality check" in text and "blend_early" in text


def test_early_clv_sign(league):
    """An over bet at an early total below the close gains line value."""
    test = pd.DataFrame([{"game_id": 1, "season": 2023, "week": 1, "total": 60, "mkt_total": 52.5,
                          "early_total": 50.5}])
    dist = totals.make("normal")
    dist.sigma0 = 14.0
    bets = totals.simulate_total_bets(test, np.array([58.0]), dist, "early_total", cfg())
    assert bets.side.tolist() == ["over"] and bets.clv_points.tolist() == [2.0] and bets.clv_prob.iloc[0] > 0
    assert bets.result.tolist() == ["win"]
