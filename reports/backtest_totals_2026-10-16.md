# Totals backtest 2026-10-16

Seasons evaluated (walk-forward): [2022, 2023, 2024, 2025]. Sources: bcftoys, sp_plus. FBS-only: True; postseason: False. Market = median closing total across CFBD providers; early market = the SP+ sheet's O/U. Min EV +2% at -110.

## Total-points distribution (walk-forward over [2014, 2015, 2016, 2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025], ranked by cover)

Cover log loss scores P(total > line) on every half-point line within 10 points of the closing total. Lower is better.

| model               |    n |    log_lik |   cover_brier |   n_lines |   mean_log_lik |   delta_log_lik |   cover_log_loss |   delta_cover_ll |
|:--------------------|-----:|-----------:|--------------:|----------:|---------------:|----------------:|-----------------:|-----------------:|
| frequency_weighted  | 5933 | -24592.629 |         0.229 |    121669 |         -4.145 |           0.000 |            0.650 |            0.000 |
| normal_linear_sigma | 5933 | -24883.493 |         0.229 |    121669 |         -4.194 |        -290.864 |            0.650 |           43.507 |
| normal              | 5933 | -24904.290 |         0.229 |    121669 |         -4.198 |        -311.661 |            0.651 |           68.254 |
| normal_shift        | 5933 | -24903.368 |         0.230 |    121669 |         -4.197 |        -310.739 |            0.651 |          135.400 |

### Paired cover-log-loss differences vs the best (summed over games; SE across games)

| model               | vs                 |   total_diff |     se |   n_games |
|:--------------------|:-------------------|-------------:|-------:|----------:|
| frequency_weighted  | frequency_weighted |        0.000 |  0.000 |      5933 |
| normal              | frequency_weighted |       68.254 | 24.550 |      5933 |
| normal_linear_sigma | frequency_weighted |       43.507 | 21.846 |      5933 |
| normal_shift        | frequency_weighted |      135.400 | 37.129 |      5933 |

## Per-season fitted parameters

|   season | distribution        | weights                                                         |   alpha |   beta |   beta_se |   alpha_n |   alpha_early |   beta_early_se |
|---------:|:--------------------|:----------------------------------------------------------------|--------:|-------:|----------:|----------:|--------------:|----------------:|
|     2022 | frequency_weighted  | {'bcftoys': 0.5, 'sp_plus': 0.5}                                |   0.827 |  0.173 |     0.120 |       727 |         0.758 |           0.125 |
|     2023 | frequency_weighted  | {'bcftoys': 0.21264979289473052, 'sp_plus': 0.7873502071052695} |   0.924 |  0.076 |     0.088 |      1454 |         0.789 |           0.096 |
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

## Line movement: drift vs information

`drift` = mean(close - early total): what always betting the under earns in CLV. `slope` = how far the close moves toward the blended sources per point they disagree with the early line (fit with an intercept, so drift is not counted as information). `clv_net` in the bet tables below is each early bet's CLV minus what always taking that side earned that season.

| season   |    n |   drift |   drift_se |   intercept |   slope |   slope_se |
|:---------|-----:|--------:|-----------:|------------:|--------:|-----------:|
| 2022     |  711 |  -0.360 |      0.085 |      -0.416 |   0.115 |      0.019 |
| 2023     |  723 |  -0.887 |      0.083 |      -0.822 |   0.056 |      0.023 |
| 2024     |  750 |  -0.467 |      0.073 |      -0.560 |   0.096 |      0.020 |
| 2025     |  758 |  -0.509 |      0.067 |      -0.612 |   0.160 |      0.019 |
| all      | 2942 |  -0.555 |      0.039 |      -0.582 |   0.111 |      0.010 |

## Source combinations (equal weights, alpha fit per season, bets vs close at -110)

| combo           |   F_rmse |   M_rmse |   alpha |   bets |   w |   l |   p |   win_pct |
|:----------------|---------:|---------:|--------:|-------:|----:|----:|----:|----------:|
| bcftoys         |   16.923 |   15.806 |   0.949 |    492 | 242 | 247 |   3 |     0.495 |
| bcftoys+sp_plus |   16.190 |   15.716 |   0.923 |     72 |  34 |  37 |   1 |     0.479 |
| sp_plus         |   16.137 |   15.697 |   0.871 |    156 |  81 |  71 |   4 |     0.533 |

### The same combinations at the early line

SP+ alone is the timing-safe row: its projection and the early O/U come from the same sheet row. BCF Toys publish times are inferred (one hour before each week's first kickoff), so its projections are not proven to exist when the early O/U was posted.

| combo           |   early_bets |   early_w |   early_l |   early_units |   early_win_pct |   early_clv_net |
|:----------------|-------------:|----------:|----------:|--------------:|----------------:|----------------:|
| bcftoys         |          425 |       211 |       206 |       -14.182 |           0.506 |           0.623 |
| bcftoys+sp_plus |          310 |       171 |       133 |        22.455 |           0.562 |           0.865 |
| sp_plus         |          339 |       178 |       156 |         5.818 |           0.533 |           0.328 |

## Simulated bets: blend_close

|   bets |      w |      l |     p |   win_pct |   win_se |    roi |   roi_se |   clv_points |   clv_prob |
|-------:|-------:|-------:|------:|----------:|---------:|-------:|---------:|-------------:|-----------:|
| 90.000 | 44.000 | 45.000 | 1.000 |     0.494 |    0.053 | -0.056 |    0.101 |        0.000 |      0.000 |

| side   |   bets |      w |      l |     p |   win_pct |   win_se |    roi |   roi_se |   clv_points |   clv_prob |
|:-------|-------:|-------:|-------:|------:|----------:|---------:|-------:|---------:|-------------:|-----------:|
| over   | 66.000 | 29.000 | 36.000 | 1.000 |     0.446 |    0.062 | -0.146 |    0.117 |        0.000 |      0.000 |
| under  | 24.000 | 15.000 |  9.000 | 0.000 |     0.625 |    0.102 |  0.193 |    0.193 |        0.000 |      0.001 |

## Simulated bets: blend_early

|    bets |       w |       l |     p |   win_pct |   win_se |   roi |   roi_se |   clv_points |   clv_prob |   clv_net |
|--------:|--------:|--------:|------:|----------:|---------:|------:|---------:|-------------:|-----------:|----------:|
| 334.000 | 179.000 | 150.000 | 5.000 |     0.544 |    0.028 | 0.038 |    0.052 |        0.695 |      0.020 |     0.664 |

| side   |    bets |      w |      l |     p |   win_pct |   win_se |   roi |   roi_se |   clv_points |   clv_prob |   clv_net |
|:-------|--------:|-------:|-------:|------:|----------:|---------:|------:|---------:|-------------:|-----------:|----------:|
| over   | 175.000 | 95.000 | 79.000 | 1.000 |     0.546 |    0.038 | 0.042 |    0.072 |        0.447 |      0.011 |     0.940 |
| under  | 159.000 | 84.000 | 71.000 | 4.000 |     0.542 |    0.040 | 0.034 |    0.075 |        0.969 |      0.031 |     0.362 |

## Calibration (P(over) at the closing total, pushes excluded). Log loss: 0.6934

| bin        |   n |   predicted |   observed |   season |
|:-----------|----:|------------:|-----------:|---------:|
| (0.4, 0.5] | 308 |       0.486 |      0.503 |     2022 |
| (0.5, 0.6] | 407 |       0.517 |      0.494 |     2022 |
| (0.6, 0.7] |   1 |       0.625 |      0.000 |     2022 |
| (0.4, 0.5] | 400 |       0.493 |      0.497 |     2023 |
| (0.5, 0.6] | 334 |       0.507 |      0.527 |     2023 |
| (0.4, 0.5] | 258 |       0.497 |      0.516 |     2024 |
| (0.5, 0.6] | 477 |       0.505 |      0.524 |     2024 |
| (0.4, 0.5] | 264 |       0.491 |      0.447 |     2025 |
| (0.5, 0.6] | 489 |       0.512 |      0.511 |     2025 |


**Sample:** 2998 games evaluated; 424 simulated bets across all bet sets.


### Reality check

- Break-even at -110 is 52.38%.
- Standard error of a win rate over N bets is about 0.5/sqrt(N): with N = 90, that is 5.3% (about 2.2 points at N = 500). A 54% backtest on a few hundred bets is not distinguishable from break-even.
- CLV is the faster, more reliable signal.
- These results are vs the **retail consensus close** (median of CFBD providers), not a sharp close.
