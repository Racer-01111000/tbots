"""Daily bars from the cached Yahoo chart JSON. Dates are the exchange (America/New_York) session date."""
import json, os
from datetime import datetime
from zoneinfo import ZoneInfo
RAW = os.path.join(os.path.dirname(__file__), "..", "cache", "raw")
NY = ZoneInfo("America/New_York")
FILES = {"ZC": "yahoo_ZC_F", "ZS": "yahoo_ZS_F", "ZW": "yahoo_ZW_F", "KE": "yahoo_KE_F", "CL": "yahoo_CL_F", "HO": "yahoo_HO_F", "NG": "yahoo_NG_F",
         "RB": "yahoo_RB_F", "BZ": "yahoo_BZ_F", "CF": "yahoo_CF", "MOS": "yahoo_MOS", "SPY": "yahoo_SPY", "DBA": "yahoo_DBA", "CORN": "yahoo_CORN",
         "WEAT": "yahoo_WEAT", "SOYB": "yahoo_SOYB", "USO": "yahoo_USO", "N225": "yahoo_IDX_N225", "HSI": "yahoo_IDX_HSI", "FTSE": "yahoo_IDX_FTSE",
         "STOXX": "yahoo_IDX_STOXX50E", "DAX": "yahoo_IDX_GDAXI", "AXJO": "yahoo_IDX_AXJO", "SSE": "yahoo_000001.SS", "VIX": "yahoo_IDX_VIX", "GSPC": "yahoo_IDX_GSPC"}


class Series:
    def __init__(self, dates, close, open_=None):
        self.dates, self.close, self.open = dates, close, open_ or []
        self.pos = {d: i for i, d in enumerate(dates)}

    def first_on_or_after(self, d):
        import bisect
        i = bisect.bisect_left(self.dates, d)
        return i if i < len(self.dates) else None

    def ret(self, i_from, i_to):
        if i_from is None or i_to is None or i_from < 0 or i_to >= len(self.close):
            return None
        a, b = self.close[i_from], self.close[i_to]
        return None if not a or not b else b / a - 1.0


def load(sym):
    j = json.load(open(os.path.join(RAW, FILES[sym] + ".json")))["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    dates, close, opn = [], [], []
    for t, c, o in zip(j["timestamp"], q["close"], q["open"]):
        if c is None:
            continue
        dates.append(datetime.fromtimestamp(t, NY).date().isoformat()); close.append(c); opn.append(o)
    return Series(dates, close, opn)
