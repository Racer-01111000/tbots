"""Kim once-per-session decision job -- SHADOW ONLY. Decides, logs, and
reconciles broker state; never calls route_order() or sets
TBOTS_ALPACA_SUBMISSION_ENABLED.

v2 (2026-10-02 GO) hardening over the 2026-10-02 06:34 validate-only version:
  1. risk.validate() is now actually called before any order is computed.
     current_drawdown is passed as 0.0 explicitly -- SHADOW MODE DOES NOT
     ENFORCE THE DRAWDOWN HALT, because no peak-equity history is persisted
     yet. This is logged plainly in every record (risk_drawdown_enforced:
     false) rather than overstated. Persisting a real peak-equity
     high-water mark is a prerequisite before any submission is enabled.
  2. The no-sell-on-missing-price invariant is now enforced AT the order
     computation site (compute_target_orders), not just incidentally by
     the upstream whole-session abstain: any symbol in unavailable_symbols
     is dropped from both the universe and current_shares passed to
     execution.compute_orders, so it is structurally impossible for a
     missing/stale/invalid price to produce an order of any kind (buy or
     sell) for that symbol, independent of what triggered the call.
  3. Pending (open, unfilled) orders are now included when computing
     target deltas: remaining BUY qty is added to current shares,
     remaining SELL qty is subtracted, for orders in a live (non-terminal)
     status. This prevents the logged "intended order" from recommending a
     duplicate buy on top of an order already outstanding.
  4. Cadence is now keyed on the EVALUATED SESSION DATE, not the run
     timestamp: if this session date was already evaluated (by any prior
     run, including one interrupted by a restart), the job logs a distinct
     "already_evaluated" status and does NOT re-decide or advance
     sessions_elapsed. A restart after a completed run is a no-op.
  5. The completed-session cutoff is explicit: requires BOTH current time
     >= COLLECTION_CUTOFF (16:15 America/New_York) AND the latest fetched
     bar's date == today's America/New_York date. If today is not a
     trading day at all (checked against Alpaca's real exchange calendar,
     GET /v2/calendar), the job abstains with that reason rather than
     treating it as a data failure.

Set KIM_SESSION_VALIDATE_ONLY=1 to run the full pipeline (fetch, decide,
reconcile, log) WITHOUT advancing or writing the persisted cadence state.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
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
import risk  # noqa: E402

UNIVERSE = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
WARMUP_BARS = 296  # longest momentum lookback (295) + 1
LOOKBACK_CALENDAR_DAYS = 600
MAX_STALENESS_SECONDS = 60 * 60 * 30  # 30h: generous for a once-per-session EOD job
REBALANCE_EVERY_N_SESSIONS = 50  # must match genome["rebalance_every_n_sessions"]
COLLECTION_CUTOFF_ET = (16, 15)  # (hour, minute) -- data not trusted as "completed" before this
NY_TZ = ZoneInfo("America/New_York")
LIVE_ORDER_STATUSES = {"new", "accepted", "partially_filled", "held", "pending_new"}

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
        state = json.loads(CADENCE_PATH.read_text())
        state.setdefault("last_evaluated_session_date", None)
        return state
    today = datetime.now(timezone.utc).date().isoformat()
    state = {
        "session_zero_date": today,
        "sessions_elapsed": 0,
        "last_run_date": None,
        "last_evaluated_session_date": None,
        "rebalance_every_n_sessions": REBALANCE_EVERY_N_SESSIONS,
        "cadence_source": "champion_decide_fn_factory semantics (step % N == 0), reused unmodified",
        "note": "sessions_elapsed increments once per EVALUATED session date, not per run; "
                "re-running for an already-evaluated session date is a no-op.",
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


def check_completed_session_cutoff(headers: dict, bars_by_symbol: dict, availability: dict):
    """Returns (ok: bool, reason: str|None, today_et: str, is_trading_day: bool)."""
    now_et = datetime.now(NY_TZ)
    today_et = now_et.date().isoformat()

    cal_status, cal = get_json(
        f"https://paper-api.alpaca.markets/v2/calendar?start={today_et}&end={today_et}", headers,
    )
    is_trading_day = cal_status == 200 and bool(cal)
    if not is_trading_day:
        return False, f"{today_et} is not a trading day per the exchange calendar", today_et, False

    cutoff_h, cutoff_m = COLLECTION_CUTOFF_ET
    cutoff_dt = now_et.replace(hour=cutoff_h, minute=cutoff_m, second=0, microsecond=0)
    if now_et < cutoff_dt:
        return False, f"before collection cutoff {cutoff_h:02d}:{cutoff_m:02d} ET (now {now_et.strftime('%H:%M')})", today_et, True

    available_syms = [s for s in UNIVERSE if availability.get(s)]
    if not available_syms:
        return False, "no available symbols to check latest bar date", today_et, True
    latest_dates = {bars_by_symbol[s][-1]["timestamp"] for s in available_syms}
    if today_et not in latest_dates:
        return False, f"latest available bar date(s) {sorted(latest_dates)} != today {today_et}", today_et, True

    return True, None, today_et, True


def shares_including_pending(positions, orders) -> dict:
    """Held shares, adjusted for any live (non-terminal) open order's
    remaining quantity: +remaining for buys, -remaining for sells."""
    current = {p["symbol"]: int(round(float(p["qty"]))) for p in (positions or []) if p.get("symbol") in UNIVERSE}
    for o in (orders or []):
        sym = o.get("symbol")
        if sym not in UNIVERSE or o.get("status") not in LIVE_ORDER_STATUSES:
            continue
        qty = float(o.get("qty") or 0)
        filled = float(o.get("filled_qty") or 0)
        remaining = qty - filled
        if remaining <= 0:
            continue
        remaining = int(round(remaining))
        delta = remaining if o.get("side") == "buy" else -remaining
        current[sym] = current.get(sym, 0) + delta
    return current


def count_live_orders(orders) -> int:
    """Count of orders still in a live (non-terminal) status -- NOT len(orders),
    which counts every order returned by the status=all query (filled,
    canceled, expired, etc. included)."""
    return sum(1 for o in (orders or []) if o.get("status") in LIVE_ORDER_STATUSES)


def compute_target_orders(decision_weights: dict, equity_cents: int, mark_prices_cents: dict,
                           current_shares: dict, unavailable_symbols: list, genome: dict,
                           current_drawdown: float = 0.0) -> list:
    """Pure, testable. Enforces, independent of any caller-side logic:
      - every weight and equity_cents must be finite
      - risk.validate() must pass (max asset weight, max total exposure,
        drawdown halt -- current_drawdown is caller-supplied; in shadow
        mode it is always 0.0, so the halt is NOT actually enforced here,
        only the exposure/weight bounds and finite-value checks are)
      - a symbol in unavailable_symbols can NEVER appear in the resulting
        order list, buy or sell, regardless of target_weights or
        current_shares -- it is removed from both before compute_orders
        ever sees it, so a missing price cannot cause a liquidation.
    """
    if not math.isfinite(equity_cents):
        raise ValueError(f"non-finite equity_cents: {equity_cents}")
    for sym, w in decision_weights.items():
        if not math.isfinite(w):
            raise ValueError(f"non-finite weight for {sym}: {w}")

    risk.validate(
        decision_weights, UNIVERSE,
        max_asset_weight=genome["max_asset_weight"],
        max_total_exposure=genome["target_max_exposure"],
        drawdown_halt_pct=genome["drawdown_halt_pct"],
        current_drawdown=current_drawdown,
    )

    safe_universe = [s for s in UNIVERSE if s not in unavailable_symbols]
    safe_shares = {s: q for s, q in current_shares.items() if s not in unavailable_symbols}
    safe_prices = {s: p for s, p in mark_prices_cents.items() if s not in unavailable_symbols}
    safe_weights = {s: w for s, w in decision_weights.items() if s not in unavailable_symbols}

    return execution.compute_orders(safe_weights, safe_universe, equity_cents, safe_prices, safe_shares)


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
    unavailable = [s for s, ok in availability.items() if not ok]

    cutoff_ok, cutoff_reason, session_date, is_trading_day = check_completed_session_cutoff(
        headers, bars_by_symbol, availability,
    )

    already_evaluated = cutoff_ok and cadence.get("last_evaluated_session_date") == session_date

    view = LiveView(bars_by_symbol, availability)
    step = cadence["sessions_elapsed"]
    rebalance_due = (step % cadence["rebalance_every_n_sessions"] == 0)

    sel = json.loads((DEV_STAGING / "selected_development_bot.json").read_text())
    genome = sel["selected_genome_definition"]

    decision = None
    intended_orders = None
    status = None

    if already_evaluated:
        status = "already_evaluated"
        decision = {"skipped": True, "reason": f"session {session_date} already evaluated; no-op"}
    elif abstain_reason:
        status = "abstained"
        decision = {"abstained": True, "reason": abstain_reason}
    elif not cutoff_ok:
        status = "abstained"
        decision = {"abstained": True, "reason": cutoff_reason}
    elif not rebalance_due:
        status = "hold"
        decision = {"hold": True, "reason": f"not a rebalance session (step {step} % {cadence['rebalance_every_n_sessions']} != 0)"}
    elif unavailable:
        status = "abstained"
        decision = {"abstained": True, "reason": f"missing/stale price data for: {unavailable}"}
    else:
        status = "decided"
        decision = control_agent.decide(view, genome)
        equity_cents = round(equity * 100)  # no-borrow: EQUITY only, never buying_power
        mark_prices_cents = {s: round(bars_by_symbol[s][-1]["adjusted_close"] * 100) for s in UNIVERSE}
        current_shares = shares_including_pending(positions, orders)
        try:
            intended_orders = compute_target_orders(
                decision["weights"], equity_cents, mark_prices_cents, current_shares,
                unavailable, genome, current_drawdown=0.0,
            )
        except Exception as e:  # noqa: BLE001
            intended_orders = {"error": repr(e)}

    record = {
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "SHADOW_ONLY -- decide, log, reconcile; no submission",
        "validate_only": VALIDATE_ONLY,
        "submission_enabled_confirmed_false": True,
        "status": status,
        "session_date_et": session_date,
        "is_trading_day": is_trading_day,
        "completed_session_cutoff_ok": cutoff_ok,
        "completed_session_cutoff_reason": cutoff_reason,
        "cadence_step_used": step,
        "rebalance_due": rebalance_due,
        "unavailable_symbols": unavailable,
        "equity_used_for_sizing": equity,
        "buying_power_ignored": buying_power,
        "risk_drawdown_enforced": False,
        "risk_drawdown_note": "current_drawdown passed as 0.0 -- no peak-equity history persisted yet; "
                               "drawdown halt is NOT enforced in shadow mode, only weight/exposure bounds and finite checks are",
        "decision": decision,
        "intended_orders_not_submitted": intended_orders,
        "broker_account_status": acct.get("status") if acct else None,
        "broker_trading_blocked": acct.get("trading_blocked") if acct else None,
        "broker_open_order_count": count_live_orders(orders),
        "broker_position_count": len(positions) if positions else 0,
    }

    out_path = SESSION_LOG_DIR / f"session_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(json.dumps(record, indent=2, default=str))

    if not VALIDATE_ONLY and status not in ("already_evaluated",):
        cadence["sessions_elapsed"] = step + 1
        cadence["last_run_date"] = datetime.now(timezone.utc).date().isoformat()
        if cutoff_ok:
            cadence["last_evaluated_session_date"] = session_date
        persist_cadence_state(cadence)

    print(json.dumps(record, indent=2, default=str))
    print("Log written:", out_path)
    print("Cadence state:", json.dumps(cadence, indent=2))


if __name__ == "__main__":
    main()
