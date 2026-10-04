"""V5 generator admission: ONE evaluation on FRESH development-only engineering worlds (tag ENG-V5FROZEN, indices 800-819 x 6 families; never generated before this run).
Writes the report AND configs/ADMISSION_V5.json from the result; the admission file is never hand-edited. Reserved validation/sealed worlds are never generated here."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent; REPO = HERE.parents[1]
for p in (str(HERE), str(REPO / "scripts"), str(REPO / "scripts" / "lib"), str(REPO / "experiments" / "sam_dev_staging_20261001")): sys.path.insert(0, p)
from gym import calibration as C, evaluation as EV, generator_v4 as G, strategies as S, validation as V0, validation_v4 as V4, stats as st
tag = sys.argv[1] if len(sys.argv) > 1 else "ENG-V5FROZEN"
GENMOD = "generator_v4"
cal = C.build(REPO / "data" / "normalized")
ws = [G.generate_world(G.WorldSpec(tag, "engineering", f, i), cal) for f in G.FAMILIES for i in range(800, 820)]
refs = S.load_references(); strat = S.strategy_for("sam", refs["sam"]["family"], refs["sam"]["genome"])    # the most active reference (short rebalance cadence) so outages/halts can overlap pending orders
rep = {"tag": tag, "generator": {"version": G.GENERATOR_VERSION, "jump_neg_prob": G.JUMP_NEG_PROB, "note": "gen-v4 parameters (anchor min severity, SPY/VNQ event scale, VNQ variance, GLD asymmetric jumps, jump_neg_prob 0.55) were SELECTED against gate statistics on development probe worlds (ENG-V4B, ENG-V5PROBE-*); this run is the single confirmation on never-seen worlds under unchanged bands and tolerances"},
       "window_matched_statistics": V4.window_validate(ws), "tail_checks_original_bands": V0.tail_checks(ws), "structure": V4.structure_checks(ws), "family_exercise": V4.family_exercise(ws),
       "stress_effective": V4.stress_effective(ws[::5], strat, EV.STRESS)}
dd = [st.max_drawdown_from_levels(w.adjuster.history("SPY", w.n - 1, w.n)[w.n_warmup:]) for w in ws]
import random; rng = random.Random(1); boots = sorted(st.quantile([rng.choice(dd) for _ in dd], .5) for _ in range(2000))
rep["spy_5y_maxdd_median"] = {"value": st.quantile(dd, .5), "bootstrap95": [boots[50], boots[1949]], "original_band": V0.TAIL_CRITERIA["spy_5y_maxdd_median"]}
rep["ALL_GATES_PASS"] = bool(rep["tail_checks_original_bands"]["n_pass"] == rep["tail_checks_original_bands"]["n"] and rep["structure"]["pass"] and rep["family_exercise"]["pass"] and rep["stress_effective"]["pass"] and not rep["window_matched_statistics"]["failures"])
out = HERE / "docs" / f"generator_v5_validation_{tag}.json"; raw = json.dumps(rep, indent=1, default=str); out.write_text(raw)
import hashlib
failed = []
if rep["tail_checks_original_bands"]["failures"]: failed.append({"gate": "original tail band", "failures": rep["tail_checks_original_bands"]["failures"], "spy_5y_maxdd_median": rep["spy_5y_maxdd_median"]})
if not rep["family_exercise"]["pass"]: failed.append({"gate": "declared family condition exercised in >=90% of worlds", "shares": {k: v["share_exercised"] for k, v in rep["family_exercise"]["rows"].items()}})
if rep["window_matched_statistics"]["failures"]: failed.append({"gate": "length-matched statistics (tolerances unchanged)", "failures": rep["window_matched_statistics"]["failures"]})
if not rep["structure"]["pass"]: failed.append({"gate": "structure", "violations": rep["structure"]["violations"][:5]})
if not rep["stress_effective"]["pass"]: failed.append({"gate": "execution stress effectiveness"})
(HERE / "configs" / "ADMISSION_V5.json").write_text(json.dumps({"experiment_id": "SYNGYM-20261005-V5", "generator_admission_pass": bool(rep["ALL_GATES_PASS"]), "report": f"docs/generator_v5_validation_{tag}.json",
    "report_sha256": hashlib.sha256(raw.encode()).hexdigest(), "failed_gates": failed, "note": "written by scripts/validate_generator_v5.py from the report; do not edit by hand"}, indent=1))
w = rep["window_matched_statistics"]
print("window-matched", w["n_pass"], "/", w["n_stats"], w["failures"]); print("tail", rep["tail_checks_original_bands"]["n_pass"], "/", rep["tail_checks_original_bands"]["n"], rep["tail_checks_original_bands"]["failures"])
print("structure", rep["structure"]["pass"], rep["structure"]["n_violations"], rep["structure"]["violations"][:3]); print("family", rep["family_exercise"]["pass"], {k: round(v["share_exercised"], 2) for k, v in rep["family_exercise"]["rows"].items()}, rep["family_exercise"]["event_kind_sets_distinct_across_families"])
print("stress", rep["stress_effective"]["pass"], rep["stress_effective"]["counts"]); print("dd median", rep["spy_5y_maxdd_median"]); print("ALL_GATES_PASS", rep["ALL_GATES_PASS"])
