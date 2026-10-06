"""Production broker object for the Kim session runner: the `submit()` /
`find_by_client_order_id()` interface that kim_order_logic's ledger path
expects, backed by Alpaca's PAPER REST API only.

Safety properties (each pinned by test_alpaca_paper_broker.py):
  * paper host allowlist is asserted on every call;
  * submit() re-checks alpaca_adapter.submission_enabled() ITSELF, so even a
    caller that bypassed the runner's gate cannot send while it is False;
  * network errors, timeouts and 5xx on a POST map to UncertainSubmissionError
    (order may or may not exist -> caller reconciles, never blindly retries);
  * a 4xx that is NOT a duplicate-client-order-id is a definitive rejection
    (recorded terminal, no retry);
  * a duplicate-client-order-id 4xx means the broker already has it ->
    UncertainSubmissionError -> reconcile by lookup;
  * find_by_client_order_id(): 404 -> None ("broker has no such order"),
    200 -> record, ANYTHING else (5xx/timeout/network) -> BrokerLookupError,
    so an unanswered lookup is never mistaken for "not found".
HTTP is injected (`http(method, url, headers, body_bytes, timeout)` ->
(status, parsed_json_or_None)) so every path is testable with no network.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import quote

import alpaca_adapter as aa
from kim_order_logic import IntendedOrderSpec, UncertainSubmissionError, BrokerLookupError

TERMINAL_REJECT_CODES = {400, 403, 422}


def default_http(method: str, url: str, headers: dict, body: bytes | None, timeout: float = 10.0):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw) if raw else None)
        except ValueError:
            return e.code, {"message": raw.decode(errors="replace")[:300]}
    # URLError / socket.timeout / ConnectionError propagate as OSError to the caller.


def _is_duplicate_client_order_id(status: int, body) -> bool:
    msg = json.dumps(body).lower() if body is not None else ""
    return status == 422 and "client_order_id" in msg and ("unique" in msg or "duplicate" in msg)


class AlpacaPaperBroker:
    def __init__(self, headers: dict, http=default_http, host: str = aa.PAPER_TRADING_HOST,
                 submission_gate=aa.submission_enabled):
        aa.assert_trading_host_allowed(host)
        self._headers = dict(headers)
        self._http = http
        self._host = host
        self._gate = submission_gate

    @staticmethod
    def _normalize(body: dict) -> dict:
        return {"id": body.get("id"), "status": body.get("status"),
                "filled_qty": float(body.get("filled_qty") or 0),
                "symbol": body.get("symbol"), "side": body.get("side"), "qty": body.get("qty")}

    def submit(self, spec: IntendedOrderSpec) -> dict:
        aa.assert_trading_host_allowed(self._host)
        if not self._gate():
            raise aa.SubmissionDisabledError("AlpacaPaperBroker.submit() refused: submission is disabled")
        if spec.side not in ("buy", "sell") or spec.qty <= 0:
            raise ValueError(f"refusing malformed order {spec}")
        payload = json.dumps({
            "symbol": spec.symbol, "qty": str(spec.qty), "side": spec.side,
            "type": spec.order_type, "time_in_force": spec.time_in_force,
            "client_order_id": spec.client_order_id, "extended_hours": False,
        }).encode()
        try:
            status, body = self._http("POST", f"{self._host}/v2/orders",
                                      {**self._headers, "Content-Type": "application/json"}, payload)
        except OSError as e:  # timeout, connection reset, DNS ...
            raise UncertainSubmissionError(f"transport failure during POST: {e!r}") from e
        if status == 200 and isinstance(body, dict):
            return self._normalize(body)
        if status >= 500 or status in (408, 429):
            raise UncertainSubmissionError(f"POST returned HTTP {status}")
        if _is_duplicate_client_order_id(status, body):
            raise UncertainSubmissionError("broker reports client_order_id already exists")
        if status in TERMINAL_REJECT_CODES:
            return {"id": None, "status": "rejected", "filled_qty": 0.0,
                    "detail": f"HTTP {status}: {json.dumps(body)[:300]}"}
        raise UncertainSubmissionError(f"unclassified HTTP {status}")

    def cancel(self, order_id: str) -> str:
        """Cancel one working order by broker id. Returns 'cancel_requested' (200/204), 'not_found' (404) or 'not_cancelable'
        (422: already filled/canceled/expired -- the caller must re-read the order, a cancel is never proof of no fill).
        Anything else (5xx/timeout) raises BrokerLookupError: state unknown, caller re-reads before acting."""
        aa.assert_trading_host_allowed(self._host)
        if not self._gate():
            raise aa.SubmissionDisabledError("AlpacaPaperBroker.cancel() refused: submission is disabled")
        try:
            status, _ = self._http("DELETE", f"{self._host}/v2/orders/{quote(str(order_id))}", self._headers, None)
        except OSError as e:
            raise BrokerLookupError(f"transport failure during cancel: {e!r}") from e
        if status in (200, 204):
            return "cancel_requested"
        if status == 404:
            return "not_found"
        if status == 422:
            return "not_cancelable"
        raise BrokerLookupError(f"cancel returned HTTP {status}")

    def find_by_client_order_id(self, client_order_id: str) -> dict | None:
        aa.assert_trading_host_allowed(self._host)
        url = f"{self._host}/v2/orders:by_client_order_id?client_order_id={quote(client_order_id)}"
        try:
            status, body = self._http("GET", url, self._headers, None)
        except OSError as e:
            raise BrokerLookupError(f"transport failure during lookup: {e!r}") from e
        if status == 404:
            return None
        if status == 200 and isinstance(body, dict):
            return self._normalize(body)
        raise BrokerLookupError(f"lookup returned HTTP {status}")
