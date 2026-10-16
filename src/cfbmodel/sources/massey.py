"""Massey composite (plan section 2.4): ordinal rankings only, converted to points.

    z = Phi^-1(1 - (rank - 0.5) / N)
    exp_margin = a * (z_home - z_away) + hfa * (not neutral)      fit per system

The compare page itself could not be fetched while this was written; the
parser below finds the header row by its "Team" cell and treats every short
all-caps column as a system. Inspect the CSV export / archive links on the
real page (plan Phase 4) before relying on it.
"""

from __future__ import annotations

import csv
import io
import re

import numpy as np
import pandas as pd
from scipy.stats import norm

from .. import config


def rank_to_z(rank: np.ndarray, n_teams: int) -> np.ndarray:
    rank = np.asarray(rank, float)
    return norm.ppf(1 - (rank - 0.5) / n_teams)


def parse_compare_csv(text: str) -> pd.DataFrame:
    """Long table: team, system, rank."""
    rows = list(csv.reader(io.StringIO(text)))
    hdr = next((i for i, r in enumerate(rows) if any(c.strip().lower() == "team" for c in r)), None)
    if hdr is None:
        return pd.DataFrame(columns=["team", "system", "rank"])
    header = [c.strip() for c in rows[hdr]]
    team_col = next(i for i, c in enumerate(header) if c.lower() == "team")
    exclude = {s.lower() for s in config.sources().get("massey", {}).get("exclude_systems", [])}
    systems = {i: c for i, c in enumerate(header)
               if i != team_col and re.fullmatch(r"[A-Z0-9]{2,4}", c) and c.lower() not in exclude}
    out = []
    for r in rows[hdr + 1:]:
        if len(r) <= team_col or not r[team_col].strip():
            continue
        for i, sys_name in systems.items():
            if i < len(r) and r[i].strip().isdigit():
                out.append({"team": r[team_col].strip(), "system": sys_name, "rank": int(r[i])})
    return pd.DataFrame(out)


def coverage_filter(long: pd.DataFrame, min_share: float = 0.95) -> pd.DataFrame:
    """Drop systems that rank fewer than min_share of the teams (spotty coverage)."""
    n = long.team.nunique()
    keep = long.groupby("system").team.nunique() >= min_share * n
    return long[long.system.isin(keep[keep].index)]


def z_scores(long: pd.DataFrame) -> pd.DataFrame:
    """Adds z per system, using that system's own team count as N."""
    n = long.groupby("system").team.transform("nunique")
    return long.assign(z=rank_to_z(long["rank"].values, n.values))
