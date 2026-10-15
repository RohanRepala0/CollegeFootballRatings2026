# Decisions

Owner's answers to plan questions. These override defaults in `CFB_MODEL_PLAN.md`.

| # | Topic | Decision | Notes |
|---|---|---|---|
| D1 | SP+ source | Bill Connelly's season sheets 2021–2026 (IDs in plan §2.2 `config/sources.yaml`) | 2021 + 2022 found via search; 2023–2025 from owner; 2023 is published-to-web. |
| D2 | BCF Toys source | `https://bcftoys.com/{YEAR}-gp` game projections | Archived 2022–2026, point-in-time, includes closing line. |
| D3 | Third source | Massey composite (`masseyratings.com/cf/compare.htm`) | Rankings only; convert rank → points per system (plan §2.4). |
| D4 | Market reference | Sharp: Pinnacle, Circa, or a blend of the two | Live only; historical backtest uses free retail consensus close (plan §2.5). |
| D8 | Odds data budget | $0 | Probe OddsPapi free tier for Pinnacle/Circa; fall back to manual line entry. No paid historical sharp lines. |
| D5 | CFBD API | Owner will register for a free key | Free tier should cover backfill with caching. |
| D6 | Backtest window | 5 seasons: 2021–2025, plus 2026 YTD as holdout | SP+ covers all; BCF starts 2022; report common-sample results (plan §5). |
| D7 | Sign convention | `exp_margin` = expected home points − away points | Plan §1. |

## Pending
See plan §8 for remaining open questions and their defaults.
