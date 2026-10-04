import json
R = json.load(open("results/frozen_test_results.json"))["splits"]
key = lambda r: (r["feature"], r["commodity"], r["target"])
D, V, H = ({key(r): r for r in R[s]} for s in ("discovery", "validation", "holdout"))
out = []
for k, v in V.items():
    d, h = D[k], H[k]
    exp = v.get("expected_sign") or 0
    same = lambda a, b: a["rho"] is not None and b["rho"] is not None and a["rho"] * b["rho"] > 0
    onesided = h["p"] is not None and same(v, h) and h["p"] / 2 < 0.10
    if v.get("fdr_significant") and same(v, h) and onesided and (exp == 0 or v["rho"] * exp > 0): lab = "SUPPORTED"
    elif v["p"] is not None and v["p"] < .05 and same(d, v): lab = "SUGGESTIVE"
    else: lab = "UNSUPPORTED"
    if k[2] == "pre" and lab != "UNSUPPORTED": lab += "(ANTICIPATION: market moved first)"
    out.append({"feature": k[0], "commodity": k[1], "target": k[2], "n_val": v["n"], "rho_disc": d["rho"], "rho_val": v["rho"], "q_val": v.get("q"), "rho_hold": h["rho"], "p_hold": h["p"], "label": lab})
json.dump(out, open("results/classification.json", "w"), indent=1)
for o in sorted(out, key=lambda o: o["label"]):
    if o["label"] != "UNSUPPORTED": print(o["label"], o["feature"], o["commodity"], o["target"], [None if o[x] is None else round(o[x], 3) for x in ("rho_disc", "rho_val", "q_val", "rho_hold", "p_hold")])
print(len(out), "tests;", sum(1 for o in out if o["label"] == "UNSUPPORTED"), "unsupported")
