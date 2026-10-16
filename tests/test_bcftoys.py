from datetime import datetime, timezone

import pandas as pd

from cfbmodel import db
from cfbmodel.sources import bcftoys
from cfbmodel.sources.base import GameIndex
from cfbmodel.teams import TeamResolver

# Column layout per the plan (section 2.3); values made up for the test.
HTML = """<html><body>
<h3>Week 7 Game Projections</h3>
<table>
<tr><th>Projected Win</th><th>Projected Loss</th><th>PW</th><th>PM</th><th>PF</th><th>PA</th><th>PT</th>
<th>Final</th><th>CL</th><th>CT</th><th>PE</th><th>SU</th><th>ATS</th><th>O/U</th></tr>
<tr><td>Auburn</td><td>Georgia</td><td>56%</td><td>2.4</td><td>24</td><td>21</td><td>45</td>
<td>L 21-31</td><td>+3.5</td><td>48.5</td><td>12.4</td><td>0</td><td>0</td><td>1</td></tr>
</table>
<h3>Week 6 Game Projections</h3>
<table><tr><th>Projected Win</th><th>Projected Loss</th><th>PW</th><th>PM</th></tr>
<tr><td>Georgia</td><td>Kentucky</td><td>0.9</td><td>20</td></tr></table>
</body></html>"""


def test_parse_and_join(con):
    db.upsert_df(con, "teams", pd.DataFrame([
        {"team_id": i, "school": n, "abbreviation": None, "conference": None, "classification": "fbs",
         "alternate_names": []} for i, n in [(61, "Georgia"), (2, "Auburn"), (96, "Kentucky")]]), "team_id")
    parsed, issues = bcftoys.parse_page(HTML, 2025)
    assert [(g.week, g.winner, g.pm) for g in parsed] == [(7, "Auburn", 2.4), (6, "Georgia", 20.0)]
    games = GameIndex(pd.DataFrame([{"game_id": 1, "season": 2025, "week": 7, "season_type": "regular",
                                     "start_date": "2025-10-11T19:30:00Z", "home_id": 61, "away_id": 2,
                                     "neutral_site": False, "home_points": 31, "away_points": 21}]))
    pub = bcftoys.published_times(games, 2025, parsed)
    r = bcftoys.to_parse_result(parsed, games, TeamResolver.from_db(con),
                                datetime(2026, 1, 1, tzinfo=timezone.utc), pub, True)
    p = r.projections[0]
    assert p.exp_margin == -2.4                       # Auburn (away) projected by 2.4
    assert p.home_win_prob == 1 - 0.56
    assert r.lines[0].home_spread == -3.5             # Auburn +3.5 -> Georgia -3.5
    assert [i["issue"] for i in r.issues] == ["no_cfbd_game"]   # week 6 game not in CFBD fixture
