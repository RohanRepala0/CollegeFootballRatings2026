"""PNG charts for the season folder, drawn twice (GitHub's light and dark themes).

Colors are the validated blue / red pair (README charts embed both variants with
<picture>, so each reader sees the one for their theme). Text stays in ink colors,
never the series color; one series per chart, so no legend box is needed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

THEMES = {
    "light": {"bg": "#ffffff", "ink": "#1f2328", "muted": "#59636e", "grid": "#d8dee4", "zero": "#9aa4af",
              "line": "#2a78d6", "pos": "#2a78d6", "neg": "#e34948", "shade": "#f2f5f8"},
    "dark": {"bg": "#0d1117", "ink": "#e6edf3", "muted": "#9198a1", "grid": "#262c36", "zero": "#4b5563",
             "line": "#3987e5", "pos": "#3987e5", "neg": "#e66767", "shade": "#151b23"},
}


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    return plt


def _axes(plt, t, figsize=(9, 3.6)):
    fig, ax = plt.subplots(figsize=figsize, dpi=150)
    fig.patch.set_facecolor(t["bg"])
    ax.set_facecolor(t["bg"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["grid"])
    ax.tick_params(colors=t["muted"], length=0)
    ax.yaxis.grid(True, color=t["grid"], linewidth=1)
    ax.set_axisbelow(True)
    return fig, ax


def _signed(v: float) -> str:
    return f"{'+' if v > 0 else '−' if v < 0 else ''}{abs(v):g}"


def _save(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.15)


def season_charts(signals: pd.DataFrame, out: Path, season: int) -> list[str]:
    """Cumulative units by week, and units per week, from the season's graded signals.
    `signals` needs week, status ('posted' = live, else backtest), result, units."""
    plt = _plt()
    graded = signals[signals.result.isin(["win", "loss", "push"])]
    weeks = sorted(signals.week.unique().astype(int))
    if not weeks:
        return []
    live_weeks = sorted(signals[signals.status == "posted"].week.unique().astype(int))
    by_week = graded.groupby("week").units.sum().reindex(weeks)
    last_graded = int(graded.week.max()) if len(graded) else 0
    xs = [0] + [w for w in weeks if w <= last_graded]
    cum = [0.0] + list(by_week.loc[[w for w in xs[1:]]].fillna(0).cumsum())
    names = []
    for theme, t in THEMES.items():
        # cumulative units
        fig, ax = _axes(plt, t)
        if live_weeks:
            ax.axvspan(min(live_weeks) - 0.5, max(weeks) + 0.5, color=t["shade"], zorder=0, linewidth=0)
            ax.text(min(live_weeks) - 0.4, 1.02, "Live", transform=ax.get_xaxis_transform(), color=t["muted"],
                    fontsize=10, va="bottom")
            pending = [w for w in live_weeks if w > last_graded]
            if pending:
                ax.text(np.mean(pending), 0.06, "results\npending", transform=ax.get_xaxis_transform(),
                        color=t["muted"], fontsize=9, ha="center", va="bottom")
        ax.text(0, 1.02, "Backtest" if live_weeks and min(live_weeks) > 1 else "", transform=ax.get_xaxis_transform(),
                color=t["muted"], fontsize=10, va="bottom")
        ax.axhline(0, color=t["zero"], linewidth=1, zorder=1)
        ax.plot(xs, cum, color=t["line"], linewidth=2, solid_capstyle="round", solid_joinstyle="round", zorder=3)
        ax.plot(xs[-1], cum[-1], "o", markersize=8, color=t["line"], markeredgecolor=t["bg"], markeredgewidth=2,
                zorder=4)
        ax.annotate(f"{cum[-1]:+.2f} units", (xs[-1], cum[-1]), xytext=(8, 0), textcoords="offset points",
                    color=t["ink"], fontsize=11, fontweight="bold", va="center")
        ax.set_xticks(range(0, max(weeks) + 1))
        ax.set_xticklabels(["Start"] + [f"Wk {w}" for w in range(1, max(weeks) + 1)])
        ax.set_xlim(-0.3, max(weeks) + 0.9)
        lo, hi = min(cum + [0]), max(cum + [0])
        ax.set_ylim(np.floor(lo) - 1, np.ceil(hi) + 1.5)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: _signed(round(v, 1))))
        ax.set_title(f"{season} signals: cumulative units (1 unit per signal)", color=t["ink"], loc="left",
                     fontsize=12, pad=18)
        _save(fig, out / f"cumulative-{theme}.png")
        plt.close(fig)
        # units per week
        fig, ax = _axes(plt, t, figsize=(9, 3.0))
        vals = by_week.loc[[w for w in weeks if w <= last_graded]]
        ax.bar(vals.index, vals.values, width=0.55,
               color=[t["pos"] if v >= 0 else t["neg"] for v in vals.values], zorder=3)
        ax.axhline(0, color=t["zero"], linewidth=1, zorder=2)
        for w in weeks:
            g = graded[graded.week == w]
            if len(g):
                wl = f"{(g.result == 'win').sum()}–{(g.result == 'loss').sum()}"
                v = vals.get(w, 0)
                ax.annotate(wl, (w, v), xytext=(0, 4 if v >= 0 else -4), textcoords="offset points",
                            ha="center", va="bottom" if v >= 0 else "top", color=t["muted"], fontsize=9)
        for w in weeks:
            if w > last_graded:
                ax.text(w, 0, "pending", ha="center", va="bottom", color=t["muted"], fontsize=9)
        lo, hi = min([0, *vals.values]), max([0, *vals.values])
        pad = 0.18 * (hi - lo or 1)
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_xticks(weeks)
        ax.set_xticklabels([f"Wk {w}" for w in weeks])
        ax.set_xlim(min(weeks) - 0.6, max(weeks) + 0.6)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: _signed(round(v, 1))))
        ax.set_title(f"{season} units by week (record above or below each bar)", color=t["ink"], loc="left",
                     fontsize=12, pad=10)
        _save(fig, out / f"weekly-{theme}.png")
        plt.close(fig)
    names += ["cumulative", "weekly"]
    return names


def backtest_chart(bets: pd.DataFrame, out: Path, title: str) -> str:
    """Cumulative units across the multi-season backtest, one step per bet, seasons marked."""
    plt = _plt()
    b = bets.sort_values(["season", "week", "game_id"]).reset_index(drop=True)
    cum = b.profit.cumsum().values
    x = np.arange(1, len(b) + 1)
    for theme, t in THEMES.items():
        fig, ax = _axes(plt, t)
        ax.axhline(0, color=t["zero"], linewidth=1, zorder=1)
        starts = b.groupby("season").head(1).index.values
        for i, s in zip(starts, b.season.iloc[starts]):
            if i:
                ax.axvline(i + 0.5, color=t["grid"], linewidth=1, zorder=1)
            ax.text(i + 3, 1.02, str(s), transform=ax.get_xaxis_transform(), color=t["muted"], fontsize=10,
                    va="bottom")
        ax.plot(x, cum, color=t["line"], linewidth=2, solid_joinstyle="round", zorder=3)
        ax.plot(x[-1], cum[-1], "o", markersize=8, color=t["line"], markeredgecolor=t["bg"], markeredgewidth=2,
                zorder=4)
        ax.annotate(f"{cum[-1]:+.1f} units", (x[-1], cum[-1]), xytext=(8, 0), textcoords="offset points",
                    color=t["ink"], fontsize=11, fontweight="bold", va="center")
        ax.set_xlim(0, len(b) * 1.1)
        ax.set_xlabel("signals, in order", color=t["muted"])
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: _signed(round(v, 1))))
        ax.set_title(title, color=t["ink"], loc="left", fontsize=12, pad=18)
        _save(fig, out / f"backtest-{theme}.png")
        plt.close(fig)
    return "backtest"


def picture(rel: str, alt: str) -> str:
    """GitHub-markdown image that follows the reader's light/dark theme."""
    return (f'<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="{rel}-dark.png">\n'
            f'  <img alt="{alt}" src="{rel}-light.png">\n</picture>\n')
