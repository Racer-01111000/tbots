"""DEVELOPMENT-ONLY diagnostic: is the documented 'thin tails / negative skew' a generator defect or an estimator/aggregation mismatch?
Estimators (identical to gym.stats, applied to daily total log returns): skew = mean(((x-m)/s)^3), excess kurtosis = mean(((x-m)/s)^4)-3, s = sample sd (n-1).
Reference sets: H_full = whole development lane (2,996 sessions); H_win_L = every rolling window of L sessions (step 21; windows overlap, so ~2 independent);
H_pool = the historical blocks that the TRAINING pool can resample (what a training world can actually reproduce); S = synthetic engineering worlds (tag ENG-V4DIAG, never a
training/validation/sealed seed), per-world statistic over (a) the full 1656-session path and (b) the 1256 evaluation sessions only; plus pooled synthetic.
No validation/sealed world, no post-development data is read."""
import json, math, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent; REPO = HERE.parents[1]
for p in (str(HERE), str(REPO / "scripts"), str(REPO / "scripts" / "lib"), str(REPO / "experiments" / "sam_dev_staging_20261001")): sys.path.insert(0, p)
from gym import calibration as C, stats as st, validation as V
import gym.generator_v2 as G
G.CFG["jump_neg_prob"] = 0.70; G.JUMP_NEG_PROB = 0.70      # v1-equivalent setting for the reproduction
cal = C.build(REPO / "data" / "normalized"); rows = C.load_dev(REPO / "data" / "normalized")
H = {}
for s in C.SYMBOLS:
    R = rows[s]; H[s] = [math.log((R[t]["c"] + R[t]["div"]) / R[t - 1]["c"]) for t in range(1, len(R))]
n = len(H["SPY"]); B = cal["block_len"]
def sk(x): return st.skew(x), st.ex_kurt(x)
def pool_series(r, name):
    out = []
    for k in cal["pools"][name]: out += r[k * B:(k + 1) * B]
    return out
res = {"estimator": "daily total log returns; skew=m3/s^3, excess kurt=m4/s^4-3 (s with n-1)", "n_sessions_history": n, "assets": {}}
ws = [G.generate_world(G.WorldSpec("ENG-V4DIAG", "engineering", f, i), cal) for f in G.FAMILIES for i in range(700, 710)]
wr = [V.world_returns(w) for w in ws]
for s in ("SPY", "EEM", "EFA", "GLD", "VNQ", "TLT"):
    full = sk(H[s]); wins = {}
    for L in (1256, 1655):
        v = [sk(H[s][i:i + L]) for i in range(0, n - L + 1, 21)]
        wins[L] = {"skew_p05_med_p95": [st.quantile([a for a, _ in v], q) for q in (.05, .5, .95)], "kurt_p05_med_p95": [st.quantile([b for _, b in v], q) for q in (.05, .5, .95)], "n_windows": len(v)}
    pool = sk(pool_series(H[s], "training"))
    syn_full = [sk(r[s]) for r in wr]; syn_eval = [sk(r[s][-1256:]) for r in wr]
    pooled = sk([x for r in wr for x in r[s]])
    q = lambda xs: [round(st.quantile(xs, p), 2) for p in (.05, .5, .95)]
    res["assets"][s] = {"hist_full": [round(full[0], 2), round(full[1], 1)], "hist_windows": wins, "hist_training_pool_blocks": [round(pool[0], 2), round(pool[1], 1)],
                        "synth_per_world_full1656": {"skew": q([a for a, _ in syn_full]), "kurt": q([b for _, b in syn_full])},
                        "synth_per_world_eval1256": {"skew": q([a for a, _ in syn_eval]), "kurt": q([b for _, b in syn_eval])},
                        "synth_pooled": [round(pooled[0], 2), round(pooled[1], 1)]}
json.dump(res, open(HERE / "docs" / "tail_diagnosis_v4.json", "w"), indent=1)
for s, v in res["assets"].items():
    print(s, "HIST full", v["hist_full"], "| train-pool blocks", v["hist_training_pool_blocks"], "| windows1256 kurt", [round(x, 1) for x in v["hist_windows"][1256]["kurt_p05_med_p95"]],
          "skew", [round(x, 2) for x in v["hist_windows"][1256]["skew_p05_med_p95"]])
    print("   SYN per-world full kurt", v["synth_per_world_full1656"]["kurt"], "skew", v["synth_per_world_full1656"]["skew"], "| eval1256 kurt", v["synth_per_world_eval1256"]["kurt"], "| pooled", v["synth_pooled"])
