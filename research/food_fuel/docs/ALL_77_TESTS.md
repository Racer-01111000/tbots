# All 77 frozen tests (FEATURE_SPEC_v1, sha256 c4d3461f...)

**Frozen thresholds (never changed):** min events 30 per split; Spearman rank correlation; 2000 within-calendar-month permutations (seed 20261004); Benjamini-Hochberg q=0.10 across all 77 tests per split; SUPPORTED = FDR-significant in validation with the expected sign AND same sign in hold-out with one-sided p<0.10; SUGGESTIVE = validation nominal p<0.05 with the discovery sign; otherwise UNSUPPORTED. Splits: discovery 2007-12, validation 2013-18, hold-out 2019-22. Sensitivity columns: A = exclude event-target pairs whose return window touches a contract-expiry roll session (expiry..expiry+2; big moves empirically cluster at expiry+1); B = drop >4 robust-sigma target outliers; C = both.

| feature | commodity | target | n val | rho disc | rho val | q val | rho hold | p hold | original | A roll | B outlier | C both |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w_stu_chg_pp | corn | react | 66 | -0.124 | -0.348 | 0.058 | -0.174 | 0.304 | SUGGESTIVE | SUGGESTIVE | SUGGESTIVE | SUGGESTIVE |
| w_stu_chg_pp | corn | drift1 | 66 | -0.076 | 0.075 | 0.853 | 0.052 | 0.740 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | corn | drift5 | 66 | -0.096 | 0.069 | 0.853 | -0.052 | 0.750 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | corn | drift20 | 66 | -0.182 | 0.083 | 0.807 | -0.109 | 0.463 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | corn | pre | 66 | -0.251 | -0.105 | 0.803 | -0.274 | 0.083 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | corn | react | 66 | -0.174 | -0.330 | 0.323 | -0.470 | 0.002 | SUGGESTIVE | SUGGESTIVE | SUGGESTIVE | SUGGESTIVE |
| w_yield_rev_pct | corn | drift1 | 66 | -0.175 | 0.209 | 0.495 | 0.041 | 0.831 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | corn | drift5 | 66 | -0.062 | 0.030 | 0.924 | 0.091 | 0.583 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | corn | drift20 | 66 | 0.116 | -0.010 | 0.965 | -0.002 | 0.995 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | corn | pre | 66 | -0.118 | -0.051 | 0.853 | -0.269 | 0.062 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | corn | react | 66 | -0.107 | -0.300 | 0.323 | -0.231 | 0.180 | UNSUPPORTED | SUGGESTIVE | UNSUPPORTED | SUGGESTIVE |
| w_prod_rev_pct | corn | drift1 | 66 | -0.089 | 0.194 | 0.561 | 0.106 | 0.554 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | corn | drift5 | 66 | -0.071 | 0.159 | 0.723 | -0.018 | 0.912 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | corn | drift20 | 66 | 0.117 | 0.096 | 0.803 | -0.029 | 0.905 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | corn | pre | 66 | -0.136 | -0.097 | 0.817 | -0.255 | 0.120 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | soybeans | react | 66 | -0.056 | -0.169 | 0.778 | -0.191 | 0.340 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | soybeans | drift1 | 66 | -0.254 | 0.066 | 0.853 | -0.155 | 0.278 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | soybeans | drift5 | 66 | 0.029 | 0.124 | 0.803 | 0.064 | 0.684 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | soybeans | drift20 | 66 | -0.038 | 0.089 | 0.807 | 0.037 | 0.809 | UNSUPPORTED | UNTESTABLE(n<30) | UNSUPPORTED | UNTESTABLE(n<30) |
| w_stu_chg_pp | soybeans | pre | 66 | -0.210 | -0.208 | 0.469 | -0.435 | 0.002 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | soybeans | react | 66 | -0.408 | -0.390 | 0.058 | -0.342 | 0.062 | SUPPORTED | SUPPORTED | SUPPORTED | SUGGESTIVE |
| w_yield_rev_pct | soybeans | drift1 | 66 | 0.057 | 0.183 | 0.853 | -0.327 | 0.036 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | soybeans | drift5 | 66 | 0.051 | 0.171 | 0.684 | 0.021 | 0.907 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | soybeans | drift20 | 66 | 0.095 | 0.123 | 0.803 | 0.076 | 0.642 | UNSUPPORTED | UNTESTABLE(n<30) | UNSUPPORTED | UNTESTABLE(n<30) |
| w_yield_rev_pct | soybeans | pre | 66 | -0.038 | 0.048 | 0.853 | -0.206 | 0.209 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | soybeans | react | 66 | -0.347 | -0.334 | 0.048 | -0.220 | 0.198 | SUPPORTED | SUGGESTIVE | SUPPORTED | SUGGESTIVE |
| w_prod_rev_pct | soybeans | drift1 | 66 | -0.044 | 0.092 | 0.803 | -0.352 | 0.045 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | soybeans | drift5 | 66 | 0.006 | 0.029 | 0.927 | 0.126 | 0.444 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | soybeans | drift20 | 66 | 0.144 | 0.095 | 0.803 | -0.004 | 0.977 | UNSUPPORTED | UNTESTABLE(n<30) | UNSUPPORTED | UNTESTABLE(n<30) |
| w_prod_rev_pct | soybeans | pre | 66 | -0.043 | -0.061 | 0.853 | -0.266 | 0.104 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | wheat | react | 66 | -0.285 | -0.250 | 0.366 | -0.227 | 0.160 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | wheat | drift1 | 66 | 0.003 | 0.126 | 0.803 | -0.057 | 0.731 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | wheat | drift5 | 66 | 0.008 | -0.092 | 0.803 | -0.012 | 0.931 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | wheat | drift20 | 66 | -0.096 | 0.207 | 0.561 | 0.015 | 0.928 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_stu_chg_pp | wheat | pre | 66 | -0.073 | -0.150 | 0.684 | 0.211 | 0.105 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | wheat | react | 66 | 0.184 | 0.119 | 0.803 | -0.193 | 0.292 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | wheat | drift1 | 66 | 0.256 | -0.078 | 0.853 | 0.074 | 0.458 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | wheat | drift5 | 66 | 0.192 | -0.142 | 0.853 | -0.275 | 0.308 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | wheat | drift20 | 66 | -0.073 | -0.125 | 0.853 | -0.168 | 0.646 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_yield_rev_pct | wheat | pre | 66 | -0.085 | 0.028 | 0.924 | 0.078 | 0.609 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | wheat | react | 66 | 0.148 | 0.193 | 0.561 | -0.156 | 0.404 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | wheat | drift1 | 66 | 0.160 | -0.079 | 0.853 | 0.084 | 0.340 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | wheat | drift5 | 66 | 0.101 | -0.124 | 0.853 | -0.241 | 0.385 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | wheat | drift20 | 66 | -0.114 | -0.101 | 0.853 | -0.183 | 0.426 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| w_prod_rev_pct | wheat | pre | 66 | -0.082 | 0.030 | 0.924 | 0.016 | 0.910 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| c_ge_chg_pp | corn | react | 120 | -0.180 | -0.046 | 0.853 | -0.355 | 0.001 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| c_ge_chg_pp | corn | drift1 | 120 | 0.021 | 0.058 | 0.853 | -0.031 | 0.774 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| c_ge_chg_pp | corn | drift5 | 120 | -0.108 | 0.023 | 0.924 | 0.060 | 0.535 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| c_ge_chg_pp | corn | pre | 120 | -0.180 | -0.027 | 0.924 | 0.079 | 0.469 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| d_belt_d1_chg_pp | corn | react | 155 | 0.010 | 0.041 | 0.853 | 0.104 | 0.268 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| d_belt_d1_chg_pp | corn | drift1 | 155 | -0.108 | -0.014 | 0.960 | 0.102 | 0.283 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| d_belt_d1_chg_pp | corn | drift5 | 155 | -0.148 | 0.016 | 0.946 | -0.147 | 0.085 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| d_belt_d1_chg_pp | corn | pre | 155 | 0.032 | 0.011 | 0.965 | -0.020 | 0.841 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| c_ge_chg_pp | soybeans | react | 109 | -0.041 | -0.006 | 0.989 | -0.119 | 0.366 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| c_ge_chg_pp | soybeans | drift1 | 109 | 0.090 | 0.001 | 0.993 | 0.032 | 0.786 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| c_ge_chg_pp | soybeans | drift5 | 109 | 0.065 | 0.186 | 0.323 | 0.122 | 0.299 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| c_ge_chg_pp | soybeans | pre | 109 | -0.165 | -0.054 | 0.853 | 0.157 | 0.198 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| d_belt_d1_chg_pp | soybeans | react | 155 | -0.095 | 0.181 | 0.205 | 0.123 | 0.200 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| d_belt_d1_chg_pp | soybeans | drift1 | 155 | -0.115 | 0.079 | 0.803 | -0.080 | 0.413 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| d_belt_d1_chg_pp | soybeans | drift5 | 155 | -0.148 | 0.044 | 0.853 | -0.065 | 0.489 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| d_belt_d1_chg_pp | soybeans | pre | 155 | -0.034 | -0.002 | 0.993 | -0.036 | 0.717 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| e_dist_stock_surprise_kbbl | distillate | react | 309 | -0.225 | -0.243 | 0.013 | -0.163 | 0.024 | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |
| e_dist_stock_surprise_kbbl | distillate | drift1 | 309 | -0.011 | -0.008 | 0.965 | 0.083 | 0.230 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| e_dist_stock_surprise_kbbl | distillate | drift5 | 309 | 0.012 | 0.057 | 0.803 | -0.014 | 0.837 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| e_dist_stock_surprise_kbbl | distillate | pre | 309 | -0.062 | -0.197 | 0.013 | -0.014 | 0.848 | SUGGESTIVE[anticipation] | SUPPORTED[anticipation] | SUGGESTIVE[anticipation] | SUGGESTIVE[anticipation] |
| e_crude_stock_surprise_kbbl | crude | react | 309 | -0.170 | -0.235 | 0.013 | -0.120 | 0.080 | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |
| e_crude_stock_surprise_kbbl | crude | drift1 | 309 | -0.025 | -0.027 | 0.870 | 0.137 | 0.073 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| e_crude_stock_surprise_kbbl | crude | drift5 | 309 | -0.157 | -0.066 | 0.803 | 0.067 | 0.362 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| e_crude_stock_surprise_kbbl | crude | pre | 309 | -0.039 | -0.084 | 0.585 | -0.119 | 0.081 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| e_util_chg_pp | distillate | react | 309 | -0.049 | -0.136 | 0.178 | 0.014 | 0.844 | SUGGESTIVE | UNSUPPORTED | SUGGESTIVE | UNSUPPORTED |
| e_util_chg_pp | distillate | drift1 | 309 | -0.120 | -0.073 | 0.803 | 0.018 | 0.824 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| e_util_chg_pp | distillate | drift5 | 309 | -0.102 | -0.048 | 0.803 | 0.097 | 0.249 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| e_util_chg_pp | distillate | pre | 309 | -0.054 | 0.002 | 0.993 | 0.072 | 0.285 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| t_ho_ret20 | corn | drift20 | 71 | -0.027 | 0.106 | 0.803 | -0.074 | 0.677 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| t_ho_ret20 | corn | drift60 | 71 | -0.011 | 0.091 | 0.803 | 0.083 | 0.634 | UNSUPPORTED | UNTESTABLE(n<30) | UNSUPPORTED | UNTESTABLE(n<30) |
| t_cf_rel_spy_ret20 | corn | drift20 | 72 | -0.083 | 0.267 | 0.170 | 0.243 | 0.080 | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| t_cf_rel_spy_ret20 | corn | drift60 | 72 | 0.077 | 0.249 | 0.212 | 0.188 | 0.188 | SUGGESTIVE | UNTESTABLE(n<30) | SUGGESTIVE | UNTESTABLE(n<30) |

## Counts
- original: {'SUGGESTIVE': 4, 'SUGGESTIVE[anticipation]': 1, 'SUPPORTED': 4, 'UNSUPPORTED': 68}
- A roll-excluded: {'SUGGESTIVE': 4, 'SUPPORTED': 3, 'SUPPORTED[anticipation]': 1, 'UNSUPPORTED': 64, 'UNTESTABLE(n<30)': 5}
- B outlier-excluded: {'SUGGESTIVE': 4, 'SUGGESTIVE[anticipation]': 1, 'SUPPORTED': 4, 'UNSUPPORTED': 68}
- C both: {'SUGGESTIVE': 5, 'SUGGESTIVE[anticipation]': 1, 'SUPPORTED': 2, 'UNSUPPORTED': 64, 'UNTESTABLE(n<30)': 5}

## The four originally SUPPORTED findings after roll/outlier exclusion

| finding | original | A | B | C | n val (A) | rho val (A) | q val (A) | rho hold (A) | p hold (A) |
|---|---|---|---|---|---|---|---|---|---|
| e_dist_stock_surprise_kbbl distillate react | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | 276 | -0.272 | 0.018 | -0.198 | 0.009 |
| e_crude_stock_surprise_kbbl crude react | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | 257 | -0.185 | 0.072 | -0.133 | 0.093 |
| w_yield_rev_pct soybeans react | SUPPORTED | SUPPORTED | SUPPORTED | SUGGESTIVE | 64 | -0.365 | 0.072 | -0.311 | 0.111 |
| w_prod_rev_pct soybeans react | SUPPORTED | SUGGESTIVE | SUPPORTED | SUGGESTIVE | 64 | -0.303 | 0.072 | -0.181 | 0.293 |

## Drift targets on rolling futures
Any drift5/drift20/drift60 window on HO/CL/ZS/ZC/ZW almost always contains a roll session; under variant A most drift tests lose a large share of events or become UNTESTABLE. Post-release drift on continuous futures is therefore UNVERIFIED rather than disproved.