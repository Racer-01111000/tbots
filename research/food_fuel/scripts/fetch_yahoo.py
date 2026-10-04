"""Daily bars (adjusted and raw close, volume, dividend/split events) from Yahoo's public chart endpoint -- the same free source the project's
normalized ETF dataset already came from. Research instruments only; none is added to the trading universe. Front-month futures ('=F') are
continuous series WITH roll gaps; that limitation is carried into every analysis."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import fetch, now

SYMS = {
 "futures": ["ZC=F", "ZW=F", "ZS=F", "KE=F", "CL=F", "HO=F", "NG=F", "RB=F", "BZ=F"],
 "etf": ["DBA", "DBC", "CORN", "WEAT", "SOYB", "MOO", "USO", "UNG", "UGA", "XLE", "SPY"],
 "equity": ["CF", "MOS", "DE", "ADM", "BG"],
 "index": ["^N225", "^HSI", "^FTSE", "^GDAXE", "^STOXX50E", "^AXJO", "000001.SS", "^VIX", "^GSPC"],
 "fx": ["DX-Y.NYB", "USDJPY=X", "EURUSD=X"],
}
P1 = 1133395200   # 2005-12-01
P2 = int(time.time()) + 86400
ok, bad = [], []
for kind, syms in SYMS.items():
    for s in syms:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{s.replace('^', '%5E')}?period1={P1}&period2={P2}&interval=1d&events=div%2Csplits"
        b = fetch(url, f"yahoo_{s.replace('^', 'IDX_').replace('=', '_')}.json")
        (ok if b else bad).append(s)
        if b:
            try:
                r = json.loads(b)["chart"]["result"][0]; n = len(r["timestamp"])
                print(f"{kind:8s} {s:10s} {n:5d} bars {time.strftime('%Y-%m-%d', time.gmtime(r['timestamp'][0]))} .. {time.strftime('%Y-%m-%d', time.gmtime(r['timestamp'][-1]))} tz={r['meta'].get('exchangeTimezoneName')}")
            except Exception as e:
                print(kind, s, "PARSE_FAIL", repr(e)[:80]); bad.append(s)
print("ok", len(ok), "failed", bad)
