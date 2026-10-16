"""Price any spread bet from a margin distribution (plan section 4.5)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
import pandas as pd

from .conventions import american_to_decimal, prob_to_american
from .distribution.base import SUPPORT, MarginDistribution

Side = Literal["home", "away"]


@dataclass
class Price:
    side: Side
    line: float            # book spread for the side bet
    american: float
    p_win: float
    p_push: float
    p_loss: float
    ev: float              # per $1 staked
    fair_american: float   # break-even price, pushes excluded
    p_win_no_push: float

    def as_dict(self) -> dict:
        return asdict(self)


def cover_probs(pmf: np.ndarray, side: Side, line: float) -> tuple[float, float, float]:
    """(p_win, p_push, p_loss) for a bet on `side` at book spread `line`.

    Home at L wins when home_margin > -L; away at L wins when home_margin < L.
    """
    if side == "home":
        win, push = SUPPORT > -line, SUPPORT == -line
    elif side == "away":
        win, push = SUPPORT < line, SUPPORT == line
    else:
        raise ValueError(side)
    p_win, p_push = float(pmf[win].sum()), float(pmf[push].sum())
    return p_win, p_push, max(0.0, 1.0 - p_win - p_push)


def ev_per_dollar(p_win: float, p_loss: float, american: float) -> float:
    return p_win * (american_to_decimal(american) - 1) - p_loss


def price(pmf: np.ndarray, side: Side, line: float, american: float = -110) -> Price:
    p_win, p_push, p_loss = cover_probs(pmf, side, line)
    p_np = p_win / (p_win + p_loss) if p_win + p_loss > 0 else float("nan")
    return Price(side, line, american, p_win, p_push, p_loss, ev_per_dollar(p_win, p_loss, american),
                 prob_to_american(p_np), p_np)


def price_game(dist: MarginDistribution, model_margin: float, home_line: float, home_price: float = -110,
               away_price: float = -110) -> dict[str, Price]:
    """Both sides of one game. `home_line` is the home spread (book convention)."""
    pmf = dist.pmf(model_margin)
    return {"home": price(pmf, "home", home_line, home_price), "away": price(pmf, "away", -home_line, away_price)}


def moneyline_prob(pmf: np.ndarray) -> float:
    """P(home wins). There are no ties, so P(away wins) = 1 - this."""
    return float(pmf[SUPPORT > 0].sum())


def alt_ladder(dist: MarginDistribution, model_margin: float, side: Side, center_line: float,
               width: float = 7.0, american: float = -110) -> pd.DataFrame:
    """Price every half-point line within `width` of `center_line` for one side."""
    pmf = dist.pmf(model_margin)
    lines = np.arange(center_line - width, center_line + width + 0.25, 0.5)
    return pd.DataFrame([price(pmf, side, float(l), american).as_dict() for l in lines])


def fair_center(dist: MarginDistribution, home_line: float, p_home_no_push: float,
                lo: float = -70.0, hi: float = 70.0, tol: float = 1e-4) -> float:
    """The center M whose distribution gives the home side p_home_no_push at home_line.

    Used to turn a (line, two-way price) market into a no-vig fair mkt_margin.
    """
    from scipy.optimize import brentq

    def f(m):
        w, _, l = cover_probs(dist.pmf(m), "home", home_line)
        return w / (w + l) - p_home_no_push

    if f(lo) > 0 or f(hi) < 0:
        return float("nan")
    return float(brentq(f, lo, hi, xtol=tol))
