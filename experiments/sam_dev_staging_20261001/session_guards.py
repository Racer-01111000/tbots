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


# ---------------------------------------------------------------------------
# Halt liquidation (quantity-based; needs NO price)
# ---------------------------------------------------------------------------
TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced"}


def plan_halt_liquidation(positions: list, open_orders: list, ledger_nonterminal_sells: list,
                          universe: list[str]) -> tuple[list[dict], dict]:
    """Sell only the remaining UNCOMMITTED long quantity of each universe symbol.

      sellable = floor(position_qty) - ceil(quantity already committed to live SELL orders)

    committed = remaining (qty - filled_qty) of every non-terminal sell known to the
    broker, plus remaining of every non-terminal sell known only to our ledger (an
    accepted order not yet visible in the broker's list), de-duplicated by
    client_order_id. Never produces a buy, never goes below zero, never touches a
    short or a non-universe holding. Pending BUYs are reported, not cancelled
    (cancelling is a different broker action this path does not take)."""
    notes = {"short_positions": [], "non_universe_positions": [], "fractional_residual": {},
             "pending_buys": [], "committed_sell_qty": {}}
    committed: dict[str, float] = {}
    seen: set = set()
    for o in open_orders or []:
        if o.get("status") in TERMINAL_STATUSES:
            continue
        sym = o.get("symbol")
        if o.get("side") == "buy":
            notes["pending_buys"].append(sym)
            continue
        remaining = max(0.0, float(o.get("qty") or 0) - float(o.get("filled_qty") or 0))
        committed[sym] = committed.get(sym, 0.0) + remaining
        seen.add(o.get("client_order_id"))
    for row in ledger_nonterminal_sells or []:
        if row["client_order_id"] in seen or row.get("side") != "sell":
            continue
        remaining = max(0.0, float(row.get("qty") or 0) - float(row.get("filled_qty") or 0))
        committed[row["symbol"]] = committed.get(row["symbol"], 0.0) + remaining
    notes["committed_sell_qty"] = {k: v for k, v in committed.items() if v}

    orders = []
    for pos in positions or []:
        sym = pos.get("symbol")
        qty = float(pos.get("qty") or 0)
        if sym not in universe:
            if qty:
                notes["non_universe_positions"].append(sym)
            continue
        if qty < 0:
            notes["short_positions"].append(sym)        # never buy to cover; a human decides
            continue
        whole = math.floor(qty + 1e-9)
        if abs(qty - whole) > 1e-9:
            notes["fractional_residual"][sym] = qty - whole
        sellable = max(0, whole - math.ceil(committed.get(sym, 0.0) - 1e-9))
        if sellable > 0:
            orders.append({"symbol": sym, "side": "sell", "shares": int(sellable)})
    return sorted(orders, key=lambda o: o["symbol"]), notes


def build_liquidation_specs(orders: list[dict], session_date: str) -> list[IntendedOrderSpec]:
    """Deterministic id kim-{session}-{symbol}-liq (distinct from a same-day rebalance id so
    one can never shadow the other). Hard-asserts sell-only: a buy here is a bug, not an input."""
    if any(o["side"] != "sell" or o["shares"] <= 0 for o in orders):
        raise OrderGuardError("halt liquidation may only contain positive-quantity sells")
    return [IntendedOrderSpec(build_client_order_id(session_date, o["symbol"]) + "-liq", o["symbol"],
                              int(o["shares"]), "sell", time_in_force="day") for o in orders]


def is_flat(positions: list, open_orders: list, universe: list[str]) -> bool:
    held = any(p.get("symbol") in universe and float(p.get("qty") or 0) != 0 for p in positions or [])
    live = any(o.get("symbol") in universe and o.get("status") not in TERMINAL_STATUSES for o in open_orders or [])
    return not held and not live
