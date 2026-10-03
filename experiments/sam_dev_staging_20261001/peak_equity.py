"""Persisted peak-equity series for Kim's drawdown halt. Pure logic, no
network calls -- the caller supplies today's equity figure (already
broker-sourced) and gets back the running peak and a real current_drawdown,
so risk.validate()'s drawdown_halt_pct check can actually fire instead of
being permanently inert on a hardcoded 0.0.

Keyed by session date, same discipline as cadence_state.json: re-recording
the same session date overwrites that date's entry rather than appending,
so a restart mid-session can't double-count or distort the peak.
"""
from __future__ import annotations

import json
from pathlib import Path


def load_state(path: Path) -> dict:
    if path.exists():
        state = json.loads(path.read_text())
        state.setdefault("history", {})
        return state
    return {"peak_equity": None, "peak_session_date": None, "history": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def record_and_get_drawdown(path: Path, session_date: str, equity: float) -> tuple[float, float]:
    """Record today's equity (idempotent per session_date -- a restart
    that re-records the same date just overwrites that one entry) and
    return (peak_equity, current_drawdown). current_drawdown is 0.0 when
    today's equity is at or above the peak, including the very first
    recording ever (peak == today's equity in that case)."""
    state = load_state(path)
    state["history"][session_date] = equity
    peak_date = max(state["history"], key=lambda d: state["history"][d])
    peak_equity = state["history"][peak_date]
    state["peak_equity"] = peak_equity
    state["peak_session_date"] = peak_date
    current_drawdown = 0.0 if peak_equity <= 0 else max(0.0, (peak_equity - equity) / peak_equity)
    save_state(path, state)
    return peak_equity, current_drawdown
