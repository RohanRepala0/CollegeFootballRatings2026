"""DuckDB schema and helpers (plan section 3).

Tables beyond the plan: `teams` (CFBD team list), `source_ratings` (team-level
ratings such as the SP+ side table), `exclusions` (every game dropped from a
backtest, with a reason) and `api_calls` (running CFBD call count).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import duckdb
import pandas as pd

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id        BIGINT PRIMARY KEY,
    season         INTEGER NOT NULL,
    week           INTEGER NOT NULL,
    season_type    VARCHAR NOT NULL,
    start_date     TIMESTAMPTZ,
    home_id        INTEGER NOT NULL,
    away_id        INTEGER NOT NULL,
    home_team      VARCHAR,
    away_team      VARCHAR,
    neutral_site   BOOLEAN NOT NULL,
    home_division  VARCHAR,
    away_division  VARCHAR,
    home_points    INTEGER,
    away_points    INTEGER
);

CREATE TABLE IF NOT EXISTS teams (
    team_id         INTEGER PRIMARY KEY,
    school          VARCHAR NOT NULL,
    abbreviation    VARCHAR,
    conference      VARCHAR,
    classification  VARCHAR,
    alternate_names VARCHAR[]
);

CREATE TABLE IF NOT EXISTS lines (
    game_id            BIGINT NOT NULL,
    provider           VARCHAR NOT NULL,
    snapshot           VARCHAR NOT NULL CHECK (snapshot IN ('open', 'early', 'bet', 'close', 'unknown')),
    home_spread        DOUBLE,
    spread_price_home  DOUBLE,
    spread_price_away  DOUBLE,
    total              DOUBLE,
    fetched_at         TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS source_projections (
    game_id        BIGINT NOT NULL,
    source         VARCHAR NOT NULL,
    exp_margin     DOUBLE NOT NULL,
    home_win_prob  DOUBLE,
    exp_total      DOUBLE,
    includes_hfa   BOOLEAN NOT NULL,
    published_at   TIMESTAMPTZ NOT NULL,
    fetched_at     TIMESTAMPTZ NOT NULL,
    raw_ref        VARCHAR
);

CREATE TABLE IF NOT EXISTS source_ratings (
    source        VARCHAR NOT NULL,
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    team_id       INTEGER NOT NULL,
    rating        DOUBLE NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL,
    raw_ref       VARCHAR
);

CREATE TABLE IF NOT EXISTS raw_snapshots (
    source        VARCHAR NOT NULL,
    url           VARCHAR NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL,
    path_on_disk  VARCHAR NOT NULL,
    sha256        VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS team_aliases (
    source       VARCHAR NOT NULL,
    source_name  VARCHAR NOT NULL,
    team_id      INTEGER NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS bet_id_seq START 1;

CREATE TABLE IF NOT EXISTS bets (
    bet_id            INTEGER PRIMARY KEY DEFAULT nextval('bet_id_seq'),
    game_id           BIGINT NOT NULL,
    side              VARCHAR NOT NULL CHECK (side IN ('home', 'away')),
    book              VARCHAR,
    line_taken        DOUBLE NOT NULL,
    price_taken       DOUBLE NOT NULL,
    stake             DOUBLE NOT NULL,
    placed_at         TIMESTAMPTZ NOT NULL,
    model_exp_margin  DOUBLE,
    model_p_win       DOUBLE,
    model_p_push      DOUBLE,
    ev                DOUBLE,
    close_line        DOUBLE,
    clv_points        DOUBLE,
    clv_prob          DOUBLE,
    result            VARCHAR
);

CREATE TABLE IF NOT EXISTS exclusions (
    game_id    BIGINT,
    season     INTEGER,
    week       INTEGER,
    stage      VARCHAR NOT NULL,
    reason     VARCHAR NOT NULL,
    logged_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS api_calls (
    source     VARCHAR NOT NULL,
    url        VARCHAR NOT NULL,
    called_at  TIMESTAMPTZ NOT NULL
);

-- College Sports Commission compensation reports (sources/csc.py). A release is one distinct
-- decoded payload; refetching an unchanged page only moves last_seen_at.
CREATE TABLE IF NOT EXISTS csc_releases (
    report          VARCHAR NOT NULL CHECK (report IN ('nil_go', 'revenue_share')),
    payload_sha256  VARCHAR NOT NULL,
    url             VARCHAR NOT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL,
    archive_path    VARCHAR NOT NULL,
    n_cohorts       INTEGER NOT NULL
);

-- One row per published percentile cell. Long format: a cohort is
-- (report, gender, sport, position, cohort, population, window_label).
CREATE TABLE IF NOT EXISTS csc_percentiles (
    report          VARCHAR NOT NULL CHECK (report IN ('nil_go', 'revenue_share')),
    payload_sha256  VARCHAR NOT NULL,
    gender          VARCHAR NOT NULL CHECK (gender IN ('men', 'women', 'mixed')),
    sport           VARCHAR NOT NULL,
    position        VARCHAR NOT NULL,
    cohort          VARCHAR NOT NULL,
    a4              BOOLEAN,
    population      VARCHAR NOT NULL CHECK (population IN ('all_rostered', 'paid', 'deal_holders')),
    n               INTEGER,
    n_text          VARCHAR,
    percentile      INTEGER NOT NULL,
    value_usd       DOUBLE,
    min_n           INTEGER,
    window_start    DATE,
    window_end      DATE,
    as_of           DATE,
    window_label    VARCHAR,
    source_ref      VARCHAR NOT NULL
);

CREATE OR REPLACE VIEW csc_latest AS
SELECT p.* FROM csc_percentiles p
JOIN (SELECT report, arg_max(payload_sha256, first_seen_at) AS payload_sha256
      FROM csc_releases GROUP BY report) r USING (report, payload_sha256);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def connect(path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    path = Path(path) if path is not None else config.DB_PATH
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("SET TimeZone = 'UTC'")
    return con


def init_db(con: duckdb.DuckDBPyConnection) -> list[str]:
    con.execute(SCHEMA)
    return sorted(r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall())


def insert_df(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    """Append rows, matching columns by name."""
    if df.empty:
        return 0
    cols = ", ".join(df.columns)
    con.register("_insert_df", df)
    try:
        con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _insert_df")
    finally:
        con.unregister("_insert_df")
    return len(df)


def upsert_df(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame, key: str) -> int:
    """Replace rows sharing `key`, then append. For tables that hold current state
    (games, teams). Snapshot tables (lines, projections) are append-only."""
    if df.empty:
        return 0
    con.register("_upsert_keys", df[[key]])
    try:
        con.execute(f"DELETE FROM {table} WHERE {key} IN (SELECT {key} FROM _upsert_keys)")
    finally:
        con.unregister("_upsert_keys")
    return insert_df(con, table, df)


def log_exclusions(con: duckdb.DuckDBPyConnection, rows: Iterable[dict]) -> int:
    """Ground rule: never silently drop a game. `rows` carry game_id, season, week, stage, reason."""
    df = pd.DataFrame(list(rows))
    if df.empty:
        return 0
    df["logged_at"] = utcnow()
    for col in ("game_id", "season", "week"):
        if col not in df:
            df[col] = None
    return insert_df(con, "exclusions", df[["game_id", "season", "week", "stage", "reason", "logged_at"]])
