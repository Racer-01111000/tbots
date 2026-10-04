"""Regression tests for the 2026-10-04 published-code review of fe72d72.
Each test here failed against fe72d72 before the matching repair."""
import multiprocessing as mp
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "kim_order_simulation_tests"))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import kim_once_per_session_v2 as k
import peak_equity
from kim_order_logic import (
    IntendedOrderSpec, PersistentIntentLedger, MockBroker, build_client_order_id,
    submit_orders_with_limits,
)
import order_limits as ol
from risk import RiskRefusal

GENOME = {"max_asset_weight": 0.18, "target_max_exposure": 0.89, "drawdown_halt_pct": 0.12}


# --- 1. drawdown sign convention -------------------------------------------

def test_halt_fires_on_real_peak_equity_output(tmp_path):
    """peak_equity's real output for a 15% drop, fed straight into the
    order path, must trip risk.validate's 12% halt (fe72d72 passed +0.15
    to a check that expects -0.15, so the halt could never fire)."""
    path = tmp_path / "peak.json"
    peak_equity.record_and_get_drawdown(path, "2026-10-01", 100_000.0)
    _, dd = peak_equity.record_and_get_drawdown(path, "2026-10-02", 85_000.0)
    with pytest.raises(RiskRefusal):
        k.compute_target_orders({"SPY": 0.18}, 85_000_00, {"SPY": 500_00}, {}, [], GENOME,
                                current_drawdown=dd)


# --- 3. order-limit accounting ---------------------------------------------

def _specs(date, syms):
    return [IntendedOrderSpec(build_client_order_id(date, s), s, 1, "buy") for s in syms]


def test_restart_replay_does_not_consume_cap(tmp_path):
    """A crashed-and-restarted batch re-presents the same orders. They are
    already in the ledger, so they must not be re-counted or blocked."""
    db, kill = str(tmp_path / "l.sqlite3"), tmp_path / "KILL"
    broker = MockBroker()
    specs = _specs("2026-10-05", ["SPY", "GLD", "TLT"])
    r1 = submit_orders_with_limits(broker, PersistentIntentLedger(db), specs, kill,
                                   "2026-10-05", max_orders_per_day=3)
    assert r1["blocked"] == 0
    r2 = submit_orders_with_limits(broker, PersistentIntentLedger(db), specs, kill,
                                   "2026-10-05", max_orders_per_day=3)
    assert r2["blocked"] == 0
    assert all(x["outcome"] == "already_resolved_no_resubmit" for x in r2["results"])


def test_concurrent_processes_never_exceed_cap(tmp_path):
    """24 spawned processes race to record intents against a cap of 8: exactly 8 may win, the rest must be
    refused -- no lost updates, no overshoot. (Spawn, not fork: a forked child inherits the parent's open
    SQLite connection, which loses POSIX locks and can break mutual exclusion -- a test artifact.)"""
    import race_helpers
    db = str(tmp_path / "race.sqlite3")
    PersistentIntentLedger(db).close()
    with mp.get_context("spawn").Pool(8) as pool:
        got = pool.map(race_helpers.record_one, [(db, i, 8, "normal", "2026-10-05") for i in range(24)])
    check = PersistentIntentLedger(db)
    assert sum(got) == 8 and check.count_for_session("2026-10-05") == 8
    check.close()
