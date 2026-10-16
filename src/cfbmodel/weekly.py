"""A live week: production parameters and the bet card (plan Phase 6)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from . import config
from .backtest import BacktestConfig, backtest_frame, distribution_frame
from .blend import combine, fit_alpha, fit_weights, point_in_time_projections, source_matrix
from .distribution import select as dist_select
from .distribution.base import MarginDistribution
from .market import latest_lines, market_margin
from .pricing import moneyline_prob, price
from .staking import StakingRules, stake

PARAMS_PATH = config.MODELS_DIR / "params.json"
DIST_PATH = config.MODELS_DIR / "distribution.json"


def fit_production(con: duckdb.DuckDBPyConnection, cfg: BacktestConfig, distribution: str = "best") -> dict:
    """Fit the distribution, weights and alpha on every available season, and save them."""
    dist_df = distribution_frame(con, cfg)
    if dist_df.empty:
        raise RuntimeError("no games with closing lines; run `cfb backfill-cfbd` first")
    name = distribution
    if name == "best":
        wf = dist_select.walk_forward(dist_df)
        name = dist_select.summarize(wf).index[0] if len(wf) else "normal"
    dist = dist_select.make(name).fit(dist_df.center.values, dist_df.home_margin.values)
    dist_select.save(dist, DIST_PATH)

    frame, sources = backtest_frame(con, cfg)
    weights = fit_weights(frame[sources], frame.home_margin) if sources else {}
    f = combine(frame[sources], weights) if sources else pd.Series(np.nan, index=frame.index)
    a = fit_alpha(f.values, frame.mkt_margin.values, frame.home_margin.values)
    alpha = a.alpha if a.n >= 30 else cfg.alpha_start
    params = {"distribution": name, "weights": weights, "alpha": alpha, "beta": a.beta, "beta_se": a.beta_se,
              "alpha_n": a.n, "alpha_is_default": a.n < 30, "seasons": cfg.seasons}
    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps(params, indent=2, default=float))
    return params


def load_production() -> tuple[dict, MarginDistribution]:
    if not PARAMS_PATH.exists() or not DIST_PATH.exists():
        raise RuntimeError("no production parameters; run `cfb fit` after the backfill")
    return json.loads(PARAMS_PATH.read_text()), dist_select.load(DIST_PATH)


def bet_card(con: duckdb.DuckDBPyConnection, season: int, week: int, params: dict, dist: MarginDistribution,
             book: str | None = None, rules: StakingRules | None = None, season_type: str = "regular") -> pd.DataFrame:
    """One row per (game, side) for the week's games that have a market line.

    Reference market: sharp fair line from 'bet' snapshots if captured, else the
    latest retail consensus. Bets are priced at `book`'s line and price when a
    'bet' snapshot exists for that book (manual entry), else at the reference
    line at -110.
    """
    rules = rules or StakingRules.from_settings()
    games = con.execute("SELECT * FROM games WHERE season = ? AND week = ? AND season_type = ?",
                        [season, week, season_type]).df()
    if games.empty:
        return pd.DataFrame()
    proj = point_in_time_projections(con, games, log=False)
    matrix = source_matrix(proj, games)
    weights = {k: v for k, v in params["weights"].items() if k in matrix.columns} or {c: 1.0 for c in matrix.columns}
    F = combine(matrix, weights) if len(matrix.columns) else pd.Series(dtype=float)

    ref = market_margin(con, "bet", dist)
    if ref.empty:
        ref = market_margin(con, "close", dist)
    ref = ref.set_index("game_id")
    book_lines = latest_lines(con, "bet", book.lower()).set_index("game_id") if book else pd.DataFrame()

    rows = []
    for g in games.itertuples():
        if g.game_id not in ref.index:
            continue
        mkt = float(ref.loc[g.game_id, "mkt_margin"])
        f = float(F.get(g.game_id, np.nan))
        M = mkt if np.isnan(f) else params["alpha"] * mkt + (1 - params["alpha"]) * f
        pmf = dist.pmf(M)
        if book and g.game_id in book_lines.index:
            bl = book_lines.loc[g.game_id]
            home_line = float(bl.home_spread)
            prices = {"home": bl.spread_price_home, "away": bl.spread_price_away}
            where = book
        else:
            home_line, prices, where = -mkt, {"home": -110, "away": -110}, "reference@-110"
        for side, line in (("home", home_line), ("away", -home_line)):
            pr = price(pmf, side, line, float(prices[side]) if pd.notna(prices[side]) else -110)
            rows.append({
                "game_id": g.game_id, "game": f"{g.away_team} at {g.home_team}" if not g.neutral_site
                else f"{g.away_team} vs {g.home_team} (N)", "kickoff": g.start_date,
                "sources": ",".join(c for c in matrix.columns if pd.notna(matrix.loc[g.game_id, c]))
                if g.game_id in matrix.index else "",
                "F": round(f, 1) if not np.isnan(f) else None, "mkt_margin": round(mkt, 2),
                "market_source": ref.loc[g.game_id, "market_source"], "model_margin": round(M, 2),
                "home_ml_prob": round(moneyline_prob(pmf), 3), "priced_at": where, "side": side,
                "line": line, "price": pr.american, "p_win": round(pr.p_win, 4), "p_push": round(pr.p_push, 4),
                "ev": round(pr.ev, 4), "fair_odds": round(pr.fair_american) if np.isfinite(pr.fair_american) else None,
                "stake_units": stake(pr, rules),
            })
    return pd.DataFrame(rows)


def write_card(card: pd.DataFrame, season: int, week: int) -> Path:
    path = config.REPORTS_DIR / f"bet_card_{season}_w{week:02d}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    card.to_csv(path, index=False)
    return path
