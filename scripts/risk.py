"""S4.9: the hard risk gate. External to the agent -- the control
organism cannot override or bypass this. Every proposed target-weight
set must pass validate() before a single order is placed. Drawdown is
enforced here independently of the agent's own halt logic, so a bug in
the agent's self-reported halted state cannot open a new position past
the limit."""
import math

EPS = 1e-9


class RiskRefusal(Exception):
    pass


def validate(target_weights: dict, universe: list, *, max_asset_weight: float,
             max_total_exposure: float, drawdown_halt_pct: float, current_drawdown: float) -> None:
    for symbol, w in target_weights.items():
        if symbol not in universe:
            raise RiskRefusal(f"unknown asset: {symbol}")
        if not math.isfinite(w):
            raise RiskRefusal(f"invalid target: non-finite weight for {symbol}: {w}")
        if w < 0:
            raise RiskRefusal(f"shorting refused: {symbol} weight {w} < 0")
        if w > max_asset_weight + EPS:
            raise RiskRefusal(f"max asset weight exceeded: {symbol} {w} > {max_asset_weight}")

    total = sum(target_weights.values())
    if total > max_total_exposure + EPS:
        raise RiskRefusal(f"max total exposure exceeded: {total} > {max_total_exposure}")

    if current_drawdown <= -drawdown_halt_pct - EPS and any(w > EPS for w in target_weights.values()):
        raise RiskRefusal(
            f"drawdown halt in effect: current drawdown {current_drawdown:.4f} <= "
            f"-{drawdown_halt_pct}; no new positions permitted regardless of agent state"
        )
