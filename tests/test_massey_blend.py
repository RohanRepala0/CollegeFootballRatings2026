import numpy as np
import pandas as pd
import pytest

from cfbmodel.blend import LinearRatingModel, all_combos, combine, fit_alpha, fit_weights
from cfbmodel.sources.massey import parse_compare_csv, rank_to_z


def test_rank_to_z_is_monotone_and_centered():
    z = rank_to_z(np.arange(1, 135), 134)
    assert (np.diff(z) < 0).all()
    assert z.mean() == pytest.approx(0, abs=1e-9)


def test_parse_compare_csv_drops_polls():
    text = "Massey compare\nTeam,Conf,MAS,SAG,AP,Mean\nGeorgia,SEC,1,2,1,1.3\nAuburn,SEC,30,28,,29\n"
    long = parse_compare_csv(text)
    assert sorted(long.system.unique()) == ["MAS", "SAG"]


def test_combine_renormalizes_when_a_source_is_missing():
    m = pd.DataFrame({"a": [10.0, 4.0], "b": [6.0, np.nan]})
    F = combine(m, {"a": 1, "b": 3})
    assert F.tolist() == [7.0, 4.0]


def test_fit_weights_simplex():
    rng = np.random.default_rng(0)
    truth = rng.normal(0, 14, 3000)
    m = pd.DataFrame({"good": truth + rng.normal(0, 3, 3000), "bad": truth + rng.normal(0, 12, 3000)})
    w = fit_weights(m, pd.Series(truth + rng.normal(0, 15, 3000)))
    assert sum(w.values()) == pytest.approx(1) and w["good"] > w["bad"] >= 0


def test_alpha_recovers_beta():
    rng = np.random.default_rng(1)
    mkt = rng.normal(0, 14, 20000)
    signal = rng.normal(0, 3, 20000)          # half of F's disagreement with the market is real
    actual = mkt + 0.5 * signal + rng.normal(0, 15, 20000)
    F = mkt + signal
    a = fit_alpha(F, mkt, actual)
    assert a.beta == pytest.approx(0.5, abs=0.1) and a.alpha == pytest.approx(0.5, abs=0.1)


def test_linear_rating_model():
    rng = np.random.default_rng(2)
    diff = rng.normal(0, 1, 5000)
    neutral = rng.random(5000) < 0.1
    y = 12 * diff + 2.5 * ~neutral + rng.normal(0, 10, 5000)
    m = LinearRatingModel().fit(diff, neutral, y)
    assert m.a == pytest.approx(12, abs=0.6) and m.hfa == pytest.approx(2.5, abs=0.6)


def test_all_combos():
    assert len(all_combos(["a", "b", "c"])) == 7
