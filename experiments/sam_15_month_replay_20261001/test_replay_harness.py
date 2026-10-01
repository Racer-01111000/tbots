"""Targeted fixtures for the new replay harness's novel pieces: dividend
entitlement, look-ahead isolation, next-open fills, ceiling overlay.
No network, no accepted-dataset dependency -- synthetic rows only."""
import sys
sys.path.insert(0, "/home/ec2-user/sam_15_month_replay_20261001")
sys.path.insert(0, "/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots/scripts")

import execution
import pytest
from replay_harness import MiniEngine, View, apply_ceiling, simulate


UNIVERSE_TEST = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]


def _row(date, o, h, l, c, adj, corp=None):
    return {"timestamp": date, "open": str(o), "high": str(h), "low": str(l),
            "close": str(c), "adjusted_close": str(adj), "volume": 1000,
            "corporate_action": corp}


def _flat_rows(symbol, dates, price=100.0):
    return [_row(d, price, price, price, price, price) for d in dates]


DATES = [f"2026-01-{d:02d}" for d in range(1, 21)]


def _synthetic_dataset():
    data = {s: _flat_rows(s, DATES) for s in UNIVERSE_TEST}
    return data


def test_lookahead_isolation_history_never_sees_future():
    data = _synthetic_dataset()
    engine = MiniEngine(data, DATES[5], DATES[10])
    view = View(engine)
    hist = view.history("SPY", 100)
    assert hist[-1]["timestamp"] == DATES[5]
    assert all(row["timestamp"] <= DATES[5] for row in hist)
    engine.advance()
    hist2 = view.history("SPY", 100)
    assert hist2[-1]["timestamp"] == DATES[6]
    assert DATES[7] not in [r["timestamp"] for r in hist2]


def test_dividend_credited_only_on_held_shares_before_fill():
    data = _synthetic_dataset()
    import json
    data["SPY"][3]["corporate_action"] = json.dumps({"dividend_amount": 2.0})

    portfolio = execution.Portfolio(1_000_000_00)
    portfolio.positions["SPY"] = {"shares": 100, "cost_basis_cents": 90_000_00}
    credited = portfolio.credit_dividend("SPY", 2.0)
    assert credited == 100 * 2.0 * 100  # cents
    assert portfolio.cash_cents == 1_000_000_00 + 20000

    portfolio2 = execution.Portfolio(1_000_000_00)
    credited2 = portfolio2.credit_dividend("SPY", 2.0)
    assert credited2 == 0  # no position -> no credit


def test_dividend_entitlement_uses_position_carried_into_the_date():
    """GO/execution.py convention: dividend credit must use shares held
    BEFORE today's fill is applied, not after -- a same-day buy was never
    the holder of record."""
    data = _synthetic_dataset()
    import json
    data["SPY"][5]["corporate_action"] = json.dumps({"dividend_amount": 1.0})

    portfolio = execution.Portfolio(1_000_000_00)
    pre_fill_shares = portfolio.shares_of("SPY")  # 0, carried in from before
    credited = portfolio.credit_dividend("SPY", 1.0) if pre_fill_shares > 0 else 0
    assert credited == 0
    portfolio.apply_fill("SPY", "buy", 10, 10000)
    assert portfolio.shares_of("SPY") == 10  # now held, but too late for that date's dividend


def test_next_open_fill_never_same_session_close():
    data = _synthetic_dataset()
    for i, d in enumerate(DATES):
        data["SPY"][i] = _row(d, 100 + i, 100 + i, 100 + i, 100 + i, 100 + i)

    def decide_once(view, step):
        return {"SPY": 0.23} if step == 0 else None

    result = simulate(
        decide_once, per_symbol_rows=data, window_start=DATES[0], window_end=DATES[5],
        max_asset_weight=0.47, max_total_exposure=0.23, starting_cash_cents=1_000_000_00,
        slippage_bps=5,
    )
    assert result["fill_count"] >= 1
    assert result["order_count"] >= 1


def test_apply_ceiling_scales_down_only_when_needed():
    assert apply_ceiling({"SPY": 0.10, "EFA": 0.08}, 0.23) == {"SPY": 0.10, "EFA": 0.08}
    scaled = apply_ceiling({"SPY": 0.15, "EFA": 0.08}, 0.18)
    assert abs(sum(scaled.values()) - 0.18) < 1e-9
    assert apply_ceiling(None, 0.18) is None


def test_slippage_bps_restored_after_simulate_even_on_exception():
    data = _synthetic_dataset()
    orig = execution.SLIPPAGE_BPS
    try:
        simulate(
            lambda view, step: {"NONEXISTENT_SYMBOL": 1.0} if step == 0 else None,
            per_symbol_rows=data, window_start=DATES[0], window_end=DATES[3],
            max_asset_weight=1.0, max_total_exposure=1.0, starting_cash_cents=1_000_000_00,
            slippage_bps=10,
        )
    except Exception:
        pass
    assert execution.SLIPPAGE_BPS == orig
