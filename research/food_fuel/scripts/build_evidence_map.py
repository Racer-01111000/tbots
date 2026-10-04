"""Joins static mechanism text with the frozen-test classification -> features/EVIDENCE_MAP.json + docs/EVIDENCE_MAP.md."""
import json
C = json.load(open("results/classification.json"))
R = json.load(open("results/frozen_test_results.json"))["splits"]
MECH = {
 "w_stu_chg_pp": dict(mechanism="WASDE revision of stocks-to-use for the new marketing year: tighter stocks raise price needed to ration demand", horizon="same session; carried-over effects beyond 1 day not detected",
   competing=["revisions already anticipated by private estimates/trade surveys", "demand-side revision (exports/ethanol) vs supply-side revision (yield/acres)", "policy or currency moves the same day"],
   counter="large revisions with small price moves occur when the trade had pre-positioned (anticipation test)", invalid="price moves >2 sd the OPPOSITE way on release day; revision sign flips next month without a price reversal"),
 "w_yield_rev_pct": dict(mechanism="yield revision changes expected production, then ending stocks", horizon="same session", competing=["acreage revisions offset yield", "carry-in stock changes", "NASS survey-based vs model-based yield (month-dependent method)"],
   counter="months without a revision give zero-valued ties; late-season revisions are smaller and better anticipated", invalid="reaction sign opposite expected for two consecutive August/September reports"),
 "w_prod_rev_pct": dict(mechanism="production revision (yield x harvested area)", horizon="same session", competing=["same as yield revision", "harvested-area changes"], counter="area-driven revisions are priced differently from yield-driven ones", invalid="as yield revision"),
 "c_ge_chg_pp": dict(mechanism="weekly drop in good+excellent corn/soy condition signals yield risk", horizon="next session (released after the close Monday); possible days-weeks via yield revisions", competing=["weather forecast changes already priced before Monday 16:00", "ratings noise/rater subjectivity", "price move preceding the release (anticipation) rather than following"],
   counter="2012 ratings fell with prices rising ahead of releases", invalid="rating change and next-session return uncorrelated over a full growing season"),
 "d_belt_d1_chg_pp": dict(mechanism="expanding drought coverage in the corn belt signals yield risk", horizon="weekly; effect on prices spread over 1-5 sessions in discovery", competing=["rainfall/forecast information already in price", "crop stage (drought matters most in Jun-Aug)", "latest-vintage map (not contemporaneous)"],
   counter="drought maps can worsen after rains arrive (lagging indicator)", invalid="sign of correlation unstable across growing seasons"),
 "e_dist_stock_surprise_kbbl": dict(mechanism="distillate inventory draw vs seasonal norm tightens diesel supply -> higher HO; diesel is an input to farm operation and freight costs", horizon="release session (10:30 ET); further 1-5 session drift not supported", competing=["refinery outages vs demand; export flows", "crude price moves the same day", "API estimate released the day before"],
   counter="large draws can be priced in via API report", invalid="reaction sign reverses for a full year"),
 "e_crude_stock_surprise_kbbl": dict(mechanism="commercial crude inventory surprise vs seasonal norm moves crude", horizon="release session", competing=["SPR changes, import timing, OPEC news"], counter="weekly noise dominates in low-volatility periods", invalid="as above"),
 "e_util_chg_pp": dict(mechanism="refinery utilization change: product supply vs crude demand (direction ambiguous a priori)", horizon="not established", competing=["maintenance seasons, outages, hurricane effects"], counter="no prior sign", invalid="n/a (no directional claim)"),
 "t_ho_ret20": dict(mechanism="diesel/fuel cost run-up raises farm and freight costs, affecting planting/application and delivered food costs with a lag", horizon="20-60 sessions (tested)", competing=["common commodity-cycle factor driving both HO and grains", "demand destruction", "substitution (biofuel)"],
   counter="2020: fuel collapse did not lower grain prices proportionally", invalid="no relation across validation and hold-out"),
 "t_cf_rel_spy_ret20": dict(mechanism="fertilizer-equity outperformance proxies fertilizer affordability stress/high nitrogen prices that precede grain price effects", horizon="20-60 sessions (tested)", competing=["equity beta/market effects", "company-specific news", "grain price already driving CF"], counter="equity is a proxy, not fertilizer spot", invalid="no relation across validation and hold-out"),
}
by = {}
for c in C: by.setdefault((c["feature"], c["commodity"]), []).append(c)
em = []
for (f, com), rows in by.items():
    sup = {r["target"]: r["label"] for r in rows if r["label"] != "UNSUPPORTED"}
    tested = {r["target"]: {"rho_val": r["rho_val"], "q_val": r["q_val"], "rho_hold": r["rho_hold"]} for r in rows}
    em.append({"feature": f, "commodity": com, **MECH[f], "support_by_target": sup or "none (all targets UNSUPPORTED)", "validation_holdout_detail": tested})
json.dump(em, open("features/EVIDENCE_MAP.json", "w"), indent=1)
L = ["# Evidence map (frozen spec FEATURE_SPEC_v1; validation 2013-2018, hold-out 2019-2022)", "",
     "Support labels: SUPPORTED / SUGGESTIVE / UNSUPPORTED per the frozen rule. 'react' = same-session reaction, 'driftH' = after-reaction drift, 'pre' = anticipation (market moved first).", ""]
for e in em:
    L += [f"## {e['feature']} - {e['commodity']}", f"- mechanism: {e['mechanism']}", f"- horizon: {e['horizon']}",
          f"- support: {e['support_by_target']}", f"- competing explanations: {'; '.join(e['competing'])}", f"- counterexamples: {e['counter']}", f"- invalidation: {e['invalid']}", ""]
open("docs/EVIDENCE_MAP.md", "w").write("\n".join(L)); print(len(em), "entries")
