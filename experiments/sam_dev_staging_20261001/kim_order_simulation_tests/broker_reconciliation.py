"""Full broker reconciliation for Kim's order ledger: walks every ledger
row not yet in a terminal state and refreshes it from the broker, covering
every status Alpaca can report (accepted, filled, partially_filled,
canceled, rejected, expired) -- not just the LIVE_ORDER_STATUSES subset
shares_including_pending already handles for position-sizing. Read-only
(GETs only); safe to run every session regardless of whether submission is
enabled, because an empty ledger makes this a no-op.

Also covers the "first submission-enabled session" case: seeding the
ledger from the broker's actual standing positions/orders before any
delta is computed, so a pre-existing position (e.g. one placed outside
this ledger, like an earlier manual test order) is the baseline instead
of the ledger starting blind.
"""
from __future__ import annotations

from kim_order_logic import IntendedOrderSpec, BrokerLookupError


def reconcile_ledger(ledger, broker, errors: list | None = None) -> list[dict]:
    """For each unresolved ledger row, ask the broker for its current
    status (by client_order_id -- unique per row) and update the ledger
    to match. Returns the list of rows that were actually changed."""
    updated = []
    for row in ledger.get_unresolved():
        try:
            found = broker.find_by_client_order_id(row["client_order_id"])
        except BrokerLookupError as e:
            # Lookup failed (5xx/timeout): the row's true state is UNKNOWN,
            # not "absent". Leave it unresolved and report it.
            if errors is not None:
                errors.append({"client_order_id": row["client_order_id"], "error": str(e)})
            continue
        if found is None:
            continue  # broker has no record yet (e.g. still 'intent_recorded') -- nothing to reconcile
        if found["status"] == row["status"] and found.get("filled_qty", 0) == row.get("filled_qty", 0):
            continue  # already in sync
        ledger.update_status(row["client_order_id"], found["id"], found["status"], found.get("filled_qty", 0))
        updated.append(ledger.get_by_client_order_id(row["client_order_id"]))
    return updated


def is_first_submission_session(ledger) -> bool:
    """True when the ledger has never recorded any intent at all -- the
    signal that submission is transitioning from off to on for the first
    time, and the ledger's baseline needs seeding from broker reality
    before any delta is computed."""
    return ledger.count_all() == 0


def seed_ledger_from_broker_state(ledger, positions, orders, session_date: str) -> int:
    """Backfill the ledger with whatever the broker already shows --
    pre-existing positions/orders placed outside this ledger entirely
    (e.g. an earlier manually-submitted test order) -- so the very first
    submission-enabled session reconciles against the broker's true
    standing state instead of starting blind. Synthesizes a
    'preexisting-' client_order_id for anything with no real
    client_order_id of its own. Idempotent: safe to call again on a
    restart mid-seeding -- rows already present are left untouched, not
    duplicated. Returns the number of rows newly seeded."""
    seeded = 0
    seen_symbols = set()

    for o in (orders or []):
        sym = o.get("symbol")
        cid = o.get("client_order_id") or f"preexisting-{session_date}-{sym}-{o.get('id')}"
        if ledger.get_by_client_order_id(cid) is not None:
            seen_symbols.add(sym)
            continue
        qty = int(round(float(o.get("qty") or 0)))
        spec = IntendedOrderSpec(cid, sym, qty, o.get("side", "buy"))
        ledger.record_intent(spec)
        ledger.update_status(cid, o.get("id"), o.get("status"), float(o.get("filled_qty") or 0))
        seeded += 1
        seen_symbols.add(sym)

    for p in (positions or []):
        sym = p.get("symbol")
        if sym in seen_symbols:
            continue  # already accounted for via its own order record above
        qty = int(round(float(p.get("qty") or 0)))
        if qty == 0:
            continue
        cid = f"preexisting-{session_date}-{sym}-position"
        if ledger.get_by_client_order_id(cid) is not None:
            continue
        spec = IntendedOrderSpec(cid, sym, abs(qty), "buy" if qty > 0 else "sell")
        ledger.record_intent(spec)
        ledger.update_status(cid, None, "filled", abs(qty))
        seeded += 1

    return seeded


def compute_first_session_delta(target_shares: dict, broker_shares: dict) -> dict:
    """The first submission-enabled session must size orders as the DELTA
    between target and what the broker already holds, never the full
    target notional as if starting from zero. Returns only the symbols
    whose delta is nonzero."""
    symbols = set(target_shares) | set(broker_shares)
    return {
        s: target_shares.get(s, 0) - broker_shares.get(s, 0)
        for s in symbols
        if target_shares.get(s, 0) != broker_shares.get(s, 0)
    }
