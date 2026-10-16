"""`cfb` command line (Typer)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import typer

from . import config, db
from .backtest import BacktestConfig

app = typer.Typer(no_args_is_help=True, help="College football spread model.")
line_app = typer.Typer(help="Manual line entry.")
app.add_typer(line_app, name="line")


def _con():
    con = db.connect()
    db.init_db(con)
    return con


def _resolver(con):
    from .teams import TeamResolver

    return TeamResolver.from_db(con)


@app.command("init-db")
def init_db():
    """Create the DuckDB schema."""
    tables = db.init_db(db.connect())
    typer.echo(f"{config.DB_PATH}: {', '.join(tables)}")


@app.command()
def status():
    """Row counts and this month's CFBD call count."""
    from .fetch import calls_this_month

    con = _con()
    for t in ("games", "teams", "lines", "source_projections", "source_ratings", "bets", "exclusions"):
        typer.echo(f"{t:20s} {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:>8}")
    budget = config.sources()["cfbd"].get("monthly_call_budget", 1000)
    typer.echo(f"CFBD calls this month: {calls_this_month(con)} / {budget}")


@app.command("backfill-cfbd")
def backfill_cfbd(seasons: list[int] = typer.Option(None, "--season", help="Repeatable; default from config."),
                  fresh: bool = typer.Option(False, help="Take new snapshots instead of using the cache.")):
    """Phase 1: teams, games, lines (and pregame Elo) from CFBD."""
    from .fetch import Fetcher
    from .sources import cfbd

    con = _con()
    client = cfbd.CFBDClient(Fetcher(con), config.require(config.secrets().cfbd_api_key, "CFBD_API_KEY"))
    typer.echo(f"teams: {cfbd.ingest_teams(con, client, fresh=fresh)}")
    issues = []
    for s in seasons or config.sources()["cfbd"]["seasons"]:
        r = cfbd.ingest_season(con, client, s, fresh=fresh)
        issues += r.pop("issues")
        typer.echo(r)
    if issues:
        path = config.REPORTS_DIR / "cfbd_line_issues.csv"
        pd.DataFrame(issues).to_csv(path, index=False)
        typer.echo(f"{len(issues)} line sign disagreements written to {path}")


@app.command("validate-sp")
def validate_sp(season: list[int] = typer.Option(..., help="Repeatable."),
                xlsx: list[Path] = typer.Option(None, help="Workbook per --season, same order. Omit to read the "
                                                             "live sheets (needs GOOGLE_API_KEY or docs.google.com).")):
    """SP+ acceptance test from the sheets alone (no CFBD needed)."""
    from .fetch import Fetcher
    from .reports import sp_validation_report
    from .sources.sp_plus import fetch_season, read_workbook

    if xlsx:
        seasons = [read_workbook(p, s) for p, s in zip(xlsx, season)]
    else:
        fetcher = Fetcher(_con())
        seasons = [fetch_season(fetcher, s, config.secrets().google_api_key)[0] for s in season]
    typer.echo(f"wrote {sp_validation_report(seasons)}")


@app.command("validate-sp-db")
def validate_sp_db(xlsx: Path = typer.Option(...), season: int = typer.Option(...)):
    """SP+ acceptance test graded with CFBD scores (after backfill-cfbd and ingest-sp)."""
    from .reports import sp_validation_from_db
    from .sources.sp_plus import read_workbook

    rep = sp_validation_from_db(_con(), season, read_workbook(xlsx, season).performance)
    path = config.REPORTS_DIR / f"sp_plus_validation_db_{season}.md"
    path.write_text(f"# SP+ acceptance test, {season}, CFBD scores\n\nWeeks passing: {int(rep.ok.sum())} / {len(rep)}\n\n"
                    + rep.to_markdown(index=False) + "\n")
    typer.echo(rep.to_string(index=False))
    typer.echo(f"wrote {path}")


@app.command("ingest-sp")
def ingest_sp(season: int = typer.Option(...), xlsx: Optional[Path] = typer.Option(None, help="Use a downloaded workbook."),
              live_week: Optional[int] = typer.Option(None, help="Snapshot this week's tab fresh.")):
    """SP+ projections, early lines and ratings into the DB (needs CFBD games + teams)."""
    from .fetch import Fetcher
    from .sources import sp_plus
    from .sources.base import GameIndex
    from .sources.store import store

    con = _con()
    games, resolver = GameIndex.from_db(con, [season]), _resolver(con)
    if xlsx is not None:
        sheet = sp_plus.read_workbook(xlsx, season)
        fetched_at, live_pub = db.utcnow(), {}
    else:
        sheet, live_pub = sp_plus.fetch_season(Fetcher(con), season, config.secrets().google_api_key, live_week)
        fetched_at = db.utcnow()
    published = sp_plus.inferred_published_at(games, season, sheet.games)
    archived = [g for g in sheet.games if (g.week, g.season_type) not in live_pub]
    result = sp_plus.to_parse_result(archived, sheet.ratings, games, resolver, fetched_at, published, True)
    if live_pub:
        live = [g for g in sheet.games if (g.week, g.season_type) in live_pub]
        result.extend(sp_plus.to_parse_result(live, [], games, resolver, fetched_at, live_pub, False))
    resolver.raise_if_unmatched(config.REPORTS_DIR / f"unmatched_sp_plus_{season}.csv")
    typer.echo(store(con, result))
    _write_issues(sheet.issues + result.issues, f"sp_plus_issues_{season}.csv")


@app.command("ingest-bcf")
def ingest_bcf(season: int = typer.Option(...), fresh: bool = False):
    """BCF Toys FEI game projections and closing lines (needs CFBD games + teams)."""
    from .fetch import Fetcher
    from .sources import bcftoys
    from .sources.base import GameIndex
    from .sources.store import store

    con = _con()
    games, resolver = GameIndex.from_db(con, [season]), _resolver(con)
    snap = bcftoys.fetch_page(Fetcher(con), season, fresh=fresh)
    parsed, issues = bcftoys.parse_page(snap.body.decode("utf-8", "replace"), season)
    pub = bcftoys.published_times(games, season, parsed)
    result = bcftoys.to_parse_result(parsed, games, resolver, snap.fetched_at, pub, inferred=True)
    resolver.raise_if_unmatched(config.REPORTS_DIR / f"unmatched_bcftoys_{season}.csv")
    typer.echo(store(con, result))
    _write_issues(issues + result.issues, f"bcftoys_issues_{season}.csv")


def _write_issues(issues: list[dict], name: str) -> None:
    if issues:
        path = config.REPORTS_DIR / name
        pd.DataFrame(issues).to_csv(path, index=False)
        typer.echo(f"{len(issues)} issues -> {path}")


@app.command("fit-distribution")
def fit_distribution():
    """Phase 2: compare margin distributions out of sample and write the report."""
    from .reports import distribution_report

    typer.echo(f"wrote {distribution_report(_con(), BacktestConfig.from_settings())}")


@app.command()
def backtest(distribution: str = typer.Option("best", help="normal | normal_linear_sigma | sides_harvill_approx | nfelo_mixture | best")):
    """Phase 5: walk-forward backtest and report."""
    from . import backtest as bt

    cfg = BacktestConfig.from_settings()
    cfg.distribution = distribution
    result = bt.run(_con(), cfg)
    typer.echo(f"wrote {bt.write_report(result)}")


@app.command()
def fit(distribution: str = "best"):
    """Fit production parameters (distribution, weights, alpha) on all seasons."""
    from .weekly import fit_production

    typer.echo(json.dumps(fit_production(_con(), BacktestConfig.from_settings(), distribution), indent=2, default=float))


@app.command()
def week(season: int = typer.Option(...), week: int = typer.Option(...),
         book: Optional[str] = typer.Option(None, help="Your NY book, as entered with `cfb line set --book`."),
         all_rows: bool = typer.Option(False, "--all", help="Show every side, not only bets that pass the EV bar.")):
    """Phase 6: print and save the bet card for a week."""
    from .weekly import bet_card, load_production, write_card

    params, dist = load_production()
    card = bet_card(_con(), season, week, params, dist, book)
    if card.empty:
        typer.echo("no games with market lines for that week")
        raise typer.Exit(1)
    path = write_card(card, season, week)
    show = card if all_rows else card[card.stake_units > 0]
    cols = ["game", "side", "line", "price", "model_margin", "mkt_margin", "p_win", "p_push", "ev", "fair_odds",
            "stake_units"]
    typer.echo(show[cols].to_string(index=False) if len(show) else "no bets pass the EV threshold")
    typer.echo(f"\nfull card: {path}")


@line_app.command("set")
def line_set(game_id: int, book: str = typer.Option(...), spread: float = typer.Option(..., help="Home spread."),
             price_home: Optional[float] = None, price_away: Optional[float] = None,
             snapshot: str = typer.Option("bet", help="bet | close")):
    """Record a line by hand (e.g. from a public odds screen)."""
    from .market import set_line

    set_line(_con(), game_id, book, snapshot, spread, price_home, price_away)
    typer.echo("saved")


@app.command("log-bet")
def log_bet(game_id: int, side: str = typer.Option(..., help="home | away"),
            line: float = typer.Option(..., help="Spread for the side you bet."),
            price: float = typer.Option(-110), stake: float = typer.Option(..., help="Units."),
            book: Optional[str] = None, model_margin: Optional[float] = None):
    """Phase 7: record a bet you actually placed."""
    from .tracking import log_bet as _log
    from .weekly import load_production

    try:
        _, dist = load_production()
    except RuntimeError:
        dist = None
    typer.echo(f"bet_id {_log(_con(), game_id, side, line, price, stake, book, model_margin, dist)}")


@app.command()
def settle():
    """Fill closing lines, CLV and results for open bets."""
    from .tracking import settle as _settle, summary
    from .weekly import load_production

    con = _con()
    _, dist = load_production()
    typer.echo(f"settled {_settle(con, dist)}")
    typer.echo(json.dumps(summary(con), indent=2, default=float))


if __name__ == "__main__":
    app()
