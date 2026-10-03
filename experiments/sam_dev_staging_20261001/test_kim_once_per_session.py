"""Regression tests for the v2 hardening of kim_once_per_session.py.
Run with: /home/ec2-user/validation/venv313/bin/python3 -m pytest -v test_kim_once_per_session.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots/scripts")

import pytest
from risk import RiskRefusal
import kim_once_per_session_v2 as k

GENOME = {
    "max_asset_weight": 0.25,
    "target_max_exposure": 1.0,
    "drawdown_halt_pct": 0.20,
    "rebalance_every_n_sessions": 50,
}


def test_no_sell_on_missing_price_even_if_decision_still_holds_it():
    # SPY is held (100 shares) but its price went unavailable this run.
    # The decision (however it got here) still assigns SPY a 0.0 weight --
    # the dangerous case where compute_orders would otherwise liquidate it.
    weights = {"SPY": 0.0, "EFA": 0.2}
    current_shares = {"SPY": 100, "EFA": 0}
    prices = {"SPY": 50000, "EFA": 2000}
    orders = k.compute_target_orders(
        weights, equity_cents=10_000_00, mark_prices_cents=prices,
        current_shares=current_shares, unavailable_symbols=["SPY"], genome=GENOME,
    )
    symbols_touched = {o["symbol"] for o in orders}
    assert "SPY" not in symbols_touched, f"SPY must never be touched when its price is unavailable: {orders}"


def test_no_sell_on_missing_price_when_decision_omits_symbol_entirely():
    # DBC is held but omitted from target_weights altogether (normally
    # compute_orders treats omission as an explicit liquidate-to-zero).
    # If DBC's price is unavailable this run, it must be left alone instead.
    weights = {"EFA": 0.2}
    current_shares = {"DBC": 50, "EFA": 0}
    prices = {"DBC": 2500, "EFA": 2000}
    orders = k.compute_target_orders(
        weights, equity_cents=10_000_00, mark_prices_cents=prices,
        current_shares=current_shares, unavailable_symbols=["DBC"], genome=GENOME,
    )
    symbols_touched = {o["symbol"] for o in orders}
    assert "DBC" not in symbols_touched


def test_available_symbol_omitted_from_weights_is_still_liquidated():
    # Control: when a symbol's price IS available and it's simply omitted
    # from target_weights, the existing liquidate-to-zero convention still
    # applies -- the hardening must not suppress real exits, only
    # missing-price-driven ones.
    weights = {"EFA": 0.2}
    current_shares = {"DBC": 50, "EFA": 0}
    prices = {"DBC": 2500, "EFA": 2000}
    orders = k.compute_target_orders(
        weights, equity_cents=10_000_00, mark_prices_cents=prices,
        current_shares=current_shares, unavailable_symbols=[], genome=GENOME,
    )
    dbc_orders = [o for o in orders if o["symbol"] == "DBC"]
    assert len(dbc_orders) == 1 and dbc_orders[0]["side"] == "sell"


def test_rejects_non_finite_weight():
    weights = {"SPY": math.nan}
    with pytest.raises(ValueError):
        k.compute_target_orders(
            weights, equity_cents=10_000_00, mark_prices_cents={"SPY": 50000},
            current_shares={}, unavailable_symbols=[], genome=GENOME,
        )


def test_rejects_non_finite_equity():
    with pytest.raises(ValueError):
        k.compute_target_orders(
            {"SPY": 0.1}, equity_cents=math.inf, mark_prices_cents={"SPY": 50000},
            current_shares={}, unavailable_symbols=[], genome=GENOME,
        )


def test_risk_validate_is_actually_wired_in():
    # A weight that exceeds max_asset_weight must raise RiskRefusal --
    # proves risk.validate() is really being called, not bypassed.
    weights = {"SPY": 0.9}  # > GENOME["max_asset_weight"] == 0.25
    with pytest.raises(RiskRefusal):
        k.compute_target_orders(
            weights, equity_cents=10_000_00, mark_prices_cents={"SPY": 50000},
            current_shares={}, unavailable_symbols=[], genome=GENOME,
        )


def test_risk_validate_drawdown_halt_not_enforced_in_shadow_mode():
    # Documents the explicit, logged limitation: current_drawdown is
    # always passed as 0.0 in shadow mode (no peak-equity history yet),
    # so a real drawdown halt would NOT fire here even though risk.validate
    # is wired in for weight/exposure/finite checks.
    weights = {"SPY": 0.1}
    orders = k.compute_target_orders(
        weights, equity_cents=10_000_00, mark_prices_cents={"SPY": 50000},
        current_shares={}, unavailable_symbols=[], genome=GENOME,
        current_drawdown=0.0,
    )
    assert isinstance(orders, list)  # does not raise despite genome's 20% halt existing


def test_pending_buy_order_reduces_recommended_additional_buy():
    positions = [{"symbol": "DBC", "qty": "0"}]
    orders = [{"symbol": "DBC", "side": "buy", "qty": "1", "filled_qty": "0", "status": "new"}]
    current = k.shares_including_pending(positions, orders)
    assert current["DBC"] == 1


def test_pending_sell_order_is_subtracted():
    positions = [{"symbol": "DBC", "qty": "10"}]
    orders = [{"symbol": "DBC", "side": "sell", "qty": "3", "filled_qty": "0", "status": "accepted"}]
    current = k.shares_including_pending(positions, orders)
    assert current["DBC"] == 7


def test_terminal_order_statuses_are_ignored():
    positions = [{"symbol": "DBC", "qty": "0"}]
    for status in ("filled", "canceled", "expired", "rejected"):
        orders = [{"symbol": "DBC", "side": "buy", "qty": "5", "filled_qty": "0", "status": status}]
        current = k.shares_including_pending(positions, orders)
        assert current.get("DBC", 0) == 0, f"status={status} must not affect current shares"


def test_partial_fill_uses_remaining_quantity_only():
    positions = [{"symbol": "DBC", "qty": "0"}]
    orders = [{"symbol": "DBC", "side": "buy", "qty": "10", "filled_qty": "6", "status": "partially_filled"}]
    current = k.shares_including_pending(positions, orders)
    assert current["DBC"] == 4


def test_end_to_end_standing_dbc_order_yields_548_not_549():
    # Reproduces tonight's actual standing state: 1 share DBC pending buy,
    # 0 held. A target of 549 shares must net to a recommended delta of
    # 548, not a duplicate-ignoring 549.
    positions = []
    orders = [{"symbol": "DBC", "side": "buy", "qty": "1", "filled_qty": "0", "status": "new"}]
    current = k.shares_including_pending(positions, orders)
    assert current["DBC"] == 1
    weights = {"DBC": 0.1830}
    prices = {"DBC": 2000}  # arbitrary mark price in cents
    result = k.compute_target_orders(
        weights, equity_cents=6_000_000_00, mark_prices_cents=prices,
        current_shares=current, unavailable_symbols=[], genome=GENOME,
    )
    dbc = [o for o in result if o["symbol"] == "DBC"]
    assert len(dbc) == 1
    expected_target = math.floor(0.1830 * 6_000_000_00 / 2000)
    assert dbc[0]["shares"] == expected_target - 1



def test_broker_open_order_count_excludes_terminal_statuses():
    # Regression for the 2026-10-03 overnight finding: broker_open_order_count
    # was literally len(orders) from a status=all query, i.e. a total-order
    # count mislabeled as "open." It must only count live (non-terminal)
    # statuses, matching the same LIVE_ORDER_STATUSES set shares_including_pending
    # already uses correctly.
    orders = [
        {"status": "filled"},
        {"status": "canceled"},
        {"status": "new"},
        {"status": "partially_filled"},
        {"status": "expired"},
    ]
    assert k.count_live_orders(orders) == 2
    assert k.count_live_orders([]) == 0
    assert k.count_live_orders(None) == 0

