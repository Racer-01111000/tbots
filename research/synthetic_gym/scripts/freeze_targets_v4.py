"""Freezes the LENGTH-MATCHED calibration targets for experiment V4 from the authorized development lane ONLY (2007-02..2018-12). Run BEFORE any V4 world is evaluated.
Reference for a per-world statistic over the 1,256 evaluation sessions = distribution of the same statistic over every rolling 1,256-session historical window (step 21).
Overlapping windows are strongly dependent (about 2 independent windows in the lane); this limits power and is recorded, not hidden."""
import hashlib, json, math, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent; REPO = HERE.parents[1]
for p in (str(HERE), str(REPO / "scripts"), str(REPO / "scripts" / "lib"), str(REPO / "experiments" / "sam_dev_staging_20261001")): sys.path.insert(0, p)
from gym import calibration as C, stats as st
DATA = REPO / "data" / "normalized"; rows = C.load_dev(DATA); L, STEP = 1256, 21
H = {s: [math.log((rows[s][t]["c"] + rows[s][t]["div"]) / rows[s][t - 1]["c"]) for t in range(1, len(rows[s]))] for s in C.SYMBOLS}
n = len(H["SPY"]); targets = {"window_sessions": L, "step": STEP, "n_history_sessions": n, "n_windows": len(range(0, n - L + 1, STEP)), "assets": {}, "corr": {}, "rolling_corr_spy_tlt": {}}
for s in C.SYMBOLS:
    per = [C.summarize_series({k: H[k][i:i + L] for k in C.SYMBOLS})["asset"][s] for i in range(0, n - L + 1, STEP)]
    targets["assets"][s] = {k: {"median": st.quantile([p[k] for p in per], .5), "p05": st.quantile([p[k] for p in per], .05), "p95": st.quantile([p[k] for p in per], .95)} for k in ("ann_vol", "ex_kurt", "skew", "ac1_abs", "ac1_ret", "mean_ann", "worst_day")}
wins = [C.summarize_series({k: H[k][i:i + L] for k in C.SYMBOLS}) for i in range(0, n - L + 1, STEP)]
for pair in wins[0]["corr"]: targets["corr"][pair] = {"median": st.quantile([w["corr"][pair] for w in wins], .5)}
for k in ("min", "median", "max"): targets["rolling_corr_spy_tlt"][k] = {"median": st.quantile([w["rolling_corr_spy_tlt"][k] for w in wins], .5)}
targets["tolerances_unchanged_from_v1"] = {"relative_on_median": 0.35, "absolute_corr_skew_ac": 0.25, "rule": "pass if the historical window-median lies inside the synthetic [P05,P95] OR the synthetic median is within tolerance of it"}
targets["inputs"] = {"dev_data_digest": C.dev_data_digest(DATA), "dev_range": [C.DEV_START, C.DEV_END], "no_post_development_or_reserved_data": True}
raw = json.dumps(targets, sort_keys=True, indent=1); (HERE / "configs" / "calibration_targets_v4.json").write_text(raw)
print("frozen targets sha256", hashlib.sha256(raw.encode()).hexdigest(), "windows", targets["n_windows"])
