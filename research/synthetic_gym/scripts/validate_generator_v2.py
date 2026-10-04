"""Regenerates docs/generator_v2_validation_report.json (+ the numbers quoted in docs/GENERATOR_VALIDATION.md) from FRESH engineering worlds
(experiment tag ENG-FROZEN, indices 600-619 per family). Never touches validation/sealed seeds."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "scripts"), str(HERE.parents[1] / "scripts" / "lib"), str(HERE.parents[1] / "experiments" / "sam_dev_staging_20261001")]
from gym import calibration as C, engine as E, generator_v2 as G, history as H, scoring as SC, stats as st, strategies as S, validation as V, world as W

DATA = HERE.parents[1] / "data" / "normalized"
cal = C.build(DATA)
ws = [G.generate_world(G.WorldSpec("ENG-V2FROZEN", "engineering", f, i), cal) for f in G.FAMILIES for i in range(600, 620)]
bad = [(w.world_id, v) for w in ws for v in [W.check_invariants(w)] if v]
rep = V.validate(cal, ws)
tail = V.tail_checks(ws)
corr = G.CFG["drift_bias_correction_annual"]
sm = [C.summarize_series(V.world_returns(w)) for w in ws]
vol_ratio = {s: st.quantile([x["asset"][s]["ann_vol"] for x in sm], .5) / cal["summary"]["asset"][s]["ann_vol"] for s in C.SYMBOLS}
drift_offset = {s: st.mean([(w.hidden.params["diag"][s]["tot"] - w.hidden.params["diag"][s]["drift"]) / w.hidden.params["N"] * 252 - corr[s] for w in ws]) for s in C.SYMBOLS}
refs = S.load_references()
strategies = {n: S.strategy_for(n, r["family"], r["genome"]) for n, r in refs.items()}
strategies["passive18"] = S.passive18()
P = E.EngineParams(ceiling=0.18)


def beh(world_list):
    out = {}
    for n, strat in strategies.items():
        res = [E.run(w, strat, P) for w in world_list]
        out[n] = {"turnover_per_year": [r.traded_notional_cents / r.start_cents / (r.n_eval / 252) for r in res],
                  "max_drawdown": [st.max_drawdown_from_levels([r.start_cents] + r.equity) for r in res], "orders": [r.orders for r in res]}
    return out


hist = beh(H.historical_windows(DATA))
syn = beh(ws[:60])
q = lambda xs: {k: st.quantile(xs, v) for k, v in (("p05", .05), ("p25", .25), ("median", .5), ("p75", .75), ("p95", .95))}
behaviour = {n: {"turnover_per_year": {"history_windows": hist[n]["turnover_per_year"], "synthetic": q(syn[n]["turnover_per_year"])},
                 "max_drawdown": {"history_windows": hist[n]["max_drawdown"], "synthetic": q(syn[n]["max_drawdown"])}} for n in strategies}
out = {"worlds": len(ws), "tag": "ENG-V2FROZEN", "invariant_violations": bad,
       "generator_hash_inputs": G.GENERATOR_VERSION, "plausibility": {"n_pass": rep["n_pass"], "n_stats": rep["n_stats"], "failures": rep["failures"],
       "stock_bond_corr_changes": rep["stock_bond_corr_changes"], "historical_caveat": rep["historical_caveat"], "rows": rep["rows"]},
       "tail_checks": tail, "volatility_ratio_synth_over_hist": vol_ratio, "mean_return_offset_pp_per_year": {s: v * 100 for s, v in drift_offset.items()},
       "reference_behaviour_history_vs_synthetic": behaviour,
       "calibration": {"dataset_id": cal["dataset_id"], "dev_range": cal["dev_range"], "sessions": cal["sessions"], "garch": cal["garch"], "tag_counts": cal["tag_counts"],
                       "pool_blocks": {k: len(v) for k, v in cal["pools"].items()}}}
(HERE / "docs").mkdir(exist_ok=True)
(HERE / "docs" / "generator_v2_validation_report.json").write_text(json.dumps(out, indent=1, default=str))
print("plausibility %d/%d %s" % (rep["n_pass"], rep["n_stats"], rep["failures"]))
print("tail %d/%d %s" % (tail["n_pass"], tail["n"], tail["failures"]))
print("invariant violations:", len(bad))
for n in strategies:
    h = behaviour[n]["turnover_per_year"]
    print(f"{n:16s} turnover/yr: history {[round(x, 2) for x in h['history_windows']]}  synthetic median {h['synthetic']['median']:.2f} [{h['synthetic']['p05']:.2f}, {h['synthetic']['p95']:.2f}]")
