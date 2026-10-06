"""Data boundary for the Kim paper pilot. One frozen feed (cfg["feed"]); never switches automatically.

Signals use ADJUSTED daily closes (adjustment=all); sizing/reconciliation use RAW closes (adjustment=raw) of the same
completed session. Everything is validated against the exchange calendar before any decision is computed:
the series must be strictly increasing, free of duplicates, finite, positive, contiguous over the expected sessions,
end exactly on the latest fully COMPLETED session, and contain nothing dated after it. Any violation raises DataInvalid;
the caller abstains. A missing sizing price is a DataInvalid, never a reason to sell."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone


class DataInvalid(RuntimeError):
    pass


def _series(raw_bars: list, label: str) -> list[tuple[str, float]]:
    out = []
    for b in raw_bars:
        t, c = b.get("t"), b.get("c")
        if not isinstance(t, str) or len(t) < 10:
            raise DataInvalid(f"{label}: bar without a timestamp")
        try:
            date.fromisoformat(t[:10])
        except ValueError as e:
            raise DataInvalid(f"{label}: unparseable bar date {t!r}") from e
        try:
            v = float(c)
        except (TypeError, ValueError) as e:
            raise DataInvalid(f"{label}: non-numeric close on {t[:10]}") from e
        if not math.isfinite(v) or v <= 0:
            raise DataInvalid(f"{label}: non-finite or non-positive close on {t[:10]}")
        out.append((t[:10], v))
    return out


def validate_symbol(symbol: str, adj_bars: list, raw_bars: list, expected_sessions: list[str], completed: str,
                    min_bars: int) -> tuple[list[float], int]:
    """-> (adjusted closes oldest-first, raw close of `completed` in cents). `expected_sessions` = every exchange session
    <= `completed` (oldest first) covering the requested span."""
    adj, raw = _series(adj_bars, f"{symbol}/adjusted"), _series(raw_bars, f"{symbol}/raw")
    for label, ser in (("adjusted", adj), ("raw", raw)):
        dates = [d for d, _ in ser]
        if len(set(dates)) != len(dates):
            raise DataInvalid(f"{symbol}/{label}: duplicate session date")
        if dates != sorted(dates):
            raise DataInvalid(f"{symbol}/{label}: out-of-order bars")
        if dates and dates[-1] > completed:
            raise DataInvalid(f"{symbol}/{label}: future-dated bar {dates[-1]} after completed session {completed}")
    if len(adj) < min_bars:
        raise DataInvalid(f"{symbol}: {len(adj)} bars < required warm-up {min_bars}")
    ad, rd = [d for d, _ in adj], [d for d, _ in raw]
    if not ad or ad[-1] != completed:
        raise DataInvalid(f"{symbol}: latest adjusted bar {ad[-1] if ad else None} is not the completed session {completed}")
    if not rd or rd[-1] != completed:
        raise DataInvalid(f"{symbol}: no raw sizing bar for the completed session {completed}")
    expect = [s for s in expected_sessions if ad[0] <= s <= completed]
    if ad != expect:
        missing = sorted(set(expect) - set(ad))
        extra = sorted(set(ad) - set(expect))
        raise DataInvalid(f"{symbol}: adjusted history is not contiguous over exchange sessions "
                          f"(missing {missing[:5]}, not-a-session {extra[:5]})")
    if completed not in set(rd):
        raise DataInvalid(f"{symbol}: raw close for {completed} missing")
    raw_close = dict(raw)[completed]
    return [v for _, v in adj], round(raw_close * 100)


def canonical_sha256(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fetch_validated(get_json, headers: dict, cfg: dict, sessions: list[str], completed: str, now_utc: datetime) -> dict:
    """Fetch + validate all symbols. `sessions` = exchange calendar rows' dates covering the lookback window up to at
    least `completed`. Returns {adjusted: {sym: [closes]}, dates, sizing_cents, provenance}. Raises DataInvalid."""
    feed = cfg["feed"]
    if feed not in ("sip", "iex"):
        raise DataInvalid(f"unknown feed {feed!r}")
    end = completed
    start = (date.fromisoformat(completed) - timedelta(days=cfg["lookback_calendar_days"])).isoformat()
    exp = [s for s in sessions if s <= completed]
    adjusted, sizing, prov, last_dates = {}, {}, {"retrieved_at_utc": now_utc.isoformat(), "feed": feed, "start": start,
                                                  "end": end, "adjustments": {"signals": "all", "sizing": "raw"}, "per_symbol": {}}, None
    for sym in cfg["universe"]:
        got = {}
        for adj in ("all", "raw"):
            url = (f"https://data.alpaca.markets/v2/stocks/{sym}/bars?timeframe=1Day&start={start}&end={end}"
                   f"&adjustment={adj}&feed={feed}&limit=10000")
            try:
                status, body = get_json(url, headers)
            except Exception as e:  # noqa: BLE001
                raise DataInvalid(f"{sym}/{adj}: request failed: {e!r}") from e
            if status != 200 or not isinstance(body, dict):
                raise DataInvalid(f"{sym}/{adj}: HTTP {status}")
            if body.get("next_page_token"):
                raise DataInvalid(f"{sym}/{adj}: paged response (unexpected for the window)")
            got[adj] = body.get("bars") or []
        first = got["all"][0]["t"][:10] if got["all"] else None
        span = [s for s in exp if first and s >= first]
        closes, raw_cents = validate_symbol(sym, got["all"], got["raw"], exp, completed, cfg["warmup_bars"])
        adjusted[sym], sizing[sym] = closes, raw_cents
        prov["per_symbol"][sym] = {"bars": len(closes), "first": first, "adjusted_sha256": canonical_sha256(got["all"]),
                                   "raw_sha256": canonical_sha256(got["raw"])}
        dates = [b["t"][:10] for b in got["all"]]
        if last_dates is None:
            last_dates = dates
        elif dates[-cfg["warmup_bars"]:] != last_dates[-cfg["warmup_bars"]:]:
            raise DataInvalid(f"{sym}: session dates differ from the other symbols")
    return {"adjusted": adjusted, "dates": last_dates, "sizing_cents": sizing, "completed_session": completed,
            "provenance": prov}
