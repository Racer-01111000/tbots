"""Offline checks of the blind-week harness on OLD data only (data/normalized, ends 2026-08-25): parity with the
accepted evaluator arms, halt/ceiling behaviour, and that no global state leaks. The test week is never touched."""
import csv
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DEV = HERE.parent
REPO = DEV.parents[1]
for p in (str(HERE), str(DEV), str(DEV / "alpaca_adapter"), str(DEV / "kim_order_simulation_tests"),
          str(REPO / "scripts"), str(REPO / "scripts" / "lib")):
    sys.path.insert(0, p)

import execution
import s6a_final as p6
import run_blind_week as rb
import shadow_compare as sc

WINDOW = ("2025-07-01", "2026-08-25")
START = 100_000_000
FIELDS = ("open", "high", "low", "close", "adjusted_close", "volume", "corporate_action", "timestamp")


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("replay_harness", REPO / "experiments/sam_15_month_replay_20261001/replay_harness.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def raw_rows():
    out = {}
    for s in p6.UNIVERSE:
        with open(REPO / "data/normalized" / f"{s}.csv") as f:
            out[s] = [{k: row[k] for k in FIELDS} for row in csv.DictReader(f)]
    return out


@pytest.fixture(scope="module")
def shadow_rows(raw_rows):
    return {s: [{"timestamp": r["timestamp"], "open": float(r["open"]), "close": float(r["close"]),
                 "adjusted_close": float(r["adjusted_close"]),
                 "dividend": execution.dividend_amount_dollars(r["corporate_action"] or None)} for r in v]
            for s, v in raw_rows.items()}


def dates_of(shadow_rows):
    return [r["timestamp"] for r in shadow_rows["SPY"] if WINDOW[0] <= r["timestamp"] <= WINDOW[1]]


@pytest.mark.parametrize("slip", [5, 10])
def test_sam_overlay_matches_the_accepted_evaluator_arm_exactly(harness, raw_rows, shadow_rows, slip):
    g = rb.load_sam()
    acc = harness.simulate(None, per_symbol_rows=raw_rows, window_start=WINDOW[0], window_end=WINDOW[1],
                           max_asset_weight=0.18, max_total_exposure=0.18, starting_cash_cents=START,
                           slippage_bps=slip, use_decide_D=True, genome_for_D=g, ceiling=0.18)
    dates = dates_of(shadow_rows)
    books, recs = rb.run_replay(shadow_rows, dates, halt_pct=0.12, slippage_bps=slip, ceiling=0.18, start_cents=START, g=g)
    s = rb.summarize(books, recs, shadow_rows, dates, START)["sam"]
    assert s["end_equity_cents"] == acc["final_equity_cents"]
    assert s["orders_placed"] == acc["order_count"] and s["fills"] == acc["fill_count"]
    assert s["traded_notional_cents"] / START == acc["turnover"]
    assert s["total_cost_cents"] == acc["transaction_cost_cents"]


def test_passive_and_cash_controls_match_the_accepted_arms(harness, raw_rows, shadow_rows):
    dates = dates_of(shadow_rows)
    books, recs = rb.run_replay(shadow_rows, dates, halt_pct=0.12, slippage_bps=10, ceiling=0.18, start_cents=START)
    out = rb.summarize(books, recs, shadow_rows, dates, START)
    acc_p = harness.simulate(harness.make_passive_decide_fn(0.18), per_symbol_rows=raw_rows, window_start=WINDOW[0],
                             window_end=WINDOW[1], max_asset_weight=0.18 / 8 + 1e-6, max_total_exposure=0.18,
                             starting_cash_cents=START, slippage_bps=10)
    acc_c = harness.simulate(harness.no_trade_decide_fn, per_symbol_rows=raw_rows, window_start=WINDOW[0],
                             window_end=WINDOW[1], max_asset_weight=1.0, max_total_exposure=1.0,
                             starting_cash_cents=START, slippage_bps=10)
    assert out["passive"]["end_equity_cents"] == acc_p["final_equity_cents"]
    assert out["cash"]["end_equity_cents"] == acc_c["final_equity_cents"] == START


def test_run_replay_restores_global_assumptions(shadow_rows):
    before = (p6.DRAWDOWN_HALT, execution.SLIPPAGE_BPS)
    rb.run_replay(shadow_rows, dates_of(shadow_rows)[:5], halt_pct=0.08, slippage_bps=10, ceiling=0.18, start_cents=START)
    assert (p6.DRAWDOWN_HALT, execution.SLIPPAGE_BPS) == before


def test_pilot_eight_percent_halt_liquidates_by_quantity_in_the_replay(shadow_rows):
    """Synthetic crash after the entry: the 8% halt must latch and sell the whole position."""
    dates = dates_of(shadow_rows)[:6]
    rows = {s: [dict(x) for x in v] for s, v in shadow_rows.items()}
    for s in rows:
        for x in rows[s]:
            if x["timestamp"] > dates[2]:
                x["close"] *= 0.5            # crash every instrument by 50% after the third session
                x["open"] *= 0.5
    books, recs = rb.run_replay(rows, dates, halt_pct=0.08, slippage_bps=10, ceiling=0.18, start_cents=START)
    sam = rb.summarize(books, recs, rows, dates, START)["sam"]
    # 18% of equity in the crash, down 50% => ~9% account drawdown > 8%: halted and flat afterwards
    assert sam["halted"] is True and sam["ending_positions_shares"] == {}


def test_ceiling_is_never_exceeded(shadow_rows):
    dates = dates_of(shadow_rows)[:80]
    books, recs = rb.run_replay(shadow_rows, dates, halt_pct=0.08, slippage_bps=10, ceiling=0.18, start_cents=START)
    assert all(x["books"]["sam"]["standing_exposure"] <= 0.18 + 1e-9 for x in recs)
    assert all(len(x["books"]["sam"]["orders_placed"]) <= 4 or x["books"]["sam"]["halted_now"] for x in recs)


def test_sam_is_not_the_champion_and_not_the_other_d_candidate():
    g = rb.load_sam()
    assert rb.genome_id(g) == "gen_8e7a622b48276bc68b7b70773bb9e2a5250dbb82584c53f6c67416bd6ffdf0cf"
    assert rb.genome_id(g) not in ("gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155",
                                   "gen_d890eb9faa7bf0309773c85620eb6652ad589ab82d6fbff4423ddcfbbb460f85")
    assert g["regime_window"] == 122 and g["rebalance_sessions"] == 31 and g["gross_exposure_cap"] == 0.23
