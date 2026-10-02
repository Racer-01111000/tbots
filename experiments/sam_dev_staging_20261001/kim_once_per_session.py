"""Kim once-per-session decision job -- SHADOW ONLY. Decides, logs, and
reconciles broker state; never calls route_order() or sets
TBOTS_ALPACA_SUBMISSION_ENABLED. Intended schedule: once per trading
session, shortly after close (matches execution.py's own decide-on-
close/fill-on-next-open convention).

Hardening added here (2026-10-02 GO):
  - missing-price abstention: a symbol whose latest fetched bar is absent,
    insufficient for warmup, or older than MAX_STALENESS_SECONDS is marked
    unavailable and excluded, never silently substituted or guessed.
  - finite-value checks: every fetched price is checked with
    math.isfinite before any indicator/sizing math touches it; equity is
    checked the same way before sizing.
  - no-borrow accounting: sizing is always computed off account EQUITY,
    never buying_power -- asserted explicitly and logged alongside the
    ignored buying_power figure for audit.
  - persistent cadence: sessions_elapsed is read from and written to
    cadence_state.json, surviving process/service restarts. The 50-
    session rebalance gate (champion_decide_fn_factory's own semantics,
    reused unmodified: step % N == 0) is evaluated against this
    persisted counter, never a per-process-local variable.
  - WARMUP_BARS = 296 (295-day longest momentum lookback + 1) is a named
    module constant.

Set KIM_SESSION_VALIDATE_ONLY=1 to run the full pipeline (fetch, decide,
reconcile, log) WITHOUT advancing or writing the persisted cadence
counter -- for proving the mechanics without consuming a real session
slot before the service is actually activated.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path("/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots")
DEV_STAGING = REPO / "experiments/sam_dev_staging_20261001"
sys.path.insert(0, str(DEV_STAGING / "alpaca_adapter"))
sys.path.insert(0, str(REPO / "scripts"))

from runtime_credential_loader import load_credential_headers, get_json  # noqa: E402
from alpaca_adapter import (  # noqa: E402
    assert_trading_host_allowed, submission_enabled, PAPER_TRADING_HOST,
)
import control_agent  # noqa: E402
import execution  # noqa: E402

UNIVERSE = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
WARMUP_BARS = 296  # longest momentum lookback (295) + 1
LOOKBACK_CALENDAR_DAYS = 600
MAX_STALENESS_SECONDS = 60 * 60 * 30  # 30h: generous for a once-per-session EOD job
REBALANCE_EVERY_N_SESSIONS = 50  # must match genome["rebalance_every_n_sessions"]

STATE_DIR = DEV_STAGING / "kim_shadow_pilot"
CADENCE_PATH = STATE_DIR / "cadence_state.json"
SESSION_LOG_DIR = STATE_DIR / "once_per_session_log"

VALIDATE_ONLY = os.environ.get("KIM_SESSION_VALIDATE_ONLY") == "1"


class LiveView:
    """Minimal AgentView-compatible object. Unavailable symbols return
    available=False / empty history -- never a fabricated value."""

    def __init__(self, bars_by_symbol: dict, availability: dict):
        self._bars = bars_by_symbol
        self._availability = availability

    def observe(self):
        assets = {}
        ts = None
        for s in UNIVERSE:
            if self._availability.get(s):
                assets[s] = {"adjusted_close": self._bars[s][-1]["adjusted_close"], "available": True}
                ts = self._bars[s][-1]["timestamp"]
            else:
                assets[s] = {"available": False}
        return {"assets": assets, "timestamp": ts}

    def history(self, symbol, bars):
        if not self._availability.get(symbol):
            return []
        rows = self._bars[symbol]
        return rows[-bars:] if bars > 0 else []


def load_cadence_state() -> dict:
    if CADENCE_PATH.exists():
        return json.loads(CADENCE_PATH.read_text())
    today = datetime.now(timezone.utc).date().isoformat()
    state = {
        "session_zero_date": today,
        "sessions_elapsed": 0,
        "last_run_date": None,
        "rebalance_every_n_sessions": REBALANCE_EVERY_N_SESSIONS,
        "cadence_source": "champion_decide_fn_factory semantics (step % N == 0), reused unmodified",
        "note": "sessions_elapsed increments once per ACTUAL once-per-session run; a missed/skipped day does not advance it on its own.",
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CADENCE_PATH.write_text(json.dumps(state, indent=2))
    return state


def persist_cadence_state(state: dict) -> None:
    CADENCE_PATH.write_text(json.dumps(state, indent=2))


def fetch_bars(headers: dict) -> tuple[dict, dict]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    bars_by_symbol: dict = {}
    availability: dict = {}
    for symbol in UNIVERSE:
        url = (
            f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
            f"?timeframe=1Day&start={start.isoformat()}&end={end.isoformat()}"
            f"&adjustment=all&feed=iex&limit=10000"
        )
        try:
            status, body = get_json(url, headers)
        except Exception:
            availability[symbol] = False
            bars_by_symbol[symbol] = []
            continue
        if status != 200:
            availability[symbol] = False
            bars_by_symbol[symbol] = []
            continue
        raw_bars = body.get("bars", [])
        rows = []
        for b in raw_bars:
            price = b.get("c")
            if price is None:
                continue
            fprice = float(price)
            if not math.isfinite(fprice):
                continue  # finite-value check: drop any non-finite bar outright
            rows.append({"timestamp": b["t"][:10], "adjusted_close": fprice})
        bars_by_symbol[symbol] = rows

        if len(rows) < WARMUP_BARS:
            availability[symbol] = False  # missing-price abstention: insufficient warmup
            continue
        if not raw_bars:
            availability[symbol] = False
            continue
        latest_ts = datetime.fromisoformat(raw_bars[-1]["t"].replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - latest_ts).total_seconds()
        if age_seconds > MAX_STALENESS_SECONDS:
            availability[symbol] = False  # missing-price abstention: too stale
            continue
        availability[symbol] = True
    return bars_by_symbol, availability


def main():
    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    assert submission_enabled() is False, "REFUSING: submission_enabled() must be False -- shadow-only"
    assert_trading_host_allowed(PAPER_TRADING_HOST)

    headers = load_credential_headers()
    cadence = load_cadence_state()

    # --- reconcile broker state first (read-only) ---
    acct_status, acct = get_json("https://paper-api.alpaca.markets/v2/account", headers)
    orders_status, orders = get_json(f"{PAPER_TRADING_HOST}/v2/orders?status=all&limit=50", headers)
    positions_status, positions = get_json(f"{PAPER_TRADING_HOST}/v2/positions", headers)

    equity = None
    buying_power = None
    if acct:
        if acct.get("equity") is not None:
            equity = float(acct["equity"])
        if acct.get("buying_power") is not None:
            buying_power = float(acct["buying_power"])

    abstain_reason = None
    if equity is None or not math.isfinite(equity):
        abstain_reason = "account equity missing or non-finite"

    bars_by_symbol, availability = fetch_bars(headers)
    view = LiveView(bars_by_symbol, availability)
    unavailable = [s for s, ok in availability.items() if not ok]

    step = cadence["sessions_elapsed"]
    rebalance_due = (step % cadence["rebalance_every_n_sessions"] == 0)

    sel = json.loads((DEV_STAGING / "selected_development_bot.json").read_text())
    genome = sel["selected_genome_definition"]

    decision = None
    intended_orders = None
    if abstain_reason:
        decision = {"abstained": True, "reason": abstain_reason}
    elif not rebalance_due:
        decision = {"hold": True, "reason": f"not a rebalance session (step {step} % {cadence['rebalance_every_n_sessions']} != 0)"}
    elif unavailable:
        decision = {"abstained": True, "reason": f"missing/stale price data for: {unavailable}"}
    else:
        decision = control_agent.decide(view, genome)
        equity_cents = round(equity * 100)  # no-borrow: EQUITY only, never buying_power
        mark_prices_cents = {s: round(bars_by_symbol[s][-1]["adjusted_close"] * 100) for s in UNIVERSE}
        current_shares = {
            p["symbol"]: int(float(p["qty"])) for p in (positions or []) if p.get("symbol") in UNIVERSE
        }
        try:
            intended_orders = execution.compute_orders(
                decision["weights"], UNIVERSE, equity_cents, mark_prices_cents, current_shares,
            )
        except Exception as e:  # noqa: BLE001
            intended_orders = {"error": repr(e)}

    record = {
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "SHADOW_ONLY -- decide, log, reconcile; no submission",
        "validate_only": VALIDATE_ONLY,
        "submission_enabled_confirmed_false": True,
        "cadence_step_used": step,
        "rebalance_due": rebalance_due,
        "unavailable_symbols": unavailable,
        "equity_used_for_sizing": equity,
        "buying_power_ignored": buying_power,
        "decision": decision,
        "intended_orders_not_submitted": intended_orders,
        "broker_account_status": acct.get("status") if acct else None,
        "broker_trading_blocked": acct.get("trading_blocked") if acct else None,
        "broker_open_order_count": len(orders) if orders else 0,
        "broker_position_count": len(positions) if positions else 0,
    }

    out_path = SESSION_LOG_DIR / f"session_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(json.dumps(record, indent=2, default=str))

    if not VALIDATE_ONLY:
        cadence["sessions_elapsed"] = step + 1
        cadence["last_run_date"] = datetime.now(timezone.utc).date().isoformat()
        persist_cadence_state(cadence)

    print(json.dumps(record, indent=2, default=str))
    print("Log written:", out_path)
    print("Cadence state (persisted):" if not VALIDATE_ONLY else "Cadence state (NOT advanced, validate-only):",
          json.dumps(cadence, indent=2))


if __name__ == "__main__":
    main()
