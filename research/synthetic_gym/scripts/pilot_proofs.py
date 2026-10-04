"""Executes the repaired pilot runner (e81ceab) against the in-memory mock broker for the specific scenarios named in the GO, and writes
a RECEIPT of the resulting broker orders and ledger rows -- evidence of behaviour, not a test count. No network, no credentials, no
real broker. Every scenario runs in its own temporary state directory."""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parents[1]
DEV = REPO / "experiments" / "sam_dev_staging_20261001"
for p in (str(DEV), str(DEV / "alpaca_adapter"), str(DEV / "kim_order_simulation_tests"), str(REPO / "scripts"), str(REPO / "scripts" / "lib")):
    sys.path.insert(0, p)

import kim_once_per_session_v2 as k
from fake_alpaca import FakeAlpaca
from alpaca_paper_broker import AlpacaPaperBroker
from kim_order_logic import IntendedOrderSpec, PersistentIntentLedger

GENOME = json.loads((DEV / "selected_development_bot.json").read_text())["selected_genome_definition"]
CFG = {"genome_id": "x", "max_orders_per_session": 4, "max_liquidation_orders_per_session": 8, "drawdown_halt_pct": 0.08, "max_asset_weight": 0.18,
       "gross_exposure_ceiling": 0.18, "min_cash_reserve_pct": 0.10, "time_in_force": "day", "kill_switch_file": "KILL_SWITCH",
       "expiry_date": "2026-11-01", "session_zero_date": "2026-10-05", "rebalance_every_n_sessions": 50}


def make(tmp, fake=None, gate=True):
    fake = fake or FakeAlpaca()
    broker = AlpacaPaperBroker({"k": "v"}, http=fake.http, submission_gate=lambda: gate)
    deps = k.Deps(get_json=fake.get_json, headers={}, broker=broker, now_utc=lambda: fake.now_utc(), submission_gate=lambda: gate,
                  state_dir=tmp, genome=GENOME, genome_id="x", config=dict(CFG))
    return fake, deps


def arm_halt(tmp, fake, positions):
    fake.positions = positions
    (tmp / "halt_state.json").write_text(json.dumps({"halted": True, "halted_session_date": "2026-10-01"}))
    (tmp / "peak_equity_state.json").write_text(json.dumps({"peak_equity": 100000.0, "peak_session_date": "2026-10-01", "history": {"2026-10-01": 100000.0}}))
    (tmp / "cadence_state.json").write_text(json.dumps({"session_zero_date": "2026-10-05", "last_evaluated_session_date": "2026-10-01"}))


def ledger_rows(tmp):
    p = tmp / "order_ledger.sqlite3"
    if not p.exists():
        return []
    L = PersistentIntentLedger(str(p))
    rows = [dict(zip(("id", "symbol", "side", "qty", "status", "session"), (r["client_order_id"], r["symbol"], r["side"], r["qty"], r["status"], r["session_date"])))
            for r in L.get_unresolved()]
    L.close()
    return rows


def broker_state(fake):
    return [{"id": o["client_order_id"], "symbol": o["symbol"], "side": o["side"], "qty": o["qty"], "status": o["status"]} for o in fake.orders]


def scenario_two_uncertain_sells():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d); fake, deps = make(tmp)
        arm_halt(tmp, fake, [{"symbol": "TLT", "qty": "10"}])
        L = PersistentIntentLedger(str(tmp / "order_ledger.sqlite3"))
        L.record_intent(IntendedOrderSpec("kim-2026-09-30-TLT", "TLT", 8, "sell"), "2026-09-30", 8)
        L.record_intent(IntendedOrderSpec("kim-2026-10-01-TLT", "TLT", 8, "sell"), "2026-10-01", 8)
        L.close()
        rec = k.run_session(deps)
        return {"scenario": "two uncertain 8-share sells against 10 held (halted account)", "status": rec["status"], "hold_reason": rec.get("halt_hold_reason"),
                "oversell_risk": rec.get("oversell_risk"), "broker_orders_created": broker_state(fake), "post_requests": len(fake.post_log),
                "total_sell_qty_at_broker": sum(int(o["qty"]) for o in fake.orders), "position_held": 10,
                "PASS": sum(int(o["qty"]) for o in fake.orders) <= 10 and rec["status"] == "halt_liquidation_held"}


def scenario_uncertain_sell_plus_pending_sell():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d); fake, deps = make(tmp)
        arm_halt(tmp, fake, [{"symbol": "TLT", "qty": "10"}])
        fake.orders.append({"id": "o1", "client_order_id": "x-TLT-sell", "symbol": "TLT", "qty": "8", "side": "sell", "status": "accepted", "filled_qty": "0"})
        L = PersistentIntentLedger(str(tmp / "order_ledger.sqlite3"))
        L.record_intent(IntendedOrderSpec("kim-2026-10-01-TLT", "TLT", 8, "sell"), "2026-10-01", 8); L.close()
        rec = k.run_session(deps)
        return {"scenario": "uncertain 8-share sell plus an existing pending 8-share sell, 10 held", "status": rec["status"], "post_requests": len(fake.post_log),
                "committed_at_broker": sum(int(o["qty"]) for o in fake.orders), "PASS": len(fake.post_log) == 0 and rec["status"] == "halt_liquidation_held"}


def scenario_uncertainty_survives_the_session_date_change():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d); fake, deps = make(tmp)
        fake.hide_new_for = 99; fake.fail["post"] = "timeout_after_accept_once"
        mon = k.run_session(deps)
        monday_orders = broker_state(fake)
        i = fake.sessions.index(fake.today); fake.today = fake.sessions[i + 1]
        tue = k.run_session(deps)
        return {"scenario": "Monday's accepted-but-invisible buy, then Tuesday (new date => new client_order_id)",
                "monday": {"status": mon["status"], "broker_orders": monday_orders, "ledger": ledger_rows(tmp)},
                "tuesday": {"status": tue["status"], "unresolved_submissions": tue.get("unresolved_submissions"), "escalate": tue.get("escalate")},
                "broker_orders_after_tuesday": broker_state(fake),
                "PASS": len(fake.orders) == len(monday_orders) and tue["status"] == "held_unresolved_prior_submission"}


def scenario_delayed_visibility_timeout():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d); fake, deps = make(tmp)
        fake.hide_new_for = 4; fake.fail["post"] = "timeout_after_accept_once"
        trace = []
        for run_no in range(1, 4):
            rec = k.run_session(deps)
            trace.append({"run": run_no, "status": rec["status"], "broker_orders": len(fake.orders), "post_attempts": len(fake.post_log)})
        return {"scenario": "POST times out AFTER the broker accepted it; order invisible to lookups for several checks", "trace": trace,
                "final_broker_orders": broker_state(fake), "PASS": len(fake.orders) == 1}


def scenario_liquidation_of_six_symbols_above_the_normal_cap():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d); fake, deps = make(tmp)
        syms = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD"]
        arm_halt(tmp, fake, [{"symbol": s, "qty": str(10 + i)} for i, s in enumerate(syms)])
        fake.missing_bars = set(["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"])
        rec = k.run_session(deps)
        return {"scenario": "latched halt, six symbols held, normal cap 4, liquidation limit 8, ALL price bars missing", "status": rec["status"],
                "sells_sent": sorted((o["symbol"], o["qty"], o["side"]) for o in fake.orders), "blocked": rec["submission"]["blocked"],
                "PASS": len(fake.orders) == 6 and all(o["side"] == "sell" for o in fake.orders)}


if __name__ == "__main__":
    results = [f() for f in (scenario_two_uncertain_sells, scenario_uncertain_sell_plus_pending_sell, scenario_uncertainty_survives_the_session_date_change,
                             scenario_delayed_visibility_timeout, scenario_liquidation_of_six_symbols_above_the_normal_cap)]
    out = {"pilot_candidate": "e81ceab43bb481cf0ae5e1ce362f8b94bf44bd6a", "mode": "in-memory mock broker; no network; no credentials", "scenarios": results,
           "all_pass": all(r["PASS"] for r in results)}
    path = HERE / "docs" / "pilot_semantics_receipts.json"
    path.parent.mkdir(exist_ok=True); path.write_text(json.dumps(out, indent=1, default=str))
    for r in results:
        print(("PASS " if r["PASS"] else "FAIL ") + r["scenario"])
    print("all pass:", out["all_pass"])
