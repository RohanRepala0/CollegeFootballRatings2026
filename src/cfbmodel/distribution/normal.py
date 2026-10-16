"""Discretized normal around the center, P(0) removed (plan section 4.4, model 1)."""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from .base import MarginDistribution, discretized_normal, drop_zero, margin_index, observed_probs


class DiscretizedNormal(MarginDistribution):
    """sigma_mode='constant': one sigma. 'linear': sigma = s0 + s1 * |M| (lopsided games noisier)."""

    def __init__(self, sigma_mode: str = "constant", sigma0: float = 15.5, sigma1: float = 0.0):
        if sigma_mode not in ("constant", "linear"):
            raise ValueError(sigma_mode)
        self.sigma_mode = sigma_mode
        self.sigma0 = sigma0
        self.sigma1 = sigma1
        self.name = "normal" if sigma_mode == "constant" else "normal_linear_sigma"

    def sigma(self, centers: np.ndarray) -> np.ndarray:
        s = self.sigma0 + (self.sigma1 * np.abs(centers) if self.sigma_mode == "linear" else 0.0)
        return np.maximum(s, 1.0)

    def pmf_matrix(self, centers: np.ndarray) -> np.ndarray:
        centers = np.atleast_1d(np.asarray(centers, float))
        return drop_zero(discretized_normal(centers, self.sigma(centers)))

    def fit(self, centers: np.ndarray, margins: np.ndarray) -> "DiscretizedNormal":
        centers, idx = np.asarray(centers, float), margin_index(margins)

        def nll(theta):
            self.sigma0 = theta[0]
            self.sigma1 = theta[1] if self.sigma_mode == "linear" else 0.0
            p = observed_probs(self, centers, idx)
            return -np.log(np.maximum(p, 1e-300)).sum()

        x0 = [float(np.std(np.asarray(margins) - centers)) or 15.0, 0.0]
        bounds = [(3.0, 40.0), (-0.3, 0.5)]
        if self.sigma_mode == "constant":
            x0, bounds = x0[:1], bounds[:1]
        res = minimize(nll, x0, bounds=bounds, method="L-BFGS-B")
        nll(res.x)
        return self

    def params(self) -> dict:
        return {"sigma_mode": self.sigma_mode, "sigma0": float(self.sigma0), "sigma1": float(self.sigma1)}
