"""Market lines: consensus, sharp fair lines, manual entry (plan section 2.5).

"The market" = sharp consensus (Pinnacle and/or Circa, price-adjusted to a
no-vig fair line) when captured; otherwise the retail consensus (median across
CFBD providers). Historical backtests only ever have the retail version.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from .conventions import no_vig_probs
from .db import utcnow
from .distribution.base import MarginDistribution
from .pricing import fair_center

SHARP_BOOKS = ("pinnacle", "circa")


def latest_lines(con: duckdb.DuckDBPyConnection, snapshot: str, provider_like: str = "%") -> pd.DataFrame:
    """Most recent row per (game, provider) for a snapshot."""
    return con.execute(
        """
        SELECT * FROM lines
        WHERE snapshot = ? AND provider LIKE ?
        QUALIFY row_number() OVER (PARTITION BY game_id, provider ORDER BY fetched_at DESC) = 1
        """,
        [snapshot, provider_like],
    ).df()


def retail_consensus(con: duckdb.DuckDBPyConnection, snapshot: str = "close") -> pd.DataFrame:
    """Median home spread across CFBD providers: game_id, home_spread, n_providers, mkt_margin."""
    df = latest_lines(con, snapshot, "cfbd:%").dropna(subset=["home_spread"])
    if df.empty:
        return pd.DataFrame(columns=["game_id", "home_spread", "n_providers", "mkt_margin"])
    g = df.groupby("game_id").home_spread.agg(home_spread="median", n_providers="count").reset_index()
    g["mkt_margin"] = -g.home_spread
    return g


def early_line(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = latest_lines(con, "early", "sp_sheet").dropna(subset=["home_spread"])
    df = df[["game_id", "home_spread", "total"]].copy()
    df["mkt_margin"] = -df.home_spread
    return df


def bcf_crosscheck(con: duckdb.DuckDBPyConnection, threshold: float = 1.0) -> dict:
    """BCF Toys' closing line vs the CFBD consensus close (plan Phase 4)."""
    cfbd = retail_consensus(con, "close")
    bcf = latest_lines(con, "close", "bcftoys_close")[["game_id", "home_spread"]]
    m = cfbd.merge(bcf, on="game_id", suffixes=("_cfbd", "_bcf"))
    if m.empty:
        return {"n": 0}
    diff = (m.home_spread_cfbd - m.home_spread_bcf).abs()
    return {"n": len(m), "mean_abs_diff": float(diff.mean()), "share_over_threshold": float((diff >= threshold).mean()),
            "threshold": threshold, "sign_flips": int((np.sign(m.home_spread_cfbd) * np.sign(m.home_spread_bcf) < 0).sum())}


def sharp_fair_margin(lines: pd.DataFrame, dist: MarginDistribution) -> pd.DataFrame:
    """No-vig fair mkt_margin per game from Pinnacle/Circa rows with two-way prices,
    averaged across the books present."""
    rows = []
    sharp = lines[lines.provider.str.lower().isin(SHARP_BOOKS)].dropna(
        subset=["home_spread", "spread_price_home", "spread_price_away"])
    for r in sharp.itertuples():
        p_home, _ = no_vig_probs(r.spread_price_home, r.spread_price_away)
        rows.append({"game_id": r.game_id, "provider": r.provider,
                     "fair_margin": fair_center(dist, r.home_spread, p_home)})
    if not rows:
        return pd.DataFrame(columns=["game_id", "mkt_margin", "books"])
    df = pd.DataFrame(rows).dropna()
    return df.groupby("game_id").agg(mkt_margin=("fair_margin", "mean"), books=("provider", ",".join)).reset_index()


def market_margin(con: duckdb.DuckDBPyConnection, snapshot: str, dist: MarginDistribution | None) -> pd.DataFrame:
    """game_id, mkt_margin, market_source. Sharp fair line when captured, else retail consensus."""
    retail = retail_consensus(con, snapshot)[["game_id", "mkt_margin"]].assign(market_source="retail_consensus")
    if dist is None:
        return retail
    sharp = sharp_fair_margin(latest_lines(con, snapshot), dist)
    sharp = sharp.assign(market_source="sharp:" + sharp.books)[["game_id", "mkt_margin", "market_source"]]
    return pd.concat([sharp, retail[~retail.game_id.isin(sharp.game_id)]], ignore_index=True)


def set_line(con: duckdb.DuckDBPyConnection, game_id: int, provider: str, snapshot: str, home_spread: float,
             price_home: float | None = None, price_away: float | None = None, total: float | None = None) -> None:
    """Manual entry (plan section 2.5 fallback): `cfb line set`."""
    con.execute(
        "INSERT INTO lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [game_id, provider.lower(), snapshot, home_spread, price_home, price_away, total, utcnow()],
    )
