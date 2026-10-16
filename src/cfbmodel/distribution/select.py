"""Fit, compare out of sample, persist (plan section 4.4)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .base import SUPPORT, MarginDistribution, margin_index, observed_probs
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


COVER_WINDOW = 10.0   # score every half-point line within this many points of the center


def cover_losses(model: MarginDistribution, centers: np.ndarray, margins: np.ndarray,
                 window: float = COVER_WINDOW) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-game cover scores: (log-loss sum, Brier sum, number of lines scored).

    For every half-point line t within `window` of the game's center, the model's
    P(margin > t) is scored against what happened. These are the alt-line cover
    probabilities the bet card prices (model margin != book line), so this ranks
    distributions by the thing that is bet, not by exact-margin likelihood. The
    line at -0.5 is skipped: with P(0) = 0 it is the same event as +0.5.
    """
    centers, margins = np.asarray(centers, float), np.asarray(margins, float)
    uniq, inv = np.unique(centers, return_inverse=True)
    surv = np.cumsum(model.pmf_matrix(uniq)[:, ::-1], axis=1)[:, ::-1]   # surv[:, j] = P(margin >= SUPPORT[j])
    offsets = np.arange(-int(window) - 1, int(window) + 2)
    j = np.floor(centers)[:, None] + offsets[None, :]                     # event: margin >= j, line j - 0.5
    use = (np.abs(j - 0.5 - centers[:, None]) <= window) & (j != 0) & (j > SUPPORT[0]) & (j <= SUPPORT[-1])
    col = np.clip(j, SUPPORT[0], SUPPORT[-1]).astype(int) - SUPPORT[0]
    p = np.clip(surv[inv[:, None], col], 1e-12, 1 - 1e-12)
    hit = margins[:, None] >= j
    ll = -np.where(hit, np.log(p), np.log1p(-p))
    brier = (p - hit) ** 2
    return (ll * use).sum(axis=1), (brier * use).sum(axis=1), use.sum(axis=1)


def walk_forward_games(df: pd.DataFrame, models: list[str] | None = None, min_train: int = 3,
                       fitted: dict | None = None) -> pd.DataFrame:
    """One row per (model, test game): exact-margin log-likelihood and cover scores.

    df has season, center (mkt_margin at close), home_margin. For each season S with
    at least `min_train` earlier seasons, every model is fit on seasons < S only.
    Pass a dict as `fitted` to keep each {(model, season): fitted model}.
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
            if fitted is not None:
                fitted[(name, s)] = m
            ll = np.log(np.maximum(observed_probs(m, test.center.values, margin_index(test.home_margin.values)),
                                   1e-300))
            c_ll, c_br, n_lines = cover_losses(m, test.center.values, test.home_margin.values)
            out.append(pd.DataFrame({"model": name, "season": s, "row": np.arange(len(test)), "log_lik": ll,
                                     "cover_ll": c_ll, "cover_brier": c_br, "n_lines": n_lines}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["model", "season", "row", "log_lik", "cover_ll", "cover_brier", "n_lines"])


def walk_forward(df: pd.DataFrame, models: list[str] | None = None, min_train: int = 3) -> pd.DataFrame:
    """Per (model, season) totals of walk_forward_games."""
    return season_table(walk_forward_games(df, models, min_train))


def season_table(games: pd.DataFrame) -> pd.DataFrame:
    g = games.groupby(["model", "season"], sort=False).agg(
        n=("row", "size"), log_lik=("log_lik", "sum"), cover_ll=("cover_ll", "sum"),
        cover_brier=("cover_brier", "sum"), n_lines=("n_lines", "sum")).reset_index()
    g["mean_log_lik"] = g.log_lik / g.n
    g["cover_log_loss"] = g.cover_ll / g.n_lines
    return g


METRICS = {"cover": ("cover_log_loss", True), "log_lik": ("log_lik", False)}


def summarize(results: pd.DataFrame, by: str = "cover") -> pd.DataFrame:
    """Pooled walk-forward scores per model, best first.

    by="cover" ranks by cover log loss over nearby lines (lower is better);
    by="log_lik" by exact-margin log-likelihood (higher is better). Both are shown.
    """
    g = results.groupby("model").agg(n=("n", "sum"), log_lik=("log_lik", "sum"), cover_ll=("cover_ll", "sum"),
                                     cover_brier=("cover_brier", "sum"), n_lines=("n_lines", "sum"))
    g["mean_log_lik"] = g.log_lik / g.n
    g["delta_log_lik"] = g.log_lik - g.log_lik.max()
    g["cover_log_loss"] = g.cover_ll / g.n_lines
    g["cover_brier"] = g.cover_brier / g.n_lines
    g["delta_cover_ll"] = g.cover_ll - g.cover_ll.min()
    col, ascending = METRICS[by]
    return g.drop(columns=["cover_ll"]).sort_values(col, ascending=ascending)


def paired_se(games: pd.DataFrame, metric: str = "cover_ll") -> pd.DataFrame:
    """Each model's per-game difference from the best model on `metric` (summed per game):
    total difference and its standard error across games. Games are the independent unit."""
    wide = games.pivot_table(index=["season", "row"], columns="model", values=metric)
    lower_is_better = metric != "log_lik"
    best = wide.sum().idxmin() if lower_is_better else wide.sum().idxmax()
    d = wide.sub(wide[best], axis=0)
    n = len(d)
    return pd.DataFrame({"vs": best, "total_diff": d.sum(), "se": d.std(ddof=1) * np.sqrt(n), "n_games": n})


def best_model(df: pd.DataFrame, by: str = "cover", min_train: int = 3) -> str:
    wf = walk_forward(df, min_train=min_train)
    return summarize(wf, by).index[0] if len(wf) else "normal"


def key_number_table(df: pd.DataFrame, fitted: dict, keys: tuple[int, ...] = (3, 7, 10, 14),
                     half_width: float = 1.0) -> pd.DataFrame:
    """Out of sample: for games whose favorite is within `half_width` of each key number,
    observed vs predicted P(favorite wins by k+) and P(by k+1+), i.e. covering -(k-0.5) and
    -(k+0.5). Predictions come from each season's walk-forward fit (`fitted` from
    walk_forward_games), so no game is scored by a model that saw it."""
    names = sorted({n for n, _ in fitted})
    seasons = sorted({s for _, s in fitted})
    test = df[df.season.isin(seasons) & (df.center != 0)]
    rows = []
    for k in keys:
        sel = test[(test.center.abs() >= k - half_width) & (test.center.abs() <= k + half_width)]
        fav_center, fav_margin = sel.center.abs().values, (sel.home_margin * np.sign(sel.center)).values
        for thr in (k, k + 1):
            obs = float((fav_margin >= thr).mean()) if len(sel) else np.nan
            row = {"fav_by": f"{k - half_width:g} to {k + half_width:g}", "fav_line": f"-{thr - 0.5:g}",
                   "n": len(sel), "observed": obs, "obs_se": float(np.sqrt(obs * (1 - obs) / max(len(sel), 1)))}
            for name in names:
                p = np.empty(len(sel))
                for s in seasons:
                    mask = (sel.season == s).values
                    if mask.any():
                        pmf = fitted[(name, s)].pmf_matrix(fav_center[mask])
                        p[mask] = pmf[:, SUPPORT >= thr].sum(axis=1)
                row[name] = float(p.mean()) if len(sel) else np.nan
            rows.append(row)
    return pd.DataFrame(rows)


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
