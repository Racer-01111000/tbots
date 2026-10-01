"""Generic B/C/D runner addition to replay_harness.py -- reuses the exact
accepted state-bookkeeping from experiments/controls_comparison_20260921T064013Z/harness.py:
_simulate_bcd_with_curve (holding_days updated from portfolio.positions
BEFORE decide_B/C is called; decide_D takes step, not positions)."""
import sys
sys.path.insert(0, "/home/ec2-user/sam_15_month_replay_20261001")
from replay_harness import (
    MiniEngine, View, UNIVERSE, DRAWDOWN_HALT, dollars_to_cents, _finalize, apply_ceiling,
)
import execution, risk, s6a_runtime as r, s6b_evaluator as s6b


def simulate_code(code, genome, *, per_symbol_rows, window_start, window_end,
                   starting_cash_cents, slippage_bps, ceiling=None):
    max_asset_weight, max_total_exposure = s6b.risk_bounds(code, genome)
    if ceiling is not None:
        max_asset_weight = min(max_asset_weight, ceiling)
        max_total_exposure = ceiling
    orig_slippage = execution.SLIPPAGE_BPS
    execution.SLIPPAGE_BPS = slippage_bps
    try:
        engine = MiniEngine(per_symbol_rows, window_start, window_end)
        view = View(engine)
        portfolio = execution.Portfolio(starting_cash_cents)
        pending = None
        equity_curve = []
        dates = []
        order_count = 0
        total_slippage_cents = 0
        holding_days = {}
        step = 0

        needed = r.warmup(code, genome)
        h = {s: [float(row["adjusted_close"]) for row in view.history(s, needed)] for s in UNIVERSE} \
            if needed > 0 else {s: [] for s in UNIVERSE}

        while True:
            obs = view.observe()
            assets = obs["assets"]
            today = obs["timestamp"]

            if step > 0:
                for s in UNIVERSE:
                    h[s].append(float(assets[s]["adjusted_close"]))

            for s in UNIVERSE:
                div = execution.dividend_amount_dollars(assets[s].get("corporate_action"))
                if div is not None:
                    portfolio.credit_dividend(s, div)

            if pending is not None:
                for order in pending:
                    open_cents = dollars_to_cents(assets[order["symbol"]]["open"])
                    fill = portfolio.apply_fill(order["symbol"], order["side"], order["shares"], open_cents)
                    total_slippage_cents += fill["slippage_cents"]
                pending = None

            if code == "B":
                prev_held = set(holding_days)
                current_held = set(portfolio.positions)
                for s in current_held - prev_held:
                    holding_days[s] = 0
                for s in current_held & prev_held:
                    holding_days[s] += 1
                for s in prev_held - current_held:
                    del holding_days[s]

            mark_prices = {s: dollars_to_cents(assets[s]["close"]) for s in UNIVERSE}
            equity_cents = portfolio.equity_cents(mark_prices)
            drawdown = portfolio.update_peak_and_drawdown(equity_cents)

            target_weights = None
            if drawdown <= -DRAWDOWN_HALT - 1e-9 and not portfolio.halted:
                portfolio.halted = True
                target_weights = {}
            elif not portfolio.halted:
                if code == "B":
                    target_weights = r.decide_B(genome, h, set(portfolio.positions), holding_days)
                elif code == "C":
                    target_weights = r.decide_C(genome, h, set(portfolio.positions))
                elif code == "D":
                    target_weights = r.decide_D(genome, h, step)
                if target_weights is not None:
                    target_weights = {s: w for s, w in target_weights.items() if s in UNIVERSE}
                if ceiling is not None:
                    target_weights = apply_ceiling(target_weights, ceiling)

            if target_weights is not None:
                risk.validate(
                    target_weights, UNIVERSE, max_asset_weight=max_asset_weight,
                    max_total_exposure=max_total_exposure,
                    drawdown_halt_pct=DRAWDOWN_HALT, current_drawdown=drawdown,
                )
                current_shares = {s: portfolio.shares_of(s) for s in UNIVERSE if portfolio.shares_of(s) > 0}
                pending = execution.compute_orders(target_weights, UNIVERSE, equity_cents, mark_prices, current_shares)
                order_count += len(pending)

            equity_curve.append(equity_cents)
            dates.append(today)
            step += 1
            if not engine.advance():
                break

        return _finalize(equity_curve, portfolio, order_count, total_slippage_cents, step,
                          starting_cash_cents, window_start, window_end,
                          {"dates": dates, "slippage_bps_used": slippage_bps})
    finally:
        execution.SLIPPAGE_BPS = orig_slippage
