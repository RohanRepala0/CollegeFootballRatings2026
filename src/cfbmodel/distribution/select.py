"""Fit, compare out of sample, persist (plan section 4.4)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .base import SUPPORT, MarginDistribution, margin_index
from .nfelo_mixture import KeyNumberMixture
from .normal import DiscretizedNormal
from .sides_harvill import FrequencyWeightedNormal

REGISTRY = {
    "normal": lambda: DiscretizedNormal("constant"),
    "normal_linear_sigma": lambda: DiscretizedNormal("linear"),
    "sides_harvill_approx": FrequencyWeightedNormal,
    "nfelo_mixture": KeyNumberMixture,
}


def make(name: str) -> MarginDistribution:
    return REGISTRY[name]()


def walk_forward(df: pd.DataFrame, models: list[str] | None = None, min_train: int = 3) -> pd.DataFrame:
    """df has season, center (mkt_margin at close), home_margin.

    For each season S with at least `min_train` earlier seasons, fit on seasons < S
    and score log-likelihood on S. Returns one row per (model, season).
    """
    models = models or list(REGISTRY)
    seasons = sorted(df.season.unique())
    out = []
    for i, s in enumerate(seasons):
        if i < min_train:
            continue
        train, test = df[df.season < s], df[df.season == s]
        for name in models:
            m = make(name).fit(train.center.values, train.home_margin.values)
            ll = m.log_likelihood(test.center.values, test.home_margin.values)
            out.append({"model": name, "season": s, "n": len(test), "log_lik": ll, "mean_log_lik": ll / len(test)})
    return pd.DataFrame(out)


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    g = results.groupby("model").agg(n=("n", "sum"), log_lik=("log_lik", "sum"))
    g["mean_log_lik"] = g.log_lik / g.n
    g["delta_vs_best"] = g.log_lik - g.log_lik.max()
    return g.sort_values("log_lik", ascending=False)


def frequency_table(model: MarginDistribution, df: pd.DataFrame, spread: float, window: float = 0.5,
                    margins: range = range(-14, 36)) -> pd.DataFrame:
    """Observed vs predicted margin frequencies (favorite's perspective) near a given spread.

    Games with |center| within `window` of `spread` are flipped so the favorite is positive.
    """
    sel = df[(df.center.abs() - spread).abs() <= window]
    sign = np.where(sel.center >= 0, 1, -1)
    fav_margin = sel.home_margin.values * sign
    p = model.pmf_matrix(sel.center.values * sign)
    obs = np.bincount(margin_index(fav_margin), minlength=len(SUPPORT)) / max(len(sel), 1)
    pred = p.mean(axis=0) if len(sel) else np.zeros(len(SUPPORT))
    ks = np.array(list(margins))
    return pd.DataFrame({"margin": ks, "observed": obs[ks + SUPPORT[-1]], "predicted": pred[ks + SUPPORT[-1]],
                         "n_games": len(sel)})


def save(model: MarginDistribution, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"name": model.name, "state": {k: (v.tolist() if isinstance(v, np.ndarray) else v)
                                           for k, v in vars(model).items()}}
    path.write_text(json.dumps(state, indent=2, default=float))


def load(path: Path) -> MarginDistribution:
    data = json.loads(path.read_text())
    model = make(data["name"])
    for k, v in data["state"].items():
        setattr(model, k, np.asarray(v) if isinstance(v, list) else v)
    return model
