# 2026 Week 8: are the signals bettable? (checked Wed Oct 21)

The model posted five totals signals for Week 8 at SP+'s early-week numbers (`2026/week-08/`). Before treating
any of them as a bet, each one went through three checks:

- **Data audit:** every number was traced back to the raw SP+ workbook, the BCF Toys page and the CFBD rows.
- **News sweep:** one search per game for injuries, weather and why the line moved.
- **Skeptic:** a final pass that defaults to "pass" unless the bet survives every objection.

**Bottom line: none is a bet at today's numbers.**

## Why the early-number signals are not the same as bets

The totals backtest (`reports/backtest_totals_2026-10-21.md`) shows the edge only at the early number:

| Rule, 2022–2025 | Record | ROI |
|---|---|---:|
| Over/under at the early SP+ sheet number | 178–154–5 | +2.3% |
| Same model at the closing number | 44–45–1 | −5.6% |

The edge is getting in before the market moves toward SP+ and FEI. Once the line has moved, it is gone.

## The five signals

DraftKings totals come from CFBD (fetched Oct 21, 19:22 UTC), priced at −110 because CFBD has no prices.

| Game | Signal | DraftKings now | EV now | Verdict | Why |
|---|---|---:|---:|---|---|
| Massachusetts at UConn | Over 47.5 | 49.5 | −2.7% | Pass | The line moved 2 points toward the model, so the edge is taken. UMass QB Pop Watson (ankle) is a game-time decision and both backups are hurt. |
| Air Force at Wyoming | Over 43.5 | 45.5 | −2.7% | Lean, 43.5 or better only | The move was public money (about 94% of handle on the over), not news. A Friday cold front with 20+ mph wind and rain is forecast for Laramie. Only consider it if the number returns to 43.5 and Friday's forecast shows wind under 15 mph and no rain. |
| Akron at Kent State | Over 42.5 | 46.5 | −3.4% | Pass | The market has fully reached the model. Kent State scored 10, 7 and 2 in its last three games and benched its kicker. |
| Miami (OH) at Central Michigan | Over 42.5 | 39.5 | +3.7% | Pass | The only positive current EV, but it comes from news the model can't see. CMU QB Angel Flores (concussion protocol, status TBD) drove the total down 3 points and the spread 4. SP+ and FEI don't adjust for QB injuries. With even a −2.5 point Flores adjustment, EV falls to about +0.9%. |
| Syracuse at North Carolina | Over 44.5 | 47.5 | −2.2% | Pass | The line moved 3 points toward the model. Syracuse has not named a QB, and Steve Angeli's return is unconfirmed. FanDuel is at 46.5 −115. |

**Spreads:** nothing clears +2% EV at the books. The fitted blend puts about 94% weight on the market for spreads,
so spread signals are rare by design.

## Data audit

Every number matched across the CFBD game rows, the SP+ Week 8 tab, the BCF Toys Week 8 table, the stored
projections and the blended totals:

- all five games are unplayed and on the right team;
- Miami (OH) resolves to team 193, not Miami (FL);
- each projection was published before kickoff;
- recomputing each blended total and EV reproduces the posted values.

No problems found.

## Note on the backtest numbers

Rebuilding the database in a new environment changed the headline early-number record:

| | Record | Units |
|---|---|---:|
| Before rebuild (2026-10-16) | 179–150–5 | +12.7 |
| After rebuild (2026-10-21) | 178–154–5 | +7.8 |

The inputs are the same. Per-season samples are identical, and source weights agree to 8 decimals. The change comes
from the 2024 season's selected distribution (`normal_linear_sigma`): its optimizer converged slightly differently,
and three borderline signals crossed the +2% bar. Numerical noise alone moving the record by about 5 units is
another sign the early-number record is not yet distinguishable from break-even (SE about 2.7 points on the win
rate). Treat it as a hypothesis that the line-movement data supports: the close moved +0.67 points toward the
signal, net of drift. It is not a proven edge.

## How to act on future weeks

The strategy only works at the early number, so the signals need to reach you when SP+'s sheet and BCF Toys
update (usually Monday or Tuesday). By Wednesday the move has usually happened. Before betting, check the
starting-QB news. SP+ and FEI are season ratings and don't price a QB injury.
