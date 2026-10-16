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
        name = dist_select.best_model(dist_df, cfg.distribution_metric)
    dist = dist_select.make(name).fit(dist_df.center.values, dist_df.home_margin.values)
    dist_select.save(dist, DIST_PATH)

    frame, sources = backtest_frame(con, cfg)
    weights = fit_weights(frame[sources], frame.home_margin) if sources else {}
    f = combine(frame[sources], weights) if sources else pd.Series(np.nan, index=frame.index)
    a = fit_alpha(f.values, frame.mkt_margin.values, frame.home_margin.values)
    alpha = a.alpha if a.n >= 30 else cfg.alpha_start
    params = {"distribution": name, "distribution_metric": cfg.distribution_metric, "weights": weights, "alpha": alpha, "beta": a.beta, "beta_se": a.beta_se,
              "alpha_n": a.n, "alpha_is_default": a.n < 30, "seasons": cfg.seasons}
    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps(params, indent=2, default=float))
    return params


def load_production() -> tuple[dict, MarginDistribution]:
    if not PARAMS_PATH.exists() or not DIST_PATH.exists():
        raise RuntimeError("no production parameters; run `cfb fit` after the backfill")
    return json.loads(PARAMS_PATH.read_text()), dist_select.load(DIST_PATH)


def bet_card(con: duckdb.DuckDBPyConnection, season: int, week: int, params: dict, dist: MarginDistribution,
             books: list[str] | None = None, rules: StakingRules | None = None,
             season_type: str = "regular") -> pd.DataFrame:
    """One row per (game, side) for the week's games that have a market line.

    Reference market: sharp fair line from 'bet' snapshots if captured, else the
    latest retail consensus. Each side is priced at every book in `books` that
    has a 'bet' line for the game (`cfb line set`), and the best EV is kept
    (line shopping). A book with no 'bet' line but a current CFBD line (DraftKings
    is a CFBD provider) is priced at that line, at -110 since CFBD carries no spread
    prices. With no book line at all, the side is priced at the reference line at -110.
    `status` marks games that are final or had started when the card was built.
    """
    rules = rules or StakingRules.from_settings()
    books = [b.lower() for b in (books if books is not None else default_books())]
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
    bet_lines = latest_lines(con, "bet")
    bet_lines = bet_lines[bet_lines.provider.isin(books)]
    cfbd_lines = latest_lines(con, "close", "cfbd:%")
    cfbd_lines = cfbd_lines.assign(book=cfbd_lines.provider.str[5:].str.lower())
    cfbd_lines = cfbd_lines[cfbd_lines.book.isin(books)]
    now = pd.Timestamp.now(tz="UTC")

    rows = []
    for g in games.itertuples():
        if g.game_id not in ref.index:
            continue
        mkt = float(ref.loc[g.game_id, "mkt_margin"])
        f = float(F.get(g.game_id, np.nan))
        M = mkt if np.isnan(f) else params["alpha"] * mkt + (1 - params["alpha"]) * f
        pmf = dist.pmf(M)
        offers = [(b.provider, float(b.home_spread), b.spread_price_home, b.spread_price_away)
                  for b in bet_lines[bet_lines.game_id == g.game_id].itertuples() if pd.notna(b.home_spread)]
        manual = {o[0] for o in offers}
        offers += [(f"{b.book} (CFBD line)", float(b.home_spread), None, None)
                   for b in cfbd_lines[cfbd_lines.game_id == g.game_id].itertuples()
                   if b.book not in manual and pd.notna(b.home_spread)]
        if not offers:
            offers = [("reference@-110", -mkt, -110, -110)]
        for side in ("home", "away"):
            quotes = []
            for where, home_line, p_home, p_away in offers:
                american = p_home if side == "home" else p_away
                american = float(american) if pd.notna(american) else -110.0
                quotes.append((price(pmf, side, home_line if side == "home" else -home_line, american), where))
            pr, where = max(quotes, key=lambda q: q[0].ev)
            rows.append({
                "game_id": g.game_id, "status": "final" if pd.notna(g.home_points) else (
                    "started" if pd.Timestamp(g.start_date) <= now else "upcoming"),
                "game": f"{g.away_team} at {g.home_team}" if not g.neutral_site
                else f"{g.away_team} vs {g.home_team} (N)", "kickoff": g.start_date,
                "sources": ",".join(c for c in matrix.columns if pd.notna(matrix.loc[g.game_id, c]))
                if g.game_id in matrix.index else "",
                "F": round(f, 1) if not np.isnan(f) else None, "mkt_margin": round(mkt, 2),
                "market_source": ref.loc[g.game_id, "market_source"], "model_margin": round(M, 2),
                "home_ml_prob": round(moneyline_prob(pmf), 3), "side": side, "priced_at": where,
                "books_checked": ",".join(w for _, w in quotes),
                "line": pr.line, "price": pr.american, "p_win": round(pr.p_win, 4), "p_push": round(pr.p_push, 4),
                "ev": round(pr.ev, 4), "fair_odds": round(pr.fair_american) if np.isfinite(pr.fair_american) else None,
                "stake_units": stake(pr, rules),
            })
    return pd.DataFrame(rows)


def default_books() -> list[str]:
    return config.settings().get("betting", {}).get("books", [])


def write_card(card: pd.DataFrame, season: int, week: int) -> Path:
    path = config.REPORTS_DIR / f"bet_card_{season}_w{week:02d}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    card.to_csv(path, index=False)
    return path
