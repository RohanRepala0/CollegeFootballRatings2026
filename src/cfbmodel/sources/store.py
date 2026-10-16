"""Write a ParseResult to DuckDB idempotently.

Snapshot tables are append-only across snapshots, but re-parsing the same raw
snapshot (same fetched_at) replaces its own rows instead of duplicating them.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from ..db import insert_df
from .base import ParseResult

_KEYS = {
    "source_projections": ["game_id", "source", "fetched_at"],
    "lines": ["game_id", "provider", "snapshot", "fetched_at"],
    "source_ratings": ["source", "season", "week", "team_id", "fetched_at"],
}


def _replace(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    keys = _KEYS[table]
    con.register("_incoming", df[keys].drop_duplicates())
    try:
        cond = " AND ".join(f"{table}.{k} = _incoming.{k}" for k in keys)
        con.execute(f"DELETE FROM {table} USING _incoming WHERE {cond}")
    finally:
        con.unregister("_incoming")
    return insert_df(con, table, df)


def store(con: duckdb.DuckDBPyConnection, result: ParseResult) -> dict[str, int]:
    return {table: _replace(con, table, df) for table, df in result.frames().items()}
