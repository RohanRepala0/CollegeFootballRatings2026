"""Sign conventions (plan section 1). Every module goes through these helpers.

- home_margin  = home points - away points (positive: home won)
- exp_margin   = expected home margin (positive: home favored)
- mkt_margin   = -home_spread (a home line of -7 is mkt_margin +7)

Book-convention spreads are converted at the edge of the system and never stored
in modeling tables.
"""

from __future__ import annotations

import math
from typing import Literal

Outcome = Literal["win", "push", "loss"]


def home_margin(home_points: float, away_points: float) -> float:
    return home_points - away_points


def mkt_margin_from_home_spread(home_spread: float) -> float:
    return -home_spread


def home_spread_from_mkt_margin(mkt_margin: float) -> float:
    return -mkt_margin


def sign_to_home(unsigned_margin: float, winner_is_home: bool) -> float:
    """A margin quoted from the projected winner's side, re-expressed as a home margin."""
    m = abs(unsigned_margin)
    return m if winner_is_home else -m


def spread_to_home(spread: float, team_is_home: bool) -> float:
    """A book spread quoted for one team, re-expressed as the home team's spread."""
    return spread if team_is_home else -spread


def bet_outcome(margin: float, side: Literal["home", "away"], line: float) -> Outcome:
    """Grade a spread bet. `line` is the spread for the side bet (book convention).

    Home at -6.5 wins when home_margin > 6.5. Away at +6.5 wins when home_margin < 6.5.
    """
    if side == "home":
        edge = margin + line
    elif side == "away":
        edge = -margin + line
    else:
        raise ValueError(f"side must be 'home' or 'away', got {side!r}")
    if edge > 0:
        return "win"
    if edge < 0:
        return "loss"
    return "push"


def american_to_decimal(american: float) -> float:
    if american == 0 or -100 < american < 100:
        raise ValueError(f"invalid American odds: {american}")
    return 1 + (american / 100 if american > 0 else 100 / -american)


def decimal_to_american(decimal: float) -> float:
    if decimal <= 1:
        raise ValueError(f"decimal odds must exceed 1, got {decimal}")
    return (decimal - 1) * 100 if decimal >= 2 else -100 / (decimal - 1)


def american_to_implied(american: float) -> float:
    return 1 / american_to_decimal(american)


def prob_to_american(p: float) -> float:
    if not 0 < p < 1:
        return math.nan
    return decimal_to_american(1 / p)


def no_vig_probs(price_a: float, price_b: float) -> tuple[float, float]:
    """Multiplicative vig removal for a two-way market."""
    a, b = american_to_implied(price_a), american_to_implied(price_b)
    return a / (a + b), b / (a + b)
