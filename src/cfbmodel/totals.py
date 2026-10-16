"""Totals (over/under): the spread backtest applied to game totals.

Market: the median closing total across CFBD providers (the same retail
consensus as the spread market). Early market: the SP+ sheet's O/U at
publication. Sources: SP+ and BCF Toys projected totals, point in time. The
final total is a distribution over integers 0..160 centered on the blended
total, picked by walk-forward cover log loss on nearby lines exactly as the
margin distribution is. Walk-forward throughout: weights, alpha and the
distribution for season S are fit on seasons < S only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar

from . import config
from .backtest import BacktestConfig, _games, _md, ats_summary, error_metrics, filter_games, reality_check
from .blend import AlphaFit, all_combos, combine, fit_alpha, fit_weights, point_in_time_projections
from .conventions import american_to_decimal
from .db import log_exclusions, utcnow
from .distribution import select as dist_select
from .distribution.base import discretized_normal
from .distribution.nfelo_mixture import grid_rebalance
from .market import latest_lines

T_MAX = 160
T_SUPPORT = np.arange(0, T_MAX + 1)
REF_TOTAL = 55.0   # sigma's reference point in the linear-sigma model


# ---------------------------------------------------------------- distributions


class TotalDistribution:
    name = "base"

    def pmf_matrix(self, centers: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def fit(self, centers: np.ndarray, totals: np.ndarray) -> "TotalDistribution":
        raise NotImplementedError

    def params(self) -> dict:
        raise NotImplementedError

    def pmf(self, center: float) -> np.ndarray:
        return self.pmf_matrix(np.array([center], float))[0]

    def log_likelihood(self, centers: np.ndarray, totals: np.ndarray) -> float:
        return float(np.log(np.maximum(self.observed_probs(centers, totals), 1e-300)).sum())

    def observed_probs(self, centers: np.ndarray, totals: np.ndarray) -> np.ndarray:
        uniq, inv = np.unique(np.asarray(centers, float), return_inverse=True)
        idx = np.clip(np.round(np.asarray(totals, float)).astype(int), 0, T_MAX)
        return self.pmf_matrix(uniq)[inv, idx]


class TotalNormal(TotalDistribution):
    """Discretized normal on 0..160 around center + shift.

    mode "constant": one sigma. "linear": sigma = s0 + s1 * (center - 55), since
    higher totals may vary more. "shift": constant sigma plus a learned offset
    (a standing over/under lean; the walk-forward score says whether it holds up).
    """

    def __init__(self, mode: str = "constant"):
        self.mode = mode
        self.name = {"constant": "normal", "linear": "normal_linear_sigma", "shift": "normal_shift"}[mode]
        self.sigma0, self.sigma1, self.shift = 16.0, 0.0, 0.0

    def _sigma(self, centers: np.ndarray) -> np.ndarray:
        return np.clip(self.sigma0 + self.sigma1 * (centers - REF_TOTAL), 4.0, 40.0)

    def pmf_matrix(self, centers: np.ndarray) -> np.ndarray:
        centers = np.atleast_1d(np.asarray(centers, float))
        return discretized_normal(centers + self.shift, self._sigma(centers), T_SUPPORT)

    def fit(self, centers: np.ndarray, totals: np.ndarray) -> "TotalNormal":
        centers, totals = np.asarray(centers, float), np.asarray(totals, float)
        self.sigma0 = float(np.std(totals - centers)) or 16.0
        if self.mode == "constant":
            def nll(s):
                self.sigma0 = s
                return -self.log_likelihood(centers, totals)
            self.sigma0 = float(minimize_scalar(nll, bounds=(4.0, 40.0), method="bounded").x)
            return self
        start = [self.sigma0, 0.0]
        bounds = [(4.0, 40.0), (-0.5, 0.5)] if self.mode == "linear" else [(4.0, 40.0), (-10.0, 10.0)]

        def nll2(theta):
            if self.mode == "linear":
                self.sigma0, self.sigma1 = theta
            else:
                self.sigma0, self.shift = theta
            return -self.log_likelihood(centers, totals)
        res = minimize(nll2, start, bounds=bounds, method="L-BFGS-B")
        nll2(res.x)
        return self

    def params(self) -> dict:
        return {"sigma0": float(self.sigma0), "sigma1": float(self.sigma1), "shift": float(self.shift)}


class TotalFrequencyWeighted(TotalDistribution):
    """Normal reweighted by how often each final total occurs (scores come in 3s and 7s, so
    some totals are more common than a normal says). Same fit as the margin
    `sides_harvill_approx`: iterative proportional scaling with a pseudocount, sigma refit.

    The weights reshape mass but must not move it: unrecentered they pulled every total
    toward the historical middle (+1.2 at 45.5, -0.6 at 65.5), a standing over/under lean
    the model never chose. So each row is rebalanced to keep the center as its 50/50
    point, as the margin mixture does."""

    name = "frequency_weighted"

    def __init__(self, pseudocount: float = 5.0, iterations: int = 8):
        self.sigma, self.pseudocount, self.iterations = 16.0, pseudocount, iterations
        self.weights = np.ones(T_MAX + 1)

    def pmf_matrix(self, centers: np.ndarray) -> np.ndarray:
        centers = np.atleast_1d(np.asarray(centers, float))
        p = discretized_normal(centers, self.sigma, T_SUPPORT) * self.weights[None, :]
        return grid_rebalance(p / p.sum(axis=1, keepdims=True), centers, T_SUPPORT)

    def fit(self, centers: np.ndarray, totals: np.ndarray) -> "TotalFrequencyWeighted":
        centers, totals = np.asarray(centers, float), np.asarray(totals, float)
        idx = np.clip(np.round(totals).astype(int), 0, T_MAX)
        observed = np.bincount(idx, minlength=T_MAX + 1).astype(float)

        def nll(s):
            self.sigma = s
            return -self.log_likelihood(centers, totals)

        for _ in range(self.iterations):
            self.sigma = float(minimize_scalar(nll, bounds=(4.0, 40.0), method="bounded").x)
            uniq, inv = np.unique(centers, return_inverse=True)
            expected = np.bincount(inv, minlength=len(uniq)) @ self.pmf_matrix(uniq)
            self.weights = self.weights * (observed + self.pseudocount) / (expected + self.pseudocount)
            self.weights /= self.weights.mean()
        self.sigma = float(minimize_scalar(nll, bounds=(4.0, 40.0), method="bounded").x)
        return self

    def params(self) -> dict:
        return {"sigma": float(self.sigma), "pseudocount": self.pseudocount,
                "weights_35_to_75": {t: round(float(self.weights[t]), 3) for t in range(35, 76)}}


REGISTRY = {
    "normal": lambda: TotalNormal("constant"),
    "normal_linear_sigma": lambda: TotalNormal("linear"),
    "normal_shift": lambda: TotalNormal("shift"),
    "frequency_weighted": TotalFrequencyWeighted,
}


def make(name: str) -> TotalDistribution:
    return REGISTRY[name]()


def walk_forward_games(df: pd.DataFrame, models: list[str] | None = None, min_train: int = 3,
                       fitted: dict | None = None) -> pd.DataFrame:
    """Per (model, test game) scores, in the column layout distribution.select expects
    (so its season_table / summarize / paired_se apply unchanged). df: season, center, total."""
    models = models or list(REGISTRY)
    seasons = sorted(df.season.unique())
    out = []
    for i, s in enumerate(seasons):
        if i < min_train:
            continue
        train, test = df[df.season < s], df[df.season == s]
        for name in models:
            m = make(name).fit(train.center.values, train.total.values)
            if fitted is not None:
                fitted[(name, s)] = m
            ll = np.log(np.maximum(m.observed_probs(test.center.values, test.total.values), 1e-300))
            c_ll, c_br, n_lines = dist_select.cover_losses(m, test.center.values, test.total.values,
                                                           support=T_SUPPORT, skip=())
            out.append(pd.DataFrame({"model": name, "season": s, "row": np.arange(len(test)), "log_lik": ll,
                                     "cover_ll": c_ll, "cover_brier": c_br, "n_lines": n_lines}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["model", "season", "row", "log_lik", "cover_ll", "cover_brier", "n_lines"])


def best_model(df: pd.DataFrame, by: str = "cover", min_train: int = 3) -> str:
    wf = dist_select.season_table(walk_forward_games(df, min_train=min_train))
    return dist_select.summarize(wf, by).index[0] if len(wf) else "normal"


# ---------------------------------------------------------------- frames


def consensus_total(con: duckdb.DuckDBPyConnection, snapshot: str = "close") -> pd.DataFrame:
    """Median total across CFBD providers: game_id, mkt_total, n_providers."""
    df = latest_lines(con, snapshot, "cfbd:%").dropna(subset=["total"])
    if df.empty:
        return pd.DataFrame(columns=["game_id", "mkt_total", "n_providers"])
    return df.groupby("game_id").total.agg(mkt_total="median", n_providers="count").reset_index()


def early_total(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = latest_lines(con, "early", "sp_sheet").dropna(subset=["total"])
    return df[["game_id", "total"]].rename(columns={"total": "early_total"})


def _with_totals(con, games: pd.DataFrame) -> pd.DataFrame:
    g = games.copy()
    g["total"] = g.home_points + g.away_points
    return g


def distribution_frame(con: duckdb.DuckDBPyConnection, cfg: BacktestConfig) -> pd.DataFrame:
    g = _with_totals(con, filter_games(con, _games(con, cfg.distribution_seasons), cfg, "distribution", log=False))
    df = g.merge(consensus_total(con, "close")[["game_id", "mkt_total"]], on="game_id")
    return df.rename(columns={"mkt_total": "center"})[["game_id", "season", "center", "total"]]


def totals_frame(con: duckdb.DuckDBPyConnection, cfg: BacktestConfig) -> tuple[pd.DataFrame, list[str]]:
    # Not logged: the spread backtest already logs the same filter for the same games every run.
    games = _with_totals(con, filter_games(con, _games(con, cfg.seasons), cfg, "totals_backtest", log=False))
    close = consensus_total(con, "close")
    no_close = games[~games.game_id.isin(close.game_id)]
    log_exclusions(con, [{"game_id": r.game_id, "season": r.season, "week": r.week, "stage": "totals_backtest",
                          "reason": "no closing total"} for r in no_close.itertuples()])
    df = games.merge(close[["game_id", "mkt_total"]], on="game_id")
    df = df.merge(early_total(con), on="game_id", how="left")
    proj = point_in_time_projections(con, df, log=False).dropna(subset=["exp_total"])
    if proj.empty:
        return df, []
    matrix = proj.pivot_table(index="game_id", columns="source", values="exp_total", aggfunc="last")
    sources = sorted(matrix.columns)
    return df.merge(matrix, left_on="game_id", right_index=True, how="left"), sources


# ---------------------------------------------------------------- pricing and bets


def total_probs(pmf: np.ndarray, side: str, line: float) -> tuple[float, float, float]:
    """(p_win, p_push, p_loss) for an over/under bet at `line`."""
    if side == "over":
        win = T_SUPPORT > line
    elif side == "under":
        win = T_SUPPORT < line
    else:
        raise ValueError(side)
    push = T_SUPPORT == line
    p_win, p_push = float(pmf[win].sum()), float(pmf[push].sum())
    return p_win, p_push, max(0.0, 1.0 - p_win - p_push)


def total_outcome(total: float, side: str, line: float) -> str:
    if total == line:
        return "push"
    return "win" if (total > line) == (side == "over") else "loss"


def simulate_total_bets(test: pd.DataFrame, model_total: np.ndarray, dist: TotalDistribution, line_col: str,
                        cfg: BacktestConfig) -> pd.DataFrame:
    """Bet the over or under at `line_col` when its EV at cfg.american is >= cfg.min_ev."""
    payout = american_to_decimal(cfg.american) - 1
    rows = []
    for g, m in zip(test.itertuples(), model_total):
        line = getattr(g, line_col)
        if pd.isna(m) or pd.isna(line):
            continue
        pmf = dist.pmf(m)
        quotes = []
        for side in ("over", "under"):
            w, p, l = total_probs(pmf, side, line)
            quotes.append((w * payout - l, side, w))
        ev, side, p_win = max(quotes)
        if ev < cfg.min_ev:
            continue
        res = total_outcome(g.total, side, line)
        w, _, l = total_probs(dist.pmf(g.mkt_total), side, line)
        rows.append({"game_id": g.game_id, "season": g.season, "week": g.week, "side": side, "line": line,
                     "ev": ev, "p_win": p_win, "result": res,
                     "profit": {"win": payout, "push": 0.0, "loss": -1.0}[res],
                     "clv_points": (g.mkt_total - line) if side == "over" else (line - g.mkt_total),
                     "clv_prob": w / (w + l) - 0.5 if w + l else np.nan})
    return pd.DataFrame(rows)


def calibration(test: pd.DataFrame, model_total: np.ndarray, dist: TotalDistribution, bins: int = 10):
    """Predicted no-push P(over) at the closing total vs what happened."""
    rows = []
    for g, m in zip(test.itertuples(), model_total):
        if pd.isna(m) or g.total == g.mkt_total:
            continue
        w, _, l = total_probs(dist.pmf(m), "over", g.mkt_total)
        rows.append({"p": w / (w + l), "y": g.total > g.mkt_total})
    df = pd.DataFrame(rows)
    if df.empty:
        return df, np.nan
    p = df.p.clip(1e-6, 1 - 1e-6)
    log_loss = float(-(df.y * np.log(p) + (~df.y) * np.log(1 - p)).mean())
    df["bin"] = pd.cut(df.p, np.linspace(0, 1, bins + 1), include_lowest=True)
    table = df.groupby("bin", observed=True).agg(n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean"))
    return table.reset_index(), log_loss


# ---------------------------------------------------------------- line movement


def season_drift(frame: pd.DataFrame) -> pd.Series:
    """Mean (close - early) per season: what always betting the under earns in CLV points."""
    d = frame.dropna(subset=["early_total", "mkt_total"])
    return (d.mkt_total - d.early_total).groupby(d.season).mean()


def net_of_drift(bets: pd.DataFrame, drift: pd.Series) -> pd.Series:
    """CLV minus what always taking the same side earned that season. Totals fall from the
    early line to the close every season, so raw CLV flatters unders and understates overs."""
    side = np.where(bets.side == "over", 1.0, -1.0)
    return bets.clv_points - side * bets.season.map(drift).fillna(0.0).values


def line_movement(frame: pd.DataFrame) -> pd.DataFrame:
    """(close - early) regressed on (F - early) with an intercept, per season and pooled.
    The intercept is the drift; the slope is information: how far the close moves toward
    the sources per point of disagreement with the early line."""
    d = frame.dropna(subset=["early_total", "mkt_total", "F"])
    rows = []
    for season, g in [*d.groupby("season"), ("all", d)]:
        y, x = (g.mkt_total - g.early_total).values, (g.F - g.early_total).values
        if len(g) < 10:
            continue
        X = np.column_stack([np.ones(len(x)), x])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ coef
        cov = resid @ resid / (len(y) - 2) * np.linalg.inv(X.T @ X)
        rows.append({"season": season, "n": len(g), "drift": float(y.mean()),
                     "drift_se": float(y.std(ddof=1) / np.sqrt(len(y))), "intercept": float(coef[0]),
                     "slope": float(coef[1]), "slope_se": float(np.sqrt(cov[1, 1]))})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- walk-forward run


@dataclass
class TotalsResult:
    cfg: BacktestConfig
    sources: list[str]
    frame: pd.DataFrame
    season_params: list[dict] = field(default_factory=list)
    errors: pd.DataFrame = field(default_factory=pd.DataFrame)
    bias: pd.DataFrame = field(default_factory=pd.DataFrame)
    combos: pd.DataFrame = field(default_factory=pd.DataFrame)
    bets: dict[str, pd.DataFrame] = field(default_factory=dict)
    calibration: pd.DataFrame = field(default_factory=pd.DataFrame)
    log_loss: float = float("nan")
    dist_comparison: pd.DataFrame = field(default_factory=pd.DataFrame)
    dist_paired: pd.DataFrame = field(default_factory=pd.DataFrame)
    dist_frame_seasons: list[int] = field(default_factory=list)
    movement: pd.DataFrame = field(default_factory=pd.DataFrame)


def _alpha(f_train: pd.Series, mkt: np.ndarray, actual: np.ndarray, cfg: BacktestConfig) -> AlphaFit:
    a = fit_alpha(f_train.values, mkt, actual)
    return a if a.n >= 30 else AlphaFit(cfg.alpha_start, 1 - cfg.alpha_start, np.nan, np.nan, a.n)


def run(con: duckdb.DuckDBPyConnection, cfg: BacktestConfig, only: list[int] | None = None) -> TotalsResult:
    """Walk-forward over cfg.seasons; `only` restricts which test seasons are scored."""
    frame, sources = totals_frame(con, cfg)
    dist_df = distribution_frame(con, cfg)
    result = TotalsResult(cfg, sources, frame, dist_frame_seasons=sorted(dist_df.season.unique().tolist()))
    if frame.empty or dist_df.empty:
        return result

    if only is None:
        games_scores = walk_forward_games(dist_df)
        result.dist_comparison = dist_select.summarize(dist_select.season_table(games_scores),
                                                       cfg.distribution_metric)
        result.dist_paired = dist_select.paired_se(games_scores, "cover_ll")

    seasons = sorted(frame.season.unique())
    combo_rows, cal_parts, log_losses, test_frames = [], [], [], []
    bet_parts = {"blend_close": [], "blend_early": []}
    for s in [s for s in seasons[cfg.min_train_seasons:] if only is None or s in only]:
        train, test = frame[frame.season < s], frame[frame.season == s].copy()
        dtrain = dist_df[dist_df.season < s]
        name = cfg.distribution if cfg.distribution in REGISTRY else best_model(
            dtrain, cfg.distribution_metric, min_train=max(1, dtrain.season.nunique() - 2))
        dist = make(name).fit(dtrain.center.values, dtrain.total.values)
        weights = fit_weights(train[sources], train.total) if sources else {}
        f_train = combine(train[sources], weights) if sources else pd.Series(np.nan, index=train.index)
        f_test = combine(test[sources], weights) if sources else pd.Series(np.nan, index=test.index)
        a = _alpha(f_train, train.mkt_total.values, train.total.values, cfg)
        a_early = _alpha(f_train, train.early_total.values, train.total.values, cfg)
        blended = np.where(f_test.isna(), np.nan, a.blend(test.mkt_total.values, f_test.values))
        # An early bet cannot see the close: blend with the early total, alpha fit against it.
        blended_early = np.where(f_test.isna(), np.nan, a_early.blend(test.early_total.values, f_test.values))
        test["F"], test["M"] = f_test.values, blended
        test_frames.append(test)
        result.season_params.append({"season": s, "distribution": name, "weights": str(weights),
                                     "alpha": a.alpha, "beta": a.beta, "beta_se": a.beta_se, "alpha_n": a.n,
                                     "alpha_early": a_early.alpha, "beta_early_se": a_early.beta_se})
        bet_parts["blend_close"].append(simulate_total_bets(test, blended, dist, "mkt_total", cfg))
        bet_parts["blend_early"].append(simulate_total_bets(test, blended_early, dist, "early_total", cfg))
        cal_table, ll = calibration(test, blended, dist)
        if len(cal_table):
            cal_parts.append(cal_table.assign(season=s))
            log_losses.append((ll, int(cal_table.n.sum())))
        for combo in all_combos(sources):
            cols = list(combo)
            fc_train, fc_test = combine(train[cols], {c: 1.0 for c in cols}), combine(test[cols], {c: 1.0 for c in cols})
            ac = _alpha(fc_train, train.mkt_total.values, train.total.values, cfg)
            mc = np.where(fc_test.isna(), np.nan, ac.blend(test.mkt_total.values, fc_test.values))
            ae = _alpha(fc_train, train.early_total.values, train.total.values, cfg)
            me = np.where(fc_test.isna(), np.nan, ae.blend(test.early_total.values, fc_test.values))
            early_bets = simulate_total_bets(test, me, dist, "early_total", cfg)
            if len(early_bets):
                early_bets["clv_net"] = net_of_drift(early_bets, season_drift(test))
            early = ats_summary(early_bets)
            combo_rows.append({"combo": "+".join(combo), "season": s, "alpha": ac.alpha,
                               "early_bets": early["bets"], "early_w": early["w"], "early_l": early["l"],
                               "early_units": float(early_bets.profit.sum()) if len(early_bets) else 0.0,
                               "early_clv_net_sum": float(early_bets.clv_net.sum()) if len(early_bets) else 0.0,
                               **{f"F_{k}": v for k, v in error_metrics(fc_test, test.total).items()},
                               **{f"M_{k}": v for k, v in error_metrics(pd.Series(mc, index=test.index),
                                                                        test.total).items()},
                               **ats_summary(simulate_total_bets(test, mc, dist, "mkt_total", cfg))})

    if not test_frames:
        return result
    tested = pd.concat(test_frames)
    result.frame = tested
    common = tested.dropna(subset=sources + ["early_total"]) if sources else tested
    named = [("market_close", "mkt_total"), ("early_line", "early_total"), ("blend", "M"), ("combined_F", "F"),
             *[(src, src) for src in sources]]
    result.errors = pd.DataFrame(
        [{"forecast": n, "sample": smp, **error_metrics(d[c], d.total)}
         for n, c in named for smp, d in (("full", tested), ("common", common))])
    result.bias = (tested.groupby("season")
                   .apply(lambda d: pd.Series({n: float((d.total - d[c]).mean()) for n, c in named}),
                          include_groups=False)
                   .reset_index())
    result.combos = pd.DataFrame(combo_rows)
    result.bets = {k: pd.concat(v, ignore_index=True) if v else pd.DataFrame() for k, v in bet_parts.items()}
    result.movement = line_movement(tested)
    if len(result.bets["blend_early"]):
        result.bets["blend_early"]["clv_net"] = net_of_drift(result.bets["blend_early"], season_drift(tested))
    result.calibration = pd.concat(cal_parts, ignore_index=True) if cal_parts else pd.DataFrame()
    if log_losses:
        result.log_loss = sum(ll * n for ll, n in log_losses) / sum(n for _, n in log_losses)
    return result


# ---------------------------------------------------------------- report


def write_report(result: TotalsResult, path: Path | None = None) -> Path:
    path = path or config.REPORTS_DIR / f"backtest_totals_{utcnow():%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    f, cfg = result.frame, result.cfg
    parts = [f"# Totals backtest {utcnow():%Y-%m-%d}\n",
             f"Seasons evaluated (walk-forward): {sorted(f.season.unique().tolist()) if len(f) else []}. "
             f"Sources: {', '.join(result.sources) or 'none'}. FBS-only: {cfg.fbs_only}; "
             f"postseason: {cfg.include_postseason}. Market = median closing total across CFBD providers; "
             f"early market = the SP+ sheet's O/U. Min EV {cfg.min_ev:+.0%} at {cfg.american:+.0f}.\n"]
    if len(result.dist_comparison):
        parts += [f"## Total-points distribution (walk-forward over {result.dist_frame_seasons}, "
                  f"ranked by {cfg.distribution_metric})\n",
                  "Cover log loss scores P(total > line) on every half-point line within "
                  f"{dist_select.COVER_WINDOW:g} points of the closing total. Lower is better.\n",
                  _md(result.dist_comparison.reset_index()),
                  "### Paired cover-log-loss differences vs the best (summed over games; SE across games)\n",
                  _md(result.dist_paired.reset_index().rename(columns={"index": "model"}))]
    parts += ["## Per-season fitted parameters\n", _md(pd.DataFrame(result.season_params)),
              "## Forecast error vs actual total\n",
              "`common` = games with every source and an SP+ early O/U.\n", _md(result.errors),
              "## Mean error (actual - forecast) by season\n",
              "A standing bias here is an over/under lean the blend's alpha cannot remove (it has no intercept).\n",
              _md(result.bias),
              "## Line movement: drift vs information\n",
              "`drift` = mean(close - early total): what always betting the under earns in CLV. `slope` = how far "
              "the close moves toward the blended sources per point they disagree with the early line (fit with "
              "an intercept, so drift is not counted as information). `clv_net` in the bet tables below is each "
              "early bet's CLV minus what always taking that side earned that season.\n",
              _md(result.movement)]
    if len(result.combos):
        pooled = result.combos.groupby("combo").agg(
            F_rmse=("F_rmse", "mean"), M_rmse=("M_rmse", "mean"), alpha=("alpha", "mean"), bets=("bets", "sum"),
            w=("w", "sum"), l=("l", "sum"), p=("p", "sum")).reset_index()
        pooled["win_pct"] = pooled.w / (pooled.w + pooled.l)
        early = result.combos.groupby("combo").agg(
            early_bets=("early_bets", "sum"), early_w=("early_w", "sum"), early_l=("early_l", "sum"),
            early_units=("early_units", "sum"), clv_net_sum=("early_clv_net_sum", "sum")).reset_index()
        early["early_win_pct"] = early.early_w / (early.early_w + early.early_l)
        early["early_clv_net"] = early.clv_net_sum / early.early_bets.where(early.early_bets > 0)
        parts += ["## Source combinations (equal weights, alpha fit per season, bets vs close at -110)\n",
                  _md(pooled),
                  "### The same combinations at the early line\n",
                  "SP+ alone is the timing-safe row: its projection and the early O/U come from the same sheet "
                  "row. BCF Toys publish times are inferred (one hour before each week's first kickoff), so its "
                  "projections are not proven to exist when the early O/U was posted.\n",
                  _md(early.drop(columns="clv_net_sum"))]
    for name, bets in result.bets.items():
        parts += [f"## Simulated bets: {name}\n", _md(pd.DataFrame([ats_summary(bets)]))]
        if len(bets):
            parts.append(_md(bets.groupby("side").apply(lambda b: pd.Series(ats_summary(b)),
                                                        include_groups=False).reset_index()))
    parts += [f"## Calibration (P(over) at the closing total, pushes excluded). Log loss: {result.log_loss:.4f}\n",
              _md(result.calibration.astype({"bin": str}) if len(result.calibration) else result.calibration)]
    n_bets = int(sum(len(b) for b in result.bets.values()))
    parts += [f"\n**Sample:** {len(f)} games evaluated; {n_bets} simulated bets across all bet sets.\n\n",
              reality_check(len(result.bets.get("blend_close", pd.DataFrame())))]
    path.write_text("\n".join(parts))
    return path
