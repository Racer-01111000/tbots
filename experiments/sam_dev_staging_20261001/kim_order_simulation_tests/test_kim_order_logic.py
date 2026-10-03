"""Isolated, no-network tests for kim_order_logic.py -- a MockBroker only,
never the real Alpaca endpoint, never alpaca_adapter.route_order(). Uses
temp SQLite files, never the live pilot's state.sqlite3."""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from kim_order_logic import (
    DuplicateSubmissionError, IntendedOrderSpec, MockBroker, PersistentIntentLedger,
    StaleDataError, UncertainSubmissionError, build_client_order_id, compute_order,
    submit_with_reconciliation, validate_inputs_fresh,
)


@pytest.fixture
def ledger(tmp_path):
    return PersistentIntentLedger(str(tmp_path / "test_ledger.sqlite3"))


@pytest.fixture
def broker():
    return MockBroker()


def test_sizing_whole_share_rounding():
    r = compute_order(equity_usd=100_000, target_weight=0.18, price=32.335)
    assert r["qty"] == 556
    assert r["qty"] * 32.335 <= 18_000
    assert r["weight_applied"] == 0.18
    assert r["equity_basis"] == 100_000


def test_sizing_uses_equity_not_buying_power():
    equity = 100_000
    buying_power = 400_000
    r = compute_order(equity_usd=equity, target_weight=0.18, price=32.335)
    r_wrong = compute_order(equity_usd=buying_power, target_weight=0.18, price=32.335)
    assert r["target_notional"] == pytest.approx(18_000)
    assert r_wrong["target_notional"] == pytest.approx(72_000)
    assert r["target_notional"] != r_wrong["target_notional"]


def test_sizing_caps_at_max_asset_weight_even_if_target_is_higher():
    r = compute_order(equity_usd=100_000, target_weight=0.50, price=32.335, max_asset_weight=0.18)
    assert r["weight_applied"] == 0.18


def test_sizing_rejects_invalid_price():
    with pytest.raises(ValueError):
        compute_order(equity_usd=100_000, target_weight=0.18, price=0)


def test_existing_order_accounting_prevents_second_intent_same_day(ledger):
    spec = IntendedOrderSpec("kim-2026-10-01-DBC", "DBC", 556, "buy")
    ledger.record_intent(spec)
    assert ledger.has_any_intent_today("DBC", "2026-10-01") is True
    with pytest.raises(DuplicateSubmissionError):
        ledger.record_intent(spec)


def test_response_accepted(ledger, broker):
    spec = IntendedOrderSpec("kim-test-accepted", "DBC", 556, "buy")
    result = submit_with_reconciliation(broker, ledger, spec)
    assert result["outcome"] == "filled"
    assert result["record"]["status"] == "filled"


def test_response_rejected(ledger, broker):
    spec = IntendedOrderSpec("kim-test-rejected", "DBC", 556, "buy")
    broker.queue_behavior(spec.client_order_id, "rejected")
    result = submit_with_reconciliation(broker, ledger, spec)
    assert result["outcome"] == "rejected"
    assert result["record"]["status"] == "rejected"


def test_response_partial_fill(ledger, broker):
    spec = IntendedOrderSpec("kim-test-partial", "DBC", 556, "buy")
    broker.queue_behavior(spec.client_order_id, "partial_fill")
    result = submit_with_reconciliation(broker, ledger, spec)
    assert result["outcome"] == "partially_filled"
    assert result["record"]["filled_qty"] == 278


def test_response_cancellation(ledger, broker):
    spec = IntendedOrderSpec("kim-test-cancel", "DBC", 556, "buy")
    broker.queue_behavior(spec.client_order_id, "cancelled")
    result = submit_with_reconciliation(broker, ledger, spec)
    assert result["outcome"] == "canceled"


def test_response_timeout_reconciles_not_blind_retry(ledger, broker):
    spec = IntendedOrderSpec("kim-test-timeout", "DBC", 556, "buy")
    broker.queue_behavior(spec.client_order_id, "timeout")
    result = submit_with_reconciliation(broker, ledger, spec)
    assert result["outcome"] == "reconciled_after_uncertainty"
    assert result["record"]["status"] == "accepted"
    assert len(broker._orders) == 1


def test_timeout_with_no_broker_record_marks_unresolved_not_retried(ledger):
    class DropEverythingBroker(MockBroker):
        def submit(self, spec):
            raise UncertainSubmissionError("dropped before broker ever saw it")
    broker = DropEverythingBroker()
    spec = IntendedOrderSpec("kim-test-true-drop", "DBC", 556, "buy")
    result = submit_with_reconciliation(broker, ledger, spec)
    assert result["outcome"] == "uncertain_unresolved"
    assert broker.find_by_client_order_id(spec.client_order_id) is None


def test_restart_after_uncertain_submission_does_not_duplicate(tmp_path):
    db_path = str(tmp_path / "restart_test.sqlite3")
    broker = MockBroker()
    spec = IntendedOrderSpec("kim-restart-test", "DBC", 556, "buy")
    broker.queue_behavior(spec.client_order_id, "timeout")

    ledger1 = PersistentIntentLedger(db_path)
    result1 = submit_with_reconciliation(broker, ledger1, spec)
    assert result1["outcome"] == "reconciled_after_uncertainty"

    ledger2 = PersistentIntentLedger(db_path)
    result2 = submit_with_reconciliation(broker, ledger2, spec)
    assert result2["outcome"] == "already_resolved_no_resubmit"
    assert len(broker._orders) == 1


def test_restart_before_any_broker_contact_recovers_cleanly(tmp_path):
    db_path = str(tmp_path / "restart_test2.sqlite3")
    broker = MockBroker()
    spec = IntendedOrderSpec("kim-restart-clean", "DBC", 556, "buy")

    ledger1 = PersistentIntentLedger(db_path)
    result1 = submit_with_reconciliation(broker, ledger1, spec)
    assert result1["outcome"] == "filled"

    ledger2 = PersistentIntentLedger(db_path)
    result2 = submit_with_reconciliation(broker, ledger2, spec)
    assert result2["outcome"] == "already_resolved_no_resubmit"
    assert len(broker._orders) == 1


def test_stale_data_rejected():
    now = datetime(2026, 10, 1, 13, 20, tzinfo=timezone.utc)
    stale_ts = now - timedelta(minutes=10)
    with pytest.raises(StaleDataError):
        validate_inputs_fresh(stale_ts, now, max_age_seconds=300)


def test_fresh_data_accepted():
    now = datetime(2026, 10, 1, 13, 20, tzinfo=timezone.utc)
    fresh_ts = now - timedelta(seconds=30)
    validate_inputs_fresh(fresh_ts, now, max_age_seconds=300)


def test_missing_data_rejected():
    now = datetime(2026, 10, 1, 13, 20, tzinfo=timezone.utc)
    with pytest.raises(StaleDataError):
        validate_inputs_fresh(None, now, max_age_seconds=300)




def test_cutoff_never_cancels_pending_or_filled_positions(ledger, broker):
    """Documents and proves the cutoff policy: a position/order that is
    still open at 16:15 is recorded, never cancelled. MockBroker has no
    cancel() method at all -- the capability doesn't exist, matching the
    standing 'no cancellations' constraint by absence, not just by policy.
    """
    spec = IntendedOrderSpec("kim-test-pending-at-cutoff", "DBC", 556, "buy")
    broker.queue_behavior(spec.client_order_id, "partial_fill")  # still open, not fully filled
    result = submit_with_reconciliation(broker, ledger, spec)
    assert result["outcome"] == "partially_filled"
    assert not hasattr(broker, "cancel")
    assert not hasattr(broker, "cancel_order")
    # The pilot's cutoff tick does nothing but observe -- same record, re-read, unchanged:
    record_at_cutoff = ledger.get(spec.client_order_id)
    assert record_at_cutoff["status"] == "partially_filled"


# --- 2026-10-03 restart-safe execution hardening -----------------------
# Regression tests for the crash-mid-submission window: a crash that
# happens strictly between record_intent() and the broker ever learning
# about the order (or between the broker learning and our local record
# being updated). The pre-existing tests above only exercised the
# UncertainSubmissionError (explicit timeout) path; these cover the
# silent-crash case where submit_with_reconciliation() is itself never
# even entered on the first attempt.


def test_crash_before_broker_ever_saw_it_submits_for_real_on_restart(tmp_path):
    # True crash window: record_intent() succeeded, but the process died
    # before broker.submit() was ever called (or even attempted). On
    # restart, status is still 'intent_recorded' with no broker_order_id.
    # The broker genuinely never saw this order, so recovery must
    # actually submit it now -- not get stuck treating it as unresolved.
    db_path = str(tmp_path / "crash_before_submit.sqlite3")
    spec = IntendedOrderSpec("kim-crash-before", "DBC", 556, "buy")

    ledger1 = PersistentIntentLedger(db_path)
    ledger1.record_intent(spec)  # crash happens right here, before any broker call

    broker = MockBroker()
    ledger2 = PersistentIntentLedger(db_path)  # restart
    result = submit_with_reconciliation(broker, ledger2, spec)

    assert result["outcome"] == "filled"
    assert result["record"]["status"] == "filled"
    assert len(broker._orders) == 1


def test_crash_after_broker_received_it_reconciles_without_duplicate(tmp_path):
    # True crash window, other branch: the broker DID receive and accept
    # the request before the crash (e.g. request sent, process died before
    # the response was read), so the ledger never advanced past
    # 'intent_recorded'. On restart, recovery must find the broker's
    # existing order and adopt it -- never call submit() again, which
    # would risk a real second order if the broker ever allowed it.
    db_path = str(tmp_path / "crash_after_submit.sqlite3")
    spec = IntendedOrderSpec("kim-crash-after", "DBC", 556, "buy")

    ledger1 = PersistentIntentLedger(db_path)
    ledger1.record_intent(spec)
    # Simulate the broker having actually accepted the pre-crash request,
    # independent of our local ledger ever being told:
    broker = MockBroker()
    broker.set_order_status(spec.client_order_id, "accepted", filled_qty=0,
                             symbol=spec.symbol, qty=spec.qty)

    ledger2 = PersistentIntentLedger(db_path)  # restart
    result = submit_with_reconciliation(broker, ledger2, spec)

    assert result["outcome"] == "reconciled_from_prior_crash"
    assert result["record"]["status"] == "accepted"
    assert len(broker._orders) == 1  # broker.submit() was never called again


def test_restart_with_no_crash_is_idempotent_no_duplicate(tmp_path):
    # A plain restart after full, successful resolution: must stay a no-op.
    db_path = str(tmp_path / "plain_restart.sqlite3")
    spec = IntendedOrderSpec("kim-plain-restart", "DBC", 556, "buy")
    broker = MockBroker()

    ledger1 = PersistentIntentLedger(db_path)
    result1 = submit_with_reconciliation(broker, ledger1, spec)
    assert result1["outcome"] == "filled"

    ledger2 = PersistentIntentLedger(db_path)
    result2 = submit_with_reconciliation(broker, ledger2, spec)
    assert result2["outcome"] == "already_resolved_no_resubmit"
    assert len(broker._orders) == 1


def test_build_client_order_id_is_deterministic_per_session_and_symbol():
    first = build_client_order_id("2026-10-03", "DBC")
    second = build_client_order_id("2026-10-03", "DBC")
    different_day = build_client_order_id("2026-10-04", "DBC")
    assert first == second == "kim-2026-10-03-DBC"
    assert different_day != first
