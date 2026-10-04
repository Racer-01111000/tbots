"""Pure order-path guards for the Kim session runner: cash constraint,
post-trade limit check, and sells-before-buys spec construction. No I/O."""
from __future__ import annotations

import math

from kim_order_logic import IntendedOrderSpec, build_client_order_id


class OrderGuardError(RuntimeError):
    """A computed order set violates a hard pilot limit; nothing is sent."""


def constrain_buys_to_cash(orders: list[dict], prices_cents: dict, cash_cents: int,
                           equity_cents: int, reserve_pct: float) -> tuple[list[dict], list[str]]:
    """No-borrow rule. Buys may only spend cash above a reserve; proceeds of
    same-batch sells get NO credit (they fill at the open, not before the
    buys are accepted), which is the conservative reading. If the buys
    exceed the budget they are scaled down proportionally (floor shares);
    sells are never touched."""
    notes: list[str] = []
    budget = max(0, cash_cents - int(reserve_pct * equity_cents))
    buys = [o for o in orders if o["side"] == "buy"]
    cost = sum(o["shares"] * prices_cents[o["symbol"]] for o in buys)
    if cost <= budget:
        return orders, notes
    factor = budget / cost if cost > 0 else 0.0
    notes.append(f"buys cost {cost}c > cash budget {budget}c; scaled by {factor:.4f}")
    out = []
    for o in orders:
        if o["side"] != "buy":
            out.append(o)
            continue
        shares = math.floor(o["shares"] * factor)
        if shares > 0:
            out.append({**o, "shares": shares})
        else:
            notes.append(f"dropped {o['symbol']} buy (scaled to 0 shares)")
    return out, notes


def check_post_trade_limits(orders: list[dict], current_shares: dict, prices_cents: dict,
                            equity_cents: int, max_asset_weight: float,
                            max_total_exposure: float) -> None:
    """Independent of the sizing code: apply the orders to current holdings
    and verify no position or the total exceeds the pilot limits at the
    last close. Raises OrderGuardError; the caller sends nothing."""
    post = dict(current_shares)
    for o in orders:
        post[o["symbol"]] = post.get(o["symbol"], 0) + (o["shares"] if o["side"] == "buy" else -o["shares"])
    total = 0.0
    for sym, sh in post.items():
        if sh < 0:
            raise OrderGuardError(f"post-trade short position in {sym}: {sh}")
        val = sh * prices_cents[sym]
        total += val
        if val > max_asset_weight * equity_cents + 1e-6:
            raise OrderGuardError(f"post-trade {sym} {val}c exceeds max asset weight {max_asset_weight}")
    if total > max_total_exposure * equity_cents + 1e-6:
        raise OrderGuardError(f"post-trade exposure {total}c exceeds {max_total_exposure} of equity")


def build_specs(orders: list[dict], session_date: str, time_in_force: str) -> list[IntendedOrderSpec]:
    """One deterministic client_order_id per (session_date, symbol): a rerun
    after a crash can only ever re-present the SAME id, which the ledger and
    the broker both deduplicate. Sells first, then buys (stable by symbol)."""
    ordered = sorted(orders, key=lambda o: (0 if o["side"] == "sell" else 1, o["symbol"]))
    return [IntendedOrderSpec(build_client_order_id(session_date, o["symbol"]), o["symbol"],
                              int(o["shares"]), o["side"], time_in_force=time_in_force)
            for o in ordered if o["shares"] > 0]
