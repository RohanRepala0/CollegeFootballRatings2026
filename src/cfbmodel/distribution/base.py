"""Margin distributions over integer home margins (plan section 4.4).

Support is k in [-K, K] (K = 80 by default). The end bins hold the tails, so
probabilities always sum to 1. P(0) = 0 because college football has overtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
from scipy.stats import norm

K_MAX = 80
SUPPORT = np.arange(-K_MAX, K_MAX + 1)
ZERO = K_MAX  # index of k = 0


def discretized_normal(centers: np.ndarray, sigma: np.ndarray | float, support: np.ndarray = SUPPORT) -> np.ndarray:
    """Rows of P(k) = Phi((k+.5-M)/s) - Phi((k-.5-M)/s), tails folded into the end bins."""
    centers = np.atleast_1d(np.asarray(centers, float))[:, None]
    sigma = np.broadcast_to(np.asarray(sigma, float), centers.shape[:1])[:, None]
    edges = np.concatenate([support - 0.5, [support[-1] + 0.5]])
    cdf = norm.cdf((edges[None, :] - centers) / sigma)
    cdf[:, 0], cdf[:, -1] = 0.0, 1.0
    return np.diff(cdf, axis=1)


def drop_zero(p: np.ndarray) -> np.ndarray:
    p = p.copy()
    p[:, ZERO] = 0.0
    return p / p.sum(axis=1, keepdims=True)


def observed_probs(model: "MarginDistribution", centers: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """P(observed margin) per game. Lines sit on half points, so there are only a few
    hundred distinct centers: compute each pmf row once and index into it."""
    uniq, inv = np.unique(np.asarray(centers, float), return_inverse=True)
    return model.pmf_matrix(uniq)[inv, idx]


def margin_index(margins: np.ndarray) -> np.ndarray:
    return np.clip(np.round(np.asarray(margins)).astype(int), -K_MAX, K_MAX) + K_MAX


class MarginDistribution(ABC):
    name: str = "base"

    @abstractmethod
    def fit(self, centers: np.ndarray, margins: np.ndarray) -> "MarginDistribution":
        ...

    @abstractmethod
    def pmf_matrix(self, centers: np.ndarray) -> np.ndarray:
        """(n, 2K+1) array; row i is P(home_margin = k | center_i)."""

    @abstractmethod
    def params(self) -> dict:
        ...

    def pmf(self, center: float) -> np.ndarray:
        return self.pmf_matrix(np.array([center]))[0]

    def log_likelihood(self, centers: np.ndarray, margins: np.ndarray) -> float:
        p = observed_probs(self, centers, margin_index(margins))
        return float(np.log(np.maximum(p, 1e-300)).sum())

    def cdf_matrix(self, centers: np.ndarray) -> np.ndarray:
        return np.cumsum(self.pmf_matrix(centers), axis=1)
