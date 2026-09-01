"""Deterministic normalization of a raw Yahoo Finance chart-API response
(source shape: https://query1.finance.yahoo.com/v8/finance/chart/{ticker})
into flat per-asset rows.

Normalization rules (fixed by NORMALIZATION_VERSION; changing any of them
requires bumping the version, since it changes normalized_content_hashes
and therefore the dataset revision):

  timezone convention:      source per-bar epoch is UTC; the row timestamp
                             is the UTC calendar date of that epoch. No
                             time-of-day is retained (these are EOD bars).
  timestamp granularity:    daily (one row per trading date).
  price precision:          full source float precision preserved via
                             repr() round-trip; no rounding is applied, so
                             normalization never discards precision Yahoo
                             actually provided.
  volume type:               integer (shares), taken as-is from source.
  null policy:               a row missing any of timestamp/open/high/low/
                             close/volume is EXCLUDED from output and
                             recorded in report['rejected_missing_field'].
                             adjusted_close is optional: if the source has
                             no adjclose series, or the value at that bar
                             is null, the row is still emitted with
                             adjusted_close = None (never fabricated from
                             close).
  duplicate policy:          if the same UTC date appears more than once
                             in the source, ALL rows sharing that date are
                             excluded (no arbitrary pick between them) and
                             the date is recorded in
                             report['duplicate_timestamps'].
  corporate-action handling: populated only when source events.dividends
                             or events.splits has an entry for that exact
                             date; otherwise left as None (explicitly
                             absent, not zero).
  adjusted-price handling:   close = source close (split-adjusted, NOT
                             dividend-adjusted, per Yahoo's chart-API
                             convention). adjusted_close = source adjclose
                             (split- and dividend-adjusted). Always two
                             distinct fields, never conflated.
  sort order:                ascending by timestamp; the single canonical
                             order for normalized output.
  invalid-numeric policy:    a row with open/high/low/close <= 0, or
                             high < low, or volume < 0 is EXCLUDED and
                             recorded in report['rejected_invalid_numeric'].
"""
import json
from datetime import datetime, timezone

NORMALIZATION_VERSION = "v1"

REQUIRED_QUOTE_FIELDS = ("open", "high", "low", "close", "volume")


def _utc_date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


def _fmt_num(x):
    """Deterministic, precision-preserving string form of a source float."""
    if x is None:
        return None
    if isinstance(x, int):
        return x
    return repr(float(x))


def normalize_yahoo_chart(raw_bytes: bytes, ticker: str, source: str,
                           source_url: str, ingested_at: str) -> tuple[list[dict], dict]:
    """Returns (rows, report). Pure function — no network, no filesystem,
    no wall-clock reads other than the ingested_at value the caller passes
    in, so it is safe to call twice on the same input and diff the output."""
    raw = json.loads(raw_bytes.decode("utf-8"))
    result = raw["chart"]["result"][0]
    timestamps = result.get("timestamp", []) or []
    quote = result["indicators"]["quote"][0]
    adjclose_series = result.get("indicators", {}).get("adjclose")
    adjclose = adjclose_series[0]["adjclose"] if adjclose_series else None

    events = result.get("events", {}) or {}
    dividends_by_date = {}
    for ev in (events.get("dividends") or {}).values():
        dividends_by_date[_utc_date(ev["date"])] = ev.get("amount")
    splits_by_date = {}
    for ev in (events.get("splits") or {}).values():
        splits_by_date[_utc_date(ev["date"])] = {
            "numerator": ev.get("numerator"),
            "denominator": ev.get("denominator"),
            "split_ratio": ev.get("splitRatio"),
        }

    report = {
        "ticker": ticker,
        "source": source,
        "raw_row_count": len(timestamps),
        "duplicate_timestamps": [],
        "rejected_missing_field": [],
        "rejected_invalid_numeric": [],
    }

    by_date: dict[str, list[int]] = {}
    for i, epoch in enumerate(timestamps):
        by_date.setdefault(_utc_date(epoch), []).append(i)

    duplicate_dates = {d for d, idxs in by_date.items() if len(idxs) > 1}
    report["duplicate_timestamps"] = sorted(duplicate_dates)

    rows = []
    for date, idxs in by_date.items():
        if date in duplicate_dates:
            continue
        i = idxs[0]
        epoch = timestamps[i]

        vals = {f: quote.get(f, [None] * len(timestamps))[i] for f in REQUIRED_QUOTE_FIELDS}
        missing = [f for f in REQUIRED_QUOTE_FIELDS if vals[f] is None]
        if missing:
            report["rejected_missing_field"].append({"date": date, "missing": missing})
            continue

        o, h, l, c, v = (vals["open"], vals["high"], vals["low"], vals["close"], vals["volume"])
        if o <= 0 or h <= 0 or l <= 0 or c <= 0 or h < l or v < 0:
            report["rejected_invalid_numeric"].append({
                "date": date, "open": o, "high": h, "low": l, "close": c, "volume": v,
            })
            continue

        adj = adjclose[i] if adjclose is not None else None

        corp_action = None
        div = dividends_by_date.get(date)
        split = splits_by_date.get(date)
        if div is not None or split is not None:
            corp_action = {}
            if div is not None:
                corp_action["dividend_amount"] = div
            if split is not None:
                corp_action["split"] = split

        rows.append({
            "asset": ticker,
            "timestamp": date,
            "open": _fmt_num(o),
            "high": _fmt_num(h),
            "low": _fmt_num(l),
            "close": _fmt_num(c),
            "adjusted_close": _fmt_num(adj),
            "volume": int(v),
            "corporate_action": json.dumps(corp_action) if corp_action is not None else None,
            "source": source,
            "source_timestamp": datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(),
            "ingested_at": ingested_at,
        })

    rows.sort(key=lambda r: r["timestamp"])

    report["output_row_count"] = len(rows)
    report["start"] = rows[0]["timestamp"] if rows else None
    report["end"] = rows[-1]["timestamp"] if rows else None
    return rows, report


CSV_COLUMNS = [
    "asset", "timestamp", "open", "high", "low", "close", "adjusted_close",
    "volume", "corporate_action", "source", "source_timestamp", "ingested_at",
]


def rows_to_csv(rows: list[dict]) -> str:
    """Deterministic CSV serialization: fixed column order, fixed row
    order (caller is expected to pass already timestamp-sorted rows),
    empty string for None (distinguishing 'field genuinely absent' from
    any numeric value)."""
    lines = [",".join(CSV_COLUMNS)]
    for r in rows:
        cells = []
        for col in CSV_COLUMNS:
            v = r[col]
            if v is None:
                cells.append("")
            elif col == "corporate_action":
                cells.append('"' + v.replace('"', '""') + '"')
            else:
                cells.append(str(v))
        lines.append(",".join(cells))
    return "\n".join(lines) + "\n"
