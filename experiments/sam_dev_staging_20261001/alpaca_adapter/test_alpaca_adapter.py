"""Fixture-based tests for the isolated Alpaca adapter scaffold.

No network access anywhere in this file. All Alpaca responses are
disposable, inline fixture dicts -- never real API output.
"""
from datetime import datetime, timedelta, timezone

import pytest

from alpaca_adapter import (
    DuplicateOrderError,
    HostNotAllowedError,
    IntendedOrder,
    LIVE_TRADING_HOST_BLOCKED,
    MarketClock,
    PAPER_TRADING_HOST,
    PaperOrderLedger,
    ReconciledOrder,
    OrderStatus,
    StaleClockError,
    SubmissionDisabledError,
    assert_trading_host_allowed,
    require_fresh_clock,
    route_order,
    submission_enabled,
)


def test_paper_host_is_allowed():
    assert_trading_host_allowed(PAPER_TRADING_HOST)  # does not raise


def test_live_host_is_blocked():
    with pytest.raises(HostNotAllowedError):
        assert_trading_host_allowed(LIVE_TRADING_HOST_BLOCKED)


def test_unknown_host_is_blocked():
    with pytest.raises(HostNotAllowedError):
        assert_trading_host_allowed("https://totally-not-alpaca.example.com")


def test_submission_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TBOTS_ALPACA_SUBMISSION_ENABLED", raising=False)
    assert submission_enabled() is False


def test_submission_stays_disabled_even_with_env_flag_because_const_is_false(monkeypatch):
    # The env flag alone must never be sufficient -- the hardcoded constant
    # also has to be True, and it is not, in this scaffold.
    monkeypatch.setenv("TBOTS_ALPACA_SUBMISSION_ENABLED", "true-i-understand-the-risk")
    assert submission_enabled() is False


FIXTURE_CLOCK_FRESH = {
    "timestamp": "2026-10-01T10:00:00+00:00",
    "is_open": True,
    "next_open": "2026-10-02T13:30:00+00:00",
    "next_close": "2026-10-01T20:00:00+00:00",
}

FIXTURE_CLOCK_STALE = {
    "timestamp": "2026-10-01T09:00:00+00:00",  # 1 hour old
    "is_open": True,
    "next_open": "2026-10-02T13:30:00+00:00",
    "next_close": "2026-10-01T20:00:00+00:00",
}


def test_clock_parses_fixture_payload():
    clock = MarketClock.from_api(FIXTURE_CLOCK_FRESH)
    assert clock.is_open is True


def test_fresh_clock_passes():
    clock = MarketClock.from_api(FIXTURE_CLOCK_FRESH)
    now = datetime(2026, 10, 1, 10, 0, 10, tzinfo=timezone.utc)  # 10s later
    require_fresh_clock(clock, now=now)  # does not raise


def test_stale_clock_fails_closed():
    clock = MarketClock.from_api(FIXTURE_CLOCK_STALE)
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(StaleClockError):
        require_fresh_clock(clock, now=now)


def _order(coid="coid-1"):
    return IntendedOrder(
        client_order_id=coid, symbol="SPY", side="buy", qty=1,
        order_type="market", time_in_force="day",
    )


def test_ledger_records_intent_once():
    ledger = PaperOrderLedger()
    ledger.record_intent(_order())
    assert ledger.is_duplicate("coid-1") is True


def test_ledger_rejects_duplicate_client_order_id():
    ledger = PaperOrderLedger()
    ledger.record_intent(_order("coid-dup"))
    with pytest.raises(DuplicateOrderError):
        ledger.record_intent(_order("coid-dup"))


def test_ledger_reconcile_unknown_order_rejected():
    ledger = PaperOrderLedger()
    with pytest.raises(RuntimeError):
        ledger.reconcile(ReconciledOrder("never-submitted", "broker-1", OrderStatus.NEW))


def test_ledger_reconcile_known_order_updates_status():
    ledger = PaperOrderLedger()
    ledger.record_intent(_order("coid-2"))
    ledger.reconcile(ReconciledOrder("coid-2", "broker-2", OrderStatus.FILLED, filled_qty=1))
    assert ledger.status_of("coid-2") == OrderStatus.FILLED


def test_route_order_refuses_live_host_before_touching_ledger():
    ledger = PaperOrderLedger()
    with pytest.raises(HostNotAllowedError):
        route_order(_order("coid-3"), host=LIVE_TRADING_HOST_BLOCKED, ledger=ledger)
    assert ledger.is_duplicate("coid-3") is False  # never recorded -- host check happens first


def test_route_order_records_intent_then_refuses_because_submission_disabled():
    ledger = PaperOrderLedger()
    with pytest.raises(SubmissionDisabledError):
        route_order(_order("coid-4"), host=PAPER_TRADING_HOST, ledger=ledger)
    assert ledger.is_duplicate("coid-4") is True  # intent WAS recorded


def test_route_order_second_attempt_is_rejected_as_duplicate_not_resubmitted():
    ledger = PaperOrderLedger()
    with pytest.raises(SubmissionDisabledError):
        route_order(_order("coid-5"), host=PAPER_TRADING_HOST, ledger=ledger)
    with pytest.raises(DuplicateOrderError):
        route_order(_order("coid-5"), host=PAPER_TRADING_HOST, ledger=ledger)


def test_strategy_config_cannot_enable_submission():
    """A strategy/genome config dict has no code path into submission_enabled()
    or route_order()'s gate -- there is no parameter here that accepts one."""
    import inspect

    sig = inspect.signature(route_order)
    assert "config" not in sig.parameters
    assert "strategy_config" not in sig.parameters
