# End-to-end dry run of Kim's restart-safe execution + hard-limits path --
# mock broker only. Never touches the real submission_enabled() gate, never
# makes a real HTTP call. Exercises the real order_ledger/broker_reconciliation/
# order_limits code (not reimplemented or simplified for this dry run):
#   (a) a normal full cycle
#   (b)+(c) a crash mid-send, then a restart that recovers via
#       query-by-client_order_id rather than double-submitting
#   (d) the first-submission-session reconcile-to-target delta, with the
#       hard-limit/kill-switch checks active throughout
#
# Run directly: python3 end_to_end_dry_run.py
# Saves a JSON log under kim_shadow_pilot/dry_run/.
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "kim_order_simulation_tests"))

from kim_order_logic import (
    IntendedOrderSpec, PersistentIntentLedger, MockBroker,
    build_client_order_id, submit_with_reconciliation, submit_orders_with_limits,
)
import broker_reconciliation

STATE_DIR = HERE / "kim_shadow_pilot"
DRY_RUN_DIR = STATE_DIR / "dry_run"
SESSION_DATE = "2026-10-03"


def scenario_normal_full_cycle(work_dir: Path) -> dict:
    broker = MockBroker()
    ledger = PersistentIntentLedger(str(work_dir / "scenario_a_ledger.sqlite3"))
    kill_switch = work_dir / "scenario_a_KILL_SWITCH"
    specs = [
        IntendedOrderSpec(build_client_order_id(SESSION_DATE, "SPY"), "SPY", 10, "buy"),
        IntendedOrderSpec(build_client_order_id(SESSION_DATE, "GLD"), "GLD", 5, "buy"),
        IntendedOrderSpec(build_client_order_id(SESSION_DATE, "TLT"), "TLT", 3, "sell"),
    ]

    result = submit_orders_with_limits(broker, ledger, specs, kill_switch, SESSION_DATE, max_orders_per_day=8)
    assert result["submitted"] == 3
    assert result["blocked"] == 0
    for s in specs:
        assert ledger.get(s.client_order_id)["status"] == "filled"

    return {
        "scenario": "normal_full_cycle",
        "attempted": len(specs), "submitted": result["submitted"], "blocked": result["blocked"],
        "detail": result,
    }


def scenario_crash_mid_send_and_restart(work_dir: Path) -> dict:
    db_path = str(work_dir / "scenario_bc_ledger.sqlite3")
    broker = MockBroker()

    # Sub-case 1: broker actually received the order before the crash.
    spec_received = IntendedOrderSpec(build_client_order_id(SESSION_DATE, "EEM"), "EEM", 7, "buy")
    ledger_before_crash = PersistentIntentLedger(db_path)
    ledger_before_crash.record_intent(spec_received)   # intent persisted BEFORE the broker call
    broker.submit(spec_received)                        # broker got it and accepted it...
    # ...then the process crashes right here, before update_status() ever
    # runs. The ledger is stuck at "intent_recorded" with no broker_order_id.
    assert ledger_before_crash.get(spec_received.client_order_id)["status"] == "intent_recorded"

    # Sub-case 2: broker never received the order before the crash.
    spec_lost = IntendedOrderSpec(build_client_order_id(SESSION_DATE, "VNQ"), "VNQ", 4, "buy")
    ledger_before_crash.record_intent(spec_lost)
    # (crash happens before broker.submit() is ever called for this one)

    # --- "restart": a fresh PersistentIntentLedger instance, same db file ---
    ledger_after_restart = PersistentIntentLedger(db_path)
    result_received = submit_with_reconciliation(broker, ledger_after_restart, spec_received)
    result_lost = submit_with_reconciliation(broker, ledger_after_restart, spec_lost)

    # The recovered order must be adopted from the broker's own record, not
    # resubmitted -- this is exactly the code path that prevents a duplicate
    # order after a mid-submission crash.
    assert result_received["outcome"] == "reconciled_from_prior_crash"
    assert ledger_after_restart.get(spec_received.client_order_id)["status"] == "filled"
    # The never-received order is genuinely new -- safe to submit for the
    # first time on restart.
    assert result_lost["outcome"] == "filled"
    assert ledger_after_restart.get(spec_lost.client_order_id)["status"] == "filled"

    return {
        "scenario": "crash_mid_send_and_restart",
        "recovered_from_broker_record": result_received["outcome"],
        "submitted_fresh_on_restart": result_lost["outcome"],
        "no_duplicate_order_created": result_received["outcome"] == "reconciled_from_prior_crash",
    }


def scenario_first_submission_session_delta(work_dir: Path) -> dict:
    broker = MockBroker()
    ledger = PersistentIntentLedger(str(work_dir / "scenario_d_ledger.sqlite3"))
    kill_switch = work_dir / "scenario_d_KILL_SWITCH"

    assert broker_reconciliation.is_first_submission_session(ledger) is True

    # A pre-existing standing position -- e.g. the real DBC position from
    # the 2026-10-02 manual test order -- that was never recorded through
    # this ledger because submission has been off the whole time.
    positions = [{"symbol": "DBC", "qty": "1"}]
    orders: list = []  # the order itself has long since filled; only the position remains
    seeded = broker_reconciliation.seed_ledger_from_broker_state(ledger, positions, orders, SESSION_DATE)
    assert seeded == 1
    assert broker_reconciliation.is_first_submission_session(ledger) is False  # no longer empty

    # Target now calls for DBC at 3 shares (a real increase) and a new SPY
    # position at 10 shares. The delta must be incremental, never sized as
    # if starting from zero.
    target_shares = {"DBC": 3, "SPY": 10}
    broker_shares = {"DBC": 1}  # from the seeded position
    delta = broker_reconciliation.compute_first_session_delta(target_shares, broker_shares)
    assert delta == {"DBC": 2, "SPY": 10}

    specs = [
        IntendedOrderSpec(build_client_order_id(SESSION_DATE, sym), sym, qty, "buy")
        for sym, qty in delta.items() if qty > 0
    ]
    result = submit_orders_with_limits(broker, ledger, specs, kill_switch, SESSION_DATE, max_orders_per_day=8)
    assert result["submitted"] == len(specs)
    assert result["blocked"] == 0

    return {
        "scenario": "first_submission_session_delta",
        "seeded_from_broker_state": seeded,
        "computed_delta": delta,
        "delta_orders_submitted": result["submitted"],
    }


def run_full_dry_run(work_dir: Path | None = None) -> dict:
    if work_dir is None:
        work_dir = DRY_RUN_DIR / f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return {
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "END_TO_END_DRY_RUN -- mock broker only; real submission_enabled() never touched, "
                "no real HTTP call made",
        "work_dir": str(work_dir),
        "scenarios": [
            scenario_normal_full_cycle(work_dir),
            scenario_crash_mid_send_and_restart(work_dir),
            scenario_first_submission_session_delta(work_dir),
        ],
    }


def main():
    results = run_full_dry_run()
    DRY_RUN_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DRY_RUN_DIR / f"end_to_end_dry_run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps(results, indent=2, default=str))
    print("Log written:", out_path)


if __name__ == "__main__":
    main()
