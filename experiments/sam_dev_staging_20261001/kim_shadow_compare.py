"""Live entrypoint for the champion-vs-lineage-D shadow comparison.

NOT scheduled by any unit. GET-only: exchange calendar + market data. It never
calls the account, positions or orders endpoints, has no broker object and no
POST, and writes only under its own directory (default
kim_shadow_pilot/shadow_compare/), so it cannot consume a paper-execution
opportunity. Both books are fed the same completed-session rows.

Cash dividends are NOT credited (live bars carry none); both books are
understated by the same mechanism, so the comparison stays like-for-like.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

DEV_STAGING = Path(__file__).resolve().parent
REPO = DEV_STAGING.parents[1]
for sub in ("alpaca_adapter", "kim_order_simulation_tests", ""):
    sys.path.insert(0, str(DEV_STAGING / sub))
sys.path.insert(0, str(REPO / "scripts"))

import session_calendar  # noqa: E402
import shadow_compare as sc  # noqa: E402
import shadow_strategies  # noqa: E402

NY_TZ = ZoneInfo("America/New_York")
CUTOFF_ET = (16, 15)
UNIVERSE = sc.UNIVERSE
MARKET_DATA = "https://data.alpaca.markets"
CALENDAR_HOST = "https://paper-api.alpaca.markets"   # calendar endpoint only
DEFAULT_STATE_DIR = DEV_STAGING / "kim_shadow_pilot" / "shadow_compare"


@dataclass
class CompareDeps:
    get_json: Callable
    headers: dict
    now_utc: Callable[[], datetime]
    state_dir: Path
    strategies: dict
    starting_cash_cents: int = sc.START_CASH_CENTS


def _bars(get_json, headers, symbol, start, end, adjustment):
    url = (f"{MARKET_DATA}/v2/stocks/{symbol}/bars?timeframe=1Day&start={start}&end={end}"
           f"&adjustment={adjustment}&feed=iex&limit=10000")
    status, body = get_json(url, headers)
    if status != 200 or not isinstance(body, dict):
        raise sc.DataGap(f"{symbol} {adjustment} bars HTTP {status}")
    return body.get("bars") or []


def build_rows(get_json, headers, now_utc, raw_days=45, hist_days=600):
    end = now_utc.date()
    rows = {}
    for s in UNIVERSE:
        adj = _bars(get_json, headers, s, (end - timedelta(days=hist_days)).isoformat(), end.isoformat(), "all")
        raw = {b["t"][:10]: b for b in _bars(get_json, headers, s, (end - timedelta(days=raw_days)).isoformat(),
                                             end.isoformat(), "raw")}
        out = []
        for b in adj:
            d = b["t"][:10]
            rb = raw.get(d)
            out.append({"timestamp": d, "adjusted_close": float(b["c"]),
                        "open": float(rb["o"]) if rb else None, "close": float(rb["c"]) if rb else None,
                        "dividend": None})
        rows[s] = out
    return rows


def run_compare(d: CompareDeps) -> dict:
    now_utc = d.now_utc()
    now_et = now_utc.astimezone(NY_TZ)
    today = now_et.date().isoformat()
    store = sc.ShadowStore(d.state_dir)
    books = {n: store.load_book(s, d.starting_cash_cents) for n, s in d.strategies.items()}
    last = min((b["last_session"] for b in books.values() if b["last_session"]), default=None)
    rec = {"run_timestamp_utc": now_utc.isoformat(), "session_date_et": today, "mode": "SHADOW_COMPARE -- no broker access",
           "processed_dates": [], "status": None}

    start = last or today
    try:
        sessions = session_calendar.fetch_sessions(d.get_json, CALENDAR_HOST, d.headers, start, today)
    except session_calendar.CalendarUnavailable as e:
        return {**rec, "status": "abstained_calendar_unavailable", "detail": str(e)}
    cutoff_passed = now_et >= now_et.replace(hour=CUTOFF_ET[0], minute=CUTOFF_ET[1], second=0, microsecond=0)
    todo = [s for s in sessions if (last is None or s > last)]
    if today in todo and not cutoff_passed:
        todo.remove(today)
    if last is None:
        todo = [s for s in todo if s == today]     # first run: session zero is today, never a backfill
    if not todo:
        return {**rec, "status": "nothing_to_do" if today in sessions else "non_trading_day"}

    try:
        rows = build_rows(d.get_json, d.headers, now_utc)
    except sc.DataGap as e:
        return {**rec, "status": "abstained_data_unavailable", "detail": str(e), "escalate": True}

    ready = []
    for date in todo:   # contiguous prefix of sessions for which EVERY symbol has a complete bar
        ok = all(any(r["timestamp"] == date and r["open"] is not None for r in rows[s][-60:]) for s in UNIVERSE)
        if not ok:
            break
        ready.append(date)
    if not ready:
        return {**rec, "status": "abstained_data_not_ready", "escalate": any(x < today for x in todo), "pending_dates": todo}

    records = sc.run_sessions(books, d.strategies, rows, ready)
    store.commit(books, records)
    rec.update(status="processed", processed_dates=ready, gap_after=ready[-1] if len(ready) < len(todo) else None,
               escalate=len(ready) < len(todo))
    if records:
        last_rec = records[-1]
        rec["latest"] = {"date": last_rec["date"], "comparison": last_rec["comparison"],
                         "books": {n: {k: b[k] for k in ("step", "rebalanced", "halted", "equity_cents", "drawdown",
                                                         "standing_exposure", "target_turnover_one_way",
                                                         "standing_target", "new_target")}
                                   for n, b in last_rec["books"].items()}}
    return rec


def main():
    from runtime_credential_loader import load_credential_headers, get_json
    deps = CompareDeps(get_json=get_json, headers=load_credential_headers(),
                       now_utc=lambda: datetime.now(timezone.utc),
                       state_dir=Path(os.environ.get("KIM_SHADOW_COMPARE_DIR", DEFAULT_STATE_DIR)),
                       strategies=shadow_strategies.both())
    print(json.dumps(run_compare(deps), indent=2, default=str))


if __name__ == "__main__":
    main()
