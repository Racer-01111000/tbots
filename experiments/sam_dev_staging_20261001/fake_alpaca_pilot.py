"""Test support for the Kim paper pilot: an in-memory Alpaca PAPER API (account, positions, orders incl. by-client-id lookup and
cancel, clock, calendar, adjusted/raw daily bars honouring start/end/feed) driven by a controllable UTC clock. One object serves
the runner's get_json and AlpacaPaperBroker's http. No network, no credentials, no real orders."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UNIVERSE = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
ACCOUNT_ID = "00000000-fake-0000-0000-paper-account"


def weekdays(start: str, end: str, holidays=()) -> list[str]:
    d, out, e = date.fromisoformat(start), [], date.fromisoformat(end)
    while d <= e:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class FakePilotAlpaca:
    def __init__(self, now_et=("2026-10-06", "09:30:05"), holidays=(), early_close=None):
        d, t = now_et
        hh, mm, ss = (int(x) for x in t.split(":"))
        dd = date.fromisoformat(d)
        self.now = datetime(dd.year, dd.month, dd.day, hh, mm, ss, tzinfo=NY).astimezone(timezone.utc)
        self.sessions = weekdays("2025-01-01", "2026-12-31", holidays)
        self.early_close = early_close or {}            # date -> "13:00"
        self.account = {"id": ACCOUNT_ID, "status": "ACTIVE", "trading_blocked": False, "account_blocked": False}
        self.cash = 99_967.76
        self.positions = {"DBC": 1}
        self.price_scale = {s: 1.0 for s in UNIVERSE}
        self.orders: list = [{"id": "diag-1", "client_order_id": "kim-manual-test-20261002T025247Z", "symbol": "DBC", "side": "buy",
                              "qty": "1", "filled_qty": "1", "status": "filled", "time_in_force": "day"}]
        self.post_log, self.delete_log, self.get_log = [], [], []
        self.fail: dict = {}                 # e.g. {"post": "timeout"|503, "clock": 500, "bars": 500, "cancel": 503}
        self.bar_mutation = None             # fn(symbol, adjustment, bars) -> bars
        self.market_open_override = None     # True/False forces the clock
        self.hidden: dict = {}               # client_order_id -> lookups still returning 404
        self.fill_on_post = False
        self.raw_over_adjusted = 1.02        # raw close = adjusted * this (so sizing visibly uses RAW)
        self._n = 0

    # ---- time -----------------------------------------------------------------
    def now_utc(self):
        return self.now

    def sleep(self, s):
        self.now += timedelta(seconds=s)

    def set_et(self, d: str, hhmm: str):
        hh, mm = (int(x) for x in hhmm.split(":")[:2])
        dd = date.fromisoformat(d)
        self.now = datetime(dd.year, dd.month, dd.day, hh, mm, tzinfo=NY).astimezone(timezone.utc)

    def _session_row(self, d: str):
        return {"date": d, "open": "09:30", "close": self.early_close.get(d, "16:00")}

    def is_open(self):
        if self.market_open_override is not None:
            return self.market_open_override
        et = self.now.astimezone(NY)
        d = et.date().isoformat()
        if d not in self.sessions:
            return False
        ch, cm = (int(x) for x in self.early_close.get(d, "16:00").split(":"))
        return (9, 30) <= (et.hour, et.minute) < (ch, cm)

    # ---- account economics -------------------------------------------------------
    def mark(self, sym):
        idx = UNIVERSE.index(sym)
        return round((50 + 10 * idx) * self.price_scale[sym], 4)

    def equity(self):
        return round(self.cash + sum(q * self.mark(s) for s, q in self.positions.items()), 2)

    def crash(self, factor):
        for s in UNIVERSE:
            self.price_scale[s] = factor

    # ---- bars ------------------------------------------------------------------------
    def bars(self, sym, adjustment, start, end):
        idx = UNIVERSE.index(sym)
        out = []
        ds = [d for d in self.sessions if start <= d <= end]
        for i, d in enumerate(ds):
            px = (50 + 10 * idx) * (1.0003 + 0.00005 * (7 - idx)) ** i * (1 + 0.004 * math.sin(i / (3 + idx)))
            c = px if adjustment == "all" else px * self.raw_over_adjusted
            out.append({"t": f"{d}T04:00:00Z", "o": round(c * 0.999, 4), "c": round(c, 4)})
        if self.bar_mutation:
            out = self.bar_mutation(sym, adjustment, out)
        return out

    # ---- GET ---------------------------------------------------------------------------
    def get_json(self, url, headers):
        u = urlparse(url)
        q = parse_qs(u.query)
        p = u.path
        self.get_log.append(url)
        if "data.alpaca.markets" in url:
            if "bars" in self.fail:
                return self.fail["bars"], None
            sym = p.split("/")[3]
            return 200, {"bars": self.bars(sym, q["adjustment"][0], q["start"][0], q["end"][0]), "next_page_token": None,
                         "_feed": q.get("feed", [None])[0]}
        if p == "/v2/account":
            if "account" in self.fail:
                return self.fail["account"], None
            return 200, {**self.account, "equity": f"{self.equity():.2f}", "cash": f"{self.cash:.2f}", "buying_power": "400000"}
        if p == "/v2/positions":
            if "positions" in self.fail:
                return self.fail["positions"], None
            return 200, [{"symbol": s, "qty": str(q), "side": "long"} for s, q in self.positions.items() if q]
        if p == "/v2/orders":
            if "orders" in self.fail:
                return self.fail["orders"], None
            st = q.get("status", ["all"])[0]
            rows = [o for o in self.orders if self.hidden.get(o["client_order_id"], 0) <= 0]
            if st == "open":
                rows = [o for o in rows if o["status"] in ("new", "accepted", "partially_filled", "pending_new")]
            return 200, [dict(o) for o in rows]
        if p == "/v2/clock":
            if "clock" in self.fail:
                return self.fail["clock"], None
            et = self.now.astimezone(NY)
            return 200, {"timestamp": self.now.astimezone(NY).isoformat(), "is_open": self.is_open(),
                         "next_open": et.isoformat(), "next_close": et.isoformat()}
        if p == "/v2/calendar":
            if "calendar" in self.fail:
                return self.fail["calendar"], None
            lo, hi = q["start"][0], q["end"][0]
            return 200, [self._session_row(d) for d in self.sessions if lo <= d <= hi]
        raise AssertionError(f"unexpected GET {url}")

    # ---- HTTP (AlpacaPaperBroker) ---------------------------------------------------------------
    def http(self, method, url, headers, body, timeout=10.0):
        u = urlparse(url)
        assert u.netloc == "paper-api.alpaca.markets", f"non-paper host reached: {url}"
        if method == "POST" and u.path == "/v2/orders":
            b = json.loads(body)
            self.post_log.append(b)
            if self.fail.get("post") == "timeout":
                raise TimeoutError("simulated")
            if self.fail.get("post") == 503:
                return 503, {"message": "unavailable"}
            if self.fail.get("post") == 403:
                return 403, {"message": "insufficient buying power"}
            if any(o["client_order_id"] == b["client_order_id"] for o in self.orders):
                return 422, {"message": "client_order_id must be unique"}
            self._n += 1
            o = {"id": f"id-{self._n}", "client_order_id": b["client_order_id"], "symbol": b["symbol"], "qty": b["qty"],
                 "side": b["side"], "status": "accepted", "filled_qty": "0", "time_in_force": b["time_in_force"],
                 "extended_hours": b.get("extended_hours")}
            self.orders.append(o)
            if self.fill_on_post:
                self.fill(o["client_order_id"])
            if self.fail.get("post") == "timeout_after_accept_once":
                del self.fail["post"]
                raise TimeoutError("response lost after the broker accepted the order")
            return 200, dict(o)
        if method == "GET" and u.path == "/v2/orders:by_client_order_id":
            if "lookup" in self.fail:
                return self.fail["lookup"], None
            cid = parse_qs(u.query)["client_order_id"][0]
            if self.hidden.get(cid, 0) > 0:
                self.hidden[cid] -= 1
                return 404, {"message": "not found"}
            o = next((o for o in self.orders if o["client_order_id"] == cid), None)
            return (200, dict(o)) if o else (404, {"message": "not found"})
        if method == "DELETE" and u.path.startswith("/v2/orders/"):
            oid = u.path.rsplit("/", 1)[1]
            self.delete_log.append(oid)
            if "cancel" in self.fail:
                return self.fail["cancel"], None
            o = next((o for o in self.orders if o["id"] == oid), None)
            if o is None:
                return 404, {"message": "not found"}
            if o["status"] in ("filled", "canceled", "expired", "rejected"):
                return 422, {"message": "order is not cancelable"}
            o["status"] = "canceled"
            return 204, None
        raise AssertionError(f"unexpected {method} {url}")

    # ---- fills ------------------------------------------------------------------------------------------
    def fill(self, cid, qty=None):
        o = next(o for o in self.orders if o["client_order_id"] == cid)
        total = int(float(o["qty"]))
        q = total if qty is None else qty
        already = int(float(o["filled_qty"]))
        add = q - already
        sign = 1 if o["side"] == "buy" else -1
        px = self.mark(o["symbol"])
        self.cash -= sign * add * px
        self.positions[o["symbol"]] = self.positions.get(o["symbol"], 0) + sign * add
        if not self.positions[o["symbol"]]:
            del self.positions[o["symbol"]]
        o["filled_qty"] = str(q)
        o["status"] = "filled" if q >= total else "partially_filled"


def pin_for(fake: FakePilotAlpaca) -> dict:
    return {"account_id_sha256": hashlib.sha256(fake.account["id"].encode()).hexdigest(),
            "expected_baseline_positions": {"DBC": 1},
            "known_diagnostic_client_order_ids": ["kim-manual-test-20261002T025247Z"]}
