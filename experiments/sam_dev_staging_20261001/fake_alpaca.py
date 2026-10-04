"""Test support: an in-memory Alpaca paper API (account, positions, orders,
calendar, bars, POST /v2/orders). One object serves both the runner's
get_json and AlpacaPaperBroker's http, so tests drive the REAL run_session
and the REAL broker adapter end to end with no network."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

UNIVERSE = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]


def weekdays(start: str, n: int) -> list[str]:
    d, out = date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class FakeAlpaca:
    def __init__(self, today="2026-10-05", sessions=None, equity=100_000.0, cash=100_000.0):
        self.today = today
        self.sessions = sessions if sessions is not None else weekdays("2026-10-05", 120)
        self.account = {"status": "ACTIVE", "trading_blocked": False, "account_blocked": False,
                        "equity": str(equity), "cash": str(cash), "buying_power": str(cash * 4)}
        self.positions: list = []
        self.orders: list = []
        self.post_log: list = []
        self.fail: dict = {}            # e.g. {"positions": 500, "calendar": 500, "post": "timeout"}
        self.missing_bars: set = set()
        self.price_scale = 1.0

    # ---- helpers ----
    def set_equity(self, equity, cash=None):
        self.account["equity"] = str(equity)
        if cash is not None:
            self.account["cash"] = str(cash)

    def now_utc(self, hhmm_et=(16, 45)):
        from zoneinfo import ZoneInfo
        d = date.fromisoformat(self.today)
        et = datetime(d.year, d.month, d.day, hhmm_et[0], hhmm_et[1], tzinfo=ZoneInfo("America/New_York"))
        return et.astimezone(timezone.utc)

    def fill_all(self):
        for o in self.orders:
            if o["status"] in ("accepted", "new"):
                o["status"], o["filled_qty"] = "filled", o["qty"]
                q = int(o["qty"]) * (1 if o["side"] == "buy" else -1)
                cur = next((p for p in self.positions if p["symbol"] == o["symbol"]), None)
                if cur is None:
                    self.positions.append({"symbol": o["symbol"], "qty": str(q)})
                else:
                    cur["qty"] = str(int(float(cur["qty"])) + q)
        self.positions = [p for p in self.positions if int(float(p["qty"])) != 0]

    def _bars(self, symbol):
        if symbol in self.missing_bars:
            return []
        idx = UNIVERSE.index(symbol)
        dates = [d for d in weekdays("2025-01-01", 600) if d <= self.today][-330:]
        out = []
        for i, d in enumerate(dates):
            px = (50 + 10 * idx) * (1.0003 + 0.00005 * (7 - idx)) ** i * (1 + 0.004 * math.sin(i / (3 + idx)))
            out.append({"t": f"{d}T04:00:00Z", "c": round(px * self.price_scale, 4)})
        return out

    # ---- GET (runner) ----
    def get_json(self, url, headers):
        u = urlparse(url)
        q = parse_qs(u.query)
        p = u.path
        if "data.alpaca.markets" in url:
            sym = p.split("/")[3]
            return 200, {"bars": self._bars(sym)}
        if p == "/v2/account":
            return self.fail.get("account", 200), (self.account if "account" not in self.fail else None)
        if p == "/v2/positions":
            return self.fail.get("positions", 200), (self.positions if "positions" not in self.fail else None)
        if p == "/v2/orders":
            return self.fail.get("orders", 200), (list(self.orders) if "orders" not in self.fail else None)
        if p == "/v2/calendar":
            if "calendar" in self.fail:
                return self.fail["calendar"], None
            lo, hi = q["start"][0], q["end"][0]
            return 200, [{"date": d, "open": "09:30", "close": "16:00"} for d in self.sessions if lo <= d <= hi]
        raise AssertionError(f"unexpected GET {url}")

    # ---- HTTP (AlpacaPaperBroker) ----
    def http(self, method, url, headers, body, timeout=10.0):
        u = urlparse(url)
        if method == "POST" and u.path == "/v2/orders":
            b = json.loads(body)
            self.post_log.append(b)
            if self.fail.get("post") == "timeout":
                raise TimeoutError("simulated")
            if self.fail.get("post") == 503:
                return 503, {"message": "unavailable"}
            if any(o["client_order_id"] == b["client_order_id"] for o in self.orders):
                return 422, {"message": "client_order_id must be unique"}
            order = {"id": f"id-{len(self.orders)+1}", "client_order_id": b["client_order_id"],
                     "symbol": b["symbol"], "qty": b["qty"], "side": b["side"],
                     "status": "accepted", "filled_qty": "0", "time_in_force": b["time_in_force"]}
            self.orders.append(order)
            return 200, dict(order)
        if method == "GET" and u.path == "/v2/orders:by_client_order_id":
            if "lookup" in self.fail:
                return self.fail["lookup"], None
            cid = parse_qs(u.query)["client_order_id"][0]
            o = next((o for o in self.orders if o["client_order_id"] == cid), None)
            return (200, dict(o)) if o else (404, {"message": "not found"})
        raise AssertionError(f"unexpected {method} {url}")
