# SP+ acceptance test (plan section 2.2)

Our parse of each FBS week tab, graded on Bill's own ATS pick against the sheet's early-week spread, using final scores from the sheet's postgame tab. FBS membership = that week's ratings table. Pass = our W and L within +/-2 of Bill's `SP+ performance` row.

## 2024

Not testable from this workbook: no results for this season in the postgame tab. 


Games parsed: 920; rating rows: 1206; result rows: 0. Issues: {'results_tab_other_season': 1}

## 2025

Weeks passing: 14 / 16

| week_label   |   w_bill |   l_bill |   p_bill |   abs_error_bill |   w |   l |   p |   abs_error | ok    |
|:-------------|---------:|---------:|---------:|-----------------:|----:|----:|----:|------------:|:------|
| 0-1          |       23 |       25 |        0 |          12.2000 |  22 |  22 |   0 |     12.5000 | False |
| 2            |       27 |       23 |        0 |          11.3000 |  27 |  23 |   0 |     11.3000 | True  |
| 3            |       20 |       27 |        0 |          13.7000 |  21 |  26 |   0 |     13.6000 | True  |
| 4            |       24 |       26 |        0 |          12.5000 |  24 |  26 |   0 |     12.5000 | True  |
| 5            |       25 |       26 |        0 |          10.2000 |  25 |  26 |   0 |     10.2000 | True  |
| 6            |       25 |       25 |        0 |          11.3000 |  25 |  25 |   0 |     11.3000 | True  |
| 7            |       31 |       25 |        0 |          14.6000 |  31 |  25 |   0 |     14.6000 | True  |
| 8            |       35 |       24 |        0 |          12.5000 |  35 |  24 |   0 |     12.1000 | True  |
| 9            |       24 |       29 |        0 |          10.9000 |  24 |  29 |   0 |     10.9000 | True  |
| 10           |       29 |       23 |        0 |          13.0000 |  29 |  23 |   0 |     13.0000 | True  |
| 11           |       23 |       28 |        0 |          11.5000 |  23 |  28 |   0 |     11.5000 | True  |
| 12           |       34 |       24 |        0 |          12.0000 |  34 |  24 |   0 |     12.0000 | True  |
| 13           |       29 |       31 |        0 |          12.9000 |  29 |  31 |   0 |     12.8000 | True  |
| 14           |       38 |       29 |        0 |          12.0000 |  38 |  29 |   0 |     12.0000 | True  |
| 15           |        4 |        5 |        0 |          11.9000 |   4 |   5 |   0 |     11.9000 | True  |
| Postseason   |       26 |       19 |        2 |          12.4000 |  26 |  16 |   2 |     12.3000 | False |

- Week 0-1: 48 FBS-vs-FBS games in the tab; 4 have no early spread in the sheet, 0 have no final score in the postgame tab. Bill graded 48.

- Week Postseason: 44 FBS-vs-FBS games in the tab; 0 have no early spread in the sheet, 0 have no final score in the postgame tab. Bill graded 47.


Games parsed: 931; rating rows: 2312; result rows: 934. Issues: {'score_list_format': 1}

## 2026

Weeks passing: 6 / 6

| week_label   |   w_bill |   l_bill |   p_bill |   abs_error_bill |   w |   l |   p |   abs_error | ok   |
|:-------------|---------:|---------:|---------:|-----------------:|----:|----:|----:|------------:|:-----|
| 0-1          |       28 |       22 |        1 |          13.7000 |  28 |  22 |   1 |     13.7000 | True |
| 2            |       25 |       24 |        0 |          12.0000 |  25 |  24 |   0 |     12.0000 | True |
| 3            |       31 |       24 |        2 |           8.9000 |  31 |  24 |   2 |      8.8000 | True |
| 4            |       29 |       29 |        0 |          11.9000 |  29 |  29 |   0 |     11.9000 | True |
| 5            |       33 |       23 |        0 |          13.1000 |  33 |  23 |   0 |     13.1000 | True |
| 6            |       33 |       24 |        1 |          11.5000 |  33 |  24 |   1 |     11.4000 | True |


Games parsed: 510; rating rows: 1104; result rows: 448. Issues: none
