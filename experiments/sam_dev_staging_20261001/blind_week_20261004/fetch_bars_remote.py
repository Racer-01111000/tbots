"""Runs ON the research instance via SSM, as ec2-user. READ-ONLY: GET requests to Alpaca's market-data API
through the existing runtime_credential_loader (host allowlisted, GET-only). Writes NOTHING to disk and
places no orders; prints one compressed base64 JSON blob to stdout.
usage: fetch_bars_remote.py START END SYM[,SYM...]"""
import base64, json, sys, zlib
sys.path.insert(0, "/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots/experiments/sam_dev_staging_20261001/alpaca_adapter")
from runtime_credential_loader import load_credential_headers, get_json

start, end, syms = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
headers = load_credential_headers()
out = {"params": {"feed": "iex", "timeframe": "1Day", "start": start, "end": end, "limit": 10000}, "symbols": {}}
for s in syms:
    entry = {}
    for adj in ("all", "raw"):
        url = (f"https://data.alpaca.markets/v2/stocks/{s}/bars?timeframe=1Day&start={start}&end={end}"
               f"&adjustment={adj}&feed=iex&limit=10000")
        status, body = get_json(url, headers)
        bars = (body or {}).get("bars") or []
        entry[adj] = {"http_status": status, "n": len(bars), "next_page_token": (body or {}).get("next_page_token"),
                      "bars": [{"t": b["t"], "o": b.get("o"), "c": b.get("c")} for b in bars]}
    out["symbols"][s] = entry
print("BLOB:" + base64.b64encode(zlib.compress(json.dumps(out, separators=(",", ":")).encode(), 9)).decode())
