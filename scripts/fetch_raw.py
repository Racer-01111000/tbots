#!/usr/bin/env python3
"""Fetch raw daily EOD history for the asset universe from Yahoo's chart
API and preserve the exact response bytes under data/raw/. Does not
parse, transform, or normalize anything — that happens in
build_dataset_revision.py, from these preserved files, not from a
second network call."""
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from hashing import sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
UNIVERSE = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
SOURCE = "yahoo-finance-chart-api-v8"


def fetch_one(ticker: str) -> dict:
    period2 = int(time.time())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?period1=0&period2={period2}&interval=1d&events=div,splits")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    fetched_at = datetime.now(timezone.utc).isoformat()
    with urllib.request.urlopen(req, timeout=20) as resp:
        status = resp.status
        data = resp.read()

    out_path = RAW_DIR / f"{ticker}.json"
    out_path.write_bytes(data)

    return {
        "ticker": ticker,
        "url": url,
        "http_status": status,
        "bytes": len(data),
        "sha256": sha256_bytes(data),
        "fetched_at": fetched_at,
        "raw_path": str(out_path.relative_to(ROOT)),
    }


if __name__ == "__main__":
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for ticker in UNIVERSE:
        print(f"fetching {ticker} ...", end=" ", flush=True)
        try:
            entry = fetch_one(ticker)
            manifest[ticker] = entry
            print(f"ok, {entry['bytes']} bytes, sha256={entry['sha256'][:12]}...")
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}")
            manifest[ticker] = {"ticker": ticker, "error": f"{type(e).__name__}: {e}"}

    (RAW_DIR / "manifest.json").write_text(
        json.dumps({"source": SOURCE, "entries": manifest}, indent=2, sort_keys=True)
    )
    ok = sum(1 for v in manifest.values() if "error" not in v)
    print(f"\n{ok}/{len(UNIVERSE)} tickers fetched successfully")
