import sys, json, hashlib, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ffmod.evaluate import run, build_all
raw = open("features/FEATURE_SPEC_v1.json", "rb").read(); spec = json.loads(raw)
ev = build_all(); res = {"spec_sha256": hashlib.sha256(raw).hexdigest(), "splits": {}}
for sp in ("discovery", "validation", "holdout"):
    res["splits"][sp] = run(spec, sp, n_perm=2000, events=ev)
json.dump(res, open("results/frozen_test_results.json", "w"), indent=1)
for sp, rows in res["splits"].items():
    ok = [r for r in rows if r["p"] is not None]
    print(sp, len(rows), "tested", len(ok), "FDR-sig", sum(1 for r in ok if r.get("fdr_significant")), "nominal p<.05", sum(1 for r in ok if r["p"] < .05))
