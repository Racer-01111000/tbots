"""Regression tests for broker_reconciliation.py -- MockBroker only, no
network, no live ledger file."""
import pytest

from broker_reconciliation import (
    compute_first_session_delta, is_first_submission_session,
    reconcile_ledger, seed_ledger_from_broker_state,
)
from kim_order_logic import IntendedOrderSpec, MockBroker, PersistentIntentLedger


@pytest.fixture
def ledger(tmp_path):
    return PersistentIntentLedger(str(tmp_path / "recon_test.sqlite3"))


@pytest.fixture
def broker():
    return MockBroker()


def test_reconcile_updates_accepted_to_filled(ledger, broker):
    spec = IntendedOrderSpec("kim-recon-filled", "DBC", 556, "buy")
    ledger.record_intent(spec)
    broker.set_order_status(spec.client_order_id, "accepted", filled_qty=0, symbol="DBC", qty=556)
    ledger.update_status(spec.client_order_id, f"broker-{spec.client_order_id}", "accepted", 0)

    broker.set_order_status(spec.client_order_id, "filled", filled_qty=556, symbol="DBC", qty=556)
    updated = reconcile_ledger(ledger, broker)

    assert len(updated) == 1
    assert ledger.get_by_client_order_id(spec.client_order_id)["status"] == "filled"


def test_reconcile_covers_partial_fill(ledger, broker):
    spec = IntendedOrderSpec("kim-recon-partial", "DBC", 556, "buy")
    ledger.record_intent(spec)
    ledger.update_status(spec.client_order_id, f"broker-{spec.client_order_id}", "accepted", 0)
    broker.set_order_status(spec.client_order_id, "partially_filled", filled_qty=200, symbol="DBC", qty=556)

    reconcile_ledger(ledger, broker)
    row = ledger.get_by_client_order_id(spec.client_order_id)
    assert row["status"] == "partially_filled"
    assert row["filled_qty"] == 200


def test_reconcile_covers_canceled(ledger, broker):
    spec = IntendedOrderSpec("kim-recon-canceled", "DBC", 556, "buy")
    ledger.record_intent(spec)
    ledger.update_status(spec.client_order_id, f"broker-{spec.client_order_id}", "accepted", 0)
    broker.set_order_status(spec.client_order_id, "canceled", filled_qty=0)

    reconcile_ledger(ledger, broker)
    assert ledger.get_by_client_order_id(spec.client_order_id)["status"] == "canceled"


def test_reconcile_covers_rejected(ledger, broker):
    spec = IntendedOrderSpec("kim-recon-rejected", "DBC", 556, "buy")
    ledger.record_intent(spec)
    ledger.update_status(spec.client_order_id, f"broker-{spec.client_order_id}", "accepted", 0)
    broker.set_order_status(spec.client_order_id, "rejected", filled_qty=0)

    reconcile_ledger(ledger, broker)
    assert ledger.get_by_client_order_id(spec.client_order_id)["status"] == "rejected"


def test_reconcile_covers_expired(ledger, broker):
    spec = IntendedOrderSpec("kim-recon-expired", "DBC", 556, "buy")
    ledger.record_intent(spec)
    ledger.update_status(spec.client_order_id, f"broker-{spec.client_order_id}", "accepted", 0)
    broker.set_order_status(spec.client_order_id, "expired", filled_qty=0)

    reconcile_ledger(ledger, broker)
    assert ledger.get_by_client_order_id(spec.client_order_id)["status"] == "expired"


def test_reconcile_skips_rows_already_in_sync(ledger, broker):
    spec = IntendedOrderSpec("kim-recon-sync", "DBC", 556, "buy")
    ledger.record_intent(spec)
    ledger.update_status(spec.client_order_id, f"broker-{spec.client_order_id}", "accepted", 0)
    broker.set_order_status(spec.client_order_id, "accepted", filled_qty=0, symbol="DBC", qty=556)

    updated = reconcile_ledger(ledger, broker)
    assert updated == []  # nothing changed -- broker already matches the ledger


def test_reconcile_leaves_unknown_rows_alone(ledger, broker):
    # 'intent_recorded' with no broker_order_id and the broker genuinely
    # has no record yet -- reconciliation must not invent a status.
    spec = IntendedOrderSpec("kim-recon-unknown", "DBC", 556, "buy")
    ledger.record_intent(spec)

    updated = reconcile_ledger(ledger, broker)
    assert updated == []
    assert ledger.get_by_client_order_id(spec.client_order_id)["status"] == "intent_recorded"


def test_is_first_submission_session_true_when_ledger_empty(ledger):
    assert is_first_submission_session(ledger) is True


def test_is_first_submission_session_false_after_any_intent(ledger):
    ledger.record_intent(IntendedOrderSpec("kim-any", "DBC", 1, "buy"))
    assert is_first_submission_session(ledger) is False


def test_seed_ledger_from_broker_state_backfills_position_and_order(ledger):
    positions = [{"symbol": "DBC", "qty": "1"}]
    orders = [{"id": "real-order-1", "client_order_id": "manual-dbc-order",
               "symbol": "DBC", "qty": "1", "filled_qty": "1", "status": "filled", "side": "buy"}]

    seeded = seed_ledger_from_broker_state(ledger, positions, orders, "2026-10-03")

    assert seeded == 1  # the order accounts for DBC; the position is not double-seeded
    row = ledger.get_by_client_order_id("manual-dbc-order")
    assert row is not None
    assert row["status"] == "filled"
    assert row["qty"] == 1


def test_seed_ledger_seeds_bare_position_with_no_matching_order(ledger):
    positions = [{"symbol": "DBC", "qty": "1"}]
    orders = []  # the order that created this position isn't in this ledger's view at all

    seeded = seed_ledger_from_broker_state(ledger, positions, orders, "2026-10-03")

    assert seeded == 1
    row = ledger.get_by_client_order_id("preexisting-2026-10-03-DBC-position")
    assert row is not None
    assert row["status"] == "filled"
    assert row["qty"] == 1


def test_seed_ledger_is_idempotent_on_restart(ledger):
    # A restart mid-seeding (or just re-running the first session's seed
    # step twice) must not duplicate rows or corrupt the ledger.
    positions = [{"symbol": "DBC", "qty": "1"}]
    orders = [{"id": "real-order-1", "client_order_id": "manual-dbc-order",
               "symbol": "DBC", "qty": "1", "filled_qty": "1", "status": "filled", "side": "buy"}]

    first = seed_ledger_from_broker_state(ledger, positions, orders, "2026-10-03")
    second = seed_ledger_from_broker_state(ledger, positions, orders, "2026-10-03")

    assert first == 1
    assert second == 0  # nothing new to seed -- already present from the first call
    assert ledger.count_all() == 1


def test_compute_first_session_delta_sizes_incrementally_not_from_zero():
    # Broker already holds 1 share DBC (e.g. the standing manual test
    # order). Target is 556. The first submission-enabled session must
    # submit a delta of 555, not a fresh order for the full 556.
    target = {"DBC": 556}
    broker_shares = {"DBC": 1}
    delta = compute_first_session_delta(target, broker_shares)
    assert delta == {"DBC": 555}


def test_compute_first_session_delta_omits_symbols_already_at_target():
    target = {"DBC": 556, "SPY": 0}
    broker_shares = {"DBC": 556, "SPY": 0}
    delta = compute_first_session_delta(target, broker_shares)
    assert delta == {}
