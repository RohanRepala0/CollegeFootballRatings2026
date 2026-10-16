"""Phase 0: sign conventions (plan section 1). Most bugs live here."""

import pytest

from cfbmodel.conventions import (
    american_to_decimal, bet_outcome, decimal_to_american, home_margin, home_spread_from_mkt_margin,
    mkt_margin_from_home_spread, no_vig_probs, sign_to_home, spread_to_home,
)


def test_home_margin_positive_when_home_wins():
    assert home_margin(28, 21) == 7
    assert home_margin(10, 24) == -14


def test_home_line_minus_seven_is_mkt_margin_plus_seven():
    assert mkt_margin_from_home_spread(-7) == 7
    assert mkt_margin_from_home_spread(3.5) == -3.5
    assert home_spread_from_mkt_margin(mkt_margin_from_home_spread(-6.5)) == -6.5


def test_projected_winner_margin_signed_to_home():
    assert sign_to_home(3.9, winner_is_home=True) == 3.9
    assert sign_to_home(3.9, winner_is_home=False) == -3.9
    assert sign_to_home(-3.9, winner_is_home=False) == -3.9  # sign in the input is ignored


def test_team_spread_re_expressed_for_home():
    # "Delaware -2.5" with Delaware away -> home spread +2.5
    assert spread_to_home(-2.5, team_is_home=False) == 2.5
    assert spread_to_home(-2.5, team_is_home=True) == -2.5


@pytest.mark.parametrize("margin,side,line,expected", [
    (7, "home", -6.5, "win"),
    (6, "home", -6.5, "loss"),
    (7, "home", -7, "push"),
    (-3, "home", 3.5, "win"),
    (6, "away", 6.5, "win"),
    (7, "away", 6.5, "loss"),
    (7, "away", 7, "push"),
    (-1, "away", -0.5, "win"),
])
def test_bet_outcome(margin, side, line, expected):
    assert bet_outcome(margin, side, line) == expected


def test_odds_round_trip():
    assert american_to_decimal(-110) == pytest.approx(1.909090909)
    assert american_to_decimal(150) == pytest.approx(2.5)
    for a in (-250, -110, 100, 145, 300):
        assert decimal_to_american(american_to_decimal(a)) == pytest.approx(a)
    with pytest.raises(ValueError):
        american_to_decimal(50)


def test_no_vig_symmetric_market_is_fifty_fifty():
    a, b = no_vig_probs(-110, -110)
    assert a == pytest.approx(0.5) and b == pytest.approx(0.5)
    a, b = no_vig_probs(-120, +100)
    assert a > 0.5 and a + b == pytest.approx(1)
