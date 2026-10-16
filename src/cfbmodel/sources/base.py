"""Standard rows every source adapter emits, plus the CFBD game join."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import duckdb
import pandas as pd


@dataclass
class ProjectionRow:
    game_id: int
    source: str
    exp_margin: float          # expected home margin, home-signed
    includes_hfa: bool         # does exp_margin already include home-field advantage?
    published_at: datetime
    fetched_at: datetime
    home_win_prob: float | None = None
    exp_total: float | None = None
    raw_ref: str | None = None


@dataclass
class LineRow:
    game_id: int
    provider: str
    snapshot: str              # open | early | bet | close | unknown
    home_spread: float | None  # book convention for the home team; mkt_margin = -home_spread
    fetched_at: datetime
    spread_price_home: float | None = None
    spread_price_away: float | None = None
    total: float | None = None


@dataclass
class RatingRow:
    source: str
    season: int
    week: int
    team_id: int
    rating: float
    published_at: datetime
    fetched_at: datetime
    raw_ref: str | None = None


@dataclass
class ParseResult:
    projections: list[ProjectionRow] = field(default_factory=list)
    lines: list[LineRow] = field(default_factory=list)
    ratings: list[RatingRow] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)   # join failures, unrecognized headers, ...

    def extend(self, other: "ParseResult") -> None:
        self.projections += other.projections
        self.lines += other.lines
        self.ratings += other.ratings
        self.issues += other.issues

    def frames(self) -> dict[str, pd.DataFrame]:
        return {
            "source_projections": pd.DataFrame([asdict(r) for r in self.projections]),
            "lines": pd.DataFrame([asdict(r) for r in self.lines]),
            "source_ratings": pd.DataFrame([asdict(r) for r in self.ratings]),
        }


class GameIndex:
    """Find a CFBD game from a source's (season, team pair, week or date)."""

    def __init__(self, games: pd.DataFrame):
        self.games = games.copy()
        self.games["start_date"] = pd.to_datetime(self.games["start_date"], utc=True)
        self._by_pair: dict[tuple[int, frozenset], list[int]] = {}
        for i, row in self.games.iterrows():
            key = (int(row.season), frozenset((int(row.home_id), int(row.away_id))))
            self._by_pair.setdefault(key, []).append(i)

    @classmethod
    def from_db(cls, con: duckdb.DuckDBPyConnection, seasons: list[int] | None = None) -> "GameIndex":
        q = "SELECT * FROM games"
        if seasons:
            q += f" WHERE season IN ({', '.join(str(int(s)) for s in seasons)})"
        return cls(con.execute(q).df())

    def find(
        self,
        season: int,
        team_a: int,
        team_b: int,
        week: int | None = None,
        date: datetime | None = None,
        season_type: str | None = None,
    ) -> pd.Series | None:
        idx = self._by_pair.get((season, frozenset((team_a, team_b))), [])
        cands = self.games.loc[idx]
        if season_type is not None and len(cands) > 1:
            cands = cands[cands.season_type == season_type]
        if len(cands) > 1 and week is not None:
            same = cands[cands.week == week]
            cands = same if len(same) else cands
        if len(cands) > 1 and date is not None:
            ts = pd.Timestamp(date)
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
            gap = (cands.start_date - ts).abs()
            cands = cands[gap == gap.min()]
        if len(cands) != 1:
            return None
        return cands.iloc[0]

    def week_games(self, season: int, week: int, season_type: str = "regular") -> pd.DataFrame:
        g = self.games
        return g[(g.season == season) & (g.week == week) & (g.season_type == season_type)]

    def inferred_published_at(self, season: int, week: int, season_type: str = "regular") -> datetime | None:
        """For archived weekly projections with no timestamp: one hour before the
        week's first kickoff. Flagged as inferred in raw_ref by the adapters."""
        wk = self.week_games(season, week, season_type)
        if wk.empty or wk.start_date.isna().all():
            return None
        return (wk.start_date.min() - timedelta(hours=1)).to_pydatetime()
