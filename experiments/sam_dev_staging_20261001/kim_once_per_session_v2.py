"""Kim once-per-session decision job.

SHADOW by default and in production today: the submission gate
(alpaca_adapter.submission_enabled(), hardcoded False) is the ONLY thing that
lets this runner send anything. With the gate closed it decides, logs and
reconciles, writes NOTHING to the order ledger, and calls no POST.

2026-10-04 integration repair (review of fe72d72). The old main() computed
intended orders and stopped; the ledger, hard limits and paper adapter were
never reached. run_session(deps) now wires them, with every dependency
injected so the whole runner is testable against a fake broker and fake data:

  * cadence step is DERIVED from the exchange calendar (session_calendar),
    not from a run counter; holidays, missed days, failed runs and catch-up
    runs cannot shift the schedule; a calendar API failure abstains and is
    never mistaken for a holiday;
  * a rebalance missed to an abstention stays PENDING (derived from
    last_rebalanced_step) instead of being skipped for N sessions;
  * peak equity / drawdown are recorded on EVERY valid post-cutoff trading
    session, rebalance or not, with the sign risk.validate() expects;
  * the drawdown halt is a persisted latch (halt_state.json): on trigger the
    runner liquidates to cash (matching the backtest's halted behaviour) and
    stays flat until a human removes the latch;
  * broker state (account, positions, orders) that cannot be read abstains;
    it is never treated as "no positions";
  * orders: cash-constrained (no borrow), checked against post-trade limits,
    sells before buys, one deterministic client_order_id per (session,
    symbol), submitted through submit_orders_with_limits (kill switch +
    ledger-derived per-session cap) to AlpacaPaperBroker, time_in_force day.

Set KIM_SESSION_VALIDATE_ONLY=1 for a run that writes no state (log only).
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]  # <repo>/experiments/sam_dev_staging_20261001/<this file>
DEV_STAGING = Path(__file__).resolve().parent
sys.path.insert(0, str(DEV_STAGING / "alpaca_adapter"))
sys.path.insert(0, str(DEV_STAGING / "kim_order_simulation_tests"))
sys.path.insert(0, str(DEV_STAGING))
sys.path.insert(0, str(REPO / "scripts"))

from alpaca_adapter import PAPER_TRADING_HOST, assert_trading_host_allowed  # noqa: E402
from kim_order_logic import PersistentIntentLedger, submit_orders_with_limits  # noqa: E402
import control_agent  # noqa: E402
import execution  # noqa: E402
import risk  # noqa: E402
import peak_equity  # noqa: E402
import broker_reconciliation  # noqa: E402
import session_calendar  # noqa: E402
import session_guards  # noqa: E402
from atomic_io import write_json_atomic, read_json_strict, StateCorrupt  # noqa: E402

UNIVERSE = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
WARMUP_BARS = 296  # longest momentum lookback (295) + 1
LOOKBACK_CALENDAR_DAYS = 600
MAX_STALENESS_SECONDS = 60 * 60 * 30  # 30h: generous for a once-per-session EOD job
COLLECTION_CUTOFF_ET = (16, 15)  # (hour, minute) -- data not trusted as "completed" before this
NY_TZ = ZoneInfo("America/New_York")
LIVE_ORDER_STATUSES = {"new", "accepted", "partially_filled", "held", "pending_new"}
ESCALATE_AFTER_ABSTAINED_SESSIONS = 3

STATE_DIR = DEV_STAGING / "kim_shadow_pilot"
PILOT_CONFIG_PATH = DEV_STAGING / "pilot_config.json"
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
        drawdown halt -- current_drawdown is caller-supplied; main() now
        passes a real value computed from the persisted peak-equity
        series, so the halt is genuinely enforced, not just logged)
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




def fetch_bars(get_json: Callable, headers: dict, now_utc: datetime) -> tuple[dict, dict]:
    end = now_utc.date()
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
        except Exception:  # noqa: BLE001
            availability[symbol] = False
            bars_by_symbol[symbol] = []
            continue
        if status != 200 or not isinstance(body, dict):
            availability[symbol] = False
            bars_by_symbol[symbol] = []
            continue
        raw_bars = body.get("bars") or []
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

        if len(rows) < WARMUP_BARS or not raw_bars:
            availability[symbol] = False  # missing-price abstention: insufficient warmup
            continue
        latest_ts = datetime.fromisoformat(raw_bars[-1]["t"].replace("Z", "+00:00"))
        if (now_utc - latest_ts).total_seconds() > MAX_STALENESS_SECONDS:
            availability[symbol] = False  # missing-price abstention: too stale
            continue
        availability[symbol] = True
    return bars_by_symbol, availability


@dataclass
class Deps:
    get_json: Callable
    headers: dict
    broker: object                      # .submit(spec) / .find_by_client_order_id(cid)
    now_utc: Callable[[], datetime]
    submission_gate: Callable[[], bool]
    state_dir: Path
    genome: dict
    genome_id: str
    config: dict
    validate_only: bool = False
    fetch_bars_fn: Callable | None = None
    trading_host: str = PAPER_TRADING_HOST


def _finite(x) -> bool:
    return x is not None and math.isfinite(float(x))


def load_cadence_state(d: Deps) -> dict:
    path = d.state_dir / "cadence_state.json"
    if path.exists():
        st = read_json_strict(path)
    else:
        st = {"session_zero_date": d.config["session_zero_date"]}
    st.setdefault("last_evaluated_session_date", None)
    st.setdefault("last_rebalanced_step", None)
    st.setdefault("abstained_sessions", [])
    st.setdefault("sessions_elapsed", 0)
    st["rebalance_every_n_sessions"] = d.config["rebalance_every_n_sessions"]
    st["cadence_source"] = ("exchange-calendar session index since session_zero_date; rebalance when "
                            "floor(step/N)*N > last_rebalanced_step (a missed rebalance stays pending)")
    return st


def run_session(d: Deps) -> dict:
    cfg = d.config
    now_utc = d.now_utc()
    now_et = now_utc.astimezone(NY_TZ)
    today_et = now_et.date().isoformat()
    persist = not d.validate_only
    d.state_dir.mkdir(parents=True, exist_ok=True)
    log_dir = d.state_dir / "once_per_session_log"
    log_dir.mkdir(parents=True, exist_ok=True)
    gate_open = bool(d.submission_gate())
    assert_trading_host_allowed(d.trading_host)

    rec: dict = {
        "run_timestamp_utc": now_utc.isoformat(),
        "mode": "SUBMISSION_ENABLED" if gate_open else "SHADOW_ONLY -- decide, log, reconcile; no submission, no ledger writes",
        "validate_only": d.validate_only,
        "submission_gate_open": gate_open,
        "genome_id": d.genome_id,
        "session_date_et": today_et,
        "status": None, "decision": None,
        "intended_orders_not_submitted": None, "submission": None,
    }

    def finish(status: str, **extra) -> dict:
        rec["status"] = status
        rec.update(extra)
        out_path = log_dir / f"session_{now_utc.strftime('%Y%m%dT%H%M%SZ')}.json"
        out_path.write_text(json.dumps(rec, indent=2, default=str))
        rec["log_path"] = str(out_path)
        return rec

    if today_et >= cfg["expiry_date"]:
        return finish("expired", decision={"skipped": True, "reason": f"pilot expired {cfg['expiry_date']}"})

    cadence = load_cadence_state(d)
    if cadence["session_zero_date"] != cfg["session_zero_date"]:
        return finish("abstained_config_mismatch", decision={
            "abstained": True, "reason": f"cadence_state session_zero {cadence['session_zero_date']} != "
                                         f"pilot_config {cfg['session_zero_date']}; reconcile deliberately"})

    # --- broker state: unreadable state is NEVER treated as 'no positions' ---
    host = d.trading_host
    acct_status, acct = d.get_json(f"{host}/v2/account", d.headers)
    orders_status, orders = d.get_json(f"{host}/v2/orders?status=all&limit=50", d.headers)
    pos_status, positions = d.get_json(f"{host}/v2/positions", d.headers)
    if (acct_status != 200 or not isinstance(acct, dict) or orders_status != 200 or not isinstance(orders, list)
            or pos_status != 200 or not isinstance(positions, list)):
        return finish("abstained_broker_state_unavailable", decision={
            "abstained": True,
            "reason": f"account={acct_status} orders={orders_status} positions={pos_status}"})
    if acct.get("trading_blocked") or acct.get("account_blocked") or acct.get("status") != "ACTIVE":
        return finish("abstained_account_not_tradable", decision={
            "abstained": True, "reason": f"status={acct.get('status')} blocked={acct.get('trading_blocked')}"})
    equity = float(acct["equity"]) if _finite(acct.get("equity")) else None
    cash = float(acct["cash"]) if _finite(acct.get("cash")) else None
    rec.update(equity_used_for_sizing=equity, cash=cash, buying_power_ignored=acct.get("buying_power"),
               broker_account_status=acct.get("status"), broker_trading_blocked=acct.get("trading_blocked"),
               broker_open_order_count=count_live_orders(orders), broker_position_count=len(positions))
    if equity is None or equity <= 0:
        return finish("abstained", decision={"abstained": True, "reason": "account equity missing or non-finite"})

    # --- ledger reconciliation (rows only exist once submission has run) ---
    ledger = None
    lookup_errors: list = []
    ledger_updates: list = []
    if persist:  # validate-only must not create the ledger file, migrate it, or update rows
        ledger = PersistentIntentLedger(str(d.state_dir / "order_ledger.sqlite3"))
        ledger_updates = broker_reconciliation.reconcile_ledger(ledger, d.broker, lookup_errors)
    unfilled = [u for u in ledger_updates if u["status"] in ("canceled", "expired", "rejected")]
    rec.update(order_ledger_reconciliation_updated_count=len(ledger_updates),
               order_ledger_lookup_errors=lookup_errors,
               unfilled_orders_since_last_run=unfilled,
               escalate=bool(unfilled or lookup_errors))

    # --- exchange calendar: today must be a real session ---
    try:
        sessions = session_calendar.fetch_sessions(
            d.get_json, host, d.headers, cadence["session_zero_date"], today_et)
    except session_calendar.CalendarUnavailable as e:
        return finish("abstained_calendar_unavailable", is_trading_day=None,
                      decision={"abstained": True, "reason": str(e)})
    step = session_calendar.session_step(sessions, cadence["session_zero_date"], today_et)
    if step is None:
        return finish("non_trading_day", is_trading_day=False,
                      decision={"skipped": True, "reason": f"{today_et} is not a session on/after session zero"})
    rec.update(is_trading_day=True, cadence_step_used=step)

    if now_et < now_et.replace(hour=COLLECTION_CUTOFF_ET[0], minute=COLLECTION_CUTOFF_ET[1], second=0, microsecond=0):
        return finish("too_early", completed_session_cutoff_ok=False, decision={
            "skipped": True, "reason": f"before collection cutoff (now {now_et.strftime('%H:%M')} ET); no state touched"})

    if cadence["last_evaluated_session_date"] == today_et:
        return finish("already_evaluated", decision={
            "skipped": True, "reason": f"session {today_et} already evaluated; no-op"})

    def abstain(status: str, reason: str) -> dict:
        if persist and today_et not in cadence["abstained_sessions"]:
            cadence["abstained_sessions"] = (cadence["abstained_sessions"] + [today_et])[-30:]
            write_json_atomic(d.state_dir / "cadence_state.json", cadence)
        n = len(cadence["abstained_sessions"] if persist else cadence["abstained_sessions"] + [today_et])
        # A latched halt that cannot liquidate because prices are unavailable is never silent.
        stuck_halt = bool(rec.get("halt_latched")) and bool(positions)
        return finish(status, consecutive_abstained_sessions=n,
                      halt_latched_but_unliquidated=stuck_halt,
                      escalate=n >= ESCALATE_AFTER_ABSTAINED_SESSIONS or stuck_halt,
                      decision={"abstained": True, "reason": reason})

    # --- drawdown: recorded on EVERY valid session, rebalance or not ---
    peak_path = d.state_dir / "peak_equity_state.json"
    try:
        prior_sessions = bool(cadence["last_evaluated_session_date"] or cadence["abstained_sessions"])
        pstate = peak_equity.load_state(peak_path, require_existing=prior_sessions)
        new_pstate, peak_value, dd = peak_equity.evaluate(pstate, today_et, equity)
    except (peak_equity.PeakStateMissing, StateCorrupt) as e:
        return finish("abstained_peak_state_unusable", critical=True,
                      decision={"abstained": True, "reason": f"{e}"})
    halt_path = d.state_dir / "halt_state.json"
    try:
        halt = read_json_strict(halt_path) if halt_path.exists() else {"halted": False}
    except StateCorrupt as e:
        return finish("abstained_halt_state_unusable", critical=True, decision={"abstained": True, "reason": f"{e}"})
    if persist:
        peak_equity.save_state(peak_path, new_pstate)
    halt_pct = cfg["drawdown_halt_pct"]
    if not halt.get("halted") and dd <= -halt_pct - 1e-9:
        halt = {"halted": True, "halted_session_date": today_et, "halted_drawdown": dd,
                "note": "latched; remove this file only on a deliberate human decision"}
        if persist:
            write_json_atomic(halt_path, halt)
    rec.update(peak_equity=peak_value, current_drawdown=dd, drawdown_halt_pct=halt_pct,
               halt_latched=bool(halt.get("halted")), risk_drawdown_enforced=True)

    fetch = d.fetch_bars_fn or (lambda: fetch_bars(d.get_json, d.headers, now_utc))
    bars_by_symbol, availability = fetch()
    unavailable = [s for s in UNIVERSE if not availability.get(s)]
    available_syms = [s for s in UNIVERSE if availability.get(s)]
    rec["unavailable_symbols"] = unavailable

    if not available_syms or today_et not in {bars_by_symbol[s][-1]["timestamp"] for s in available_syms}:
        return abstain("abstained_data_not_ready", "no available symbol has a bar dated today")
    rec["completed_session_cutoff_ok"] = True

    # --- decision ---
    eff_genome = {**d.genome, "max_asset_weight": cfg["max_asset_weight"],
                  "target_max_exposure": cfg["gross_exposure_ceiling"], "drawdown_halt_pct": halt_pct}
    equity_cents = round(equity * 100)  # EQUITY only, never buying_power
    mark_prices_cents = {s: round(bars_by_symbol[s][-1]["adjusted_close"] * 100) for s in available_syms}
    current_shares = shares_including_pending(positions, orders)
    last_reb = cadence["last_rebalanced_step"] if cadence["last_rebalanced_step"] is not None else -1
    due_step = session_calendar.latest_due_rebalance_step(step, cfg["rebalance_every_n_sessions"])
    rebalance_due = due_step > last_reb
    rec.update(rebalance_due=rebalance_due, rebalance_due_step=due_step)

    weights = None
    rebalance_consumed = False
    if halt.get("halted"):
        weights = {}
        rec["decision"] = {"halted": True, "reason": "drawdown halt latched; target is cash"}
        status = "halted"
        # Positions in symbols with no usable price are NEVER sold blind (the no-sell-on-missing-price
        # invariant holds even under a halt); they are reported and escalated for a human instead.
        stuck = sorted(s for s in unavailable if current_shares.get(s, 0) > 0)
        if stuck:
            rec["halt_unliquidated_symbols"] = stuck
            rec["escalate"] = True
    elif not rebalance_due:
        rec["decision"] = {"hold": True, "reason": f"no rebalance due (step {step}, last rebalanced step {last_reb})"}
        status = "hold"
    elif unavailable:
        return abstain("abstained_rebalance_pending", f"missing/stale price data for: {unavailable}; "
                                                      f"rebalance step {due_step} stays pending")
    else:
        decision = control_agent.decide(LiveView(bars_by_symbol, availability), d.genome)
        weights = decision["weights"]
        rec["decision"] = decision
        status = "decided"
        rebalance_consumed = True

    intended: list | dict | None = None
    specs: list = []
    guard_notes: list = []
    if weights is not None:
        try:
            if cash is None:
                raise session_guards.OrderGuardError("account cash missing; refusing to size buys")
            raw = compute_target_orders(weights, equity_cents, mark_prices_cents, current_shares,
                                        unavailable, eff_genome, current_drawdown=dd)
            intended, guard_notes = session_guards.constrain_buys_to_cash(
                raw, mark_prices_cents, round(cash * 100), equity_cents, cfg["min_cash_reserve_pct"])
            session_guards.check_post_trade_limits(
                intended, {s: q for s, q in current_shares.items() if s in mark_prices_cents},
                mark_prices_cents, equity_cents, cfg["max_asset_weight"], cfg["gross_exposure_ceiling"])
            specs = session_guards.build_specs(intended, today_et, cfg["time_in_force"])
        except (risk.RiskRefusal, session_guards.OrderGuardError, ValueError) as e:
            intended, specs = {"error": repr(e)}, []
            status = "order_refused"
            rebalance_consumed = False  # a refused/guard-failed rebalance stays pending for the next session
    rec.update(intended_orders_not_submitted=intended, order_guard_notes=guard_notes)

    # --- submission (only with the gate open, never in validate-only) ---
    blocked = False
    live_decision = gate_open and persist
    if specs and live_decision:
        if broker_reconciliation.is_first_submission_session(ledger):
            rec["first_submission_session_seeded_count"] = broker_reconciliation.seed_ledger_from_broker_state(
                ledger, positions, orders, today_et)
        result = submit_orders_with_limits(
            d.broker, ledger, specs, d.state_dir / cfg["kill_switch_file"], today_et,
            cfg["max_orders_per_session"])
        rec["submission"] = result
        blocked = result["blocked"] > 0 or any(
            r["outcome"] == "uncertain_unresolved" for r in result["results"])
        if any(r["outcome"] == "rejected" for r in result["results"]):
            # a definitive broker rejection (e.g. insufficient buying power): do NOT
            # silently spend the rebalance on an account that is still flat.
            rebalance_consumed = False
            rec["escalate"] = True
            rec["submission_rejected"] = True
    elif specs:
        rec["submission"] = {"skipped": True, "reason": "submission gate closed or validate-only; ledger untouched"}

    # --- persist cadence last; a blocked/uncertain submission leaves the session retryable ---
    if persist and not blocked:
        cadence["last_evaluated_session_date"] = today_et
        cadence["sessions_elapsed"] = step + 1
        cadence["abstained_sessions"] = []
        if rebalance_consumed and live_decision:
            # Only a LIVE decision spends the rebalance. A shadow session logs its
            # decision but leaves the step pending, so pre-activation shadow runs can
            # never use up the entry the pilot window depends on.
            cadence["last_rebalanced_step"] = due_step
        write_json_atomic(d.state_dir / "cadence_state.json", cadence)
    rec["cadence_state"] = cadence
    return finish(status)


def main():
    from runtime_credential_loader import load_credential_headers, get_json
    from alpaca_adapter import submission_enabled
    from alpaca_paper_broker import AlpacaPaperBroker
    from pilot_config import load_pilot_config
    from lib.ids import genome_id

    sel = json.loads((DEV_STAGING / "selected_development_bot.json").read_text())
    genome = sel["selected_genome_definition"]
    gid = genome_id(genome)
    cfg = load_pilot_config(PILOT_CONFIG_PATH, genome, gid)
    headers = load_credential_headers()
    deps = Deps(
        get_json=get_json, headers=headers, broker=AlpacaPaperBroker(headers),
        now_utc=lambda: datetime.now(timezone.utc), submission_gate=submission_enabled,
        state_dir=STATE_DIR, genome=genome, genome_id=gid, config=cfg, validate_only=VALIDATE_ONLY,
    )
    rec = run_session(deps)
    print(json.dumps(rec, indent=2, default=str))


if __name__ == "__main__":
    main()
