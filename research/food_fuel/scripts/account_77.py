"""Full accounting of the 77 frozen tests: original results, frozen thresholds, and the roll/outlier sensitivity labels. Thresholds are NOT modified."""
import json
spec = json.load(open("features/FEATURE_SPEC_v1.json"))
orig = json.load(open("results/frozen_test_results.json"))["splits"]; rc = json.load(open("results/roll_recheck_results.json"))
key = lambda r: (r["feature"], r["commodity"], r["target"])


def label(R):
    D, V, H = ({key(r): r for r in R[s]} for s in ("discovery", "validation", "holdout")); out = {}
    for k, v in V.items():
        d, h = D[k], H[k]; exp = v.get("expected_sign") or 0
        same = lambda a, b: a["rho"] is not None and b["rho"] is not None and a["rho"] * b["rho"] > 0
        if v.get("fdr_significant") and same(v, h) and h["p"] is not None and h["p"] / 2 < 0.10 and (exp == 0 or v["rho"] * exp > 0): lab = "SUPPORTED"
        elif v["p"] is not None and v["p"] < .05 and same(d, v): lab = "SUGGESTIVE"
        elif v["rho"] is None: lab = "UNTESTABLE(n<%d)" % spec["min_events"]
        else: lab = "UNSUPPORTED"
        if k[2] == "pre" and lab in ("SUPPORTED", "SUGGESTIVE"): lab += "[anticipation]"
        out[k] = lab
    return out
L0 = label(orig); LA, LB, LC = (label(rc[n]) for n in ("A_expiry_window", "B_outlier_4mad", "C_both"))
f = lambda x: "-" if x is None else f"{x:.3f}"
lines = ["# All 77 frozen tests (FEATURE_SPEC_v1, sha256 c4d3461f...)", "",
 "**Frozen thresholds (never changed):** min events 30 per split; Spearman rank correlation; 2000 within-calendar-month permutations (seed 20261004); Benjamini-Hochberg q=0.10 across all 77 tests per split; "
 "SUPPORTED = FDR-significant in validation with the expected sign AND same sign in hold-out with one-sided p<0.10; SUGGESTIVE = validation nominal p<0.05 with the discovery sign; otherwise UNSUPPORTED. "
 "Splits: discovery 2007-12, validation 2013-18, hold-out 2019-22. Sensitivity columns: A = exclude event-target pairs whose return window touches a contract-expiry roll session (expiry..expiry+2; big moves empirically cluster at expiry+1); B = drop >4 robust-sigma target outliers; C = both.", "",
 "| feature | commodity | target | n val | rho disc | rho val | q val | rho hold | p hold | original | A roll | B outlier | C both |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
D = {key(r): r for r in orig["discovery"]}; V = {key(r): r for r in orig["validation"]}; H = {key(r): r for r in orig["holdout"]}
for k in V:
    lines.append(f"| {k[0]} | {k[1]} | {k[2]} | {V[k]['n']} | {f(D[k]['rho'])} | {f(V[k]['rho'])} | {f(V[k].get('q'))} | {f(H[k]['rho'])} | {f(H[k]['p'])} | {L0[k]} | {LA[k]} | {LB[k]} | {LC[k]} |")
count = lambda L: {lab: sum(1 for v in L.values() if v == lab) for lab in sorted(set(L.values()))}
lines += ["", "## Counts", f"- original: {count(L0)}", f"- A roll-excluded: {count(LA)}", f"- B outlier-excluded: {count(LB)}", f"- C both: {count(LC)}", ""]
four = [("e_dist_stock_surprise_kbbl", "distillate", "react"), ("e_crude_stock_surprise_kbbl", "crude", "react"), ("w_yield_rev_pct", "soybeans", "react"), ("w_prod_rev_pct", "soybeans", "react")]
lines += ["## The four originally SUPPORTED findings after roll/outlier exclusion", "", "| finding | original | A | B | C | n val (A) | rho val (A) | q val (A) | rho hold (A) | p hold (A) |", "|---|---|---|---|---|---|---|---|---|---|"]
VA = {key(r): r for r in rc["A_expiry_window"]["validation"]}; HA = {key(r): r for r in rc["A_expiry_window"]["holdout"]}
for k in four:
    lines.append(f"| {k[0]} {k[1]} {k[2]} | {L0[k]} | {LA[k]} | {LB[k]} | {LC[k]} | {VA[k]['n']} | {f(VA[k]['rho'])} | {f(VA[k].get('q'))} | {f(HA[k]['rho'])} | {f(HA[k]['p'])} |")
lines += ["", "## Drift targets on rolling futures", "Any drift5/drift20/drift60 window on HO/CL/ZS/ZC/ZW almost always contains a roll session; under variant A most drift tests lose a large share of events or become UNTESTABLE. Post-release drift on continuous futures is therefore UNVERIFIED rather than disproved."]
open("docs/ALL_77_TESTS.md", "w").write("\n".join(lines)); print("\n".join(lines[-18:]))
json.dump({"original": {"|".join(k): v for k, v in L0.items()}, "A": {"|".join(k): v for k, v in LA.items()}, "B": {"|".join(k): v for k, v in LB.items()}, "C": {"|".join(k): v for k, v in LC.items()}}, open("results/labels_original_and_sensitivity.json", "w"), indent=1)
