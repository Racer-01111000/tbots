"""S6B episode simulator: the population-execution entrypoint S6A intentionally
did not ship (see s6a_runtime.py's module docstring). Builds on top of, and
never modifies, the frozen S6A operators/execution/risk primitives.

Reuses the S5A frozen development episode manifest verbatim (same dataset
revision, same DEVELOPMENT lane, same 12 rolling-anniversary-year windows
2007-02-07..2018-12-31) -- S6A's HISTORY protocol names this same lane and
episode count (12) but does not mint a new episode-manifest hash of its own,
so there is exactly one frozen source of truth for "what are the 12
development episodes" and this reuses it rather than re-deriving dates.

Per-episode fresh state: $1,000,000 cash, empty portfolio, fresh peak
equity/drawdown/halt -- one call to simulate_episode() per (genome, episode).
"""
from __future__ import annotations
import hashlib
import math
import statistics

import execution
import risk
import s6a_final as p
import s6a_runtime as r
from lib.ids import canonical_json
from lib.replay import AgentView, ReplayEngine
from s5a_config import EPISODE_PROTOCOL

STARTING_CASH_CENTS = p.EXECUTION["starting_cash_cents"]
LANE_END = p.HISTORY["development"]["end"]
ANNUALIZATION = 252  # matches s5a's frozen daily_return_annualization; S6A
                      # shares s5a's exact fitness formula/weights and defines
                      # no S6-specific constant of its own.
DECIDE_ARITY = {"B": "positions_holding", "C": "positions", "D": "step",
                "E": "step", "F": "step", "G": "weights"}


def dollars_to_cents(value: str) -> int:
    return round(float(value) * 100)


def episode_sharpe(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    stdev = statistics.stdev(daily_returns)
    if stdev == 0:
        return 0.0
    return statistics.mean(daily_returns) / stdev * math.sqrt(ANNUALIZATION)


def risk_bounds(code: str, genome: dict) -> tuple[float, float]:
    """Derived only from each genome's own already-validated frozen-schema
    fields -- never a new hardcoded constant. B/C/D/E/F carry
    max_asset_weight/gross_exposure_cap directly. G has neither field (its
    schema instead bounds each target_weights_bps item to <=4000 and forces
    their sum to exactly 10000), so its risk bounds are read off those same
    frozen bounds rather than invented."""
    if code == "G":
        return max(genome["target_weights_bps"]) / 10000, sum(genome["target_weights_bps"]) / 10000
    return genome["max_asset_weight"], genome["gross_exposure_cap"]


def require_admission_token(bundle, code: str, genome: dict, admission,
                            *, availability=None) -> r.CandidateAdmission:
    """Reject forged, stale, cross-lineage, or wrong-dataset admission tokens."""
    expected = r.require_development_feasible(
        code, genome, bundle=bundle if availability is None else None,
        availability=availability,
    )
    if not isinstance(admission, r.CandidateAdmission) or admission != expected:
        raise r.FeasibilityError("invalid development-feasibility admission token")
    return admission


def _simulate_admitted_episode(bundle, code: str, genome: dict,
                               episode_index: int) -> dict:
    """Run one fresh-capital development episode. No state is carried in
    from any other episode or any other genome."""
    r.validate_genome(code, genome)
    episodes = EPISODE_PROTOCOL["episodes"]
    if not 0 <= episode_index < len(episodes):
        raise ValueError("episode_index is outside the frozen DEVELOPMENT manifest")
    episode = episodes[episode_index]
    engine = ReplayEngine(
        p.ROOT, p.DATASET, episode["start_date"], episode["end_date"],
        masked_time=True, random_seed=0, retention_end_date=LANE_END,
        _verified_bundle=bundle,
    )
    view = AgentView(engine)
    universe = p.UNIVERSE
    portfolio = execution.Portfolio(STARTING_CASH_CENTS)
    pending = None
    equity_curve = []
    order_count = 0
    total_slippage_cents = 0
    holding_days: dict[str, int] = {}
    last_traded: dict[str, int] = {s: 0 for s in universe}
    step = 0

    needed = r.warmup(code, genome)
    h = {s: [float(row["adjusted_close"]) for row in view.history(s, needed)] for s in universe} \
        if needed > 0 else {s: [] for s in universe}

    while True:
        full = engine.observe()
        obs = view.observe()
        assets = obs["assets"]

        if step > 0:
            for s in universe:
                h[s].append(float(assets[s]["adjusted_close"]))

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

        mark_prices = {s: dollars_to_cents(assets[s]["close"]) for s in universe}
        equity_cents = portfolio.equity_cents(mark_prices)
        drawdown = portfolio.update_peak_and_drawdown(equity_cents)

        target_weights = None
        current_g_weights = None
        if drawdown <= -p.DRAWDOWN_HALT - 1e-9 and not portfolio.halted:
            portfolio.halted = True
            target_weights = {}
        elif not portfolio.halted:
            if code == "B":
                target_weights = r.decide_B(genome, h, set(portfolio.positions), holding_days)
            elif code == "C":
                target_weights = r.decide_C(genome, h, set(portfolio.positions))
            elif code == "D":
                target_weights = r.decide_D(genome, h, step)
            elif code == "E":
                target_weights = r.decide_E(genome, h, step)
            elif code == "F":
                target_weights = r.decide_F(genome, h, step)
            elif code == "G":
                current_g_weights = {
                    s: (portfolio.shares_of(s) * mark_prices[s]) / equity_cents if equity_cents else 0.0
                    for s in universe
                }
                holding = {s: step - last_traded.get(s, 0) for s in universe}
                target_weights = r.decide_G(genome, current_g_weights, holding, step, initial=(step == 0))

        if target_weights is not None:
            # decide_X's _cash() helper folds a residual "CASH" entry into every
            # weights dict; CASH is not a tradeable symbol so it never reaches
            # the risk gate or the order builder, both of which iterate over
            # exactly `universe`.
            target_weights = {s: w for s, w in target_weights.items() if s in universe}
            max_asset_weight, max_total_exposure = risk_bounds(code, genome)
            risk.validate(
                target_weights, universe, max_asset_weight=max_asset_weight,
                max_total_exposure=max_total_exposure,
                drawdown_halt_pct=p.DRAWDOWN_HALT, current_drawdown=drawdown,
            )
            current_shares = {s: portfolio.shares_of(s) for s in universe if portfolio.shares_of(s) > 0}
            pending = execution.compute_orders(target_weights, universe, equity_cents, mark_prices, current_shares)
            order_count += len(pending)
            if code == "G" and current_g_weights is not None:
                for s in universe:
                    if abs(target_weights.get(s, 0.0) - current_g_weights.get(s, 0.0)) > 1e-9:
                        last_traded[s] = step

        equity_curve.append(equity_cents)
        step += 1
        if not engine.advance():
            break

    daily_returns = [
        equity_curve[i] / equity_curve[i - 1] - 1.0 if equity_curve[i - 1] else 0.0
        for i in range(1, len(equity_curve))
    ]
    final_equity_cents = equity_curve[-1]
    max_drawdown = min(
        equity / max(equity_curve[:index + 1]) - 1.0
        for index, equity in enumerate(equity_curve)
    )
    transaction_cost_cents = portfolio.total_commission_cents + total_slippage_cents
    return {
        "episode_index": episode["episode_index"],
        "start_date": episode["start_date"],
        "end_date": episode["end_date"],
        "starting_cash_cents": STARTING_CASH_CENTS,
        "final_equity_cents": final_equity_cents,
        "total_return": final_equity_cents / STARTING_CASH_CENTS - 1.0,
        "sharpe": episode_sharpe(daily_returns),
        "max_drawdown": max_drawdown,
        "halted": portfolio.halted,
        "turnover": portfolio.total_traded_notional_cents / STARTING_CASH_CENTS,
        "commission_cents": portfolio.total_commission_cents,
        "slippage_cents": total_slippage_cents,
        "transaction_cost_cents": transaction_cost_cents,
        "transaction_cost_rate": transaction_cost_cents / STARTING_CASH_CENTS,
        "order_count": order_count,
        "fill_count": portfolio.fill_count,
        "step_count": step,
    }


def simulate_episode(bundle, code: str, genome: dict, episode_index: int, *,
                     admission, availability=None) -> dict:
    """Public single-episode fixture surface; admission always comes first."""
    require_admission_token(
        bundle, code, genome, admission, availability=availability,
    )
    return _simulate_admitted_episode(bundle, code, genome, episode_index)


def evaluate_genome(bundle, code: str, genome: dict, *, admission,
                    availability=None) -> dict:
    """Evaluate one admitted genome over the complete frozen DEVELOPMENT lane."""
    admission = require_admission_token(
        bundle, code, genome, admission, availability=availability,
    )
    episode_count = len(EPISODE_PROTOCOL["episodes"])
    episodes = [
        _simulate_admitted_episode(bundle, code, genome, index)
        for index in range(episode_count)
    ]
    aggregate = r.compute_fitness(
        episodes, episode_count, admission=admission,
    )
    return {
        "episode_metrics": episodes,
        "aggregate": aggregate,
        "metrics_hash": hashlib.sha256(
            canonical_json({"episodes": episodes, "aggregate": aggregate}).encode()
        ).hexdigest(),
    }
