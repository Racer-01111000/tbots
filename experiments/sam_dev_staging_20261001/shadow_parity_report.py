"""Regenerates shadow_compare_evidence_20261004.json: genome-hash verification,
decision/state parity of the shadow books against the accepted evaluator
(replay_harness.simulate with the accepted primitives) on identical inputs, and
the champion-vs-lineage-D head-to-head on the same completed sessions.
Data: the accepted normalized dataset in data/normalized (last session 2026-08-25).
Run:  PYTHONPATH=<same as the tests> python shadow_parity_report.py"""
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import test_shadow_parity_20261004 as t   # reuse the loaders / accepted-run helpers (also sets sys.path)
import lineage_d
import s6a_final as p
import shadow_compare as sc
import shadow_strategies as ss
from lib.ids import genome_id
import importlib.util

REPO = t.REPO


def main():
    spec = importlib.util.spec_from_file_location("replay_harness", REPO / "experiments/sam_15_month_replay_20261001/replay_harness.py")
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    raw = t._load_rows()
    import execution
    rows = {s: [{"timestamp": r["timestamp"], "open": float(r["open"]), "close": float(r["close"]),
                 "adjusted_close": float(r["adjusted_close"]),
                 "dividend": execution.dividend_amount_dollars(r["corporate_action"] or None)} for r in v] for s, v in raw.items()}
    out = {"data": "data/normalized/*.csv (accepted dataset), last session " + max(r["timestamp"] for r in rows["SPY"]),
           "genome": {"id": lineage_d.EXPECTED_ID, "independent_sha256_matches": lineage_d.independent_genome_id(lineage_d.load_verified_genome()) == lineage_d.EXPECTED_ID,
                      "lib_ids_matches": genome_id(lineage_d.load_verified_genome()) == lineage_d.EXPECTED_ID}, "windows": {}}
    for label, (w0, w1) in {"2024-01-02_to_2026-08-25": ("2024-01-02", "2026-08-25"),
                            "2025-07-01_to_2026-08-25": ("2025-07-01", "2026-08-25")}.items():
        t.WINDOW = (w0, w1)
        dates = [r["timestamp"] for r in rows["SPY"] if w0 <= r["timestamp"] <= w1]
        g = lineage_d.load_verified_genome()
        acc, calls = t._run_accepted(harness, raw, None, use_d=True, genome=g, bounds=lineage_d.risk_bounds(g))
        book, recs, seen = t._run_shadow(ss.lineage_d_strategy(), rows, dates)
        compared = exact = 0
        for (st, h_acc, o), (st2, h_sh), rec in zip(calls, seen, recs):
            if rec["halted_now"]:
                continue
            exp = None if o is None else {s: v for s, v in o.items() if s in p.UNIVERSE}
            compared += 1
            exact += (rec["new_target"] == exp) and (h_acc == h_sh)
        pf = sc._load(book["portfolio"])
        last = {s: sc._cents([r for r in rows[s] if r["timestamp"] == dates[-1]][0]["close"]) for s in p.UNIVERSE}
        parity = {"steps": len(dates), "steps_compared": compared, "steps_inputs_and_targets_exactly_equal": exact,
                  "accepted_final_equity_cents": acc["final_equity_cents"], "shadow_final_equity_cents": pf.equity_cents(last),
                  "accepted_order_count": acc["order_count"], "shadow_order_count": book["order_count"],
                  "accepted_turnover": acc["turnover"], "shadow_turnover": pf.total_traded_notional_cents / t.START,
                  "accepted_halted": acc["halted"], "shadow_halted": pf.halted}
        parity["PASS"] = (compared == exact and parity["accepted_final_equity_cents"] == parity["shadow_final_equity_cents"]
                          and parity["accepted_order_count"] == parity["shadow_order_count"]
                          and parity["accepted_turnover"] == parity["shadow_turnover"])

        strategies = ss.both()
        books = {n: sc.new_book(s, t.START) for n, s in strategies.items()}
        recs2 = sc.run_sessions(books, strategies, rows, dates)
        summary = {}
        for n in strategies:
            b = books[n]; pf = sc._load(b["portfolio"])
            eq = [x["books"][n]["equity_cents"] for x in recs2]
            summary[n] = {"final_return": eq[-1] / t.START - 1, "max_drawdown": b["max_drawdown"],
                          "rebalances": sum(x["books"][n]["rebalanced"] for x in recs2), "orders": b["order_count"],
                          "turnover_traded_notional_over_start": pf.total_traded_notional_cents / t.START,
                          "mean_standing_exposure": statistics.mean(x["books"][n]["standing_exposure"] for x in recs2),
                          "max_standing_exposure": max(x["books"][n]["standing_exposure"] for x in recs2),
                          "mean_target_turnover_per_rebalance_one_way": statistics.mean(
                              [x["books"][n]["target_turnover_one_way"] for x in recs2 if x["books"][n]["rebalanced"]] or [0]),
                          "halted": pf.halted}
        cmp_ = [x["comparison"] for x in recs2]
        diff = {"sessions": len(cmp_), "sessions_with_identical_standing_targets": sum(c["same_targets"] for c in cmp_),
                "mean_l1_distance": statistics.mean(c["l1_distance"] for c in cmp_),
                "sessions_with_no_symbol_overlap": sum(1 for c in cmp_ if not c["both"]),
                "mean_exposure_gap_champion_minus_lineage_d": statistics.mean(c["exposure_champion"] - c["exposure_lineage_d"] for c in cmp_)}
        rb = [{"date": x["date"], "champion": x["books"]["champion"]["new_target"], "lineage_d": x["books"]["lineage_d"]["new_target"]}
              for x in recs2 if x["books"]["champion"]["rebalanced"] or x["books"]["lineage_d"]["rebalanced"]]
        out["windows"][label] = {"parity_lineage_d_vs_accepted_evaluator": parity, "head_to_head": summary,
                                 "decision_differences": diff, "rebalance_targets": rb}
    Path(HERE / "shadow_compare_evidence_20261004.json").write_text(json.dumps(out, indent=2, default=str))
    for k, w in out["windows"].items():
        print(k, "PARITY", w["parity_lineage_d_vs_accepted_evaluator"]["PASS"], json.dumps(w["head_to_head"], default=str)[:600])


if __name__ == "__main__":
    main()
