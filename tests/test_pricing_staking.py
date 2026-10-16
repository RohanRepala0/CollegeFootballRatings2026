import numpy as np
import pytest

from cfbmodel.distribution import DiscretizedNormal, KeyNumberMixture
from cfbmodel.pricing import alt_ladder, cover_probs, ev_per_dollar, fair_center, moneyline_prob, price, price_game
from cfbmodel.staking import StakingRules, kelly_fraction, stake


def test_model_minus_8_vs_market_minus_6_5():
    dist = DiscretizedNormal(sigma0=15.5)
    pmf = dist.pmf(8.0)                     # we make home -8
    p = price(pmf, "home", -6.5, -110)      # book offers home -6.5
    assert p.p_push == 0                    # half-point line cannot push
    assert p.p_win > 0.5
    assert p.p_win + p.p_loss == pytest.approx(1)
    assert p.ev == pytest.approx(p.p_win * (100 / 110) - p.p_loss)


def test_whole_number_line_can_push_and_sides_mirror():
    dist = DiscretizedNormal()
    sides = price_game(dist, 5.0, home_line=-7)
    assert sides["home"].p_push == pytest.approx(sides["away"].p_push)
    assert sides["home"].p_push > 0
    assert sides["home"].p_win == pytest.approx(sides["away"].p_loss)


def test_fair_line_at_market_is_coin_flip_for_rebalanced_mixture():
    m = KeyNumberMixture()
    w, _, l = cover_probs(m.pmf(6.5), "home", -6.5)
    assert w / (w + l) == pytest.approx(0.5, abs=1e-9)
    assert abs(price(m.pmf(6.5), "home", -6.5).fair_american) == pytest.approx(100, abs=0.5)  # even money


def test_dropping_zero_tilts_the_plain_normal_toward_the_favorite():
    # Plan 4.4: zero out k=0 and renormalize. P(0) sits on the underdog's side of a
    # 6.5 line, so the favorite covers slightly more than half the time.
    w, _, l = cover_probs(DiscretizedNormal().pmf(6.5), "home", -6.5)
    assert 0.5 < w / (w + l) < 0.52


def test_key_number_half_points_are_worth_more():
    m = KeyNumberMixture()
    m.heights[:] = 0.0
    m.heights[list(m.keys).index(7)] = 0.3
    ladder = alt_ladder(m, 7.0, "home", -7.0, width=1.0)
    gain_through_7 = ladder.set_index("line").p_win[-6.5] - ladder.set_index("line").p_win[-7.5]
    flat = DiscretizedNormal(sigma0=m.sigma)
    lf = alt_ladder(flat, 7.0, "home", -7.0, width=1.0).set_index("line").p_win
    assert gain_through_7 > lf[-6.5] - lf[-7.5]


def test_moneyline_and_fair_center():
    dist = DiscretizedNormal()
    assert moneyline_prob(dist.pmf(0.0)) == pytest.approx(0.5)
    assert moneyline_prob(dist.pmf(10.0)) > 0.7
    c = fair_center(dist, -7.0, 0.55)
    w, _, l = cover_probs(dist.pmf(c), "home", -7.0)
    assert w / (w + l) == pytest.approx(0.55, abs=1e-3)


def test_kelly_with_pushes_and_caps():
    assert kelly_fraction(0.5, 0.5, 100) == pytest.approx(0)
    assert kelly_fraction(0.55, 0.45, 100) == pytest.approx(0.10)
    # pushes drop out: same win/loss ratio, same fraction
    assert kelly_fraction(0.55 * 0.9, 0.45 * 0.9, 100) == pytest.approx(0.10)
    rules = StakingRules(kelly_fraction=0.25, max_bet_fraction=0.02, min_ev=0.02, bankroll=100)
    pmf = DiscretizedNormal().pmf(14.0)
    big = price(pmf, "home", -3.5, -110)
    assert stake(big, rules) == 2.0           # capped at 2% of 100 units
    small = price(DiscretizedNormal().pmf(4.0), "home", -3.5, -110)
    assert small.ev < 0.02 and stake(small, rules) == 0


def test_ev_formula():
    assert ev_per_dollar(0.5238095, 0.4761905, -110) == pytest.approx(0, abs=1e-6)
