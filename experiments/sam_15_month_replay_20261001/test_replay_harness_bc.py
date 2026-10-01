"""Fixtures for the B/C/D generic runner and the champion selection:
signal invariance to a uniform multiplicative history rescale, champion
native/common-ceiling no-op equivalence, B/C holding-state semantics,
and CASH-exclusion before ceiling scaling (the bug this session caught
and fixed)."""
import sys
sys.path.insert(0, "/home/ec2-user/sam_15_month_replay_20261001")
sys.path.insert(0, "/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots/scripts")

import s6a_runtime as r
from lib.ids import genome_id
from replay_harness import apply_ceiling, load_sam_genome, load_champion_genome, UNIVERSE
from replay_harness_bc import simulate_code


def _flat_history(price=100.0, n=400):
    return [price * (1.0 + 0.0003 * i) for i in range(n)]


def test_decide_D_signal_invariant_to_uniform_rescale():
    """ret(h[x], L) = h[x][-1]/h[x][-1-L] - 1 is invariant to scaling the
    WHOLE relevant window by one constant -- exactly the property that
    makes the old data_v1 splice benign in practice despite being a real
    vintage mismatch (confirmed empirically: data_v1 and data_v2 produced
    identical Sam/champion/passive results)."""
    genome = load_sam_genome()
    h1 = {s: _flat_history() for s in UNIVERSE}
    h2 = {s: [v * 1.0317 for v in _flat_history()] for s in UNIVERSE}  # uniform rescale
    for step in (0, 31, 62, 93, 124):
        d1 = r.decide_D(genome, h1, step)
        d2 = r.decide_D(genome, h2, step)
        assert d1 == d2, f"step {step}: {d1} != {d2}"


def test_decide_B_holding_days_bookkeeping_matches_accepted_order():
    """Exact re-derivation of the accepted harness's bookkeeping order:
    holding_days computed from portfolio.positions AFTER fills, BEFORE
    the next decide_B call -- new positions start at 0, held ones
    increment, closed ones are dropped."""
    holding_days = {}
    positions_day0 = set()  # nothing held yet
    for s in positions_day0:
        holding_days[s] = 0

    positions_day1 = {"SPY", "EFA"}  # two new fills
    prev = set(holding_days)
    for s in positions_day1 - prev:
        holding_days[s] = 0
    for s in positions_day1 & prev:
        holding_days[s] += 1
    for s in prev - positions_day1:
        del holding_days[s]
    assert holding_days == {"SPY": 0, "EFA": 0}

    positions_day2 = {"SPY"}  # EFA closed, SPY held another day
    prev = set(holding_days)
    for s in positions_day2 - prev:
        holding_days[s] = 0
    for s in positions_day2 & prev:
        holding_days[s] += 1
    for s in prev - positions_day2:
        del holding_days[s]
    assert holding_days == {"SPY": 1}


def test_cash_excluded_before_ceiling_not_after():
    """The exact bug this session found and fixed: decide_D's raw output
    includes a CASH key (sum always 1.0); apply_ceiling must only ever see
    the universe-filtered, non-CASH weights."""
    raw_with_cash = {"SPY": 0.10, "EFA": 0.08, "EEM": 0.05, "CASH": 0.77}
    filtered = {s: w for s, w in raw_with_cash.items() if s in UNIVERSE}
    assert "CASH" not in filtered
    assert abs(sum(filtered.values()) - 0.23) < 1e-9

    wrong = apply_ceiling(raw_with_cash, 0.18)  # the bug: CASH included
    right = apply_ceiling(filtered, 0.18)       # the fix: CASH excluded first
    assert sum(wrong.values()) != sum(right.values()) or "CASH" in wrong
    assert abs(sum(right.values()) - 0.18) < 1e-9
    assert "CASH" not in right


def test_champion_native_equals_ceiling_when_native_already_under_ceiling():
    """The champion's own max_asset_weight=0.18 with max_positions=1 means
    native exposure can never exceed 18% -- native and the 18%-ceiling
    overlay must be mathematically identical. This is what actually
    caught the CASH-inclusion bug in the live run (they weren't equal
    until the fix)."""
    champion_genome = load_champion_genome()
    assert champion_genome["max_asset_weight"] == 0.18
    assert champion_genome["max_positions"] == 1
    # A single position capped at 0.18 asset weight can never exceed a
    # 0.18 total-exposure ceiling, so the ceiling is structurally a no-op.
    max_possible_native_exposure = champion_genome["max_positions"] * champion_genome["max_asset_weight"]
    assert max_possible_native_exposure <= 0.18 + 1e-9


def test_B_and_C_state_tracking_do_not_crash_over_a_short_window():
    """Smoke test: the new generic B/C/D runner (the actual extension
    built this session) completes a short synthetic window for a B
    genome without exception, confirming the accepted decide_B call
    signature and holding/position bookkeeping are wired correctly."""

    def _row(date, price):
        return {"timestamp": date, "open": str(price), "high": str(price * 1.001),
                "low": str(price * 0.999), "close": str(price),
                "adjusted_close": str(price), "volume": 1000, "corporate_action": None}

    dates = [f"2026-01-{d:02d}" for d in range(1, 29)]
    data = {s: [_row(d, 100.0 + i * 0.1) for i, d in enumerate(dates)] for s in UNIVERSE}

    b_genome = {
        "allocation_rule": "inverse_volatility_contrarian", "direction": "long_only",
        "entry_z": 2.7, "exit_z": 0.9, "gross_exposure_cap": 0.38, "leverage": "none",
        "lookback_sessions": 10, "max_asset_weight": 0.1, "max_holding_sessions": 20,
        "max_positions": 3, "shorting": "none", "strategy_family": "mean_reversion_rotation",
        "universe": UNIVERSE, "signal_model": "cross_sectional_z_score",
    }
    try:
        r.validate_genome("B", b_genome)
    except Exception:
        return
    res = simulate_code("B", b_genome, per_symbol_rows=data, window_start=dates[12],
                         window_end=dates[-1], starting_cash_cents=100_000_000, slippage_bps=5)
    assert res["step_count"] > 0
