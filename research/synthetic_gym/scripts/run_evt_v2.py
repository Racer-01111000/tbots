"""python scripts/run_evt_v2.py OUT_DIR [--smoke]  -- offline, one worker. Writes evt_v2_results.json + RESULTS_EVT_V2.md"""
import json, os, random, sys, time, resource
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]; REPO = HERE.parents[1]
for p in (str(HERE), str(REPO / "scripts"), str(REPO / "scripts" / "lib"), str(REPO / "experiments" / "sam_dev_staging_20261001"),
          str(REPO / "experiments" / "sam_dev_staging_20261001" / "alpaca_adapter"), str(REPO / "experiments" / "sam_dev_staging_20261001" / "kim_order_simulation_tests")):
    sys.path.insert(0, p)
from gym import calibration as C, generator as G, engine as E, strategies as S, event_aware_v2 as V, scoring as SC, stats as st

EXP = "SYNGYM-20261004-EVT-V2"


def boot_ci(d, n=5000, seed=20261004):
    rng = random.Random(seed); m = len(d); means = sorted(sum(d[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return means[int(.025 * n)], means[int(.975 * n)]


def main(out, smoke=False):
    t0 = time.time(); out = Path(out); out.mkdir(parents=True, exist_ok=True)
    cal = C.build(REPO / "data" / "normalized"); refs = S.load_references(); P = E.EngineParams()
    per_fam = 2 if smoke else 12
    fams = list(G.FAMILIES); worlds = [(fam, i, G.generate_world(G.WorldSpec(EXP, "training", fam, i), cal)) for fam in fams for i in range(per_fam)]
    res = {"experiment": EXP, "worlds": len(worlds), "bases": {}, "world_hashes": {w.world_id: w.content_hash() for _, _, w in worlds}}
    passive = [E.run(w, S.passive18(), P) for _, _, w in worlds]
    for bname in ("sam", "d_primary_rank3", "champion"):
        r = refs[bname]; base_res = []
        for (fam, i, w), pas in zip(worlds, passive):
            base_res.append(E.run(w, S.strategy_for(bname, r["family"], r["genome"]), P))
        out_b = {"base_genome_id": r["genome_id"], "event_aware_id": V.strategy_id(r["genome_id"]), "price_only_mean_ann": None, "conditions": {}}
        ann = lambda res_: SC.annualized(res_.equity[-1], res_.start_cents, res_.n_eval)
        out_b["price_only_mean_ann"] = sum(ann(x) for x in base_res) / len(base_res)
        for cnd in V.CONDITIONS + ["placebo"]:
            diffs, ddiff, tdiff, byfam, viol = [], [], [], {}, 0
            for k, ((fam, i, w), b) in enumerate(zip(worlds, base_res)):
                if cnd == "placebo":
                    other = worlds[(k + 1) % len(worlds)][2] if worlds[(k + 1) % len(worlds)][0] == fam else worlds[k - 1][2]
                    feed = V.IndicatorFeed(other, "clean")
                else:
                    feed = V.IndicatorFeed(w, cnd)
                a = E.run(w, V.event_aware_strategy(bname + "_evt", r["family"], r["genome"], w, feed), P)
                viol += len(a.violations)
                d = ann(a) - ann(b); diffs.append(d); byfam.setdefault(fam, []).append(d)
                ddiff.append(SC.st.max_drawdown_from_levels(SC.levels_with_start(a)) - SC.st.max_drawdown_from_levels(SC.levels_with_start(b)))
                tdiff.append((a.traded_notional_cents - b.traded_notional_cents) / a.start_cents)
            lo, hi = boot_ci(diffs)
            out_b["conditions"][cnd] = {"mean_diff_ann": sum(diffs) / len(diffs), "ci95": [lo, hi], "frac_improved": sum(1 for d in diffs if d > 1e-9) / len(diffs),
                                       "frac_worsened": sum(1 for d in diffs if d < -1e-9) / len(diffs), "mean_maxdd_diff": sum(ddiff) / len(ddiff),
                                       "mean_turnover_diff": sum(tdiff) / len(tdiff), "by_family": {f: sum(v) / len(v) for f, v in byfam.items()}, "violations": viol}
        res["bases"][bname] = out_b
    res["resource"] = {"wall_s": time.time() - t0, "cpu_s": time.process_time(), "maxrss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0}
    json.dump(res, open(out / "evt_v2_results.json", "w"), indent=1)
    L = [f"# {EXP} results (synthetic; paired, identical worlds)", "", f"{len(worlds)} worlds, wall {res['resource']['wall_s']:.0f}s, maxrss {res['resource']['maxrss_mib']:.0f} MiB", ""]
    for b, o in res["bases"].items():
        L += [f"## base {b} ({o['base_genome_id'][:12]}) -> event-aware {o['event_aware_id'][:12]}; price-only mean ann return {o['price_only_mean_ann']*100:.2f}%", "",
              "| condition | mean diff (ann, pp) | 95% CI | improved | worsened | dMaxDD (pp) | dTurnover |", "|---|---|---|---|---|---|---|"]
        for c, m in o["conditions"].items():
            L.append(f"| {c} | {m['mean_diff_ann']*100:+.3f} | [{m['ci95'][0]*100:+.3f}, {m['ci95'][1]*100:+.3f}] | {m['frac_improved']:.2f} | {m['frac_worsened']:.2f} | {m['mean_maxdd_diff']*100:+.2f} | {m['mean_turnover_diff']:+.2f} |")
        L.append("")
    (out / "RESULTS_EVT_V2.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[1], "--smoke" in sys.argv)
