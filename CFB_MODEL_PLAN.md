# College Football Spread Model: Build Plan

> Handoff document for Claude Code. Read this whole file before writing code.
> Sections marked **[OPEN]** are decisions the owner has not made yet. Use the stated default unless `DECISIONS.md` in the repo root overrides it.

---

## 0. Goal

Build a weekly pipeline that:

1. Pulls game-level projections from several public rating systems (SP+, BCF Toys/FEI, Massey, plus any others we add).
2. Converts each into a single number: **expected home margin**.
3. Blends those sources into a combined forecast, then blends that forecast with the **market** line.
4. Converts the final number into a full **probability distribution over final margins**, so any offered line (e.g. -6.5 when we make it -8) can be priced as win / push / loss probabilities, fair odds, and expected value.
5. Backtests every step honestly (walk-forward, no look-ahead) and tracks live results, especially **closing line value (CLV)**.

Non-goals for v1: automated bet placement, player-level injury modeling, live/in-game betting.

---

## 1. Conventions (get these right first; most bugs live here)

| Concept | Convention |
|---|---|
| `home_margin` | home points − away points. Positive = home won. |
| `exp_margin` | model's expected home margin. Positive = home favored. |
| Market spread | Stored as `mkt_margin = -home_spread`. A home line of -7 becomes `mkt_margin = +7`. Never store raw book-convention spreads in modeling tables. |
| Neutral sites | Flag `neutral_site = True`. "Home" is whatever CFBD lists as home. Every source adapter must report whether its number already includes home-field advantage. |
| Ties | College football has had overtime since 1996, so a final margin of 0 is impossible. Margin distributions must put zero mass on 0. |
| Team identity | CFBD `team_id` is the primary key. Every other source maps to it through `data/reference/team_aliases.csv`. |
| Time | Every raw snapshot stores `fetched_at` (UTC). Nothing is ever overwritten. |

Write unit tests for the sign conventions before anything else (Phase 0).

---

## 2. Data Sources

### 2.1 CollegeFootballData.com (CFBD) — backbone
- API v2, Bearer-token key in `.env` as `CFBD_API_KEY` (owner is registering for one at collegefootballdata.com/key). Free tier is roughly 1,000 calls/month; Patreon tiers raise it. Season-level queries (`year=` with no week) return a whole season per call, so a 5-season backfill of games + lines + teams is a few dozen calls. Start on the free tier; cache every response to disk; log a running call count.
- Endpoints we need (verify exact paths and field names against the live docs before coding):
  - `/games` — schedule, scores, `neutral_site`, conference, division (FBS/FCS).
  - `/lines` — spreads and totals from several providers per game, including an opening spread. **Note:** these are not timestamped, so treat the main `spread` field as "near close" and verify that assumption against a few known games.
  - `/ratings/sp`, `/ratings/fpi`, `/ratings/elo`, `/ratings/srs`, FEI if available — candidate extra sources. **Leakage warning:** check whether these return end-of-season ratings or week-by-week snapshots. End-of-season ratings used to "predict" week 5 games are look-ahead bias and must not enter the backtest.
  - `/teams` — canonical names, ids, alternates (seed for the alias table).
- Python client: use the official `cfbd` package or plain `httpx`; owner's choice is the default (`httpx`, fewer surprises).

### 2.2 SP+ (Bill Connelly, ESPN) — public Google Sheets, 2021–2026
One spreadsheet per season, one tab per week. All six seasons are located:

```yaml
# config/sources.yaml
sp_plus:
  2021: {type: sheet, id: 1q7T5HDF9Kp5SBpOqkDF_1dal3GYdPipaLSn75p2I-rg}
  2022: {type: sheet, id: 1llrN8luL0XWuP8Y-Pb1NXKU84JhXLeUPafy1RfITEDw}
  2023: {type: published, pub_id: 2PACX-1vRh9Slymcisd5-uEIvAD4zjGkJ7aeARseChhne-HdpyQeQiTSJeZD0WfyuG40O5S7Z20wz1XLYSUDUj}
  2024: {type: sheet, id: 1CJImfkg0ouHIIIGOWRfbvwC0TNWh76n47xkz8nqrVBc}
  2025: {type: sheet, id: 1a6hboWNnPeUzx5oUEjwJwf9vw4DuAV7lTaW4Q92Zpls}
  2026: {type: sheet, id: 1vwoVl-Dxy0es87Z9I1RTvFzr72Lb1fAkREfbLxbK-eg}
```

- **Fetching:**
  - `type: sheet` → CSV per tab: `https://docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=<gid>`
  - `type: published` (2023 is "published to web") → `https://docs.google.com/spreadsheets/d/e/<pub_id>/pub?output=csv&gid=<gid>`; tab gids are listed in the raw HTML of `/pubhtml`.
  - Discover tab names + gids with the Google Sheets API v4 (`spreadsheets.get`, free API key as `GOOGLE_API_KEY`) for `sheet` types; parse `/pubhtml` for the published one. Cache the tab map to `data/reference/sp_tabs_<year>.json`.
- **Tabs to use:** names like `FBS Week 0` … `FBS Week 15`, plus the bowl tab (`FBS BOWL PICKS` in 2024, `FBS bowls (and Week 16)` in 2025). Ignore FCS/D2/D3/NAIA/TOP-7xx tabs, `FINAL` tabs (end-of-season, look-ahead), and `postgame win expectancy`. 2021 tab names are formatted `Week N FBS`; match both patterns with a regex.
- **`SP+ performance` tab = free validation target.** It reports Bill's weekly ATS record vs the early/midweek spread (FBS vs FBS) and vs the closing spread, plus absolute error. Acceptance test for the SP+ adapter: our parsed projections + lines must reproduce his weekly ATS records within ±2 games per week. If they don't, the parser or the join is wrong.
- **Verified 2026 layout** (earlier years may differ — parse by header name, never by column letter; log any unrecognized header):
  - Game picks: `Date | Time (ET) | Game | Proj. winner | Proj. margin | Win prob. | Proj. score (rounded) | Spread | ATS Pick | Spread diff | O/U | O/U pick | O/U diff`
  - Side table: `Team | Conference | SP+ | Rk | Off. SP+ | Rk | Def. SP+ | Rk | ST SP+ | Rk`
- **Parsing rules:**
  - `Game` is `"Away at Home"` or `"Team vs. Team"` (neutral). Confirm against CFBD `neutral_site`.
  - `Proj. margin` is unsigned, from the projected winner's side; sign it to home convention.
  - `Spread` is the early-week line Bill used. Store as `provider='sp_sheet', snapshot='early'`. Across 2021–2026 this is a free, consistent "early-week" market snapshot, which lets us measure line movement (early → close) for free.
  - Includes FCS opponents; the ratings side-table covers every FBS team and can price any missing matchup.
- **Caveats to record in the data-coverage report:**
  - Bill sometimes revises numbers in place (e.g. an October 2025 tweak to the scoring curve). Historical tabs are therefore "as currently shown", not guaranteed as originally posted. From now on, our own weekly snapshots are the record of truth.
  - Bill has said SP+ is treated like a sharp signal and moves lines after it posts. Expect SP+ to look better vs the early-week line than vs the close; the backtest must report both.
  - SP+ does not adjust for QB injuries (Bill notes this on his posts).

### 2.3 BCF Toys (Brian Fremeau, FEI) — archived and point-in-time
- **Game projections pages exist for 2022–2026:** `https://bcftoys.com/{YEAR}-gp`. Each page has one table per week, newest first, and the projections are "based on FEI ratings calculated as of the previous week" — i.e. genuinely point-in-time. This is the best historical source we have.
- **Verified table columns per week:** `Projected Win | Projected Loss | PW (win prob) | PM (margin) | PF | PA | PT (total) || Final | CL (closing line) | CT (closing total) || PE | SU | ATS | O/U`
- **Parsing rules:**
  - No home/away column. Join to CFBD `games` on (season, week, unordered team pair) to recover home/away/neutral, then sign the margin. Log every row that fails to join.
  - `CL` is from the projected winner's perspective (e.g. `-6.5`, `+2.5`). Convert to home convention. Store as `provider='bcftoys_close', snapshot='close'` — a second free source of closing lines to cross-check CFBD.
  - `Final` is `"W 28-7"` / `"L 3-10"` from the projected winner's side; use only to validate the CFBD join, never as the result of record.
  - FBS games only (FBS vs FCS games are not projected).
  - Week headers ("Week 7 Game Projections") define the week; map to CFBD week numbers, watch for week 0 and postseason.
- Other pages (`-fei`, `-wfei` weekly ratings, `-ofei`, `-dfei`) archived back to 2007 can be added later as features, but FEI game projections are the primary input.
- Be polite: one fetch per page per run, identify a user agent, respect robots.txt, cache raw HTML.

### 2.4 Massey Composite — rankings only, needs conversion
- `https://masseyratings.com/cf/compare.htm` (FBS) lists dozens of computer systems side by side, plus consensus mean/median. The page also links an archive, a CSV export, and a Kaggle archive; Claude Code should inspect those for weekly historical snapshots (point-in-time) before scraping HTML.
- **Important:** Massey states the comparison lists only ordinal rankings (1st, 2nd, 3rd…), not the underlying ratings. Rankings can't be subtracted to get a point spread, so each system needs a **rank → points** conversion:
  1. Transform rank to a z-score: `z = Φ⁻¹(1 − (rank − 0.5) / N_teams)`.
  2. Fit `exp_margin = a · (z_home − z_away) + HFA` per system (or for the consensus) on training seasons, against actual margins.
  3. Each converted system becomes a candidate source in the combo search — this is how "test as many combos as you can" gets done cheaply.
- Exclude human polls (AP, Coaches) and any system with spotty weekly coverage.
- Optional later: Massey's own game predictions page (projected scores) as a direct-margin source.

### 2.5 Market lines — sharp reference = Pinnacle / Circa, budget $0
- **Target definition of "the market":** sharp consensus = average of Pinnacle and Circa fair lines when both exist, otherwise whichever exists.
- **Price-adjusting a line:** -7 at -120 is not the same as -7 at -110. Use the §4.4 distribution to convert (line, price) on both sides into a no-vig fair line before averaging.
- **Historical (free sources only):**
  - Close: median across CFBD `/lines` providers (verify the provider list; Pinnacle/Circa are very likely absent), cross-checked against BCF Toys `CL` (2022+).
  - Early-week: SP+ sheet `Spread` column (2021+).
  - Backtest results are therefore labeled "vs retail consensus close". Sharp historical closes are out of scope at $0; the market layer stays pluggable in case that changes.
- **Live (free):**
  1. Test OddsPapi's free tier first. Its own marketing claims the free key covers NCAAF with Pinnacle and Circa (~250 requests/month). Treat that as unverified: Phase 6 starts with a probe script that pulls one week of NCAAF and reports which books actually return spreads.
  2. If the probe fails, fall back to manual entry: `cfb line set <game> --book pinnacle --spread -6.5 --price -108` from any public odds screen. The bet card works either way.
  3. Budget the free quota: one pull Tuesday (open-ish), one Friday, one Saturday morning per slate, one at close for CLV.
- **Where bets are placed:** the owner is in New York, where Pinnacle and Circa don't take action. Sharp books are the *reference*; bets are placed at the owner's NY book(s), so the bet card prices the line and price available there against the sharp-based fair line. See §8 Q-books.
- Store snapshots: early (SP+ sheet), bet-time, close. CLV is measured against the sharp close when captured, else retail consensus close.

### 2.6 Extra sources to test ("test as many combos as you can")
Candidates: ESPN FPI, CFBD Elo, SRS, other entries on the Massey composite page. Each new source is just a new adapter that emits the standard schema (§3), so adding one should take under an hour.

---

## 3. Data Model (DuckDB, single file at `data/cfb.duckdb`)

```
games(game_id, season, week, season_type, start_date, home_id, away_id,
      neutral_site, home_division, away_division, home_points, away_points)

lines(game_id, provider, snapshot ('open'|'bet'|'close'|'unknown'),
      home_spread, spread_price_home, spread_price_away, total, fetched_at)

source_projections(game_id, source, exp_margin, home_win_prob, exp_total,
                   includes_hfa, published_at, fetched_at, raw_ref)

raw_snapshots(source, url, fetched_at, path_on_disk, sha256)

team_aliases(source, source_name, team_id)

bets(bet_id, game_id, side, line_taken, price_taken, stake, placed_at,
     model_exp_margin, model_p_win, model_p_push, ev, close_line, result)
```

`source_projections.published_at` is what makes point-in-time backtesting possible: the backtest only uses projections with `published_at < kickoff`.

---

## 4. The Model

### 4.1 Per-source expected margin
For each source *i*, compute `m_i = exp_margin` for every game. If a source reports scores, `m_i = home_pts − away_pts`. If it excludes HFA, add the fitted HFA (fit per season from data, not hard-coded) unless neutral site.

### 4.2 Combined forecast
`F = Σ w_i · m_i / Σ w_i` over sources available for that game (renormalize when one is missing).
- v1 default: equal weights.
- v2: fit weights (non-negative, sum to 1) by minimizing squared error of `home_margin` on training seasons.
- Report every combination (singletons, pairs, all) in the backtest table.

### 4.3 Blend with the market
`M = α · mkt_margin + (1 − α) · F`
- Thread's starting point: α = 0.6.
- Fit α empirically. Equivalent and easier to interpret: regress `(home_margin − mkt_margin)` on `(F − mkt_margin)`. The slope β is how much of our disagreement with the market turns out to be real, and α = 1 − β. If β is near zero, the sources add nothing beyond the market, and the backtest should say so plainly.
- Fit α on the **close** for evaluation, but apply it to the **bet-time** line in production.

### 4.4 Margin distribution: pricing any line
Goal: `P(home_margin = k | M)` for every integer k in [-80, 80], with P(0) = 0. Implement three versions behind one interface and select by out-of-sample log-likelihood:

1. **Discretized normal** — `P(k) = Φ((k+0.5−M)/σ) − Φ((k−0.5−M)/σ)`, zero out k=0 and renormalize. Fit σ; also test σ as a function of |M| (lopsided games may be noisier).
2. **Sides & Harvill (arXiv 2212.08116)** — normal distribution modified with weights from historical point-differential frequencies so common margins (3, 7, 10, 14, ...) get appropriate mass. Read the paper's PDF and implement its exact method; the authors also published a free online tool that can serve as a sanity check.
3. **nfelo-style mixture** (nfeloapp "Margin Probabilities from NFL Spreads") — baseline normal around M plus a normal component at each key number, weighted by a super-gaussian centered at M, plus an "opposite-sign lift" for losses, renormalized so 50% of mass sits on each side of M. Refit all parameters on college data; NFL parameters will not transfer (CFB spreads are much wider and key numbers are weaker).

Training data: all FBS games with a closing line in CFBD history (10+ seasons), using `mkt_margin` (close) as the center. This module depends only on CFBD games and lines, so it can be built and validated before any rating source is ingested.

### 4.5 Bet pricing
Betting home at book spread `L` (e.g. -6.5): home covers if `home_margin > -L`, pushes if `home_margin == -L`.
- `p_win, p_push, p_loss` from the distribution.
- EV per $1 at American odds A: convert to decimal d; `EV = p_win·(d−1) − p_loss`.
- Fair odds: no-push win probability `p_win / (p_win + p_loss)` → American odds.
- Also output: moneyline win probability, and EV across the alt-line ladder (half-points off key numbers are worth more than elsewhere — this is the whole point of §4.4).

### 4.6 Staking
- Fractional Kelly: `f = (b·p − q) / b` using push-adjusted p and q, times a Kelly fraction **[OPEN, default 0.25]**, capped at a max per bet **[OPEN, default 2% of bankroll]**.
- Minimum EV threshold to bet **[OPEN, default +2%]**.

---

## 5. Backtesting Protocol

- **Window: five seasons, 2021–2025**, with 2026 weeks-to-date as a live holdout. Expected source coverage (Phase 4 confirms):

  | Source | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 YTD |
  |---|---|---|---|---|---|---|
  | Market (CFBD retail close) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
  | BCF Toys FEI projections | ✗ (archive starts 2022) | ✓ | ✓ | ✓ | ✓ | ✓ |
  | Massey composite (converted) | if archived | if archived | if archived | if archived | if archived | ✓ |
  | SP+ game projections | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
  | Early-week line (SP+ sheet) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
  | Sharp close (Pinnacle/Circa) | ✗ (budget $0) | ✗ | ✗ | ✗ | ✗ | live capture from now on |

  Because coverage differs, every comparison table reports results on the **common sample** (games where all compared sources exist) as well as each source's full sample.
- The margin-distribution model (§4.4) is not limited to this window: fit it on as much CFBD history as is clean (target 2014–2025, excluding 2020), since it only needs scores and closing lines and has no look-ahead risk.
- **Walk-forward by season:** fit weights, α and distribution parameters on seasons < S, evaluate on season S. Optionally walk forward week by week within a season.
- **Point-in-time only:** a game may use only projections with `published_at < kickoff`. Any source without point-in-time history is excluded from that season's backtest and the report says so.
- **Metrics:**
  - Margin RMSE / MAE vs actual for each source, each combo, the market, and the blend.
  - Cover-probability calibration (reliability plot) and log loss.
  - Simulated ATS record and ROI vs the **closing** line at -110, for bets passing the EV threshold.
  - CLV: points and no-vig probability gained between bet-time and close (use §4.4 to convert points to probability).
- **Reality checks to print in every report:** break-even at -110 is 52.38%. The standard error of a win rate over N bets is about 0.5/√N (≈2.2 points at N=500), so a 54% backtest on a few hundred bets is not distinguishable from break-even. CLV is the faster, more reliable signal.
- Filter options: FBS vs FBS only (default), exclude games where any source is missing, exclude week 0–2 (preseason priors dominate).

---

## 6. Repo Layout

```
cfb-model/
  CFB_MODEL_PLAN.md        # this file
  DECISIONS.md             # owner's answers to [OPEN] items
  .env.example             # CFBD_API_KEY=, ODDS_API_KEY=
  pyproject.toml           # uv-managed, Python 3.11+
  src/cfbmodel/
    config.py
    db.py                  # DuckDB schema + helpers
    teams.py               # alias resolution + unmatched-name report
    sources/
      base.py              # SourceAdapter interface -> standard rows
      cfbd.py
      sp_plus.py
      bcftoys.py
      massey.py
    market.py              # lines ingestion, consensus, snapshots
    blend.py               # §4.2–4.3
    distribution/
      normal.py
      sides_harvill.py
      nfelo_mixture.py
      select.py
    pricing.py             # §4.5
    staking.py             # §4.6
    backtest.py            # §5
    weekly.py              # orchestrates a live week
    cli.py                 # Typer CLI
  data/
    raw/<source>/<date>/   # immutable snapshots
    reference/team_aliases.csv
    cfb.duckdb
  reports/
  tests/
```

Stack: httpx, beautifulsoup4, pandas (or polars), duckdb, scipy, numpy, typer, pytest, matplotlib. Optional: streamlit for a dashboard.

---

## 7. Build Phases (each ends with tests passing and a short report)

**Phase 0 — Scaffold.** Repo, config, DuckDB schema, sign-convention tests, `.env` handling.
*Done when:* `pytest` passes; `cfb init-db` creates the schema.

**Phase 1 — CFBD backfill.** Games, lines, teams for the configured seasons, cached to `data/raw/cfbd/`.
*Done when:* row counts per season printed; spot-check 5 known games' scores and closing spreads.

**Phase 2 — Margin distribution (§4.4).** All three models, fit on history, compared out-of-sample.
*Done when:* report shows log-likelihood per model, a plot of predicted vs observed margin frequencies at spreads of 3, 7, 14, 21, and a pricing table for "model -8, market -6.5" and similar pairs.

**Phase 3 — Source adapters + archiver.** SP+, BCF Toys, Massey, plus CFBD rating endpoints. Team alias table with an unmatched-names report that fails loudly.
*Done when:* every FBS game in the current week has a row from every available source, zero unmatched names.

**Phase 4 — Historical backfill of sources.** BCF Toys 2022–2026 game-projection pages (all weeks), SP+ 2021–2026 sheets (all FBS week tabs; pass the `SP+ performance` reproduction test), Massey composite archive if it has weekly snapshots. Fill the coverage table in §5 with real counts in `reports/data_coverage.md`; cross-check BCF `CL` against CFBD closing lines and report the disagreement rate.

**Phase 5 — Blend + backtest (§4.2–4.3, §5).** Combo table, fitted weights, fitted α, calibration, simulated ATS and ROI.
*Done when:* `reports/backtest_<date>.md` generated with all metrics and the reality-check block.

**Phase 6 — Weekly pipeline.** Starts with the free odds-feed probe (§2.5). `cfb week --season 2026 --week N` fetches everything, archives raw snapshots, prints a bet card (game, our line, market line, p_win, p_push, EV, fair odds, suggested stake) to terminal and CSV.
*Done when:* runs end to end on the current week.

**Phase 7 — Tracking.** `cfb log-bet` to record bets actually placed at the actual line/price; nightly job fills closing lines and results; CLV and ROI dashboard/report.

---

## 8. Decisions and Open Questions

Resolved answers live in `DECISIONS.md`. Still open (defaults in brackets):

**Data sources**
1. Massey composite: does the archive/Kaggle link have weekly snapshots back to 2021? [Claude Code checks in Phase 4]
2. Additional sources beyond the composite systems? [CFBD Elo, SRS]

**Market**
3. **Q-books:** which sportsbook app(s) in New York do you actually bet at? This is the line and price the bet card evaluates. [manual entry per bet]

**Scope**
4. Spreads only, or also totals and moneylines? [spreads only for v1; BCF and SP+ both publish totals, so totals are a cheap Phase 8]
5. FBS-only, or include FBS vs FCS games? [FBS vs FBS only; BCF doesn't project FCS games]
6. Bowl/playoff games included? [excluded from backtest by default]

**Model**
7. Fixed weights or fitted? [report both, use fitted]
8. Start α at 0.6 or let the backtest pick? [fit it, report 0.6 alongside]

**Betting**
9. Kelly fraction, max bet, minimum EV? [0.25, 2%, +2%]
10. Units or dollars? [units]

**Output & ops**
11. Terminal + CSV, Google Sheet, or Streamlit dashboard? [terminal + CSV]
12. Manual runs or scheduled (e.g. Tuesday after SP+ and FEI update, Saturday morning line refresh)? [manual]
13. Laptop, server, or GitHub Actions? [laptop]

---

## 9. Ground Rules for the Implementer

- Never let a number from after kickoff touch a backtest prediction.
- Never silently drop a game: log every exclusion with a reason.
- Fail loudly on unmatched team names.
- Cache every external fetch; never re-download history that's already on disk.
- Respect each site's terms and robots.txt; keep scraping to one request per page per run.
- Every report ends with the sample size and the reality-check block from §5.
- This is a decision-support tool, not a guarantee of profit; the backtest should be allowed to conclude that a source or the whole blend adds nothing.
