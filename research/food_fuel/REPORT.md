# Food & Fuel Evidence Module - report

Branch `research/food-fuel-evidence-20261004`. Research only: no orders, no bot promoted, no recurring collection activated.

## Method discipline
1. Collected primary sources (free access), original vintages where they exist (`sources/SOURCE_VINTAGE_MANIFEST.json`, `cache/MANIFEST.jsonl`).
2. Discovery pass on 2007-2012 only, then **froze** `features/FEATURE_SPEC_v1.json` (77 tests; sha256 c4d3461f...; commit a821cab) before any validation (2013-2018) or hold-out (2019-2022) test. 2019-2022 were used only as this module's hold-out; no bot evaluation touched them.
3. Spearman rank statistic (robust to futures roll gaps), within-month permutation p-values, seasonal baseline from discovery years, Benjamini-Hochberg q=0.10 over all 77 tests per split. All 77 results are in `results/frozen_test_results.json`; classification in `results/classification.json`.

## Strongest supported indicators (all are SAME-SESSION reactions, not leading signals)
| indicator | instrument | validation rho (q) | hold-out rho |
|---|---|---|---|
| EIA distillate stock surprise vs seasonal norm | HO | -0.243 (0.013) | -0.163 (p .024) |
| EIA commercial crude stock surprise | CL | -0.235 (0.013) | -0.120 (p .080) |
| WASDE soybean yield revision | ZS | -0.390 (0.058) | -0.342 (p .062) |
| WASDE soybean production revision | ZS | -0.334 (0.048) | -0.220 (p .198; marginal, passes only the one-sided rule) |
Suggestive only: WASDE corn stocks-to-use change and corn yield revision (reaction), refinery utilization change, and **fertilizer-equity relative strength (CF minus SPY, 20 sessions) -> corn 60-session drift** (rho +0.25 validation, +0.19 hold-out, q=0.21; the only transmission candidate, an equity proxy that may just be commodity beta).

## Negative / unsupported findings
* **No post-release drift anywhere**: zero FDR-significant `drift1/5/20` tests in discovery, validation or hold-out. The markets price these releases on the day; the indicators are not leading signals at daily horizons.
* 68 of 77 tests UNSUPPORTED, including crop-condition change, drought-coverage change (weak sign opposite to expectation at 5 days), wheat revisions, heating-oil 20-day return -> later corn (the fuel-cost transmission hypothesis), and all `drift` targets.
* An anticipation result: distillate surprise correlates with the 5 days BEFORE release in validation (rho -0.20, q 0.013) but not in hold-out; corn condition changes correlate with the pre-release 5-day return in discovery. Markets often move before the published number.
* Episode studies (`docs/EPISODES.md`, descriptive and discovery-grade): 2007-08 wheat stocks-to-use fell 0.198 -> 0.102 while price roughly doubled then fell 25% within 20 days after the March 2008 report; 2012 corn yield 166 -> 146 -> 123.4 bu with ending stocks 1881 -> 650; 2013-14 shows the failed-shortage follow-through (yield rebound, price decline); 2020 and 2022 price paths included. These illustrate, they do not prove, transmission.

## Gym experiment v2 (`research/synthetic_gym`, config `experiment_evt_v2.json`, results `results/SYNGYM-20261004-EVT-V2`)
Fresh 72-world bank; v1 generator/scoring/results untouched. Event-aware identity `EVT_AWARE_v1` (overlay on a frozen price-only parent; parameters hashed into the id) vs the same price-only bases on identical worlds, with clean, delayed, missing (40%), revised and contradictory information plus a placebo feed from another world. **Result: null.** Mean paired differences are within +/-0.006 pp annualised with 95% CIs spanning zero in every base x condition (clean: sam +0.001 [-0.003,+0.006], d_primary_rank3 +0.001 [-0.004,+0.006], champion -0.001 [-0.010,+0.008]); placebo similarly flat. The decision rule (positive CI lower bound in clean and no placebo gain) is not met. No bot is promoted.

## Data gaps
FAO Food Price Index and IEA data blocked by bot protection (not circumvented); USDA FAS export sales not machine-readable; no freight or fertilizer-spot series with vintages (World Bank monthly is latest-vintage, context only); EIA and USDM are latest-vintage and EIA release time is inferred; Yahoo futures are continuous contracts with roll gaps; no intraday data, so within-day timing is untested; ONI fetched but unused in tests.

## Resource use
See `RESOURCE_RECEIPT.json`: data collection ran on HOST (1,345 polite requests, 305 MiB cache) because the EC2 sandbox has no network; EC2 ran the v2 gym (51 s CPU, 367 MiB RSS, 1 worker) and tests.

## Smallest next experiment
A no-automation forward log: on each of the next ~12 Wednesday EIA releases (and the next WASDE), run the brief manually and record the feature and the same-session HO/CL (or ZS) return, to test the two best-supported reactions on genuinely contemporaneous vintages. It needs no timers, credentials or trading-universe change.
