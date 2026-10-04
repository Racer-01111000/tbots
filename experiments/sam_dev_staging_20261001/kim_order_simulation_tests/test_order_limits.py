"""Tests for order_limits.py's kill switch and daily order cap, plus
kim_order_logic.submit_orders_with_limits() which composes both with the
existing PersistentIntentLedger/MockBroker path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

import order_limits as ol
from kim_order_logic import (
    IntendedOrderSpec, PersistentIntentLedger, MockBroker, submit_orders_with_limits,
)


# --- kill switch, standalone -------------------------------------------------

def test_kill_switch_absent_by_default(tmp_path):
    path = tmp_path / "KILL_SWITCH"
    assert ol.kill_switch_active(path) is False
    ol.check_kill_switch(path)  # must not raise


def test_kill_switch_present_blocks(tmp_path):
    path = tmp_path / "KILL_SWITCH"
    path.write_text("stop")
    assert ol.kill_switch_active(path) is True
    with pytest.raises(ol.KillSwitchActive):
        ol.check_kill_switch(path)


# --- per-session cap, ledger-derived -----------------------------------------

def test_cap_counts_distinct_intents_per_session_date(tmp_path):
    ledger = PersistentIntentLedger(str(tmp_path / "l.sqlite3"))
    for sym in ("SPY", "EFA"):
        ledger.record_intent(IntendedOrderSpec(f"kim-2026-10-03-{sym}", sym, 1, "buy"), "2026-10-03", 5)
    ledger.record_intent(IntendedOrderSpec("kim-2026-10-04-SPY", "SPY", 1, "buy"), "2026-10-04", 5)
    assert ledger.count_for_session("2026-10-03") == 2
    assert ledger.count_for_session("2026-10-04") == 1


def test_cap_reached_raises_and_inserts_nothing(tmp_path):
    ledger = PersistentIntentLedger(str(tmp_path / "l.sqlite3"))
    for sym in ("SPY", "EFA", "GLD"):
        ledger.record_intent(IntendedOrderSpec(f"kim-2026-10-03-{sym}", sym, 1, "buy"), "2026-10-03", 3)
    with pytest.raises(ol.DailyOrderLimitReached):
        ledger.record_intent(IntendedOrderSpec("kim-2026-10-03-TLT", "TLT", 1, "buy"), "2026-10-03", 3)
    assert ledger.count_for_session("2026-10-03") == 3
    assert ledger.get("kim-2026-10-03-TLT") is None


def test_seeded_preexisting_rows_do_not_count_against_cap(tmp_path):
    import broker_reconciliation
    ledger = PersistentIntentLedger(str(tmp_path / "l.sqlite3"))
    broker_reconciliation.seed_ledger_from_broker_state(ledger, [{"symbol": "DBC", "qty": "1"}], [], "2026-10-03")
    assert ledger.count_for_session("2026-10-03") == 0


def test_old_ledger_without_session_date_column_is_migrated(tmp_path):
    import sqlite3
    db = str(tmp_path / "old.sqlite3")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE order_intents (client_order_id TEXT PRIMARY KEY, symbol TEXT, qty INTEGER, side TEXT, "
              "intent_recorded_at TEXT NOT NULL, broker_order_id TEXT, status TEXT, filled_qty REAL, last_updated_at TEXT)")
    c.execute("INSERT INTO order_intents VALUES ('x','SPY',1,'buy','t',NULL,'filled',1,NULL)")
    c.commit(); c.close()
    ledger = PersistentIntentLedger(db)
    assert ledger.count_all() == 1 and ledger.count_for_session("2026-10-03") == 0


# --- submit_orders_with_limits: composition with the real submission path ---

def _spec(symbol, side="buy", qty=1, session_date="2026-10-03"):
    from kim_order_logic import build_client_order_id
    return IntendedOrderSpec(build_client_order_id(session_date, symbol), symbol, qty, side)


def test_normal_batch_all_submitted(tmp_path):
    broker = MockBroker()
    ledger = PersistentIntentLedger(str(tmp_path / "ledger.sqlite3"))
    kill_switch = tmp_path / "KILL_SWITCH"
    specs = [_spec("SPY"), _spec("EFA"), _spec("GLD")]

    result = submit_orders_with_limits(broker, ledger, specs, kill_switch, "2026-10-03", max_orders_per_day=8)

    assert result["submitted"] == 3
    assert result["blocked"] == 0
    assert result["startup_blocked"] is False
    for spec in specs:
        row = ledger.get(spec.client_order_id)
        assert row["status"] == "filled"


def test_kill_switch_present_at_startup_blocks_everything(tmp_path):
    broker = MockBroker()
    ledger = PersistentIntentLedger(str(tmp_path / "ledger.sqlite3"))
    kill_switch = tmp_path / "KILL_SWITCH"
    kill_switch.write_text("stop")
    specs = [_spec("SPY"), _spec("EFA")]

    result = submit_orders_with_limits(broker, ledger, specs, kill_switch, "2026-10-03", max_orders_per_day=8)

    assert result["submitted"] == 0
    assert result["blocked"] == 2
    assert result["startup_blocked"] is True
    # never even recorded -- startup block happens before any ledger write
    for spec in specs:
        assert ledger.get(spec.client_order_id) is None


def test_kill_switch_dropped_mid_batch_blocks_second_not_first(tmp_path):
    broker = MockBroker()
    ledger = PersistentIntentLedger(str(tmp_path / "ledger.sqlite3"))
    kill_switch = tmp_path / "KILL_SWITCH"
    spec_a = _spec("SPY")
    spec_b = _spec("EFA")

    # Drop the kill switch as a side effect of the first order's submission,
    # simulating an operator intervening mid-run between two sends.
    orig_submit = broker.submit
    def submit_then_kill(spec):
        res = orig_submit(spec)
        kill_switch.write_text("stop")
        return res
    broker.submit = submit_then_kill

    result = submit_orders_with_limits(broker, ledger, [spec_a, spec_b], kill_switch, "2026-10-03", max_orders_per_day=8)

    assert result["submitted"] == 1
    assert result["blocked"] == 1
    assert ledger.get(spec_a.client_order_id)["status"] == "filled"
    assert ledger.get(spec_b.client_order_id) is None  # blocked before record_intent


def test_daily_cap_reached_blocks_remaining_orders_in_batch(tmp_path):
    broker = MockBroker()
    ledger = PersistentIntentLedger(str(tmp_path / "ledger.sqlite3"))
    kill_switch = tmp_path / "KILL_SWITCH"
    specs = [_spec("SPY"), _spec("EFA"), _spec("GLD")]

    result = submit_orders_with_limits(broker, ledger, specs, kill_switch, "2026-10-03", max_orders_per_day=2)

    assert result["submitted"] == 2
    assert result["blocked"] == 1
    assert ledger.get(specs[0].client_order_id)["status"] == "filled"
    assert ledger.get(specs[1].client_order_id)["status"] == "filled"
    assert ledger.get(specs[2].client_order_id) is None


def test_cap_blocks_sends_but_reconciliation_reads_still_work(tmp_path):
    """A blocked send must never prevent read-only reconciliation from
    running -- they are independent code paths."""
    import broker_reconciliation

    broker = MockBroker()
    ledger = PersistentIntentLedger(str(tmp_path / "ledger.sqlite3"))
    kill_switch = tmp_path / "KILL_SWITCH"
    specs = [_spec("SPY"), _spec("EFA")]
    broker.queue_behavior(specs[0].client_order_id, "partial_fill")  # non-terminal, needs later reconciliation

    submit_orders_with_limits(broker, ledger, specs, kill_switch, "2026-10-03", max_orders_per_day=1)
    # SPY submitted and partially filled; EFA blocked, never reached the broker.
    # Simulate the broker completing the fill some time after submission.
    broker.set_order_status(specs[0].client_order_id, "filled", filled_qty=1)

    updates = broker_reconciliation.reconcile_ledger(ledger, broker)
    assert len(updates) == 1
    assert ledger.get(specs[0].client_order_id)["status"] == "filled"
    assert ledger.get(specs[1].client_order_id) is None  # still never recorded


def test_kill_switch_and_cap_both_active_kill_switch_wins_at_startup(tmp_path):
    broker = MockBroker()
    ledger = PersistentIntentLedger(str(tmp_path / "ledger.sqlite3"))
    kill_switch = tmp_path / "KILL_SWITCH"
    kill_switch.write_text("stop")
    specs = [_spec("SPY")]

    result = submit_orders_with_limits(broker, ledger, specs, kill_switch, "2026-10-03", max_orders_per_day=2)

    assert result["startup_blocked"] is True
    assert result["submitted"] == 0
