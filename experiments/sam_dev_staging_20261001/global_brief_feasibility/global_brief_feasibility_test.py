"""Standalone diagnostic -- NOT part of the production tick path. Tests
feasibility of the expanded global pre-open brief's data sources before
any production integration. Read-only, GET-only. Alpaca headers are only
ever sent to Alpaca hosts; never to Yahoo/EIA/USDA."""
import json, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots/experiments/sam_dev_staging_20261001/alpaca_adapter")
from runtime_credential_loader import load_credential_headers, get_json

def yahoo_chart(symbol, range_="5d", interval="1d"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return None, str(e)

results = {}

print("=== 1. Delayed SIP, completed session ===")
headers = load_credential_headers()
end = datetime.now(timezone.utc) - timedelta(minutes=30)
url = f"https://data.alpaca.markets/v2/stocks/SPY/bars?timeframe=1Day&feed=sip&start=2026-09-24&end={end.strftime('%Y-%m-%dT%H:%M:%SZ')}&limit=10"
status, body = get_json(url, headers)
bars = (body or {}).get("bars", [])
print(f"status={status} bars_returned={len(bars)}")
if bars:
    print("sample:", bars[-1])
results["delayed_sip_completed_session"] = {"status": status, "bars_returned": len(bars), "sample": bars[-1] if bars else None}

print()
print("=== 2. Global instruments via free Yahoo chart API ===")
UNIVERSE_TEST = {
    "Nikkei 225": "^N225", "Hang Seng": "^HSI", "CSI 300": "000300.SS",
    "KOSPI": "^KS11", "ASX 200": "^AXJO",
    "FTSE 100": "^FTSE", "DAX": "^GDAXI", "Euro Stoxx 50": "^STOXX50E",
    "SP500 futures": "ES=F", "Nasdaq futures": "NQ=F",
    "WTI": "CL=F", "Brent": "BZ=F", "Gold": "GC=F", "USD Index": "DX=F",
}
instr_results = {}
for name, sym in UNIVERSE_TEST.items():
    status, data = yahoo_chart(sym)
    ok = status == 200 and data and data.get("chart", {}).get("result")
    label = "OK"
    sample = None
    if ok:
        res = data["chart"]["result"][0]
        meta = res.get("meta", {})
        sample = {
            "regularMarketPrice": meta.get("regularMarketPrice"),
            "previousClose": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "exchangeTimezoneName": meta.get("exchangeTimezoneName"),
            "regularMarketTime": meta.get("regularMarketTime"),
            "instrumentType": meta.get("instrumentType"),
            "currency": meta.get("currency"),
        }
    else:
        label = f"UNAVAILABLE (status={status})"
    instr_results[name] = {"symbol": sym, "label": label, "sample": sample}
    print(f"{name:16s} ({sym:12s}) -> {label}  {sample}")
    time.sleep(0.3)
results["global_instruments"] = instr_results

print()
print("=== 3. News poll re-check (dedup) ===")
import sqlite3
conn = sqlite3.connect("/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots/experiments/sam_dev_staging_20261001/kim_shadow_pilot/state.sqlite3")
before = conn.execute("SELECT count(*) FROM news_items").fetchone()[0]
print(f"news_items before: {before}")
results["news_items_count_before_test"] = before
conn.close()

out_path = "/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots/experiments/sam_dev_staging_20261001/kim_shadow_pilot/global_brief_feasibility_test.json"
with open(out_path, "w") as f:
    json.dump(results, f, indent=2, default=str)
print()
print(f"Wrote {out_path}")

