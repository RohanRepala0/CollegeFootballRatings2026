"""Key-number mixture in the style of nfelo's "Margin Probabilities from NFL
Spreads" (plan section 4.4, model 3), refit on college data.

    p(k | M) = Normal_disc(k | M, sigma)
             + sum over key numbers kappa (both signs) of
                 h_|kappa| * SG(kappa; M) * lift(kappa, M) * Normal_disc(k | kappa, tau)

    SG(kappa; M)   = exp(-(|kappa - M| / s) ** q)        super-gaussian centered at M
    lift(kappa, M) = 1 + lam when kappa and M have opposite signs (the favorite loses)

then P(0) is removed and the two sides are rescaled so half the mass sits below
M and half above it (mass exactly at M is split evenly). The structure follows
the plan's description; nfelo's own parameter values are NFL-specific and are
not used. Every parameter is fit by maximum likelihood.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from .base import SUPPORT, MarginDistribution, discretized_normal, drop_zero, margin_index, observed_probs

DEFAULT_KEYS = (3, 7, 10, 14, 17, 21, 24, 28)


def median_rebalance(p: np.ndarray, centers: np.ndarray) -> np.ndarray:
    below = SUPPORT[None, :] < centers[:, None]
    above = SUPPORT[None, :] > centers[:, None]
    at = ~(below | above)
    L, U, A = (p * below).sum(1), (p * above).sum(1), (p * at).sum(1)
    target = (0.5 - A / 2)[:, None]
    a = target / np.maximum(L, 1e-12)[:, None]
    b = target / np.maximum(U, 1e-12)[:, None]
    return p * (below * a + above * b + at)


class KeyNumberMixture(MarginDistribution):
    name = "nfelo_mixture"

    def __init__(self, key_numbers=DEFAULT_KEYS, rebalance: bool = True):
        self.keys = np.array(sorted(set(key_numbers)), dtype=float)
        self.rebalance = rebalance
        self.sigma, self.tau, self.s, self.q, self.lam = 15.5, 0.6, 14.0, 2.0, 0.0
        self.heights = np.full(len(self.keys), 0.02)

    # theta packs every parameter so scipy can optimize them together
    def _unpack(self, theta):
        self.sigma, self.tau, self.s, self.q, self.lam = theta[:5]
        self.heights = np.asarray(theta[5:])

    def _pack(self):
        return np.concatenate([[self.sigma, self.tau, self.s, self.q, self.lam], self.heights])

    def pmf_matrix(self, centers: np.ndarray) -> np.ndarray:
        centers = np.atleast_1d(np.asarray(centers, float))
        p = discretized_normal(centers, self.sigma)
        signed = np.concatenate([-self.keys, self.keys])
        h = np.concatenate([self.heights, self.heights])
        comps = discretized_normal(signed, self.tau)                         # (2k, support)
        sg = np.exp(-(np.abs(signed[None, :] - centers[:, None]) / self.s) ** self.q)
        opposite = (np.sign(signed)[None, :] * np.sign(centers)[:, None]) < 0
        weight = h[None, :] * sg * (1 + self.lam * opposite)                  # (n, 2k)
        p = drop_zero(p + weight @ comps)
        if not self.rebalance:
            return p
        # The rebalance puts the 50/50 point on a line, which is exact when the center is a
        # market line (integer or half point). A blended model margin is rarely on that
        # grid, so blend the rebalances at the two neighboring grid points by distance:
        # unchanged on the grid, continuous between (otherwise every center in (7, 8)
        # would price like 7.5).
        lo = np.floor(centers * 2) / 2
        w = ((centers - lo) / 0.5)[:, None]
        return (1 - w) * median_rebalance(p, lo) + w * median_rebalance(p, lo + 0.5)

    def fit(self, centers: np.ndarray, margins: np.ndarray) -> "KeyNumberMixture":
        centers, idx = np.asarray(centers, float), margin_index(margins)
        self.sigma = float(np.std(np.asarray(margins) - centers)) or 15.0

        def nll(theta):
            self._unpack(theta)
            return -np.log(np.maximum(observed_probs(self, centers, idx), 1e-300)).sum()

        bounds = [(3.0, 40.0), (0.3, 3.0), (2.0, 60.0), (1.0, 8.0), (-0.9, 3.0)] + [(0.0, 0.5)] * len(self.keys)
        res = minimize(nll, self._pack(), bounds=bounds, method="L-BFGS-B", options={"maxiter": 500})
        self._unpack(res.x)
        return self

    def params(self) -> dict:
        return {"sigma": float(self.sigma), "tau": float(self.tau), "sg_scale": float(self.s),
                "sg_power": float(self.q), "opposite_sign_lift": float(self.lam),
                "key_heights": {int(k): round(float(h), 4) for k, h in zip(self.keys, self.heights)}}
