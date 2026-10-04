"""Exchange-calendar session accounting for Kim's cadence. The step counter
is DERIVED from the calendar (sessions since session zero), never from how
many times a job happened to run: holidays, missed days, failed runs and
catch-up runs cannot shift the rebalance schedule, and the schedule matches
the backtest's `step` (one increment per trading session)."""
from __future__ import annotations

from datetime import date


class CalendarUnavailable(RuntimeError):
    """Calendar could not be fetched/parsed. NOT the same as 'today is a
    holiday' -- callers must abstain without treating the day as non-trading."""


def fetch_sessions(get_json, host: str, headers: dict, start: str, end: str) -> list[str]:
    try:
        status, body = get_json(f"{host}/v2/calendar?start={start}&end={end}", headers)
    except Exception as e:  # noqa: BLE001
        raise CalendarUnavailable(f"calendar fetch failed: {e!r}") from e
    if status != 200 or not isinstance(body, list):
        raise CalendarUnavailable(f"calendar returned HTTP {status} / non-list body")
    dates = []
    for row in body:
        d = row.get("date") if isinstance(row, dict) else None
        try:
            date.fromisoformat(d)
        except (TypeError, ValueError) as e:
            raise CalendarUnavailable(f"malformed calendar row {row!r}") from e
        dates.append(d)
    return sorted(set(dates))


def session_step(sessions: list[str], session_zero: str, today: str) -> int | None:
    """0-based index of `today` among trading sessions since session zero
    (session zero itself is step 0). None if today is not a trading session
    in the supplied calendar (holiday / weekend / before session zero)."""
    if today not in sessions or today < session_zero:
        return None
    return sum(1 for s in sessions if session_zero <= s < today)


def latest_due_rebalance_step(step: int, every_n: int) -> int:
    return (step // every_n) * every_n
