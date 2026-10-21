"""Week folders: <season>/week-NN/ with the week's ratings and actionable bets.

    2026/README.md            season record and an index of weeks
    2026/week-07/README.md    the week's bets, then results once the games are final
    2026/week-07/bets.csv
    2026/week-07/ratings.md   every game: line, model line, win %, SP+/BCF, totals
    2026/week-07/ratings.csv

A week is written once. Its bets and ratings are then frozen: re-running only
fills in final scores and results, so a posted pick can never be changed after
the fact (and the git history shows when each week was committed). A bet written
after its game kicked off is marked `backfilled` (shown as "backtest"): those come from the
walk-forward record and are never presented as posted before kickoff.

Rules. A live week posts the record's headline rule (totals at the early-week
SP+-sheet O/U, `record.week_picks`) and any spread side that clears the EV bar at
your books' current numbers (`weekly.bet_card`). A backtest week posts the
record's signals for that week (`record.season_record`). All signals are 1 unit;
spreads at the book's price, totals at -110.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from . import config
from .backtest import BacktestConfig
from .conventions import american_to_decimal, bet_outcome
from .db import utcnow
from .record import _fmt_line, season_record, week_picks
from .totals import total_outcome

RULES = {
    ("total", "early"): "Totals, early number",
    ("total", "close"): "Totals, closing number",
    ("spread", "close"): "Spreads, closing number",
    ("spread", "early"): "Spreads, early number",
    ("spread", "book"): "Spreads, your book now",
}
BET_COLS = ["season", "week", "game_id", "kickoff", "game", "rule", "market", "side", "line", "price", "pick",
            "model", "ev", "now_book", "now_line", "now_ev", "posted_at", "status", "final", "result", "units"]
BOOK_NAMES = {"draftkings": "DraftKings", "fanduel": "FanDuel", "fanatics": "Fanatics"}


def week_dir(season: int, week: int, root: Path | None = None) -> Path:
    return (root or config.ROOT) / str(season) / f"week-{week:02d}"


def _et(ts) -> str:
    return pd.Timestamp(ts).tz_convert("America/New_York").strftime("%a %b %-d, %-I:%M %p ET")


def _game(r) -> str:
    return f"{r.away_team} {'vs' if r.neutral_site else 'at'} {r.home_team}"


# ---------------------------------------------------------------- building a week


def ratings(con: duckdb.DuckDBPyConnection, cfg: BacktestConfig, season: int, week: int, params: dict,
            dist) -> pd.DataFrame:
    """One row per game: your book's spread, the model's line and win probability, each source's
    projection, and totals (early O/U, current consensus, model total)."""
    from .blend import point_in_time_projections
    from .weekly import bet_card

    card = bet_card(con, season, week, params, dist)
    if card.empty:
        return pd.DataFrame()
    home = card[card.side == "home"].set_index("game_id")
    games = con.execute("SELECT * FROM games WHERE season = ? AND week = ? AND season_type = 'regular'",
                        [season, week]).df().set_index("game_id")
    proj = point_in_time_projections(con, games.reset_index(), log=False)
    margin = proj.pivot_table(index="game_id", columns="source", values="exp_margin", aggfunc="last")
    total = proj.pivot_table(index="game_id", columns="source", values="exp_total", aggfunc="last")
    tot = week_picks(con, cfg, season, week)
    tot = tot.set_index("game_id") if len(tot) else pd.DataFrame()
    rows = []
    for gid, c in home.iterrows():
        g = games.loc[gid]
        if g.home_division != "fbs" and g.away_division != "fbs":   # no projections: the model is the market
            continue
        t = tot.loc[gid] if gid in tot.index else None
        rows.append({
            "game_id": int(gid), "kickoff": pd.Timestamp(g.start_date).tz_convert("UTC"),
            "away_team": g.away_team, "home_team": g.home_team, "neutral_site": bool(g.neutral_site),
            "book": c.priced_at, "home_spread": c.line, "model_margin": c.model_margin,
            "home_win_prob": c.home_ml_prob,
            "sp_plus_margin": margin.loc[gid].get("sp_plus") if gid in margin.index else np.nan,
            "bcftoys_margin": margin.loc[gid].get("bcftoys") if gid in margin.index else np.nan,
            "early_total": t.early_total if t is not None else np.nan,
            "current_total": t.current_total if t is not None else np.nan,
            "sp_plus_total": total.loc[gid].get("sp_plus") if gid in total.index else np.nan,
            "bcftoys_total": total.loc[gid].get("bcftoys") if gid in total.index else np.nan,
            "model_total": t.model_total if t is not None else np.nan,
            "away_points": g.away_points, "home_points": g.home_points,
        })
    return pd.DataFrame(rows).sort_values(["kickoff", "game_id"]).reset_index(drop=True)


def live_bets(con: duckdb.DuckDBPyConnection, cfg: BacktestConfig, season: int, week: int, params: dict,
              dist) -> pd.DataFrame:
    """Bets a live week posts: totals at the early O/U (headline rule) and spread sides that clear
    the EV bar at your books' current numbers."""
    from .weekly import bet_card

    games = con.execute("SELECT game_id, start_date, home_team, away_team, neutral_site FROM games "
                        "WHERE season = ? AND week = ?", [season, week]).df().set_index("game_id")
    rows = []
    tot = week_picks(con, cfg, season, week)
    for r in (tot[tot.pick].itertuples() if len(tot) else []):
        g = games.loc[r.game_id]
        rows.append({"game_id": r.game_id, "kickoff": g.start_date, "game": _game(g), "market": "total",
                     "timing": "early", "side": r.side, "line": r.early_total, "price": -110.0,
                     "pick": f"{r.side.title()} {r.early_total:g}", "model": f"total {r.model_total:.1f}",
                     "ev": r.ev, "now_book": r.now_book, "now_line": r.now_line, "now_ev": r.now_ev})
    card = bet_card(con, season, week, params, dist)
    for r in (card[(card.stake_units > 0) & (card.status == "upcoming")].itertuples() if len(card) else []):
        g = games.loc[r.game_id]
        team = g.home_team if r.side == "home" else g.away_team
        rows.append({"game_id": r.game_id, "kickoff": g.start_date, "game": _game(g), "market": "spread",
                     "timing": "book", "side": r.side, "line": r.line, "price": float(r.price),
                     "pick": f"{team} {_fmt_line(r.line)} ({r.priced_at})", "model": f"margin {r.model_margin:+.1f}",
                     "ev": r.ev, "now_book": r.priced_at, "now_line": r.line, "now_ev": r.ev})
    return pd.DataFrame(rows)


def backtest_bets(rec: dict, week: int) -> pd.DataFrame:
    p = pd.DataFrame([x for x in rec["picks"] if x["week"] == week])
    if p.empty:
        return p
    return pd.DataFrame({"game_id": p.game_id, "kickoff": pd.to_datetime(p.start_date, utc=True), "game": p.game,
                         "market": p.market, "timing": p.timing, "side": p.side, "line": p.line, "price": -110.0,
                         "pick": p.pick, "model": "", "ev": p.ev})


def _grade(bets: pd.DataFrame, con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    if bets.empty:
        return bets
    scores = con.execute(f"""SELECT game_id, home_points, away_points, home_team, away_team FROM games
                             WHERE game_id IN ({','.join(str(int(g)) for g in bets.game_id)})""").df().set_index("game_id")
    out = bets.copy()
    finals, results, units = [], [], []
    for b in out.itertuples():
        s = scores.loc[b.game_id] if b.game_id in scores.index else None
        if s is None or pd.isna(s.home_points):
            finals.append(""); results.append(""); units.append(np.nan)
            continue
        if b.market == "total":
            res = total_outcome(int(s.home_points + s.away_points), b.side, b.line)
        else:
            res = bet_outcome(int(s.home_points - s.away_points), b.side, b.line)
        finals.append(f"{s.away_team} {int(s.away_points)}, {s.home_team} {int(s.home_points)}")
        results.append(res)
        units.append({"win": american_to_decimal(b.price) - 1, "push": 0.0, "loss": -1.0}[res])
    out["final"], out["result"], out["units"] = finals, results, units
    return out


# ---------------------------------------------------------------- writing


def _fmt(x, nd=1, signed=False):
    if x is None or (isinstance(x, float) and np.isnan(x)) or pd.isna(x):
        return "—"
    return f"{x:+.{nd}f}" if signed else f"{x:.{nd}f}"


def _side_line(r, margin) -> str:
    if pd.isna(margin):
        return "—"
    if abs(margin) < 0.05:
        return "even"
    return f"{r.home_team if margin > 0 else r.away_team} by {abs(margin):.1f}"


def _book_line(r) -> str:
    if pd.isna(r.home_spread):
        return "—"
    if r.home_spread == 0:
        return "PK"
    team = r.home_team if r.home_spread < 0 else r.away_team
    return f"{team} −{abs(r.home_spread):g}"


def _ratings_md(rt: pd.DataFrame, season: int, week: int, asof: datetime) -> str:
    lines = [f"# {season} · Week {week} ratings\n",
             f"Lines as of {_et(asof)}. Model = SP+ and BCF Toys FEI blended with the market (it leans about 94% on "
             "the market for spreads). Win % is the favorite's chance to win outright.\n",
             "| Kickoff | Game | Line | Model | Win % | SP+ | BCF | Early O/U | Now | Model total | Final |",
             "|---|---|---|---|---:|---|---|---:|---:|---:|---|"]
    for r in rt.itertuples():
        fav = max(r.home_win_prob, 1 - r.home_win_prob) if pd.notna(r.home_win_prob) else np.nan
        favteam = (r.home_team if r.home_win_prob >= 0.5 else r.away_team) if pd.notna(r.home_win_prob) else ""
        final = (f"{int(r.away_points)}–{int(r.home_points)}" if pd.notna(r.home_points) else "")
        lines.append(f"| {_et(r.kickoff)} | {r.away_team} {'vs' if r.neutral_site else 'at'} {r.home_team} | "
                     f"{_book_line(r)} | {_side_line(r, r.model_margin)} | "
                     f"{'—' if np.isnan(fav) else f'{fav:.0%} {favteam}'} | {_side_line(r, r.sp_plus_margin)} | "
                     f"{_side_line(r, r.bcftoys_margin)} | {_fmt(r.early_total)} | {_fmt(r.current_total)} | "
                     f"{_fmt(r.model_total)} | {final} |")
    return "\n".join(lines) + "\n"


def _record_line(b: pd.DataFrame) -> str:
    g = b[b.result.isin(["win", "loss", "push"])]
    if g.empty:
        return "no results yet"
    w, l, p = (g.result == "win").sum(), (g.result == "loss").sum(), (g.result == "push").sum()
    return f"{w}–{l}{f'–{p}' if p else ''}, {g.units.sum():+.2f} units"


def _week_readme(bets: pd.DataFrame, rt: pd.DataFrame, season: int, week: int) -> str:
    posted = pd.Timestamp(bets.posted_at.iloc[0]) if len(bets) else None
    parts = [f"# {season} · Week {week}\n"]
    if len(bets):
        n_bf = int((bets.status == "backfilled").sum())
        if n_bf == len(bets):
            parts.append(f"Backtest week: the model, fit on seasons before {season} only, applied to this week's "
                         "games.\n")
        elif n_bf:
            parts.append(f"Live: posted {_et(posted)}. {n_bf} of {len(bets)} signals are backtest (their games had "
                         "already kicked off).\n")
        else:
            parts.append(f"Live: posted {_et(posted)}, before every signal's kickoff. Signals are frozen once "
                         "posted; only results are added.\n")
        show_now = bets.now_ev.notna().any()
        parts += ["## Signals\n", "1 unit each. Totals at −110 at the early-week number from SP+'s sheet; spreads at "
                  "the listed book's price.\n"]
        if show_now:
            parts.append("**Now** is the best number at your books when this was posted (DraftKings is the only one "
                         "of them CFBD carries, at −110) and the same bet's EV there. The record grades every signal "
                         "at its listed number, but if Now is below +2%, the edge was already gone when this was posted.\n")
        parts += [f"| Kickoff | Game | Bet | Rule | Model | EV |{' Now |' if show_now else ''} Result |",
                  f"|---|---|---|---|---|---:|{'---|' if show_now else ''}---|"]
        for b in bets.sort_values("kickoff").itertuples():
            res = {"win": "✅ W", "loss": "❌ L", "push": "➖ P"}.get(b.result, "pending")
            res = f"{res} ({b.units:+.2f}) · {b.final}" if b.result else res
            model = b.model if isinstance(b.model, str) and b.model else "—"
            now = ""
            if show_now:
                now = "—" if pd.isna(b.now_ev) else (
                    f"{BOOK_NAMES.get(str(b.now_book).split(' ')[0], b.now_book)} {b.now_line:g} · {b.now_ev:+.1%}")
                now = f" {now} |"
            parts.append(f"| {_et(b.kickoff)} | {b.game} | **{b.pick}** | {b.rule} | {model} | "
                         f"{'—' if pd.isna(b.ev) else f'{b.ev:+.1%}'} |{now} {res} |")
        parts.append(f"\n**Week:** {_record_line(bets)}\n")
    else:
        parts.append("No signals this week: nothing cleared the +2% EV bar.\n")
    parts.append(f"## Ratings\n\nEvery game's line, model line and win probability: [ratings.md](ratings.md) · "
                 f"[ratings.csv](ratings.csv) ({len(rt)} games).\n")
    return "\n".join(parts)


def publish_week(con: duckdb.DuckDBPyConnection, cfg: BacktestConfig, season: int, week: int,
                 root: Path | None = None, now: datetime | None = None, rec: dict | None = None) -> Path:
    from .weekly import load_production

    out = week_dir(season, week, root)
    out.mkdir(parents=True, exist_ok=True)
    bets_path, rt_path = out / "bets.csv", out / "ratings.csv"
    now = pd.Timestamp(now or utcnow()).tz_convert("UTC")
    if bets_path.exists() and rt_path.exists():           # frozen: refresh finals and results only
        bets = pd.read_csv(bets_path, keep_default_na=False, na_values=[""])
        bets["kickoff"] = pd.to_datetime(bets.kickoff, utc=True)
        rt = pd.read_csv(rt_path)
        rt["kickoff"] = pd.to_datetime(rt.kickoff, utc=True)
        fresh = con.execute(f"SELECT game_id, home_points, away_points FROM games WHERE season = {int(season)} "
                            f"AND week = {int(week)}").df().set_index("game_id")
        rt["home_points"] = rt.game_id.map(fresh.home_points)
        rt["away_points"] = rt.game_id.map(fresh.away_points)
        asof = pd.Timestamp(bets.posted_at.iloc[0]) if len(bets) else now
    else:
        params, dist = load_production()
        rt = ratings(con, cfg, season, week, params, dist)
        if len(rt) and (rt.kickoff > now).any():           # live week: post bets on games not yet started
            raw = live_bets(con, cfg, season, week, params, dist)
            if len(raw):
                raw = raw[pd.to_datetime(raw.kickoff, utc=True) > now]
        else:                                               # the week is over: backtest signals from the record
            rec = rec or season_record(con, cfg, season)
            raw = backtest_bets(rec, week)
        bets = raw.copy()
        if len(bets):
            bets["kickoff"] = pd.to_datetime(bets.kickoff, utc=True)
            bets["season"], bets["week"] = season, week
            bets["rule"] = [RULES[(m, t)] for m, t in zip(bets.market, bets.timing)]
            bets["posted_at"] = now.isoformat()
            bets["status"] = np.where(bets.kickoff > now, "posted", "backfilled")
        else:
            bets = pd.DataFrame(columns=BET_COLS)
        asof = now
    bets = _grade(bets, con) if len(bets) else bets
    bets = bets.reindex(columns=BET_COLS)
    bets.to_csv(bets_path, index=False)
    rt.to_csv(rt_path, index=False)
    (out / "ratings.md").write_text(_ratings_md(rt, season, week, asof))
    (out / "README.md").write_text(_week_readme(bets, rt, season, week))
    return out


def publish_season_index(con: duckdb.DuckDBPyConnection, season: int, root: Path | None = None,
                         cfg: BacktestConfig | None = None) -> Path:
    from .charts import backtest_chart, picture, season_charts
    from .record import real_bets

    sdir = (root or config.ROOT) / str(season)
    weeks = sorted(sdir.glob("week-*/bets.csv"))
    frames = []
    for p in weeks:
        b = pd.read_csv(p, keep_default_na=False, na_values=[""])
        frames.append(b.assign(folder=p.parent.name))
    allb = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=BET_COLS + ["folder"])
    posted, backfilled = allb[allb.status == "posted"], allb[allb.status == "backfilled"]
    real = real_bets(con, season)
    real_line = "none logged yet" if real.empty else _record_line(real.rename(columns={"profit": "units"}))
    charts = season_charts(allb, sdir / "charts", season) if len(allb) else []
    parts = [f"# {season} CFB Model\n",
             "Weekly ratings and signals from SP+ and BCF Toys FEI projections blended with the betting market. Each "
             "week folder holds that week's signals (README.md, bets.csv) and the model's ratings for every game "
             "(ratings.md, ratings.csv).\n",
             "## Record\n", "| | Signals | Record |", "|---|---:|---|",
             f"| Live, posted before kickoff | {len(posted)} | {_record_line(posted)} |",
             f"| Backtest, out-of-sample | {len(backfilled)} | {_record_line(backfilled)} |",
             f"| Real bets logged (`cfb log-bet`) | {len(real)} | {real_line} |\n"]
    parts += [picture(f"charts/{c}", f"{season} {c} units") for c in charts]
    parts += ["Live signals are frozen when posted and graded at the price shown. Backtest weeks apply the same rules "
              f"with a model fit on seasons before {season} only, so it never saw those games. Closing-number signals "
              "are graded at the median line across books.\n",
              "## Weeks\n", "| Week | Signals | Record | Status |", "|---|---:|---|---|"]
    for p in weeks:
        b = allb[allb.folder == p.parent.name]
        status = ("backtest" if len(b) and (b.status == "backfilled").all()
                  else "live" if len(b) else "no signals")
        wk = int(p.parent.name.split("-")[1])
        parts.append(f"| [Week {wk}]({p.parent.name}/) | {len(b)} | {_record_line(b) if len(b) else '—'} | {status} |")
    if cfg is not None:
        parts += _longer_backtest(con, cfg, season, sdir, backtest_chart, picture)
    parts += ["\n## Rules\n",
              "- **Totals, early number** (headline): bet the over or under at the O/U printed in SP+'s weekly sheet "
              "when the expected value at −110 is at least +2%.",
              "- **Spreads, your book now**: bet a side at FanDuel, DraftKings or Fanatics when it clears +2% EV. "
              "The spread market already prices SP+ and FEI, so these are rare.",
              "- Backtest weeks also include the closing-number rules.",
              "- How it works, with diagrams: [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md). Backtests and caveats: "
              "the repository README and `reports/`.\n"]
    path = sdir / "README.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts))
    return path


def _longer_backtest(con, cfg: BacktestConfig, season: int, sdir: Path, backtest_chart, picture) -> list[str]:
    """The headline rule over the seasons before `season`, walk-forward (chart drawn once, then reused)."""
    from copy import copy

    from . import totals

    c = copy(cfg)
    c.seasons = [s for s in cfg.seasons if s < season]
    tested = [s for s in c.seasons[c.min_train_seasons:]]
    if not tested:
        return []
    res = totals.run(con, c, only=tested)
    b = res.bets.get("blend_early", pd.DataFrame())
    if b.empty:
        return []
    w, l, p = (b.result == "win").sum(), (b.result == "loss").sum(), (b.result == "push").sum()
    span = f"{b.season.min()}–{b.season.max()}"
    title = f"Totals at the early number, {span} backtest: cumulative units"
    backtest_chart(b, sdir / "charts", title)
    return [f"\n## Backtest, {span}\n",
            picture("charts/backtest", f"Totals at the early number, {span}"),
            f"The headline rule over {span}, each season scored by a model fit only on earlier seasons: {len(b)} "
            f"signals, {w}–{l}–{p} ({w / (w + l):.1%}), {b.profit.sum():+.1f} units, ROI {b.profit.mean():+.1%}. "
            f"The closing total moved toward the signal by {b.clv_points.mean():+.2f} points on average "
            f"({b.clv_net.mean():+.2f} after the usual early-to-close drift).\n"]
