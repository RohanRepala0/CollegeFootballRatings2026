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
    summary = select.summarize(select.walk_forward(df, min_train=2))
    assert summary.index[0] in ("nfelo_mixture", "sides_harvill_approx")
    assert summary.loc["normal", "delta_vs_best"] < 0


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
