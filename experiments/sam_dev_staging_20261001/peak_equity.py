"""Persisted peak-equity series for Kim's drawdown halt. Pure logic, no
network calls.

SIGN CONVENTION (pinned by tests): drawdown is returned NEGATIVE or zero
(-0.15 == 15% below peak), identical to execution.Portfolio
.update_peak_and_drawdown() and to what risk.validate() compares against
(`current_drawdown <= -drawdown_halt_pct`). fe72d72 returned a positive
number into that check, so the halt could never fire.

Keyed by session date: re-recording a date overwrites that date's entry.
Writes are atomic. An existing-but-unreadable file raises StateCorrupt, and
a missing file raises PeakStateMissing when the caller says prior sessions
exist -- a silent reset would make drawdown 0.
"""
from __future__ import annotations

import math
from pathlib import Path

from atomic_io import write_json_atomic, read_json_strict


class PeakStateMissing(RuntimeError):
    pass


def load_state(path: Path, require_existing: bool = False) -> dict:
    if path.exists():
        state = read_json_strict(path)
        state.setdefault("history", {})
        return state
    if require_existing:
        raise PeakStateMissing(f"{path} missing but earlier sessions were recorded; refusing to reset the peak")
    return {"peak_equity": None, "peak_session_date": None, "history": {}}


def save_state(path: Path, state: dict) -> None:
    write_json_atomic(path, state)


def evaluate(state: dict, session_date: str, equity: float) -> tuple[dict, float, float]:
    """Pure: returns (new_state, peak_equity, drawdown<=0) without touching disk."""
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError(f"invalid equity for drawdown: {equity}")
    hist = dict(state["history"])
    hist[session_date] = equity
    peak_date = max(hist, key=lambda d: hist[d])
    peak = hist[peak_date]
    new_state = {"peak_equity": peak, "peak_session_date": peak_date, "history": hist}
    return new_state, peak, min(0.0, (equity - peak) / peak)


def record_and_get_drawdown(path: Path, session_date: str, equity: float,
                            require_existing: bool = False) -> tuple[float, float]:
    state = load_state(path, require_existing)
    new_state, peak, dd = evaluate(state, session_date, equity)
    save_state(path, new_state)
    return peak, dd
