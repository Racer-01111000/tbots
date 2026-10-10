"""Test support for the recurring runner: FakePilotAlpaca plus FILL activities, scripted HTTP faults per URL fragment, and a
calendar with real-looking holidays/early closes. No network, no credentials, no orders."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from fake_alpaca_pilot import FakePilotAlpaca


class FakeRecurringAlpaca(FakePilotAlpaca):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.activities: list = []           # {"order_id","side","qty","id"}
        self.script: dict = {}               # url fragment -> list of outcomes consumed in order: int status | "oserror" | "garbage" | "ok"
        self.http_script: dict = {}          # same, for the broker http (POST/GET/DELETE): fragment -> outcomes

    def _scripted(self, table, url):
        for frag, outs in table.items():
            if frag in url and outs:
                return outs.pop(0)
        return "ok"

    def get_json(self, url, headers):
        o = self._scripted(self.script, url)
        if o == "oserror":
            raise OSError("connection reset")
        if o == "garbage":
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        if isinstance(o, int):
            return o, {"message": "scripted"}
        if urlparse(url).path == "/v2/account/activities/FILL":
            return 200, [dict(a) for a in self.activities]
        return super().get_json(url, headers)

    def http(self, method, url, headers, body, timeout=10.0):
        o = self._scripted(self.http_script, method + " " + url)
        if o == "oserror":
            raise OSError("timed out")
        if o == "garbage":
            raise ValueError("Expecting value")
        if isinstance(o, int):
            return o, {"message": "scripted"}
        return super().http(method, url, headers, body, timeout)

    def record_fill_activity(self, cid):
        o = next(x for x in self.orders if x["client_order_id"] == cid)
        self.activities.append({"id": f"act-{len(self.activities)}", "order_id": o["id"], "side": o["side"], "qty": o["filled_qty"]})
