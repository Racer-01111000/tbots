"""Generator admission report for experiment V4 on FRESH engineering worlds (tag ENG-V4FROZEN, indices 800-819 x 6 families). Development-only."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent; REPO = HERE.parents[1]
for p in (str(HERE), str(REPO / "scripts"), str(REPO / "scripts" / "lib"), str(REPO / "experiments" / "sam_dev_staging_20261001")): sys.path.insert(0, p)
from gym import calibration as C, evaluation as EV, generator_v3 as G, strategies as S, validation as V0, validation_v4 as V4, stats as st
tag = sys.argv[1] if len(sys.argv) > 1 else "ENG-V4B"
GENMOD = "generator_v3"
cal = C.build(REPO / "data" / "normalized")
ws = [G.generate_world(G.WorldSpec(tag, "engineering", f, i), cal) for f in G.FAMILIES for i in range(800, 820)]
refs = S.load_references(); strat = S.strategy_for("sam", refs["sam"]["family"], refs["sam"]["genome"])    # the most active reference (short rebalance cadence) so outages/halts can overlap pending orders
rep = {"tag": tag, "generator": {"version": G.GENERATOR_VERSION, "jump_neg_prob": G.JUMP_NEG_PROB, "note": "jump_neg_prob 0.55 was selected AFTER the v1 drawdown check missed (post-hoc parameter choice, disclosed); it is evaluated here on fresh worlds under the unchanged original tail bands"},
       "window_matched_statistics": V4.window_validate(ws), "tail_checks_original_bands": V0.tail_checks(ws), "structure": V4.structure_checks(ws), "family_exercise": V4.family_exercise(ws),
       "stress_effective": V4.stress_effective(ws[::5], strat, EV.STRESS)}
dd = [st.max_drawdown_from_levels(w.adjuster.history("SPY", w.n - 1, w.n)[w.n_warmup:]) for w in ws]
import random; rng = random.Random(1); boots = sorted(st.quantile([rng.choice(dd) for _ in dd], .5) for _ in range(2000))
rep["spy_5y_maxdd_median"] = {"value": st.quantile(dd, .5), "bootstrap95": [boots[50], boots[1949]], "original_band": V0.TAIL_CRITERIA["spy_5y_maxdd_median"]}
rep["ALL_GATES_PASS"] = bool(rep["tail_checks_original_bands"]["n_pass"] == rep["tail_checks_original_bands"]["n"] and rep["structure"]["pass"] and rep["family_exercise"]["pass"] and rep["stress_effective"]["pass"] and not rep["window_matched_statistics"]["failures"])
out = HERE / "docs" / f"generator_v4_validation_{tag}.json"; out.write_text(json.dumps(rep, indent=1, default=str))
w = rep["window_matched_statistics"]
print("window-matched", w["n_pass"], "/", w["n_stats"], w["failures"]); print("tail", rep["tail_checks_original_bands"]["n_pass"], "/", rep["tail_checks_original_bands"]["n"], rep["tail_checks_original_bands"]["failures"])
print("structure", rep["structure"]["pass"], rep["structure"]["n_violations"], rep["structure"]["violations"][:3]); print("family", rep["family_exercise"]["pass"], {k: round(v["share_exercised"], 2) for k, v in rep["family_exercise"]["rows"].items()}, rep["family_exercise"]["event_kind_sets_distinct_across_families"])
print("stress", rep["stress_effective"]["pass"], rep["stress_effective"]["counts"]); print("dd median", rep["spy_5y_maxdd_median"]); print("ALL_GATES_PASS", rep["ALL_GATES_PASS"])
