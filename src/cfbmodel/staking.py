"""Fractional Kelly with pushes (plan section 4.6). Stakes are in units."""

from __future__ import annotations

from dataclasses import dataclass

from . import config
from .conventions import american_to_decimal
from .pricing import Price


@dataclass
class StakingRules:
    kelly_fraction: float = 0.25
    max_bet_fraction: float = 0.02
    min_ev: float = 0.02
    bankroll: float = 100.0

    @classmethod
    def from_settings(cls) -> "StakingRules":
        b = config.settings().get("betting", {})
        return cls(b.get("kelly_fraction", 0.25), b.get("max_bet_fraction", 0.02), b.get("min_ev", 0.02),
                   b.get("bankroll_units", 100.0))


def kelly_fraction(p_win: float, p_loss: float, american: float) -> float:
    """Full-Kelly fraction of bankroll. A push returns the stake, so only the
    win/loss split matters: f = (b*p - q) / b with p = p_win/(p_win+p_loss), q = 1-p."""
    if p_win + p_loss <= 0:
        return 0.0
    b = american_to_decimal(american) - 1
    p = p_win / (p_win + p_loss)
    return max(0.0, (b * p - (1 - p)) / b)


def stake(price: Price, rules: StakingRules) -> float:
    if price.ev < rules.min_ev:
        return 0.0
    f = rules.kelly_fraction * kelly_fraction(price.p_win, price.p_loss, price.american)
    return round(min(f, rules.max_bet_fraction) * rules.bankroll, 2)
