"""Contract-expiry calendars for the continuous front-month series, used ONLY as post-hoc sensitivity filters (frozen thresholds are never changed)."""
from datetime import date, timedelta
DELIV = {"ZS": (1, 3, 5, 7, 8, 9, 11), "ZC": (3, 5, 7, 9, 12), "ZW": (3, 5, 7, 9, 12), "KE": (3, 5, 7, 9, 12)}


def prev_bd(d):
    d -= timedelta(days=1)
    while d.weekday() >= 5: d -= timedelta(days=1)
    return d


def expiries(sym, y0, y1):
    out = []
    for y in range(y0, y1 + 1):
        if sym in DELIV:
            for m in DELIV[sym]: out.append(prev_bd(date(y, m, 15)))                       # business day before the 15th of the delivery month
        elif sym == "HO":
            for m in range(1, 13):                                                         # last business day of the month BEFORE the delivery month
                d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
                while d.weekday() >= 5: d -= timedelta(days=1)
                out.append(d)
        elif sym == "CL":
            for m in range(1, 13):                                                         # 3 business days before the 25th calendar day of the month prior to delivery
                d = date(y, m, 25)
                if d.weekday() >= 5: d = prev_bd(d)
                for _ in range(3): d = prev_bd(d)
                out.append(d)
    return sorted(out)


def session_offset(series, expiry):
    """index offset helper: index of the first session on/after the expiry date"""
    return series.first_on_or_after(expiry.isoformat())
