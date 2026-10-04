"""Pure order-sizing/reconciliation logic for Kim's paper-execution path --
no network code, no HTTP client, no broker implementation. Depends only on
an injected `broker` object (real or fake) so it is fully testable without
touching the actual (still-unimplemented, still-blocked) submission path.

This module does NOT enable order submission. It only defines the logic
that a future, supported submission path would need: sizing math, a
persistent intent ledger, and crash-safe reconciliation. Nothing here
calls alpaca_adapter.route_order() or makes any HTTP request.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import order_limits


class StaleDataError(RuntimeError):
    pass


class DuplicateSubmissionError(RuntimeError):
    pass


# Every terminal status Alpaca can report for an order. Reconciliation
# treats anything outside this set (including the ledger's own
# "intent_recorded" and the live "accepted"/"new"/"partially_filled"/etc.)
# as still needing attention.
TERMINAL_ORDER_STATUSES = {"filled", "rejected", "canceled", "expired", "abandoned_by_operator"}


@dataclass
class IntendedOrderSpec:
    client_order_id: str
    symbol: str
    qty: int
    side: str
    order_type: str = "market"
    # "day", not "opg": Alpaca REJECTS opg orders submitted between 09:28 and
    # 19:00 ET (docs.alpaca.markets/docs/orders-at-alpaca), and the session job
    # submits at ~16:45 ET. A day order submitted after the close is queued
    # for the next trading day's open.
    time_in_force: str = "day"


def build_client_order_id(session_date: str, symbol: str) -> str:
    """Deterministic key: one order per (session_date, symbol). A retry
    after a crash reuses this same id instead of minting a fresh one --
    that's what lets both the local ledger and the broker's own
    duplicate-client-order-id rejection recognize a resubmission as the
    same intent rather than a new one."""
    return f"kim-{session_date}-{symbol}"


def validate_inputs_fresh(last_price_ts: datetime | None, now: datetime, max_age_seconds: float = 300.0) -> None:
    if last_price_ts is None:
        raise StaleDataError("price timestamp missing -- refusing to size an order on no data")
    age = (now - last_price_ts).total_seconds()
    if age > max_age_seconds:
        raise StaleDataError(f"price is {age:.0f}s old, exceeds {max_age_seconds:.0f}s freshness bound")


def compute_order(equity_usd: float, target_weight: float, price: float,
                   max_asset_weight: float = 0.18) -> dict:
    """Sizes off equity, never buying power. Whole-share floor rounding.
    Caps at max_asset_weight regardless of what target_weight claims."""
    if price <= 0:
        raise ValueError(f"invalid price: {price}")
    weight = min(target_weight, max_asset_weight)
    notional_target = equity_usd * weight
    qty = math.floor(notional_target / price)
    actual_notional = qty * price
    return {
        "qty": qty,
        "price_basis": price,
        "target_notional": notional_target,
        "actual_notional": actual_notional,
        "weight_applied": weight,
        "equity_basis": equity_usd,
    }


class PersistentIntentLedger:
    """SQLite-backed, survives process restarts. Intent is written BEFORE
    any broker call, so a crash between submission and local record-write
    can be detected and reconciled rather than silently retried.

    Autocommit connection with explicit BEGIN IMMEDIATE transactions, so the
    per-session cap check and the intent INSERT are one atomic step across
    processes. synchronous=FULL so a committed intent survives power loss."""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS order_intents (
                client_order_id TEXT PRIMARY KEY, symbol TEXT, qty INTEGER, side TEXT,
                intent_recorded_at TEXT NOT NULL,
                broker_order_id TEXT, status TEXT, filled_qty REAL, last_updated_at TEXT,
                session_date TEXT
            )"""
        )
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(order_intents)")}
        if "session_date" not in cols:  # ledgers created by fe72d72
            self.conn.execute("ALTER TABLE order_intents ADD COLUMN session_date TEXT")

    def count_for_session(self, session_date: str) -> int:
        """Distinct order intents recorded for a session date. Rows seeded
        from pre-existing broker state have session_date NULL and never
        count against the cap."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM order_intents WHERE session_date=?", (session_date,)
        ).fetchone()[0]

    def record_intent(self, spec: IntendedOrderSpec, session_date: str | None = None,
                      max_per_session: int | None = None) -> None:
        """Atomically: reject a duplicate client_order_id, enforce the
        per-session cap (when session_date and max_per_session are given),
        and insert. Replays of an existing intent raise DuplicateSubmission
        BEFORE any cap accounting, so a restart never consumes cap."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self.conn.execute(
                "SELECT 1 FROM order_intents WHERE client_order_id=?", (spec.client_order_id,),
            ).fetchone()
            if existing:
                raise DuplicateSubmissionError(
                    f"intent for {spec.client_order_id} already recorded -- will not submit again"
                )
            if session_date is not None and max_per_session is not None:
                n = self.conn.execute(
                    "SELECT COUNT(*) FROM order_intents WHERE session_date=?", (session_date,)
                ).fetchone()[0]
                if n >= max_per_session:
                    raise order_limits.DailyOrderLimitReached(
                        f"{n} order intents already recorded for {session_date}, cap is {max_per_session}"
                    )
            self.conn.execute(
                "INSERT INTO order_intents (client_order_id, symbol, qty, side, intent_recorded_at, "
                "status, session_date) VALUES (?,?,?,?,?,?,?)",
                (spec.client_order_id, spec.symbol, spec.qty, spec.side,
                 datetime.now(timezone.utc).isoformat(), "intent_recorded", session_date),
            )
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def update_status(self, client_order_id: str, broker_order_id: str | None,
                       status: str, filled_qty: float = 0.0) -> None:
        self.conn.execute(
            "UPDATE order_intents SET broker_order_id=?, status=?, filled_qty=?, last_updated_at=? "
            "WHERE client_order_id=?",
            (broker_order_id, status, filled_qty, datetime.now(timezone.utc).isoformat(), client_order_id),
        )

    def get(self, client_order_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT client_order_id, symbol, qty, side, status, broker_order_id, filled_qty "
            "FROM order_intents WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        if not row:
            return None
        keys = ["client_order_id", "symbol", "qty", "side", "status", "broker_order_id", "filled_qty"]
        return dict(zip(keys, row))

    def get_by_client_order_id(self, client_order_id: str) -> dict | None:
        """Alias for get() -- named to match the broker-side lookup it
        mirrors, used by the reconciliation/seeding callers."""
        return self.get(client_order_id)

    def get_unresolved(self) -> list[dict]:
        """Every row not yet in a terminal broker status -- includes rows
        still at 'intent_recorded' (never got a confirmed response) as
        well as live statuses like 'accepted'/'partially_filled'."""
        placeholders = ",".join("?" for _ in TERMINAL_ORDER_STATUSES)
        rows = self.conn.execute(
            f"SELECT client_order_id, symbol, qty, side, status, broker_order_id, filled_qty, session_date "
            f"FROM order_intents WHERE status IS NULL OR status NOT IN ({placeholders})",
            tuple(TERMINAL_ORDER_STATUSES),
        ).fetchall()
        keys = ["client_order_id", "symbol", "qty", "side", "status", "broker_order_id", "filled_qty", "session_date"]
        return [dict(zip(keys, row)) for row in rows]

    def get_uncertain(self) -> list[dict]:
        """Rows whose submission outcome is UNKNOWN (never got a confirmed broker answer)."""
        return [r for r in self.get_unresolved() if r["status"] in ("intent_recorded", "uncertain_unresolved")]

    def abandon(self, client_order_id: str, reason: str) -> None:
        """Operator action ONLY. A broker lookup returning 'not found' is NOT sufficient evidence to
        call this: an order can exist at the broker and still 404 on lookup for a while (delayed
        visibility). Require independent evidence (order history/list for that symbol and window,
        position/cash consistency, enough elapsed time) and put it in `reason`. Retires a row that is
        currently unresolved so it stops holding the account; refuses anything else."""
        row = self.get(client_order_id)
        if row is None or row["status"] not in ("intent_recorded", "uncertain_unresolved"):
            raise ValueError(f"{client_order_id} is not an unresolved submission; refusing to abandon it")
        if not reason or not reason.strip():
            raise ValueError("an abandonment reason is required")
        self.update_status(client_order_id, row.get("broker_order_id"), "abandoned_by_operator", row.get("filled_qty") or 0.0)

    def count_all(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0]

    def has_any_intent_today(self, symbol: str, date_prefix: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM order_intents WHERE symbol=? AND client_order_id LIKE ?",
            (symbol, f"%{date_prefix}%"),
        ).fetchone()
        return row is not None


class UncertainSubmissionError(RuntimeError):
    """Raised when the broker call timed out / connection dropped -- the
    order may or may not have actually been accepted. Caller must
    reconcile via broker.find_by_client_order_id(), never blindly retry."""


class BrokerLookupError(RuntimeError):
    """A find_by_client_order_id() lookup could not be answered (5xx,
    timeout, network). Distinct from 'not found' (None): the order's
    existence is UNKNOWN, so the caller must not submit."""


def submit_with_reconciliation(broker, ledger: PersistentIntentLedger, spec: IntendedOrderSpec,
                               session_date: str | None = None,
                               max_per_session: int | None = None) -> dict:
    existing = ledger.get(spec.client_order_id)
    if existing and existing["status"] not in ("intent_recorded", "uncertain_unresolved"):
        return {"outcome": "already_resolved_no_resubmit", "record": existing}

    if existing is None:
        ledger.record_intent(spec, session_date, max_per_session)  # intent persisted BEFORE the broker call
    elif existing["status"] in ("intent_recorded", "uncertain_unresolved"):
        # A prior run recorded intent but never got a confirmed response
        # (crash, or a timed-out POST whose lookup also found nothing). It may
        # have died before OR after the broker received the request. Never
        # guess: ask the broker directly first. If it knows this
        # client_order_id, adopt its answer; if it answers 404, resubmit the
        # SAME client_order_id (the broker rejects a duplicate id, so this is
        # idempotent). A failed lookup raises BrokerLookupError -> no send.
        found = broker.find_by_client_order_id(spec.client_order_id)
        if found is not None:
            ledger.update_status(spec.client_order_id, found["id"], found["status"], found.get("filled_qty", 0))
            return {"outcome": "reconciled_from_prior_crash", "record": ledger.get(spec.client_order_id)}

    try:
        response = broker.submit(spec)
    except UncertainSubmissionError:
        try:
            found = broker.find_by_client_order_id(spec.client_order_id)
        except BrokerLookupError:
            ledger.update_status(spec.client_order_id, None, "uncertain_unresolved")
            return {"outcome": "uncertain_unresolved", "record": ledger.get(spec.client_order_id)}
        if found is None:
            ledger.update_status(spec.client_order_id, None, "uncertain_unresolved")
            return {"outcome": "uncertain_unresolved", "record": ledger.get(spec.client_order_id)}
        ledger.update_status(spec.client_order_id, found["id"], found["status"], found.get("filled_qty", 0))
        return {"outcome": "reconciled_after_uncertainty", "record": ledger.get(spec.client_order_id)}

    ledger.update_status(spec.client_order_id, response["id"], response["status"], response.get("filled_qty", 0))
    return {"outcome": response["status"], "record": ledger.get(spec.client_order_id)}


# Orchestrates submit_with_reconciliation() for a batch of orders,
# enforcing the kill switch and the per-day order cap -- a blocked order
# never reaches submit_with_reconciliation()/broker.submit() at all.
# Two checkpoints: (a) once at the start of this batch, before touching
# anything -- if the kill switch is already present, nothing in the batch
# is even attempted; (b) again immediately before every individual send --
# a kill switch dropped mid-batch stops the NEXT send, not just a fresh
# process start, and once the daily cap is hit, every remaining order in
# this batch is blocked too (the cap is global for the day).
def submit_orders_with_limits(broker, ledger: PersistentIntentLedger, specs: list[IntendedOrderSpec],
                               kill_switch_path: Path, session_date: str,
                               max_orders_per_day: int = order_limits.DEFAULT_MAX_ORDERS_PER_DAY) -> dict:
    results = []

    try:
        order_limits.check_kill_switch(kill_switch_path)
    except order_limits.KillSwitchActive as e:
        return {
            "results": [{"client_order_id": s.client_order_id, "outcome": "blocked_kill_switch_at_startup"}
                        for s in specs],
            "submitted": 0, "blocked": len(specs), "startup_blocked": True, "detail": str(e),
        }

    blocked_from_here = False
    for spec in specs:
        if blocked_from_here:
            results.append({"client_order_id": spec.client_order_id, "outcome": "blocked_batch_halted"})
            continue

        try:
            order_limits.check_kill_switch(kill_switch_path)
        except order_limits.KillSwitchActive as e:
            results.append({"client_order_id": spec.client_order_id, "outcome": "blocked_kill_switch",
                             "detail": str(e)})
            blocked_from_here = True
            continue

        try:
            result = submit_with_reconciliation(broker, ledger, spec, session_date, max_orders_per_day)
        except order_limits.DailyOrderLimitReached as e:
            results.append({"client_order_id": spec.client_order_id, "outcome": "blocked_daily_limit",
                             "detail": str(e)})
            blocked_from_here = True
            continue
        except BrokerLookupError as e:
            results.append({"client_order_id": spec.client_order_id, "outcome": "blocked_lookup_failed",
                             "detail": str(e)})
            blocked_from_here = True
            continue
        results.append({"client_order_id": spec.client_order_id, **result})

    blocked_outcomes = {"blocked_kill_switch", "blocked_daily_limit", "blocked_batch_halted",
                         "blocked_kill_switch_at_startup", "blocked_lookup_failed"}
    submitted = sum(1 for r in results if r["outcome"] not in blocked_outcomes)
    blocked = sum(1 for r in results if r["outcome"] in blocked_outcomes)
    return {"results": results, "submitted": submitted, "blocked": blocked, "startup_blocked": False}


class MockBroker:
    """Pure in-memory simulation. No network. Simulates realistic broker
    semantics: rejects a duplicate client_order_id exactly like a real
    broker would (this is NOT the same protection as the ledger -- it's
    the second, independent layer a real broker also provides)."""

    def __init__(self):
        self._orders: dict[str, dict] = {}
        self._next_behavior: dict[str, str] = {}

    def queue_behavior(self, client_order_id: str, behavior: str) -> None:
        self._next_behavior[client_order_id] = behavior

    def submit(self, spec: IntendedOrderSpec) -> dict:
        if spec.client_order_id in self._orders:
            raise RuntimeError(f"broker-side duplicate rejection for {spec.client_order_id}")
        behavior = self._next_behavior.pop(spec.client_order_id, "accepted")
        if behavior == "timeout":
            self._orders[spec.client_order_id] = {
                "id": f"broker-{spec.client_order_id}", "status": "accepted",
                "symbol": spec.symbol, "qty": spec.qty, "filled_qty": 0,
            }
            raise UncertainSubmissionError("simulated timeout after broker-side acceptance")
        if behavior == "rejected":
            result = {"id": f"broker-{spec.client_order_id}", "status": "rejected",
                       "symbol": spec.symbol, "qty": spec.qty, "filled_qty": 0}
            self._orders[spec.client_order_id] = result
            return result
        if behavior == "partial_fill":
            result = {"id": f"broker-{spec.client_order_id}", "status": "partially_filled",
                       "symbol": spec.symbol, "qty": spec.qty, "filled_qty": spec.qty // 2}
            self._orders[spec.client_order_id] = result
            return result
        if behavior == "cancelled":
            result = {"id": f"broker-{spec.client_order_id}", "status": "canceled",
                       "symbol": spec.symbol, "qty": spec.qty, "filled_qty": 0}
            self._orders[spec.client_order_id] = result
            return result
        result = {"id": f"broker-{spec.client_order_id}", "status": "filled",
                   "symbol": spec.symbol, "qty": spec.qty, "filled_qty": spec.qty}
        self._orders[spec.client_order_id] = result
        return result

    def find_by_client_order_id(self, client_order_id: str) -> dict | None:
        return self._orders.get(client_order_id)

    def set_order_status(self, client_order_id: str, status: str, filled_qty: float = 0,
                          symbol: str = "TEST", qty: int = 0) -> None:
        """Test helper: simulate the broker's own state changing over time
        (e.g. accepted -> filled, or accepted -> expired) between an
        original submission and a later reconciliation poll. Also usable
        to seed a broker-side order that was never placed through
        broker.submit() at all (an externally-placed/pre-existing order)."""
        existing = self._orders.get(client_order_id, {})
        self._orders[client_order_id] = {
            "id": existing.get("id", f"broker-{client_order_id}"),
            "status": status,
            "symbol": existing.get("symbol", symbol),
            "qty": existing.get("qty", qty),
            "filled_qty": filled_qty,
        }
