"""Re-run the frozen 77-test family with roll-artifact exclusion (sensitivity only; spec, thresholds, support rule and BH family are unchanged)."""
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ffmod.evaluate import run, build_all
spec = json.load(open("features/FEATURE_SPEC_v1.json")); ev = build_all(); out = {}
for name, kw in (("A_expiry_window", dict(roll=True)), ("B_outlier_4mad", dict(outlier=True)), ("C_both", dict(roll=True, outlier=True))):
    out[name] = {sp: run(spec, sp, n_perm=2000, events=ev, **kw) for sp in ("discovery", "validation", "holdout")}
json.dump(out, open("results/roll_recheck_results.json", "w"), indent=1); print("done")
