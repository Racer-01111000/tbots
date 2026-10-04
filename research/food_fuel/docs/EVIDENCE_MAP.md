# Evidence map (frozen spec FEATURE_SPEC_v1; validation 2013-2018, hold-out 2019-2022)

Support labels: SUPPORTED / SUGGESTIVE / UNSUPPORTED per the frozen rule. 'react' = same-session reaction, 'driftH' = after-reaction drift, 'pre' = anticipation (market moved first).

## w_stu_chg_pp - corn
- mechanism: WASDE revision of stocks-to-use for the new marketing year: tighter stocks raise price needed to ration demand
- horizon: same session; carried-over effects beyond 1 day not detected
- support: {'react': 'SUGGESTIVE'}
- competing explanations: revisions already anticipated by private estimates/trade surveys; demand-side revision (exports/ethanol) vs supply-side revision (yield/acres); policy or currency moves the same day
- counterexamples: large revisions with small price moves occur when the trade had pre-positioned (anticipation test)
- invalidation: price moves >2 sd the OPPOSITE way on release day; revision sign flips next month without a price reversal

## w_yield_rev_pct - corn
- mechanism: yield revision changes expected production, then ending stocks
- horizon: same session
- support: {'react': 'SUGGESTIVE'}
- competing explanations: acreage revisions offset yield; carry-in stock changes; NASS survey-based vs model-based yield (month-dependent method)
- counterexamples: months without a revision give zero-valued ties; late-season revisions are smaller and better anticipated
- invalidation: reaction sign opposite expected for two consecutive August/September reports

## w_prod_rev_pct - corn
- mechanism: production revision (yield x harvested area)
- horizon: same session
- support: none (all targets UNSUPPORTED)
- competing explanations: same as yield revision; harvested-area changes
- counterexamples: area-driven revisions are priced differently from yield-driven ones
- invalidation: as yield revision

## w_stu_chg_pp - soybeans
- mechanism: WASDE revision of stocks-to-use for the new marketing year: tighter stocks raise price needed to ration demand
- horizon: same session; carried-over effects beyond 1 day not detected
- support: none (all targets UNSUPPORTED)
- competing explanations: revisions already anticipated by private estimates/trade surveys; demand-side revision (exports/ethanol) vs supply-side revision (yield/acres); policy or currency moves the same day
- counterexamples: large revisions with small price moves occur when the trade had pre-positioned (anticipation test)
- invalidation: price moves >2 sd the OPPOSITE way on release day; revision sign flips next month without a price reversal

## w_yield_rev_pct - soybeans
- mechanism: yield revision changes expected production, then ending stocks
- horizon: same session
- support: {'react': 'SUPPORTED'}
- competing explanations: acreage revisions offset yield; carry-in stock changes; NASS survey-based vs model-based yield (month-dependent method)
- counterexamples: months without a revision give zero-valued ties; late-season revisions are smaller and better anticipated
- invalidation: reaction sign opposite expected for two consecutive August/September reports

## w_prod_rev_pct - soybeans
- mechanism: production revision (yield x harvested area)
- horizon: same session
- support: {'react': 'SUPPORTED'}
- competing explanations: same as yield revision; harvested-area changes
- counterexamples: area-driven revisions are priced differently from yield-driven ones
- invalidation: as yield revision

## w_stu_chg_pp - wheat
- mechanism: WASDE revision of stocks-to-use for the new marketing year: tighter stocks raise price needed to ration demand
- horizon: same session; carried-over effects beyond 1 day not detected
- support: none (all targets UNSUPPORTED)
- competing explanations: revisions already anticipated by private estimates/trade surveys; demand-side revision (exports/ethanol) vs supply-side revision (yield/acres); policy or currency moves the same day
- counterexamples: large revisions with small price moves occur when the trade had pre-positioned (anticipation test)
- invalidation: price moves >2 sd the OPPOSITE way on release day; revision sign flips next month without a price reversal

## w_yield_rev_pct - wheat
- mechanism: yield revision changes expected production, then ending stocks
- horizon: same session
- support: none (all targets UNSUPPORTED)
- competing explanations: acreage revisions offset yield; carry-in stock changes; NASS survey-based vs model-based yield (month-dependent method)
- counterexamples: months without a revision give zero-valued ties; late-season revisions are smaller and better anticipated
- invalidation: reaction sign opposite expected for two consecutive August/September reports

## w_prod_rev_pct - wheat
- mechanism: production revision (yield x harvested area)
- horizon: same session
- support: none (all targets UNSUPPORTED)
- competing explanations: same as yield revision; harvested-area changes
- counterexamples: area-driven revisions are priced differently from yield-driven ones
- invalidation: as yield revision

## c_ge_chg_pp - corn
- mechanism: weekly drop in good+excellent corn/soy condition signals yield risk
- horizon: next session (released after the close Monday); possible days-weeks via yield revisions
- support: none (all targets UNSUPPORTED)
- competing explanations: weather forecast changes already priced before Monday 16:00; ratings noise/rater subjectivity; price move preceding the release (anticipation) rather than following
- counterexamples: 2012 ratings fell with prices rising ahead of releases
- invalidation: rating change and next-session return uncorrelated over a full growing season

## d_belt_d1_chg_pp - corn
- mechanism: expanding drought coverage in the corn belt signals yield risk
- horizon: weekly; effect on prices spread over 1-5 sessions in discovery
- support: none (all targets UNSUPPORTED)
- competing explanations: rainfall/forecast information already in price; crop stage (drought matters most in Jun-Aug); latest-vintage map (not contemporaneous)
- counterexamples: drought maps can worsen after rains arrive (lagging indicator)
- invalidation: sign of correlation unstable across growing seasons

## c_ge_chg_pp - soybeans
- mechanism: weekly drop in good+excellent corn/soy condition signals yield risk
- horizon: next session (released after the close Monday); possible days-weeks via yield revisions
- support: none (all targets UNSUPPORTED)
- competing explanations: weather forecast changes already priced before Monday 16:00; ratings noise/rater subjectivity; price move preceding the release (anticipation) rather than following
- counterexamples: 2012 ratings fell with prices rising ahead of releases
- invalidation: rating change and next-session return uncorrelated over a full growing season

## d_belt_d1_chg_pp - soybeans
- mechanism: expanding drought coverage in the corn belt signals yield risk
- horizon: weekly; effect on prices spread over 1-5 sessions in discovery
- support: none (all targets UNSUPPORTED)
- competing explanations: rainfall/forecast information already in price; crop stage (drought matters most in Jun-Aug); latest-vintage map (not contemporaneous)
- counterexamples: drought maps can worsen after rains arrive (lagging indicator)
- invalidation: sign of correlation unstable across growing seasons

## e_dist_stock_surprise_kbbl - distillate
- mechanism: distillate inventory draw vs seasonal norm tightens diesel supply -> higher HO; diesel is an input to farm operation and freight costs
- horizon: release session (10:30 ET); further 1-5 session drift not supported
- support: {'react': 'SUPPORTED', 'pre': 'SUGGESTIVE(ANTICIPATION: market moved first)'}
- competing explanations: refinery outages vs demand; export flows; crude price moves the same day; API estimate released the day before
- counterexamples: large draws can be priced in via API report
- invalidation: reaction sign reverses for a full year

## e_crude_stock_surprise_kbbl - crude
- mechanism: commercial crude inventory surprise vs seasonal norm moves crude
- horizon: release session
- support: {'react': 'SUPPORTED'}
- competing explanations: SPR changes, import timing, OPEC news
- counterexamples: weekly noise dominates in low-volatility periods
- invalidation: as above

## e_util_chg_pp - distillate
- mechanism: refinery utilization change: product supply vs crude demand (direction ambiguous a priori)
- horizon: not established
- support: {'react': 'SUGGESTIVE'}
- competing explanations: maintenance seasons, outages, hurricane effects
- counterexamples: no prior sign
- invalidation: n/a (no directional claim)

## t_ho_ret20 - corn
- mechanism: diesel/fuel cost run-up raises farm and freight costs, affecting planting/application and delivered food costs with a lag
- horizon: 20-60 sessions (tested)
- support: none (all targets UNSUPPORTED)
- competing explanations: common commodity-cycle factor driving both HO and grains; demand destruction; substitution (biofuel)
- counterexamples: 2020: fuel collapse did not lower grain prices proportionally
- invalidation: no relation across validation and hold-out

## t_cf_rel_spy_ret20 - corn
- mechanism: fertilizer-equity outperformance proxies fertilizer affordability stress/high nitrogen prices that precede grain price effects
- horizon: 20-60 sessions (tested)
- support: {'drift60': 'SUGGESTIVE'}
- competing explanations: equity beta/market effects; company-specific news; grain price already driving CF
- counterexamples: equity is a proxy, not fertilizer spot
- invalidation: no relation across validation and hold-out
