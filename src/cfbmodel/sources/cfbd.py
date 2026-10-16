"""CollegeFootballData.com: games, lines, teams (plan section 2.1).

Field names follow API v2 (camelCase). The parser also accepts the v1
snake_case names, because the live docs could not be checked when this was
written. Run `cfb backfill-cfbd` and the spot checks in Phase 1 before
trusting either.

Line conventions: CFBD `spread` is assumed to be the home team's spread (book
convention, negative = home favored). `check_spread_sign` cross-checks every
line against `formattedSpread` ("Georgia -7.5") and reports disagreements, so
the assumption is tested on real data instead of trusted.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

import duckdb
import pandas as pd

from .. import config
from ..db import upsert_df
from ..fetch import Fetcher, Snapshot
from ..teams import normalize
from .base import LineRow, ParseResult, RatingRow
from .store import store


def _get(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return default


def _num(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class CFBDClient:
    def __init__(self, fetcher: Fetcher, api_key: str, base_url: str | None = None):
        self.fetcher = fetcher
        self.api_key = api_key
        self.base_url = (base_url or config.sources()["cfbd"]["base_url"]).rstrip("/")

    def get(self, path: str, params: dict | None = None, fresh: bool = False) -> tuple[Any, Snapshot]:
        snap = self.fetcher.fetch(
            "cfbd",
            f"{self.base_url}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
            fresh=fresh,
            ext="json",
            count_call=True,
        )
        return json.loads(snap.body), snap

    def games(self, year: int, season_type: str = "both", week: int | None = None, fresh: bool = False):
        return self.get("/games", {"year": year, "seasonType": season_type, "week": week}, fresh)

    def lines(self, year: int, season_type: str = "both", week: int | None = None, fresh: bool = False):
        return self.get("/lines", {"year": year, "seasonType": season_type, "week": week}, fresh)

    def teams(self, fresh: bool = False):
        return self.get("/teams", None, fresh)


# ---------------------------------------------------------------- parsers


def parse_teams(payload: list[dict]) -> pd.DataFrame:
    rows = []
    for t in payload:
        alts = _get(t, "alternateNames", "alternate_names")
        if alts is None:
            alts = [t.get(k) for k in ("alt_name_1", "alt_name_2", "alt_name_3", "alt_name1", "alt_name2", "alt_name3")]
        rows.append(
            {
                "team_id": int(t["id"]),
                "school": t["school"],
                "abbreviation": t.get("abbreviation"),
                "conference": t.get("conference"),
                "classification": _get(t, "classification", "division"),
                "alternate_names": [a for a in alts if a],
            }
        )
    return pd.DataFrame(rows)


def parse_games(payload: list[dict]) -> tuple[pd.DataFrame, list[RatingRow]]:
    """Games table rows, plus CFBD pregame Elo as point-in-time team ratings."""
    rows, elo = [], []
    for g in payload:
        start = pd.to_datetime(_get(g, "startDate", "start_date"), utc=True)
        row = {
            "game_id": int(g["id"]),
            "season": int(g["season"]),
            "week": int(g["week"]),
            "season_type": _get(g, "seasonType", "season_type"),
            "start_date": start,
            "home_id": int(_get(g, "homeId", "home_id")),
            "away_id": int(_get(g, "awayId", "away_id")),
            "home_team": _get(g, "homeTeam", "home_team"),
            "away_team": _get(g, "awayTeam", "away_team"),
            "neutral_site": bool(_get(g, "neutralSite", "neutral_site", default=False)),
            "home_division": _get(g, "homeClassification", "home_division"),
            "away_division": _get(g, "awayClassification", "away_division"),
            "home_points": _get(g, "homePoints", "home_points"),
            "away_points": _get(g, "awayPoints", "away_points"),
        }
        rows.append(row)
        if pd.notna(start):
            # Pregame Elo is computed from earlier results, so it is known before kickoff.
            published = (start - timedelta(minutes=1)).to_pydatetime()
            for side in ("home", "away"):
                rating = _get(g, f"{side}PregameElo", f"{side}_pregame_elo")
                if rating is not None:
                    elo.append(
                        RatingRow("cfbd_elo", row["season"], row["week"], row[f"{side}_id"], float(rating),
                                  published, published, f"game:{row['game_id']}")
                    )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["home_points"] = df["home_points"].astype("Int64")
        df["away_points"] = df["away_points"].astype("Int64")
    return df, elo


_FORMATTED = re.compile(r"^(?P<team>.+?)\s+(?P<num>[+-]?\d+(?:\.\d+)?)\s*$")


def check_spread_sign(home_team: str, away_team: str, spread: float, formatted: str | None) -> bool | None:
    """True when `spread` (home convention) agrees with e.g. 'Georgia -7.5'. None if not checkable."""
    if not formatted:
        return None
    text = formatted.strip()
    if re.fullmatch(r"(?i)(pk|pick|pick'?em|even)", text):
        return spread == 0
    m = _FORMATTED.match(text)
    if not m:
        return None
    team, num = normalize(m["team"]), float(m["num"])
    if team == normalize(home_team):
        return abs(num - spread) < 1e-9
    if team == normalize(away_team):
        return abs(-num - spread) < 1e-9
    return None


def parse_lines(payload: list[dict], fetched_at: datetime) -> ParseResult:
    """One row per (game, provider, snapshot). `spread` -> close, `spreadOpen` -> open."""
    out = ParseResult()
    for g in payload:
        game_id = int(g["id"])
        home, away = _get(g, "homeTeam", "home_team", default=""), _get(g, "awayTeam", "away_team", default="")
        for ln in g.get("lines") or []:
            provider = f"cfbd:{ln.get('provider', 'unknown')}"
            spread = _num(ln.get("spread"))
            total = _num(_get(ln, "overUnder", "over_under"))
            if spread is not None or total is not None:
                out.lines.append(LineRow(game_id, provider, "close", spread, fetched_at, total=total))
                if spread is not None:
                    ok = check_spread_sign(home, away, spread, _get(ln, "formattedSpread", "formatted_spread"))
                    if ok is False:
                        out.issues.append(
                            {"game_id": game_id, "provider": provider, "issue": "spread_sign_disagrees",
                             "spread": spread, "formatted": _get(ln, "formattedSpread", "formatted_spread")}
                        )
            spread_open = _num(_get(ln, "spreadOpen", "spread_open"))
            total_open = _num(_get(ln, "overUnderOpen", "over_under_open"))
            if spread_open is not None or total_open is not None:
                out.lines.append(LineRow(game_id, provider, "open", spread_open, fetched_at, total=total_open))
    return out


# ---------------------------------------------------------------- ingest


def ingest_teams(con: duckdb.DuckDBPyConnection, client: CFBDClient, fresh: bool = False) -> int:
    payload, _ = client.teams(fresh=fresh)
    return upsert_df(con, "teams", parse_teams(payload), "team_id")


def ingest_season(con: duckdb.DuckDBPyConnection, client: CFBDClient, season: int, fresh: bool = False) -> dict:
    games_payload, _ = client.games(season, fresh=fresh)
    games, elo = parse_games(games_payload)
    n_games = upsert_df(con, "games", games, "game_id")
    lines_payload, snap = client.lines(season, fresh=fresh)
    parsed = parse_lines(lines_payload, snap.fetched_at)
    parsed.ratings += elo
    counts = store(con, parsed)
    return {"season": season, "games": n_games, "lines": counts["lines"], "elo_ratings": counts["source_ratings"],
            "issues": parsed.issues}
