"""Bets actually placed, closing lines, results and CLV (plan Phase 7)."""

from __future__ import annotations

import duckdb
import pandas as pd

from .conventions import american_to_decimal, bet_outcome
from .db import utcnow
from .distribution.base import MarginDistribution
from .market import market_margin
from .pricing import cover_probs, price


def log_bet(con: duckdb.DuckDBPyConnection, game_id: int, side: str, line: float, american: float, stake: float,
            book: str | None, model_margin: float | None, dist: MarginDistribution | None) -> int:
    p = price(dist.pmf(model_margin), side, line, american) if dist is not None and model_margin is not None else None
    return con.execute(
        """INSERT INTO bets (game_id, side, book, line_taken, price_taken, stake, placed_at,
                             model_exp_margin, model_p_win, model_p_push, ev)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING bet_id""",
        [game_id, side, book, line, american, stake, utcnow(), model_margin,
         p.p_win if p else None, p.p_push if p else None, p.ev if p else None],
    ).fetchone()[0]


def settle(con: duckdb.DuckDBPyConnection, dist: MarginDistribution) -> int:
    """Fill close line, CLV and result for open bets whose games have finished."""
    open_bets = con.execute(
        """SELECT b.*, g.home_points, g.away_points FROM bets b JOIN games g USING (game_id)
           WHERE b.result IS NULL""").df()
    if open_bets.empty:
        return 0
    close = market_margin(con, "close", dist).set_index("game_id")
    n = 0
    for b in open_bets.itertuples():
        close_line = clv_pts = clv_prob = None
        if b.game_id in close.index:
            mkt = float(close.loc[b.game_id, "mkt_margin"])
            close_line = -mkt if b.side == "home" else mkt
            clv_pts = b.line_taken - close_line
            w, _, l = cover_probs(dist.pmf(mkt), b.side, b.line_taken)
            clv_prob = w / (w + l) - 0.5 if w + l else None
        result = None
        if pd.notna(b.home_points) and pd.notna(b.away_points):
            result = bet_outcome(b.home_points - b.away_points, b.side, b.line_taken)
        con.execute("UPDATE bets SET close_line = ?, clv_points = ?, clv_prob = ?, result = ? WHERE bet_id = ?",
                    [close_line, clv_pts, clv_prob, result, b.bet_id])
        n += result is not None
    return n


def summary(con: duckdb.DuckDBPyConnection) -> dict:
    bets = con.execute("SELECT * FROM bets").df()
    if bets.empty:
        return {"bets": 0}
    done = bets[bets.result.notna()]
    profit = sum(
        (american_to_decimal(r.price_taken) - 1) * r.stake if r.result == "win" else (-r.stake if r.result == "loss" else 0)
        for r in done.itertuples())
    return {"bets": len(bets), "settled": len(done), "w": int((done.result == "win").sum()),
            "l": int((done.result == "loss").sum()), "p": int((done.result == "push").sum()),
            "units_staked": float(done.stake.sum()), "units_profit": float(profit),
            "roi": float(profit / done.stake.sum()) if done.stake.sum() else None,
            "avg_clv_points": float(bets.clv_points.mean()) if bets.clv_points.notna().any() else None,
            "avg_clv_prob": float(bets.clv_prob.mean()) if bets.clv_prob.notna().any() else None}
