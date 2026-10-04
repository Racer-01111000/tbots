# Food & fuel pre-market evidence brief - 2026-10-05_0915
Edition target 2026-10-05T13:15:00+00:00; actually run 2026-10-04T07:19:56+00:00 (if the run time precedes the target, quotes are from the last completed sessions and are labelled stale). Trading-day check: rule-based NYSE calendar, not exchange-verified.

> Research brief only; it cannot issue orders and does not alter any bot.
> ETF/equity/futures quotes are proxies, NOT spot commodity prices and NOT opening-auction data.
> Evidence labels come from FEATURE_SPEC_v1 tests: indicators are priced on release; no post-release drift was supported.

## Food - what changed (latest releases)

- **corn / w_stu_chg_pp**: -0.438 (forecast revision (interpretation of NASS/FAS inputs); WASDE 2026-09-11); level {'stocks_to_use': 0.0968, 'yield': 178.5}
  - tested support: {'react': 'SUGGESTIVE'}
  - mechanism: WASDE revision of stocks-to-use for the new marketing year: tighter stocks raise price needed to ration demand
  - competing explanations: revisions already anticipated by private estimates/trade surveys; demand-side revision (exports/ethanol) vs supply-side revision (yield/acres); policy or currency moves the same day
  - transmission horizon: same session; carried-over effects beyond 1 day not detected; invalidation: price moves >2 sd the OPPOSITE way on release day; revision sign flips next month without a price reversal
- **corn / w_yield_rev_pct**: -1.217 (forecast revision (interpretation of NASS/FAS inputs); WASDE 2026-09-11); level {'stocks_to_use': 0.0968, 'yield': 178.5}
  - tested support: {'react': 'SUGGESTIVE'}
  - mechanism: yield revision changes expected production, then ending stocks
  - competing explanations: acreage revisions offset yield; carry-in stock changes; NASS survey-based vs model-based yield (month-dependent method)
  - transmission horizon: same session; invalidation: reaction sign opposite expected for two consecutive August/September reports
- **wheat / w_stu_chg_pp**: +0.0 (forecast revision (interpretation of NASS/FAS inputs); WASDE 2026-09-11); level {'stocks_to_use': 0.3826, 'yield': 47.8}
  - tested support: none (all targets UNSUPPORTED)
  - mechanism: WASDE revision of stocks-to-use for the new marketing year: tighter stocks raise price needed to ration demand
  - competing explanations: revisions already anticipated by private estimates/trade surveys; demand-side revision (exports/ethanol) vs supply-side revision (yield/acres); policy or currency moves the same day
  - transmission horizon: same session; carried-over effects beyond 1 day not detected; invalidation: price moves >2 sd the OPPOSITE way on release day; revision sign flips next month without a price reversal
- **wheat / w_yield_rev_pct**: +0.0 (forecast revision (interpretation of NASS/FAS inputs); WASDE 2026-09-11); level {'stocks_to_use': 0.3826, 'yield': 47.8}
  - tested support: none (all targets UNSUPPORTED)
  - mechanism: yield revision changes expected production, then ending stocks
  - competing explanations: acreage revisions offset yield; carry-in stock changes; NASS survey-based vs model-based yield (month-dependent method)
  - transmission horizon: same session; invalidation: reaction sign opposite expected for two consecutive August/September reports
- **soybeans / w_stu_chg_pp**: -0.259 (forecast revision (interpretation of NASS/FAS inputs); WASDE 2026-09-11); level {'stocks_to_use': 0.0678, 'yield': 52.8}
  - tested support: none (all targets UNSUPPORTED)
  - mechanism: WASDE revision of stocks-to-use for the new marketing year: tighter stocks raise price needed to ration demand
  - competing explanations: revisions already anticipated by private estimates/trade surveys; demand-side revision (exports/ethanol) vs supply-side revision (yield/acres); policy or currency moves the same day
  - transmission horizon: same session; carried-over effects beyond 1 day not detected; invalidation: price moves >2 sd the OPPOSITE way on release day; revision sign flips next month without a price reversal
- **soybeans / w_yield_rev_pct**: +0.19 (forecast revision (interpretation of NASS/FAS inputs); WASDE 2026-09-11); level {'stocks_to_use': 0.0678, 'yield': 52.8}
  - tested support: {'react': 'SUPPORTED'}
  - mechanism: yield revision changes expected production, then ending stocks
  - competing explanations: acreage revisions offset yield; carry-in stock changes; NASS survey-based vs model-based yield (month-dependent method)
  - transmission horizon: same session; invalidation: reaction sign opposite expected for two consecutive August/September reports
- **corn / c_ge_chg_pp**: +0 (observation; Crop Progress 2026-09-28); level {'good_excellent_pct': 57}
  - tested support: none (all targets UNSUPPORTED)
  - mechanism: weekly drop in good+excellent corn/soy condition signals yield risk
  - competing explanations: weather forecast changes already priced before Monday 16:00; ratings noise/rater subjectivity; price move preceding the release (anticipation) rather than following
  - transmission horizon: next session (released after the close Monday); possible days-weeks via yield revisions; invalidation: rating change and next-session return uncorrelated over a full growing season
- **soybeans / c_ge_chg_pp**: +0 (observation; Crop Progress 2026-09-28); level {'good_excellent_pct': 58}
  - tested support: none (all targets UNSUPPORTED)
  - mechanism: weekly drop in good+excellent corn/soy condition signals yield risk
  - competing explanations: weather forecast changes already priced before Monday 16:00; ratings noise/rater subjectivity; price move preceding the release (anticipation) rather than following
  - transmission horizon: next session (released after the close Monday); possible days-weeks via yield revisions; invalidation: rating change and next-session return uncorrelated over a full growing season
- **corn/soybeans / d_belt_d1_chg_pp**: +1.1 (observation; USDM 2026-09-29); level {'belt_d1_pct': 14.58}
  - tested support: none (all targets UNSUPPORTED)
  - mechanism: expanding drought coverage in the corn belt signals yield risk
  - competing explanations: rainfall/forecast information already in price; crop stage (drought matters most in Jun-Aug); latest-vintage map (not contemporaneous)
  - transmission horizon: weekly; effect on prices spread over 1-5 sessions in discovery; invalidation: sign of correlation unstable across growing seasons
  - note: feature defined for Apr-Sep only; outside the window it is context, not a tested signal

## Fuel - what changed (latest releases)

- **distillate / e_dist_stock_surprise_kbbl**: -1088.2 (observation; EIA week ending 2026-09-25); level {'distillate_stocks_kbbl': 105180, 'crude_stocks_ex_spr_kbbl': 427320, 'refinery_utilization_pct': 92.5}
  - tested support: {'react': 'SUPPORTED', 'pre': 'SUGGESTIVE(ANTICIPATION: market moved first)'}
  - mechanism: distillate inventory draw vs seasonal norm tightens diesel supply -> higher HO; diesel is an input to farm operation and freight costs
  - competing explanations: refinery outages vs demand; export flows; crude price moves the same day; API estimate released the day before
  - transmission horizon: release session (10:30 ET); further 1-5 session drift not supported; invalidation: reaction sign reverses for a full year
- **crude / e_crude_stock_surprise_kbbl**: +32.8 (observation; EIA week ending 2026-09-25); level {'distillate_stocks_kbbl': 105180, 'crude_stocks_ex_spr_kbbl': 427320, 'refinery_utilization_pct': 92.5}
  - tested support: {'react': 'SUPPORTED'}
  - mechanism: commercial crude inventory surprise vs seasonal norm moves crude
  - competing explanations: SPR changes, import timing, OPEC news
  - transmission horizon: release session; invalidation: as above
- **distillate / e_util_chg_pp**: -1.5 (observation; EIA week ending 2026-09-25); level {'distillate_stocks_kbbl': 105180, 'crude_stocks_ex_spr_kbbl': 427320, 'refinery_utilization_pct': 92.5}
  - tested support: {'react': 'SUGGESTIVE'}
  - mechanism: refinery utilization change: product supply vs crude demand (direction ambiguous a priori)
  - competing explanations: maintenance seasons, outages, hurricane effects
  - transmission horizon: not established; invalidation: n/a (no directional claim)

## Sources (publication time / reference period / retrieved / units)

- USDA WASDE: published 2026-09-11T12:00:00Z; period marketing year 2026/27; retrieved 2026-10-04T07:19:56+00:00; mil bu, bu/acre; stocks-to-use ratio; forecast (USDA projection); previous-month projection from the same document; https://esmis.nal.usda.gov/sites/default/release-files/796054/wasde0926.txt
- USDA NASS Crop Progress: published 2026-09-28T12:00:00Z; period week ending 2026-09-27; retrieved 2026-10-04T07:19:56+00:00; percent of crop; observation (NASS survey); https://esmis.nal.usda.gov/sites/default/release-files/796082/prog3926.txt
- US Drought Monitor: published 2026-09-29 (Tuesday map; published Thursday 08:30 ET); period map 2026-09-29; retrieved 2026-10-04T07:19:56+00:00; percent area D1+, mean of IA/IL/IN/NE/MN; observation (expert-assessed map); https://usdmdataservices.unl.edu/api/StateStatistics (FIPS 19,17,18,31,27; cumulative)
- EIA Weekly Petroleum Status (bulk PET): published 2026-09-30T10:30 ET (assumed Wednesday schedule); period week ending 2026-09-25; retrieved 2026-10-04T05:42:17+00:00; thousand bbl; percent; observation (weekly survey); https://www.eia.gov/opendata/bulkfiles.php (PET.zip)

## Overnight / intraday context (indicative quotes)

**asia**
- Nikkei 225: 68309.46 (+3.69%) [stale_3d_unverified_cause, 2026-10-02]
- Hang Seng: 23972.29 (-3.19%) [stale_3d_unverified_cause, 2026-10-02]
- CSI 300 (mainland, Shanghai): 4357.62 (+0.29%) [stale_5d_unverified_cause, 2026-09-30]
- KOSPI: 7003.74 (-1.09%) [stale_3d_unverified_cause, 2026-10-02]
- S&P/ASX 200: 8682.10 (+0.20%) [stale_4d_unverified_cause, 2026-10-02]

**europe**
- FTSE 100: 10461.95 (-2.18%) [stale_3d_unverified_cause, 2026-10-02]
- DAX Performance Index: 25231.20 (-0.70%) [stale_3d_unverified_cause, 2026-10-02]
- Euro Stoxx 50: 6238.50 (-1.00%) [stale_3d_unverified_cause, 2026-10-02]

**us_futures**
- S&P 500 futures (ES): 7777.25 (+0.59%) [stale_3d_unverified_cause, 2026-10-02]
- Nasdaq futures (NQ): 31061.75 (+1.47%) [stale_3d_unverified_cause, 2026-10-02]

**commodities_fx**
- WTI Crude (CL): 91.11 (+1.94%) [stale_3d_unverified_cause, 2026-10-02]
- Brent Crude (BZ): 102.25 (-0.33%) [stale_3d_unverified_cause, 2026-10-02]
- Gold (GC): 4162.30 (-0.42%) [stale_3d_unverified_cause, 2026-10-02]
- ICE US Dollar Index, spot/cash (DXY): 101.92 (+0.55%) [stale_3d_unverified_cause, 2026-10-02]

**food_fuel_proxies**
- Corn futures (ZC, front-month continuous): 497.75 (-4.65%) [stale_3d_unverified_cause, 2026-10-02]
- Soybean futures (ZS): 1278.25 (-1.50%) [stale_3d_unverified_cause, 2026-10-02]
- Chicago wheat futures (ZW): 683.00 (-1.41%) [stale_3d_unverified_cause, 2026-10-02]
- Heating oil / ULSD futures (HO): 4.50 (-8.11%) [stale_3d_unverified_cause, 2026-10-02]
- Natural gas futures (NG): 3.04 (+0.80%) [stale_3d_unverified_cause, 2026-10-02]
- Corn ETF proxy (CORN): 18.85 (-4.46%) [stale_3d_unverified_cause, 2026-10-02]
- Agriculture ETF proxy (DBA): 28.21 (-1.16%) [stale_3d_unverified_cause, 2026-10-02]
- Oil ETF proxy (USO): 147.37 (-0.65%) [stale_3d_unverified_cause, 2026-10-02]
- CF Industries (fertilizer equity): 115.01 (+0.30%) [stale_3d_unverified_cause, 2026-10-02]
- Mosaic (fertilizer equity): 21.07 (-8.43%) [stale_3d_unverified_cause, 2026-10-02]

## Coverage and data quality

- quotes available 24/24; unavailable: none; stale: ['^N225', '^HSI', '000300.SS', '^KS11', '^AXJO', '^FTSE', '^GDAXI', '^STOXX50E', 'ES=F', 'NQ=F', 'CL=F', 'BZ=F', 'GC=F', 'DX-Y.NYB', 'ZC=F', 'ZS=F', 'ZW=F', 'HO=F', 'NG=F', 'CORN', 'DBA', 'USO', 'CF', 'MOS']
- not covered: FAO Food Price Index (page blocked: bot protection, not circumvented)
- not covered: IEA Oil Market Report (blocked)
- not covered: USDA FAS export sales (no accessible machine-readable page found)
- not covered: freight/shipping indices
- not covered: fertilizer spot prices (World Bank monthly, latest-vintage only)
