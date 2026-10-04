# What "surprise" means here, and how times and vintages are kept

## Definitions (no feature is a market-consensus forecast error; no free consensus source was available)
| feature | measure | type |
|---|---|---|
| w_stu_chg_pp, w_yield_rev_pct, w_prod_rev_pct | current-month WASDE projection minus the previous-month projection for the newest marketing year (both printed in the same USDA document) | **prior-release change** |
| c_ge_chg_pp | change in national good+excellent % vs the previous weekly Crop Progress release | prior-release change |
| d_belt_d1_chg_pp | change in mean D1+ drought area (IA, IL, IN, NE, MN) vs the previous weekly map | prior-release change |
| e_dist_stock_surprise_kbbl, e_crude_stock_surprise_kbbl | this week's inventory change minus the mean change in the same week of the previous five years (364-day steps; >=3 reference years) | **seasonal-norm deviation** (not analyst/API forecast error) |
| e_util_chg_pp | change in refinery utilization vs the previous week | prior-release change |
| t_ho_ret20, t_cf_rel_spy_ret20 | trailing 20-session return of heating oil / CF minus SPY, measured to the month-end close | trend, not a surprise |
A genuine pre-release forecast error (actual minus consensus) is **not available** under the free-access rule; claims here are about reactions to published changes, not to news relative to expectations.

## Three different clocks
* **Publication time**: when the source says the number became public (WASDE 12:00 ET; Crop Progress Monday 16:00 ET; USDM Thursday 08:30 ET for a Tuesday map; EIA Wednesday 10:30 ET). Historical EIA release dates were inferred (Wednesday, +1 day after a Monday holiday); the live pipeline reads the release date printed on the EIA page.
* **Retrieval time**: when we downloaded it. Historical research files were all retrieved 2026-10-04 (`cache/MANIFEST.jsonl`, sha256 per request); the live pipeline stores UTC retrieval time with every capture (`captures.retrieved_at_utc`).
* **Revision time**: WASDE and Crop Progress are original contemporaneous documents (their prior-month column is itself the earlier vintage). EIA bulk history and USDM history are latest-vintage: later revisions are not observable in the historical tests, so those results are labelled latest-vintage. Going forward, a changed document for the same release key is stored as a new capture (unique on source+key+sha256), so a revision is visible with its own retrieval time.
