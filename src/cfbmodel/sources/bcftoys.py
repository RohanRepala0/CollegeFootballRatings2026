"""BCF Toys (Brian Fremeau) FEI game projections, bcftoys.com/{YEAR}-gp (plan section 2.3).

One table per week, newest first, built from FEI "as of the previous week", so
the projections are point-in-time. Columns per the plan:
  Projected Win | Projected Loss | PW | PM | PF | PA | PT || Final | CL | CT || PE | SU | ATS | O/U

The page itself could not be fetched while this was written, so the HTML
walk is tolerant (any heading containing "Week N" followed by a table) and the
header names drive the column map. Verify on the first real page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from bs4 import BeautifulSoup

from .. import config
from ..conventions import sign_to_home, spread_to_home
from ..fetch import Fetcher
from ..teams import TeamResolver
from .base import GameIndex, LineRow, ParseResult, ProjectionRow

SOURCE = "bcftoys"
LINE_PROVIDER = "bcftoys_close"

HEADERS = {
    "projected win": "winner", "projected winner": "winner", "proj win": "winner",
    "projected loss": "loser", "projected loser": "loser", "proj loss": "loser",
    "pw": "pw", "pm": "pm", "pf": "pf", "pa": "pa", "pt": "pt",
    "final": "final", "cl": "cl", "ct": "ct", "pe": "pe", "su": "su", "ats": "ats", "o/u": "ou", "ou": "ou",
}
_WEEK = re.compile(r"\bweek\s+(\d+)\b", re.I)
_POST = re.compile(r"\b(bowl|postseason|playoff|championship)s?\b", re.I)


@dataclass
class BCFGame:
    season: int
    week: int | None
    postseason: bool
    winner: str
    loser: str
    pw: float | None
    pm: float | None
    pt: float | None
    final: str | None
    cl: float | None       # closing spread from the projected winner's side
    ct: float | None


def _num(text: str) -> float | None:
    t = text.strip().replace("%", "").replace("+", "")
    if t.lower() in ("pk", "pick"):
        return 0.0
    try:
        return float(t)
    except ValueError:
        return None


def parse_page(html: str, season: int) -> tuple[list[BCFGame], list[dict]]:
    soup = BeautifulSoup(html, "html.parser")
    games, issues = [], []
    for table in soup.find_all("table"):
        heading = table.find_previous(string=lambda s: s and (_WEEK.search(s) or _POST.search(s)))
        if heading is None:
            issues.append({"issue": "table_without_week_heading"})
            continue
        m = _WEEK.search(heading)
        week = int(m.group(1)) if m else None
        post = week is None and bool(_POST.search(heading))
        rows = table.find_all("tr")
        cols, start = None, 0
        for i, tr in enumerate(rows):
            cells = [c.get_text(" ", strip=True).lower() for c in tr.find_all(["th", "td"])]
            mapped = [HEADERS.get(c) for c in cells]
            if "winner" in mapped and "pm" in mapped:
                cols = {k: mapped.index(k) for k in set(mapped) if k}
                start = i + 1
                unknown = [c for c, k in zip(cells, mapped) if c and not k]
                if unknown:
                    issues.append({"week": week, "issue": "unrecognized_headers", "headers": unknown})
                break
        if cols is None:
            issues.append({"week": week, "issue": "no_header_row"})
            continue
        for tr in rows[start:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) <= max(cols.values()):
                continue

            def get(k):
                return cells[cols[k]] if k in cols else ""

            winner, loser = get("winner"), get("loser")
            if not winner or not loser:
                continue
            games.append(BCFGame(season, week, post, winner, loser, _prob(get("pw")), _num(get("pm")),
                                 _num(get("pt")), get("final") or None, _num(get("cl")), _num(get("ct"))))
    return games, issues


def _prob(text: str) -> float | None:
    v = _num(text)
    if v is None:
        return None
    return v / 100 if v > 1 else v


_FINAL = re.compile(r"^\s*([WL])\s+(\d+)\s*-\s*(\d+)\s*$", re.I)


def to_parse_result(bcf_games: list[BCFGame], games: GameIndex, resolver: TeamResolver, fetched_at: datetime,
                    published_at: dict[tuple[int | None, bool], datetime], inferred: bool) -> ParseResult:
    out = ParseResult()
    suffix = "|published_at=inferred" if inferred else ""
    for g in bcf_games:
        w, l = resolver.resolve(SOURCE, g.winner), resolver.resolve(SOURCE, g.loser)
        if w is None or l is None:
            continue
        game = games.find(g.season, w, l, week=g.week, season_type="postseason" if g.postseason else None)
        if game is None:
            out.issues.append({"week": g.week, "issue": "no_cfbd_game", "text": f"{g.winner} / {g.loser}"})
            continue
        game_id, winner_home = int(game.game_id), w == int(game.home_id)
        pub = published_at.get((g.week, g.postseason))
        if pub is None or g.pm is None:
            out.issues.append({"game_id": game_id, "issue": "no_published_at_or_margin"})
            continue
        fm = _FINAL.match(g.final or "")
        if fm and pd.notna(game.home_points):
            # "W 28-7" / "L 3-10": the first number is always the projected winner's score.
            wp, lp = int(fm.group(2)), int(fm.group(3))
            if (fm.group(1).upper() == "W") != (wp > lp):
                out.issues.append({"game_id": game_id, "issue": "final_inconsistent", "final": g.final})
            home_pts, away_pts = (wp, lp) if winner_home else (lp, wp)
            if (home_pts, away_pts) != (int(game.home_points), int(game.away_points)):
                out.issues.append({"game_id": game_id, "issue": "final_disagrees_with_cfbd", "final": g.final})
        out.projections.append(ProjectionRow(
            game_id, SOURCE, sign_to_home(g.pm, winner_home), True, pub, fetched_at,
            home_win_prob=None if g.pw is None else (g.pw if winner_home else 1 - g.pw),
            exp_total=g.pt, raw_ref=f"{g.season}-gp|week{g.week}{suffix}"))
        if g.cl is not None:
            out.lines.append(LineRow(game_id, LINE_PROVIDER, "close", spread_to_home(g.cl, winner_home), fetched_at,
                                     total=g.ct))
    return out


def fetch_page(fetcher: Fetcher, season: int, fresh: bool = False):
    url = config.sources()["bcftoys"]["url_template"].format(year=season)
    return fetcher.fetch(SOURCE, url, fresh=fresh, ext="html")


def published_times(games: GameIndex, season: int, bcf_games: list[BCFGame]) -> dict:
    out = {}
    for week, post in {(g.week, g.postseason) for g in bcf_games}:
        if week is not None:
            t = games.inferred_published_at(season, week, "postseason" if post else "regular")
            if t is None and not post:
                t = games.inferred_published_at(season, week, "postseason")
        else:
            gg = games.games[(games.games.season == season) & (games.games.season_type == "postseason")]
            t = (gg.start_date.min() - pd.Timedelta(hours=1)).to_pydatetime() if len(gg) else None
        if t is not None:
            out[(week, post)] = t
    return out
