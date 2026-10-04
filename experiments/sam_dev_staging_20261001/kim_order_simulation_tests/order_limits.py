"""Hard limits for Kim's order-submission path: a file-based kill switch
and the per-session order-cap exception. Pure logic / file checks only.

The per-session order COUNT is no longer a JSON counter file (fe72d72's
read-modify-write lost updates under concurrency, wasn't crash-atomic, and
burned a cap slot on every restart replay of an already-sent order). It is
derived from the SQLite intent ledger and enforced inside the same
BEGIN IMMEDIATE transaction that inserts the intent -- see
kim_order_logic.PersistentIntentLedger.record_intent().

The kill switch is checked at batch start AND before every individual send,
so a switch dropped mid-batch stops the next send.
"""
from __future__ import annotations

from pathlib import Path

# One order per instrument in Kim's 8-symbol UNIVERSE is the realistic
# ceiling for a once-per-session rebalance; anything beyond that for a
# single session date is treated as a runaway condition.
DEFAULT_MAX_ORDERS_PER_DAY = 8


class KillSwitchActive(RuntimeError):
    """Kill-switch sentinel present. Not a retryable condition."""


class DailyOrderLimitReached(RuntimeError):
    """This session date already has the maximum number of distinct order intents."""


def kill_switch_active(path: Path) -> bool:
    return path.exists()


def check_kill_switch(path: Path) -> None:
    """Call before every individual send; never cache the result."""
    if kill_switch_active(path):
        raise KillSwitchActive(f"kill switch present at {path} -- refusing to submit")
