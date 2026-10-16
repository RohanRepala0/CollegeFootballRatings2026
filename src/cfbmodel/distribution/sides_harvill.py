"""Normal reweighted by historical margin frequencies (plan section 4.4, model 2).

APPROXIMATION, NOT THE PAPER'S EXACT METHOD. Sides & Harvill (arXiv 2212.08116,
"Converting College Football Point Spread Differentials to Probabilities")
adjust normal probabilities with weights from historical point-differential
frequencies so common margins get more mass. arxiv.org was unreachable from
the build environment, so this implements that idea in its plainest form:

    P(k | M)  proportional to  Normal_disc(k | M, sigma) * w(|k|),  P(0) = 0

with w(|k|) fitted by iterative proportional scaling until the model's
expected count of each |margin| matches the training data (plus a pseudocount
for shrinkage), and sigma refit between passes. Replace with the paper's exact
procedure once it can be read; keep this as a baseline if it scores well.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize_scalar

from .base import K_MAX, SUPPORT, MarginDistribution, discretized_normal, drop_zero, margin_index, observed_probs


class FrequencyWeightedNormal(MarginDistribution):
    name = "sides_harvill_approx"

    def __init__(self, sigma: float = 15.5, pseudocount: float = 5.0, iterations: int = 8):
        self.sigma = sigma
        self.pseudocount = pseudocount
        self.iterations = iterations
        self.weights = np.ones(K_MAX + 1)   # indexed by |k|

    def _w_row(self) -> np.ndarray:
        return self.weights[np.abs(SUPPORT)]

    def pmf_matrix(self, centers: np.ndarray) -> np.ndarray:
        centers = np.atleast_1d(np.asarray(centers, float))
        return drop_zero(discretized_normal(centers, self.sigma) * self._w_row()[None, :])

    def fit(self, centers: np.ndarray, margins: np.ndarray) -> "FrequencyWeightedNormal":
        centers, idx = np.asarray(centers, float), margin_index(margins)
        observed = np.bincount(np.abs(SUPPORT[idx]), minlength=K_MAX + 1).astype(float)

        def nll(sigma):
            self.sigma = sigma
            return -np.log(np.maximum(observed_probs(self, centers, idx), 1e-300)).sum()

        for _ in range(self.iterations):
            minimize_scalar(nll, bounds=(3.0, 40.0), method="bounded")
            p = self.pmf_matrix(centers)
            expected = np.zeros(K_MAX + 1)
            np.add.at(expected, np.abs(SUPPORT), p.sum(axis=0))
            ratio = (observed + self.pseudocount) / (expected + self.pseudocount)
            ratio[0] = 1.0
            self.weights = self.weights * ratio
            self.weights /= self.weights[1:].mean()
        minimize_scalar(nll, bounds=(3.0, 40.0), method="bounded")
        return self

    def params(self) -> dict:
        return {"sigma": float(self.sigma), "pseudocount": self.pseudocount,
                "weights": {int(k): round(float(w), 4) for k, w in enumerate(self.weights) if k <= 35}}
