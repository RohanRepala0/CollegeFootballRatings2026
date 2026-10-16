# Data architecture

Three layers:

- **Sources**, all read-only: CollegeFootballData (scores, lines, Elo), Bill Connelly's SP+ sheets, and BCF Toys FEI game projections.
- **One DuckDB log.** Every raw download is kept on disk, and every projection carries a published time (estimated, an hour before the week's first kickoff, for archived weeks), so a game only ever uses numbers dated before it kicked off.
- **Outputs**, built from that log: the weekly signal folders in `2026/` (written once, then only graded), the bet card, and the reports.

Nothing flows back out. No code path logs in to a sportsbook or places a bet; bets are placed by hand.

## End to end

```mermaid
flowchart TB
    subgraph SRC["Sources (read-only)"]
        CFBD["CollegeFootballData API<br/>games · scores · lines · Elo"]
        SPP["SP+ season sheets<br/>Google Sheets xlsx export"]
        BCF["BCF Toys FEI<br/>bcftoys.com/YEAR-gp"]
    end
    HAND["You, by hand<br/>cfb line set · log-bet · settle"]

    subgraph GATE["Ingest gate"]
        FETCH["fetch.py<br/>cached raw snapshots ·<br/>CFBD call ledger"]
        SIGN{"Sign checks<br/>formattedSpread: reported ·<br/>SP+ ATS pick: repairs rows"}
        TEAMS{"teams.py<br/>every name maps to a<br/>CFBD team, or it fails"}
    end

    subgraph LOG["Storage: DuckDB log + raw files"]
        RAW[("data/raw/ on disk<br/>immutable downloads")]
        CALLS[("api_calls · raw_snapshots")]
        GAMES[("games · teams · lines<br/>open · early · close · bet")]
        PROJ[("source_projections<br/>with published_at")]
        BETS[("bets")]
    end

    subgraph MODEL["Model layer"]
        BLEND["blend.py<br/>point-in-time sources ·<br/>weights · market alpha"]
        DIST["distribution/<br/>margin distribution,<br/>picked by cover log loss"]
        TOT["totals.py<br/>total-points distribution ·<br/>drift-adjusted CLV"]
        PRICE["pricing.py · staking.py<br/>spread cover odds · EV · Kelly"]
        WF["backtest.py · record.py<br/>walk-forward by season"]
    end

    subgraph OUT["Outputs"]
        WEEK["publish.py<br/>→ 2026/week-NN/<br/>signals + ratings, frozen"]
        SEASON["2026/README.md<br/>record + charts"]
        CARD["weekly.py → bet card<br/>line-shopped by book"]
        REP["reports/<br/>backtests · distributions"]
    end

    CFBD --> FETCH
    SPP --> FETCH
    BCF --> FETCH
    FETCH --> RAW
    FETCH --> CALLS
    FETCH -- "CFBD games (CFBD ids)" --> GAMES
    FETCH --> SIGN
    SIGN -- "CFBD lines" --> GAMES
    SIGN -- "SP+ · BCF rows" --> TEAMS
    TEAMS --> GAMES
    TEAMS --> PROJ
    HAND -- "your line" --> GAMES
    HAND -- "your bets" --> BETS
    PROJ --> BLEND
    GAMES --> BLEND
    GAMES --> DIST
    GAMES --> TOT
    PROJ --> TOT
    GAMES --> WF
    PROJ --> WF
    BLEND --> PRICE
    DIST --> PRICE
    PRICE --> WEEK
    PRICE --> CARD
    TOT -- "totals: own pricing,<br/>1 unit at -110" --> WEEK
    WF --> REP
    WF --> SEASON
    WF -- "backtest weeks" --> WEEK
    BETS -- "real bets" --> SEASON
    WEEK --> SEASON
    GAMES -- "final scores" --> WEEK
```

Arrows into storage write rows. Arrows out of it are reads. Not drawn: every backtest run logs each game it drops to the `exclusions` table, with the reason.

## The point-in-time rules

```mermaid
flowchart TB
    P["A projection for game G"] --> Q{"Published before<br/>G kicks off?"}
    Q -- no --> X["Never used<br/>(logged to exclusions by the<br/>backtest pass once the game is final)"]
    Q -- yes --> F{"Model parameters for<br/>season S fit on<br/>seasons before S only?"}
    F -- yes --> S["Signal priced at the<br/>line it names"]
    S --> W{"Week already<br/>written?"}
    W -- yes --> K["Kept as posted;<br/>only results are added"]
    W -- no --> N["Written once to<br/>2026/week-NN/"]
```

## A week, end to end

```mermaid
sequenceDiagram
    autonumber
    participant You as Rohan
    participant CLI as cfb (CLI)
    participant SRC as CFBD · SP+ · BCF Toys
    participant DB as DuckDB log
    participant Repo as 2026/week-NN/
    participant Book as FanDuel · DraftKings · Fanatics

    You->>CLI: backfill-cfbd --season 2026 --fresh
    CLI->>SRC: scores and current lines
    SRC-->>DB: games, lines
    You->>CLI: ingest-sp --season 2026 --download<br/>ingest-bcf --season 2026 --fresh
    CLI->>SRC: the season's SP+ workbook (xlsx)<br/>and BCF Toys season page
    SRC-->>DB: source_projections, SP+ early spread + O/U,<br/>SP+ ratings, BCF closing lines
    You->>CLI: publish --season 2026 --week N
    CLI->>DB: projections published before kickoff + latest lines
    CLI-->>Repo: signals (README.md, bets.csv) and ratings
    You->>Repo: git commit + push (the timestamp is the proof)
    You->>Book: place the signals by hand at the best number
    Note over CLI,Book: No code path logs in to a book or places a bet.
    You->>CLI: after the games: backfill-cfbd --season 2026 --fresh<br/>then publish --season 2026 --week N
    CLI-->>Repo: results, record, charts
```

## Tables

| Table | Written by | Read by | One row is |
|---|---|---|---|
| `games` | `backfill-cfbd` | everything game-scoped | one game, with its final score once played |
| `teams` | `backfill-cfbd` | `teams.py` | one CFBD team and its alternate names |
| `lines` | `backfill-cfbd`, `ingest-sp`, `ingest-bcf`, `line set` | `market.py`, `totals.py`, `weekly.py` | one provider's spread and total at one snapshot (open, early, close, bet) |
| `source_projections` | `ingest-sp`, `ingest-bcf` | `blend.py` | one source's projected margin and total for one game, with `published_at` |
| `source_ratings` | `backfill-cfbd`, `ingest-sp` | nothing yet; only `status` counts it | one team's rating from one source in one week |
| `raw_snapshots` | `fetch.py` | `fetch.py` cache | one downloaded file and where it is on disk |
| `team_aliases` | nothing yet (`sync_aliases_table` is never called) | nothing; `teams.py` reads `data/reference/team_aliases.csv` directly | a source's spelling of a team mapped to its CFBD id |
| `bets` | `log-bet`, `settle` | `record`, `publish` | one real bet: line, price, stake, close, CLV, result |
| `exclusions` | `backtest.py`, `totals.py` (via `backtest`, `backtest-totals`, `fit`, `record`, `picks`, `publish`) | `status` | one game dropped by one run, with the reason; re-logged every run |
| `api_calls` | `fetch.py` | `status` | one CFBD request, against the 1,000-a-month free tier |

Every timestamp is UTC. A week folder in `2026/` is written once; re-running `publish` only adds final scores and results.
