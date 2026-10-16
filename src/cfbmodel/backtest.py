"""Walk-forward backtest (plan section 5).

For each test season S: the margin distribution, source weights and alpha are
fit only on seasons < S, and a game only sees projections published before its
kickoff. Every game dropped from the evaluation is logged to `exclusions` with
a reason. Results are labeled "vs retail consensus close": sharp historical
closes are out of scope at a $0 budget.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from . import config
from .blend import AlphaFit, all_combos, combine, fit_alpha, fit_weights, point_in_time_projections, source_matrix
from .conventions import american_to_decimal, bet_outcome
from .db import log_exclusions, utcnow
from .distribution import select as dist_select
from .distribution.base import MarginDistribution
from .market import early_line, retail_consensus
from .pricing import cover_probs, price

BREAK_EVEN_110 = 110 / 210


@dataclass
class BacktestConfig:
    seasons: list[int]
    distribution_seasons: list[int]
    fbs_only: bool = True
    include_postseason: bool = False
    exclude_weeks_before: int = 0
    min_ev: float = 0.02
    american: float = -110
    distribution: str = "best"          # a registry name, or "best" = highest walk-forward log-lik
    alpha_start: float = 0.6
    min_train_seasons: int = 1

    @classmethod
    def from_settings(cls) -> "BacktestConfig":
        s = config.settings()
        return cls(seasons=s["backtest"]["seasons"], distribution_seasons=s["backtest"]["distribution_seasons"],
                   fbs_only=s["scope"]["fbs_only"], include_postseason=s["scope"]["include_postseason"],
                   exclude_weeks_before=s["scope"]["exclude_weeks_before"], min_ev=s["betting"]["min_ev"],
                   american=s["betting"]["default_price"], alpha_start=s["model"]["alpha_start"],
                   min_train_seasons=s["backtest"]["min_train_seasons"])


# ---------------------------------------------------------------- frames


def _games(con: duckdb.DuckDBPyConnection, seasons: list[int]) -> pd.DataFrame:
    q = f"SELECT * FROM games WHERE season IN ({', '.join(map(str, seasons))})"
    g = con.execute(q).df()
    g["home_margin"] = g.home_points - g.away_points
    return g


def filter_games(con, games: pd.DataFrame, cfg: BacktestConfig, stage: str, log: bool = True) -> pd.DataFrame:
    reasons = pd.Series("", index=games.index)
    if cfg.fbs_only:
        reasons[(games.home_division != "fbs") | (games.away_division != "fbs")] += "not FBS vs FBS;"
    if not cfg.include_postseason:
        reasons[games.season_type != "regular"] += "postseason;"
    if cfg.exclude_weeks_before:
        reasons[games.week < cfg.exclude_weeks_before] += f"week < {cfg.exclude_weeks_before};"
    reasons[games.home_margin.isna()] += "no final score;"
    dropped = games[reasons != ""]
    if log and len(dropped):
        log_exclusions(con, [{"game_id": r.game_id, "season": r.season, "week": r.week, "stage": stage,
                              "reason": reasons[i]} for i, r in dropped.iterrows()])
    return games[reasons == ""].copy()


def distribution_frame(con, cfg: BacktestConfig) -> pd.DataFrame:
    g = filter_games(con, _games(con, cfg.distribution_seasons), cfg, "distribution", log=False)
    close = retail_consensus(con, "close")[["game_id", "mkt_margin"]]
    df = g.merge(close, on="game_id")
    df = df[df.home_margin != 0]
    return df.rename(columns={"mkt_margin": "center"})[["game_id", "season", "center", "home_margin"]]


def backtest_frame(con, cfg: BacktestConfig) -> tuple[pd.DataFrame, list[str]]:
    games = filter_games(con, _games(con, cfg.seasons), cfg, "backtest")
    close = retail_consensus(con, "close")[["game_id", "mkt_margin"]]
    no_close = games[~games.game_id.isin(close.game_id)]
    log_exclusions(con, [{"game_id": r.game_id, "season": r.season, "week": r.week, "stage": "backtest",
                          "reason": "no closing line"} for r in no_close.itertuples()])
    df = games.merge(close, on="game_id")
    early = early_line(con)[["game_id", "home_spread"]].rename(columns={"home_spread": "early_home_spread"})
    df = df.merge(early, on="game_id", how="left")
    proj = point_in_time_projections(con, df)
    matrix = source_matrix(proj, df)
    sources = sorted(matrix.columns)
    df = df.merge(matrix, left_on="game_id", right_index=True, how="left")
    return df, sources


# ---------------------------------------------------------------- evaluation


def pick_distribution(dist_df: pd.DataFrame, cfg: BacktestConfig, test_season: int) -> MarginDistribution:
    train = dist_df[dist_df.season < test_season]
    if len(train) == 0:
        raise RuntimeError(f"no distribution training data before {test_season}")
    name = cfg.distribution
    if name == "best":
        wf = dist_select.walk_forward(train, min_train=max(1, train.season.nunique() - 2))
        name = dist_select.summarize(wf).index[0] if len(wf) else "normal"
    return dist_select.make(name).fit(train.center.values, train.home_margin.values)


def simulate_bets(test: pd.DataFrame, model_margin: np.ndarray, dist: MarginDistribution, line_col: str,
                  cfg: BacktestConfig, close_col: str = "mkt_margin") -> pd.DataFrame:
    """Bet the side with the higher EV at `line_col` (a home spread) when EV >= min_ev."""
    rows = []
    for (_, g), m in zip(test.iterrows(), model_margin):
        home_line = g[line_col] if line_col != "mkt_margin" else -g.mkt_margin
        if pd.isna(m) or pd.isna(home_line):
            continue
        pmf = dist.pmf(m)
        best = max((price(pmf, "home", home_line, cfg.american), price(pmf, "away", -home_line, cfg.american)),
                   key=lambda p: p.ev)
        if best.ev < cfg.min_ev:
            continue
        res = bet_outcome(g.home_margin, best.side, best.line)
        profit = {"win": american_to_decimal(cfg.american) - 1, "push": 0.0, "loss": -1.0}[res]
        close_home = -g[close_col]
        bet_close = close_home if best.side == "home" else -close_home
        w, _, l = cover_probs(dist.pmf(g[close_col]), best.side, best.line)
        rows.append({"game_id": g.game_id, "season": g.season, "week": g.week, "side": best.side,
                     "line": best.line, "ev": best.ev, "p_win": best.p_win, "result": res, "profit": profit,
                     "clv_points": best.line - bet_close, "clv_prob": w / (w + l) - 0.5 if w + l else np.nan})
    return pd.DataFrame(rows)


def ats_summary(bets: pd.DataFrame) -> dict:
    if bets.empty:
        return {"bets": 0, "w": 0, "l": 0, "p": 0, "win_pct": np.nan, "roi": np.nan, "se": np.nan,
                "clv_points": np.nan, "clv_prob": np.nan}
    w, l, p = (bets.result == "win").sum(), (bets.result == "loss").sum(), (bets.result == "push").sum()
    n = w + l
    return {"bets": len(bets), "w": int(w), "l": int(l), "p": int(p), "win_pct": w / n if n else np.nan,
            "roi": bets.profit.sum() / len(bets), "se": 0.5 / math.sqrt(n) if n else np.nan,
            "clv_points": bets.clv_points.mean(), "clv_prob": bets.clv_prob.mean()}


def error_metrics(pred: pd.Series, actual: pd.Series) -> dict:
    ok = pred.notna() & actual.notna()
    e = (pred[ok] - actual[ok]).astype(float)
    return {"n": int(ok.sum()), "rmse": float(np.sqrt((e ** 2).mean())) if len(e) else np.nan,
            "mae": float(e.abs().mean()) if len(e) else np.nan}


def calibration(test: pd.DataFrame, model_margin: np.ndarray, dist: MarginDistribution, bins: int = 10):
    """Predicted no-push home-cover probability at the close vs what happened."""
    rows = []
    for (_, g), m in zip(test.iterrows(), model_margin):
        if pd.isna(m):
            continue
        res = bet_outcome(g.home_margin, "home", -g.mkt_margin)
        if res == "push":
            continue
        w, _, l = cover_probs(dist.pmf(m), "home", -g.mkt_margin)
        rows.append({"p": w / (w + l), "y": res == "win"})
    df = pd.DataFrame(rows)
    if df.empty:
        return df, np.nan
    p = df.p.clip(1e-6, 1 - 1e-6)
    log_loss = float(-(df.y * np.log(p) + (~df.y) * np.log(1 - p)).mean())
    df["bin"] = pd.cut(df.p, np.linspace(0, 1, bins + 1), include_lowest=True)
    table = df.groupby("bin", observed=True).agg(n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean"))
    return table.reset_index(), log_loss


@dataclass
class BacktestResult:
    cfg: BacktestConfig
    sources: list[str]
    frame: pd.DataFrame
    season_params: list[dict] = field(default_factory=list)
    source_errors: pd.DataFrame = field(default_factory=pd.DataFrame)
    combos: pd.DataFrame = field(default_factory=pd.DataFrame)
    bets: dict[str, pd.DataFrame] = field(default_factory=dict)
    calibration: pd.DataFrame = field(default_factory=pd.DataFrame)
    log_loss: float = float("nan")
    dist_comparison: pd.DataFrame = field(default_factory=pd.DataFrame)


def run(con: duckdb.DuckDBPyConnection, cfg: BacktestConfig) -> BacktestResult:
    frame, sources = backtest_frame(con, cfg)
    dist_df = distribution_frame(con, cfg)
    result = BacktestResult(cfg, sources, frame)
    if frame.empty:
        return result

    seasons = sorted(frame.season.unique())
    test_seasons = seasons[cfg.min_train_seasons:]
    combo_rows, err_rows, cal_parts, bet_parts = [], [], [], {"blend_close": [], "blend_early": []}
    test_frames, log_losses = [], []
    for s in test_seasons:
        train, test = frame[frame.season < s], frame[frame.season == s].copy()
        dist = pick_distribution(dist_df, cfg, s)
        weights = fit_weights(train[sources], train.home_margin) if sources else {}
        f_train = combine(train[sources], weights) if sources else pd.Series(np.nan, index=train.index)
        f_test = combine(test[sources], weights) if sources else pd.Series(np.nan, index=test.index)
        alpha = fit_alpha(f_train.values, train.mkt_margin.values, train.home_margin.values)
        if alpha.n < 30:
            alpha = AlphaFit(cfg.alpha_start, 1 - cfg.alpha_start, np.nan, np.nan, alpha.n)
        blended = np.where(f_test.isna(), np.nan, alpha.blend(test.mkt_margin.values, f_test.values))
        test["F"], test["M"] = f_test.values, blended
        test_frames.append(test)
        result.season_params.append({"season": s, "distribution": dist.name, "dist_params": dist.params(),
                                     "weights": weights, "alpha": alpha.alpha, "beta": alpha.beta,
                                     "beta_se": alpha.beta_se, "alpha_n": alpha.n})
        bet_parts["blend_close"].append(simulate_bets(test, blended, dist, "mkt_margin", cfg))
        bet_parts["blend_early"].append(simulate_bets(test, blended, dist, "early_home_spread", cfg))
        cal_table, ll = calibration(test, blended, dist)
        cal_parts.append(cal_table.assign(season=s))
        if not np.isnan(ll):
            log_losses.append((ll, int(cal_table.n.sum())))

        for combo in all_combos(sources):
            cols = list(combo)
            fc_train = combine(train[cols], {c: 1.0 for c in cols})
            fc_test = combine(test[cols], {c: 1.0 for c in cols})
            a = fit_alpha(fc_train.values, train.mkt_margin.values, train.home_margin.values)
            if a.n < 30:
                a = AlphaFit(cfg.alpha_start, 1 - cfg.alpha_start, np.nan, np.nan, a.n)
            mc = np.where(fc_test.isna(), np.nan, a.blend(test.mkt_margin.values, fc_test.values))
            bets = simulate_bets(test, mc, dist, "mkt_margin", cfg)
            combo_rows.append({"combo": "+".join(combo), "season": s, "alpha": a.alpha,
                               **{f"F_{k}": v for k, v in error_metrics(fc_test, test.home_margin).items()},
                               **{f"M_{k}": v for k, v in error_metrics(pd.Series(mc, index=test.index),
                                                                        test.home_margin).items()},
                               **ats_summary(bets)})

    tested = pd.concat(test_frames)
    result.frame = tested
    common = tested.dropna(subset=sources) if sources else tested
    for name, col in [("market_close", "mkt_margin"), ("blend", "M"), ("combined_F", "F"), *[(s, s) for s in sources]]:
        err_rows.append({"forecast": name, "sample": "full", **error_metrics(tested[col], tested.home_margin)})
        err_rows.append({"forecast": name, "sample": "common", **error_metrics(common[col], common.home_margin)})
    result.source_errors = pd.DataFrame(err_rows)
    result.combos = pd.DataFrame(combo_rows)
    result.bets = {k: pd.concat(v, ignore_index=True) if v else pd.DataFrame() for k, v in bet_parts.items()}
    result.calibration = pd.concat(cal_parts, ignore_index=True) if cal_parts else pd.DataFrame()
    if log_losses:
        result.log_loss = sum(ll * n for ll, n in log_losses) / sum(n for _, n in log_losses)
    if dist_df.season.nunique() > 3:
        result.dist_comparison = dist_select.summarize(dist_select.walk_forward(dist_df))
    return result


# ---------------------------------------------------------------- report


def reality_check(n_bets: int) -> str:
    se = 0.5 / math.sqrt(n_bets) if n_bets else float("nan")
    return (
        "### Reality check\n\n"
        f"- Break-even at -110 is {BREAK_EVEN_110:.2%}.\n"
        f"- Standard error of a win rate over N bets is about 0.5/sqrt(N): with N = {n_bets}, "
        f"that is {se:.1%} (about 2.2 points at N = 500). A 54% backtest on a few hundred bets "
        "is not distinguishable from break-even.\n"
        "- CLV is the faster, more reliable signal.\n"
        "- These results are vs the **retail consensus close** (median of CFBD providers), not a sharp close.\n"
    )


def _md(df: pd.DataFrame, floatfmt: str = ".3f") -> str:
    if df is None or df.empty:
        return "_none_\n"
    return df.to_markdown(index=False, floatfmt=floatfmt) + "\n"


def write_report(result: BacktestResult, path: Path | None = None) -> Path:
    path = path or config.REPORTS_DIR / f"backtest_{utcnow():%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    f, cfg = result.frame, result.cfg
    parts = [f"# Backtest {utcnow():%Y-%m-%d}\n",
             f"Seasons evaluated (walk-forward): {sorted(f.season.unique().tolist()) if len(f) else []}. "
             f"Sources: {', '.join(result.sources) or 'none'}. FBS-only: {cfg.fbs_only}; "
             f"postseason: {cfg.include_postseason}; weeks < {cfg.exclude_weeks_before} dropped. "
             f"Min EV {cfg.min_ev:+.0%} at {cfg.american:+.0f}.\n"]
    if len(result.dist_comparison):
        parts += ["## Margin distribution (walk-forward log-likelihood)\n", _md(result.dist_comparison.reset_index())]
    parts += ["## Per-season fitted parameters\n",
              _md(pd.DataFrame([{k: v for k, v in p.items() if k != "dist_params"} | {"weights": str(p["weights"])}
                                for p in result.season_params])),
              "## Forecast error vs actual margin\n", _md(result.source_errors)]
    if len(result.combos):
        pooled = result.combos.groupby("combo").agg(
            F_rmse=("F_rmse", "mean"), M_rmse=("M_rmse", "mean"), alpha=("alpha", "mean"), bets=("bets", "sum"),
            w=("w", "sum"), l=("l", "sum"), p=("p", "sum")).reset_index()
        pooled["win_pct"] = pooled.w / (pooled.w + pooled.l)
        parts += ["## Source combinations (equal weights, alpha fit per season, bets vs close at -110)\n", _md(pooled)]
    for name, bets in result.bets.items():
        s = ats_summary(bets)
        parts += [f"## Simulated bets: {name}\n", _md(pd.DataFrame([s]))]
    parts += [f"## Calibration (home cover prob at the close, pushes excluded). Log loss: {result.log_loss:.4f}\n",
              _md(result.calibration.astype({"bin": str}) if len(result.calibration) else result.calibration)]
    n_bets = int(sum(len(b) for b in result.bets.values()))
    parts += [f"\n**Sample:** {len(f)} games evaluated; {n_bets} simulated bets across all bet sets.\n\n",
              reality_check(len(result.bets.get("blend_close", pd.DataFrame())))]
    path.write_text("\n".join(parts))
    return path
