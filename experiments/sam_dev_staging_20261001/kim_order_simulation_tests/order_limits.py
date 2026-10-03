"""Hard limits for Kim's order-submission path: a persistent per-day order
count cap and a file-based kill switch. Pure logic / file I/O only -- no
network, no broker calls. Designed to be checked (a) once at the start of
a submission batch and (b) again immediately before every individual send,
so a kill switch dropped mid-batch still stops the next send, not just a
fresh process start.
"""
from __future__ import annotations

import json
from pathlib import Path

# One order per instrument in Kim's 8-symbol UNIVERSE is the realistic
# ceiling for a once-per-session rebalance; anything beyond that for a
# single session date is treated as a runaway condition, not normal
# operation.
DEFAULT_MAX_ORDERS_PER_DAY = 8


class KillSwitchActive(RuntimeError):
    """Raised when the kill-switch sentinel file is present. Caller must
    not submit -- this is not a retryable condition."""


class DailyOrderLimitReached(RuntimeError):
    """Raised when today's recorded order count is already at or above
    the configured cap. Caller must not submit."""


def kill_switch_active(path: Path) -> bool:
    return path.exists()


def check_kill_switch(path: Path) -> None:
    """Call this before every individual send (and once more at batch
    start) -- never cache the result across sends."""
    if kill_switch_active(path):
        raise KillSwitchActive(f"kill switch present at {path} -- refusing to submit")


def load_order_count_state(path: Path) -> dict:
    if path.exists():
        state = json.loads(path.read_text())
        state.setdefault("date_counts", {})
        return state
    return {"date_counts": {}}


def save_order_count_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def count_orders_today(path: Path, session_date: str) -> int:
    """Read-only: does not increment. Useful for tests/inspection."""
    return load_order_count_state(path)["date_counts"].get(session_date, 0)


def check_and_increment_order_count(path: Path, session_date: str,
                                     max_per_day: int = DEFAULT_MAX_ORDERS_PER_DAY) -> int:
    """Atomically check-then-increment the per-day counter for a single
    send. Raises DailyOrderLimitReached BEFORE incrementing if the cap is
    already met. Call this immediately before each individual send, not
    once per session -- a session placing multiple orders is capped
    per-order, not per-call."""
    state = load_order_count_state(path)
    current = state["date_counts"].get(session_date, 0)
    if current >= max_per_day:
        raise DailyOrderLimitReached(
            f"{current} orders already recorded for {session_date}, cap is {max_per_day}"
        )
    state["date_counts"][session_date] = current + 1
    save_order_count_state(path, state)
    return state["date_counts"][session_date]
