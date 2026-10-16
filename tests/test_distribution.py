import numpy as np
import pandas as pd
import pytest

from cfbmodel.distribution import SUPPORT, DiscretizedNormal, FrequencyWeightedNormal, KeyNumberMixture, select
from cfbmodel.distribution.base import ZERO


def synthetic(n=4000, seed=1):
    """Synthetic margins with key-number spikes. For tests only, not real data."""
    rng = np.random.default_rng(seed)
    centers = np.round(rng.normal(0, 13, n) * 2) / 2
    m = np.round(rng.normal(centers, 16)).astype(int)
    spike = rng.random(n) < 0.12
    sign = np.sign(centers[spike] + rng.normal(0, 8, spike.sum())).astype(int)
    sign[sign == 0] = 1
    m[spike] = sign * rng.choice([3, 7, 10, 14], spike.sum())
    m[m == 0] = rng.choice([-1, 1], (m == 0).sum())
    return centers, m


@pytest.mark.parametrize("model", [DiscretizedNormal(), DiscretizedNormal("linear"), FrequencyWeightedNormal(),
                                   KeyNumberMixture()])
def test_pmf_valid(model):
    p = model.pmf_matrix(np.array([-21.5, -7.0, 0.0, 3.0, 6.5, 45.0]))
    assert p.shape == (6, len(SUPPORT))
    np.testing.assert_allclose(p.sum(axis=1), 1.0)
    assert (p[:, ZERO] == 0).all()
    assert (p >= 0).all()


def test_normal_recovers_sigma():
    rng = np.random.default_rng(0)
    centers = rng.normal(0, 10, 5000)
    margins = np.round(rng.normal(centers, 14.0))
    margins[margins == 0] = 1
    m = DiscretizedNormal().fit(centers, margins)
    assert m.sigma0 == pytest.approx(14.0, abs=0.6)


def test_normal_is_symmetric_at_pickem():
    p = DiscretizedNormal().pmf(0.0)
    np.testing.assert_allclose(p, p[::-1])


def test_mixture_puts_half_the_mass_on_each_side():
    m = KeyNumberMixture()
    m.heights[:] = 0.2
    for c in (-6.5, 3.0, 10.5):
        p = m.pmf(c)
        below, at = p[SUPPORT < c].sum(), p[SUPPORT == c].sum()
        assert below + at / 2 == pytest.approx(0.5)


def test_key_number_models_beat_normal_on_spiky_data():
    centers, m = synthetic()
    df = pd.DataFrame({"season": np.repeat([1, 2, 3, 4], len(m) // 4), "center": centers, "home_margin": m})
    wf = select.walk_forward(df, min_train=2)
    by_ll = select.summarize(wf, "log_lik")
    assert by_ll.index[0] in ("nfelo_mixture", "sides_harvill_approx")
    assert by_ll.loc["normal", "delta_log_lik"] < 0
    by_cover = select.summarize(wf, "cover")
    assert by_cover.index[0] != "normal" and by_cover.loc["normal", "delta_cover_ll"] > 0


def test_cover_losses_count_lines_and_prefer_the_true_spread():
    one = DiscretizedNormal()
    _, _, n = select.cover_losses(one, np.array([7.5, 7.0, -3.0]), np.array([10, -3, 1]))
    # 7.5: lines -1.5..17.5 skipping -0.5 -> 20; 7.0: -1.5..16.5 -> 19; -3.0: -12.5..6.5 -> 19
    assert n.tolist() == [20, 19, 19]

    rng = np.random.default_rng(3)
    centers = np.round(rng.normal(0, 12, 6000) * 2) / 2
    margins = np.round(rng.normal(centers, 14.0))
    margins[margins == 0] = 1
    right, wide = DiscretizedNormal(), DiscretizedNormal()
    right.sigma0, wide.sigma0 = 14.0, 22.0
    ll_right, br_right, _ = select.cover_losses(right, centers, margins)
    ll_wide, br_wide, _ = select.cover_losses(wide, centers, margins)
    assert ll_right.sum() < ll_wide.sum() and br_right.sum() < br_wide.sum()


def test_cover_losses_ignore_lines_beyond_the_support():
    _, _, n = select.cover_losses(DiscretizedNormal(), np.array([79.5]), np.array([85]))
    assert n[0] == 11            # 69.5 .. 79.5 only


def test_paired_se_and_key_number_table():
    centers, m = synthetic(3000)
    df = pd.DataFrame({"season": np.repeat([1, 2, 3], len(m) // 3), "center": centers, "home_margin": m})
    fitted = {}
    games = select.walk_forward_games(df, ["normal", "nfelo_mixture"], min_train=1, fitted=fitted)
    assert set(fitted) == {(n, s) for n in ("normal", "nfelo_mixture") for s in (2, 3)}
    paired = select.paired_se(games, "cover_ll")
    assert paired.loc[paired.vs.iloc[0], "total_diff"] == 0 and (paired.total_diff >= 0).all()
    kt = select.key_number_table(df, fitted, keys=(7,))
    assert kt.fav_line.tolist() == ["-6.5", "-7.5"]
    assert kt.n.iloc[0] == ((df.season > 1) & df.center.abs().between(6, 8)).sum()
    assert kt[["observed", "normal", "nfelo_mixture"]].apply(lambda c: c.between(0, 1)).all().all()


def test_frequency_weights_pick_up_key_numbers():
    centers, m = synthetic()
    fw = FrequencyWeightedNormal().fit(centers, m)
    assert fw.weights[7] > fw.weights[6] and fw.weights[3] > fw.weights[4]


def test_save_load_round_trip(tmp_path):
    centers, m = synthetic(1500)
    model = KeyNumberMixture().fit(centers, m)
    select.save(model, tmp_path / "d.json")
    again = select.load(tmp_path / "d.json")
    np.testing.assert_allclose(again.pmf(-6.5), model.pmf(-6.5))


def test_mixture_prices_continuously_between_market_lines():
    centers, m = synthetic(3000)
    model = KeyNumberMixture().fit(centers, m)
    # Off the integer/half-point grid the old rebalance jumped: 6.99 and 7.01 priced like 6.5 and 7.5.
    below, above = model.pmf(6.99), model.pmf(7.01)
    assert abs(below[SUPPORT >= 7].sum() - above[SUPPORT >= 7].sum()) < 0.005
    # Away from pick'em, where the opposite-sign lift switches sides by design (0.02 pts on real data).
    grid = np.linspace(0.5, 20, 1951)
    means = model.pmf_matrix(grid) @ SUPPORT
    assert np.all(np.diff(means) > 0) and np.diff(means).max() < 0.05
    np.testing.assert_allclose(model.pmf_matrix(-grid) @ SUPPORT, -means, atol=1e-9)


def test_mixture_on_grid_is_the_plain_rebalance():
    from cfbmodel.distribution.nfelo_mixture import median_rebalance

    centers, m = synthetic(3000)
    model = KeyNumberMixture().fit(centers, m)
    grid = np.array([-10.5, -7.0, -3.0, 2.5, 7.0, 7.5, 14.0])
    model.rebalance = False
    raw = model.pmf_matrix(grid)
    model.rebalance = True
    np.testing.assert_allclose(model.pmf_matrix(grid), median_rebalance(raw, grid), atol=1e-15)
    p = model.pmf(7.5)
    assert p[SUPPORT <= 7].sum() == pytest.approx(0.5)          # a half-point line is the 50/50 point
