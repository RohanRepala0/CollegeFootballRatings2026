# Totals backtest 2026-10-16

Seasons evaluated (walk-forward): [2022, 2023, 2024, 2025]. Sources: bcftoys, sp_plus. FBS-only: True; postseason: False. Market = median closing total across CFBD providers; early market = the SP+ sheet's O/U. Min EV +2% at -110.

## Total-points distribution (walk-forward over [2014, 2015, 2016, 2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025], ranked by cover)

Cover log loss scores P(total > line) on every half-point line within 10 points of the closing total. Lower is better.

| model               |    n |    log_lik |   cover_brier |   n_lines |   mean_log_lik |   delta_log_lik |   cover_log_loss |   delta_cover_ll |
|:--------------------|-----:|-----------:|--------------:|----------:|---------------:|----------------:|-----------------:|-----------------:|
| frequency_weighted  | 5933 | -24591.499 |         0.229 |    121669 |         -4.145 |           0.000 |            0.650 |            0.000 |
| normal_linear_sigma | 5933 | -24883.493 |         0.229 |    121669 |         -4.194 |        -291.994 |            0.650 |           33.217 |
| normal              | 5933 | -24904.290 |         0.229 |    121669 |         -4.198 |        -312.791 |            0.651 |           57.965 |
| normal_shift        | 5933 | -24903.368 |         0.230 |    121669 |         -4.197 |        -311.869 |            0.651 |          125.111 |

### Paired cover-log-loss differences vs the best (summed over games; SE across games)

| model               | vs                 |   total_diff |     se |   n_games |
|:--------------------|:-------------------|-------------:|-------:|----------:|
| frequency_weighted  | frequency_weighted |        0.000 |  0.000 |      5933 |
| normal              | frequency_weighted |       57.965 | 57.456 |      5933 |
| normal_linear_sigma | frequency_weighted |       33.217 | 57.068 |      5933 |
| normal_shift        | frequency_weighted |      125.111 | 66.283 |      5933 |

## Per-season fitted parameters

|   season | distribution        | weights                                                         |   alpha |   beta |   beta_se |   alpha_n |   alpha_early |   beta_early_se |
|---------:|:--------------------|:----------------------------------------------------------------|--------:|-------:|----------:|----------:|--------------:|----------------:|
|     2022 | frequency_weighted  | {'bcftoys': 0.5, 'sp_plus': 0.5}                                |   0.827 |  0.173 |     0.120 |       727 |         0.758 |           0.125 |
|     2023 | normal_linear_sigma | {'bcftoys': 0.21264979289473052, 'sp_plus': 0.7873502071052695} |   0.924 |  0.076 |     0.088 |      1454 |         0.789 |           0.096 |
|     2024 | normal_linear_sigma | {'bcftoys': 0.18872717060475133, 'sp_plus': 0.8112728293952487} |   0.942 |  0.058 |     0.074 |      2197 |         0.814 |           0.082 |
|     2025 | frequency_weighted  | {'bcftoys': 0.20464224290408378, 'sp_plus': 0.7953577570959162} |   0.847 |  0.153 |     0.066 |      2947 |         0.756 |           0.073 |

## Forecast error vs actual total

`common` = games with every source and an SP+ early O/U.

| forecast     | sample   |    n |   rmse |    mae |
|:-------------|:---------|-----:|-------:|-------:|
| market_close | full     | 2998 | 15.694 | 12.467 |
| market_close | common   | 2942 | 15.722 | 12.489 |
| early_line   | full     | 2942 | 15.840 | 12.616 |
| early_line   | common   | 2942 | 15.840 | 12.616 |
| blend        | full     | 2978 | 15.714 | 12.477 |
| blend        | common   | 2942 | 15.724 | 12.491 |
| combined_F   | full     | 2978 | 16.123 | 12.841 |
| combined_F   | common   | 2942 | 16.109 | 12.830 |
| bcftoys      | full     | 2978 | 16.937 | 13.390 |
| bcftoys      | common   | 2942 | 16.931 | 13.389 |
| sp_plus      | full     | 2951 | 16.137 | 12.894 |
| sp_plus      | common   | 2942 | 16.143 | 12.898 |

## Mean error (actual - forecast) by season

A standing bias here is an over/under lean the blend's alpha cannot remove (it has no intercept).

|   season |   market_close |   early_line |   blend |   combined_F |   bcftoys |   sp_plus |
|---------:|---------------:|-------------:|--------:|-------------:|----------:|----------:|
| 2022.000 |         -0.103 |       -0.480 |  -0.259 |       -1.049 |    -1.506 |    -0.536 |
| 2023.000 |          1.334 |        0.521 |   1.387 |        1.737 |     4.553 |     0.928 |
| 2024.000 |          1.254 |        0.807 |   1.189 |       -0.173 |     0.517 |    -0.333 |
| 2025.000 |          0.233 |       -0.246 |   0.087 |       -0.891 |     1.065 |    -1.394 |

## Source combinations (equal weights, alpha fit per season, bets vs close at -110)

| combo           |   F_rmse |   M_rmse |   alpha |   bets |   w |   l |   p |   win_pct |
|:----------------|---------:|---------:|--------:|-------:|----:|----:|----:|----------:|
| bcftoys         |   16.923 |   15.806 |   0.949 |    535 | 257 | 272 |   6 |     0.486 |
| bcftoys+sp_plus |   16.190 |   15.716 |   0.923 |    418 | 204 | 207 |   7 |     0.496 |
| sp_plus         |   16.137 |   15.697 |   0.871 |    539 | 261 | 269 |   9 |     0.492 |

## Simulated bets: blend_close

|    bets |       w |       l |     p |   win_pct |    roi |    se |   clv_points |   clv_prob |
|--------:|--------:|--------:|------:|----------:|-------:|------:|-------------:|-----------:|
| 445.000 | 220.000 | 218.000 | 7.000 |     0.502 | -0.040 | 0.024 |        0.000 |      0.035 |

| side   |    bets |       w |       l |     p |   win_pct |    roi |    se |   clv_points |   clv_prob |
|:-------|--------:|--------:|--------:|------:|----------:|-------:|------:|-------------:|-----------:|
| over   | 167.000 |  79.000 |  84.000 | 4.000 |     0.485 | -0.073 | 0.039 |        0.000 |      0.038 |
| under  | 278.000 | 141.000 | 134.000 | 3.000 |     0.513 | -0.021 | 0.030 |        0.000 |      0.034 |

## Simulated bets: blend_early

|    bets |       w |       l |      p |   win_pct |   roi |    se |   clv_points |   clv_prob |
|--------:|--------:|--------:|-------:|----------:|------:|------:|-------------:|-----------:|
| 624.000 | 322.000 | 292.000 | 10.000 |     0.524 | 0.001 | 0.020 |        0.578 |      0.039 |

| side   |    bets |       w |       l |     p |   win_pct |    roi |    se |   clv_points |   clv_prob |
|:-------|--------:|--------:|--------:|------:|----------:|-------:|------:|-------------:|-----------:|
| over   | 228.000 | 116.000 | 110.000 | 2.000 |     0.513 | -0.020 | 0.033 |        0.247 |      0.031 |
| under  | 396.000 | 206.000 | 182.000 | 8.000 |     0.531 |  0.013 | 0.025 |        0.768 |      0.044 |

## Calibration (P(over) at the closing total, pushes excluded). Log loss: 0.6939

| bin        |   n |   predicted |   observed |   season |
|:-----------|----:|------------:|-----------:|---------:|
| (0.4, 0.5] | 450 |       0.471 |      0.509 |     2022 |
| (0.5, 0.6] | 262 |       0.525 |      0.481 |     2022 |
| (0.6, 0.7] |   4 |       0.627 |      0.250 |     2022 |
| (0.4, 0.5] | 400 |       0.494 |      0.497 |     2023 |
| (0.5, 0.6] | 334 |       0.507 |      0.527 |     2023 |
| (0.4, 0.5] | 258 |       0.497 |      0.516 |     2024 |
| (0.5, 0.6] | 477 |       0.505 |      0.524 |     2024 |
| (0.4, 0.5] | 377 |       0.476 |      0.469 |     2025 |
| (0.5, 0.6] | 374 |       0.525 |      0.511 |     2025 |
| (0.6, 0.7] |   2 |       0.615 |      0.000 |     2025 |


**Sample:** 2998 games evaluated; 1069 simulated bets across all bet sets.


### Reality check

- Break-even at -110 is 52.38%.
- Standard error of a win rate over N bets is about 0.5/sqrt(N): with N = 445, that is 2.4% (about 2.2 points at N = 500). A 54% backtest on a few hundred bets is not distinguishable from break-even.
- CLV is the faster, more reliable signal.
- These results are vs the **retail consensus close** (median of CFBD providers), not a sharp close.
