"""Isolated Alpaca paper/data adapter -- order submission gated by two
independent switches, both defaulting closed, neither reachable from
strategy/genome configuration.

No credentials are read or held by this module. The one HTTP call this
module can make (a POST to {host}/v2/orders) only happens inside
route_order(), only when submission_enabled() is True, only to an
allowlisted host, and only with an explicitly passed `headers` dict --
callers may inject a fake `http_post` so every code path remains
testable with fixtures without touching the network.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ---------------------------------------------------------------------------
# Host allowlist -- paper trading only. Live trading host is named ONLY to be
# blocked, never to be dialed. Market-data host is believed shared between
# paper/live accounts, differentiated by API key -- VERIFY against current
# Alpaca docs before relying on this; not assumed true by this scaffold.
# ---------------------------------------------------------------------------
PAPER_TRADING_HOST = "https://paper-api.alpaca.markets"
LIVE_TRADING_HOST_BLOCKED = "https://api.alpaca.markets"
MARKET_DATA_HOST = "https://data.alpaca.markets"  # UNVERIFIED vs current docs

ALLOWED_TRADING_HOSTS = frozenset({PAPER_TRADING_HOST})


class HostNotAllowedError(RuntimeError):
    """Raised if anything ever points at a non-paper trading host."""


def assert_trading_host_allowed(host: str) -> None:
    if host not in ALLOWED_TRADING_HOSTS:
        raise HostNotAllowedError(
            f"refusing trading host {host!r}; only {sorted(ALLOWED_TRADING_HOSTS)} allowed"
        )
    if host == LIVE_TRADING_HOST_BLOCKED:
        raise HostNotAllowedError("live trading host is explicitly blocked")


# ---------------------------------------------------------------------------
# Submission gate -- disabled by default, enforced independently of any
# strategy/genome config. Two independent switches must BOTH be true; a
# strategy's config dict is never consulted here and cannot flip either one.
# ---------------------------------------------------------------------------
_SUBMISSION_ENABLED_CONST = False  # hardcoded; change requires a code review, not a config edit


def submission_enabled() -> bool:
    env_flag = os.environ.get("TBOTS_ALPACA_SUBMISSION_ENABLED", "") == "true-i-understand-the-risk"
    return _SUBMISSION_ENABLED_CONST and env_flag


class SubmissionDisabledError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Clock/calendar handling -- caller injects an HTTP client; this module only
# parses/validates the shape and staleness of what comes back.
# ---------------------------------------------------------------------------
@dataclass
class MarketClock:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime

    @classmethod
    def from_api(cls, payload: dict) -> "MarketClock":
        return cls(
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            is_open=bool(payload["is_open"]),
            next_open=datetime.fromisoformat(payload["next_open"]),
            next_close=datetime.fromisoformat(payload["next_close"]),
        )

    def is_stale(self, now: datetime, max_age_seconds: float = 30.0) -> bool:
        return (now - self.timestamp).total_seconds() > max_age_seconds


class StaleClockError(RuntimeError):
    pass


def require_fresh_clock(clock: MarketClock, now: datetime | None = None) -> MarketClock:
    now = now or datetime.now(timezone.utc)
    if clock.is_stale(now):
        raise StaleClockError(f"clock timestamp {clock.timestamp} stale relative to {now}")
    return clock


# ---------------------------------------------------------------------------
# Order state reconciliation + duplicate prevention. All in-memory / pure;
# no network, no persistence beyond this process.
# ---------------------------------------------------------------------------
class OrderStatus(str, Enum):
    NEW = "new"
    ACCEPTED = "accepted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass
class IntendedOrder:
    client_order_id: str
    symbol: str
    side: str
    qty: float
    order_type: str
    time_in_force: str


@dataclass
class ReconciledOrder:
    client_order_id: str
    broker_order_id: str | None
    status: OrderStatus
    filled_qty: float = 0.0


@dataclass
class DuplicateOrderError(RuntimeError):
    client_order_id: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"duplicate client_order_id {self.client_order_id!r}"


@dataclass
class PaperOrderLedger:
    """Tracks intended vs broker-reported order state. Pure in-memory --
    no network, no brokerage calls. Rejects a resubmission of a
    client_order_id already seen, whether or not the broker has
    acknowledged it yet."""

    _intended: dict = field(default_factory=dict)
    _reconciled: dict = field(default_factory=dict)

    def record_intent(self, order: IntendedOrder) -> None:
        if order.client_order_id in self._intended:
            raise DuplicateOrderError(order.client_order_id)
        self._intended[order.client_order_id] = order

    def reconcile(self, report: ReconciledOrder) -> None:
        if report.client_order_id not in self._intended:
            raise RuntimeError(
                f"reconciliation for unknown client_order_id {report.client_order_id!r} "
                "-- an order this ledger never intended"
            )
        self._reconciled[report.client_order_id] = report

    def status_of(self, client_order_id: str) -> OrderStatus | None:
        rec = self._reconciled.get(client_order_id)
        return rec.status if rec else None

    def is_duplicate(self, client_order_id: str) -> bool:
        return client_order_id in self._intended


# ---------------------------------------------------------------------------
# Paper-only order routing. The single choke point: every attempted
# submission goes through here, and it refuses unless BOTH the host is
# allowlisted AND submission_enabled() is true. A strategy/genome cannot
# reach either check -- it only ever calls route_order(...).
# ---------------------------------------------------------------------------
def _default_http_post(host: str, order: IntendedOrder, headers: dict) -> dict:
    """PROPOSED, NOT DEPLOYED. The only place in this module that would
    make a real HTTP request. POST only, to {host}/v2/orders only -- host
    was already allowlisted by the caller (route_order) before this is
    ever reached."""
    body = json.dumps({
        "symbol": order.symbol,
        "qty": str(order.qty),
        "side": order.side,
        "type": order.order_type,
        "time_in_force": order.time_in_force,
        "client_order_id": order.client_order_id,
    }).encode()
    req = urllib.request.Request(
        f"{host}/v2/orders", data=body, method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"order POST rejected: HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")


def route_order(order: IntendedOrder, *, host: str, ledger: PaperOrderLedger,
                 headers: dict | None = None, http_post=None) -> dict | None:
    assert_trading_host_allowed(host)
    ledger.record_intent(order)  # duplicate check happens before the enable check
    if not submission_enabled():
        raise SubmissionDisabledError(
            "order recorded in ledger as intent only -- submission is disabled by default "
            "and this gate does not authorize brokerage calls"
        )
    poster = http_post or _default_http_post
    response = poster(host, order, headers or {})
    ledger.reconcile(ReconciledOrder(
        client_order_id=order.client_order_id,
        broker_order_id=response.get("id"),
        status=OrderStatus(response.get("status", "new")),
        filled_qty=float(response.get("filled_qty") or 0),
    ))
    return response
