"""Kim four-session PAPER pilot: opening-window runner (GO TBOTS_KIM_REPAIR_AND_FOUR_SESSION_PAPER_PILOT_20261006).

Modes (one process per systemd firing):
  freeze-campaign FIRST_DATE  write-once: four eligible sessions from Alpaca's calendar (+ ET/UTC conversions)
  init                        write-once: ledger, baseline seeding from the broker, cadence anchor, pilot-start equity peak
  preflight (09:15 ET)        validate-only: identity, account pin, clock, calendar, state, data, decision; NO submission
  open (09:30-09:35 ET)       decide from the latest fully COMPLETED session, then submit whole-share DAY orders
  monitor (every 5 min)       reconcile orders, track equity vs the persisted peak, latch the 8% halt, liquidate pilot-owned shares
  expiry (after 4th close)    block new strategy orders, cancel pilot orders, reconcile, write the HOLD receipt

Safety properties (each pinned by test_kim_paper_pilot_20261006.py): fail-closed Kim identity; paper host + pinned account id
checked independent of the submission switch; single-writer lock; stable client order ids with broker lookup before any uncertain
retry; the baseline (non-pilot) holding is never sold; a missing price abstains, never sells; no automatic reset after a hard stop.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for _p in (str(HERE / "alpaca_adapter"), str(HERE / "kim_order_simulation_tests"), str(HERE), str(REPO / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import broker_reconciliation  # noqa: E402
import control_agent  # noqa: E402
import peak_equity  # noqa: E402
import pilot_data  # noqa: E402
import pilot_identity  # noqa: E402
import session_calendar  # noqa: E402
import session_guards  # noqa: E402
from alpaca_adapter import PAPER_TRADING_HOST, assert_trading_host_allowed  # noqa: E402
from atomic_io import StateCorrupt, read_json_strict, write_json_atomic  # noqa: E402
from kim_once_per_session_v2 import LiveView, compute_target_orders, shares_including_pending  # noqa: E402
from kim_order_logic import (BrokerLookupError, IntendedOrderSpec, PersistentIntentLedger,  # noqa: E402
                             submit_orders_with_limits, submit_with_reconciliation)

NY = ZoneInfo("America/New_York")
EXIT_OK, EXIT_HARD_STOP, EXIT_BUSY = 0, 3, 75
PILOT_ID_RE = re.compile(r"^kim-\d{4}-\d{2}-\d{2}-[A-Z]{1,5}(-liq)?$")
LIVE_STATUSES = {"new", "accepted", "partially_filled", "held", "pending_new", "pending_cancel", "pending_replace", "accepted_for_bidding"}
TERMINAL = {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced"}
MODES = ("freeze-campaign", "init", "preflight", "open", "monitor", "expiry")


class HardStop(RuntimeError):
    """Identity / account / state / clock / safety failure: durably blocks new exposure (STOP sentinel) and exits 3 so the
    unit's OnFailure disables the opening timer. Never auto-cleared."""


class Abstain(RuntimeError):
    """Nothing unsafe happened, but no exposure may be created this run (missed or pending opportunity)."""


@dataclass
class Env:
    cfg: dict
    cfg_sha: str
    get_json: Callable
    headers: dict
    broker: object
    now_utc: Callable[[], datetime]
    submission_gate: Callable[[], bool]
    state_dir: Path
    pin: dict
    sleep: Callable[[float], None] = time.sleep
    trading_host: str = PAPER_TRADING_HOST
    dev_dir: Path = HERE
    repo_dir: Path = REPO
    genome: dict | None = None
    identity: dict | None = None
    _ledgers: list = field(default_factory=list)


# --------------------------------------------------------------------------- small helpers
def _p(env: Env, name: str) -> Path:
    return env.state_dir / name


def _parse_ts(s: str) -> datetime:
    """ISO timestamps from Alpaca may carry 9 fractional digits; keep 6."""
    m = re.match(r"^(.*?\.\d{6})\d*(.*)$", s)
    return datetime.fromisoformat(m.group(1) + m.group(2) if m else s)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _et(dt: datetime) -> datetime:
    return dt.astimezone(NY)


def _hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def _finite(x) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def load_config(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    cfg = json.loads(raw)
    need = {"schema", "submission_scope", "paper_host", "strategy", "genome_id", "universe", "rebalance_every_n_sessions", "feed",
            "lookback_calendar_days", "warmup_bars", "max_orders_per_session", "max_liquidation_orders_per_session",
            "drawdown_halt_pct", "max_asset_weight", "gross_exposure_ceiling", "min_cash_reserve_pct", "time_in_force",
            "extended_hours", "kill_switch_file", "campaign_sessions", "preflight_et", "open_window_et",
            "hold_deadline_minutes_after_close", "account_pin_file", "state_dir", "frozen_champion_file",
            "frozen_champion_file_sha256", "decision_function_sha256", "forbidden_genome_ids"}
    miss = need - set(cfg)
    if miss:
        raise HardStop(f"sealed config missing keys {sorted(miss)}")
    if cfg["schema"] != "kim-paper-pilot-v1" or cfg["paper_host"] != PAPER_TRADING_HOST:
        raise HardStop("sealed config schema/host mismatch")
    if cfg["time_in_force"] != "day" or cfg["extended_hours"] is not False:
        raise HardStop("sealed config must use DAY orders with extended hours disabled")
    if cfg["warmup_bars"] != 296 or cfg["rebalance_every_n_sessions"] != 50 or cfg["campaign_sessions"] != 4:
        raise HardStop("sealed config warm-up/cadence/campaign length differ from the authorized terms")
    if not (0 < cfg["max_orders_per_session"] <= 4 and cfg["max_liquidation_orders_per_session"] <= 8
            and cfg["drawdown_halt_pct"] <= 0.08 and cfg["max_asset_weight"] <= 0.18 and cfg["gross_exposure_ceiling"] <= 0.18):
        raise HardStop("sealed config loosens an authorized risk term")
    return cfg, hashlib.sha256(raw).hexdigest()


def load_pin(path: Path) -> dict:
    try:
        pin = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise HardStop(f"account pin file unreadable: {e!r}") from e
    if not (isinstance(pin.get("account_id_sha256"), str) and len(pin["account_id_sha256"]) == 64
            and isinstance(pin.get("expected_baseline_positions"), dict)
            and isinstance(pin.get("known_diagnostic_client_order_ids"), list)):
        raise HardStop("account pin file has an unexpected shape")
    return pin


def verify_identity(env: Env) -> None:
    try:
        env.genome, env.identity = pilot_identity.load_kim(env.dev_dir, env.repo_dir, env.cfg)
    except pilot_identity.IdentityError as e:
        raise HardStop(f"identity: {e}") from e


def acquire_lock(env: Env, wait_seconds: float = 0.0):
    env.state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(env.state_dir / "writer.lock", os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.2)


def write_receipt(env: Env, mode: str, rec: dict) -> Path:
    d = env.state_dir / "receipts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{env.now_utc().strftime('%Y%m%dT%H%M%S%fZ')}_{mode}.json"
    rec = {**rec, "mode": mode, "receipt_written_at_utc": _iso(env.now_utc()), "config_sha256": env.cfg_sha}
    write_json_atomic(p, rec)
    return p


def stop_present(env: Env) -> bool:
    return _p(env, "STOP").exists()


def hold_present(env: Env) -> bool:
    return _p(env, "HOLD").exists()


def write_stop(env: Env, reason: str) -> None:
    p = _p(env, "STOP")
    if not p.exists():
        write_json_atomic(p, {"stopped_at_utc": _iso(env.now_utc()), "reason": reason,
                              "note": "hard stop: new exposure blocked; remove only by a deliberate human decision"})


# --------------------------------------------------------------------------- broker / clock / calendar reads
def read_broker(env: Env) -> dict:
    assert_trading_host_allowed(env.trading_host)
    host = env.trading_host
    a_s, acct = env.get_json(f"{host}/v2/account", env.headers)
    p_s, pos = env.get_json(f"{host}/v2/positions", env.headers)
    o_s, orders = env.get_json(f"{host}/v2/orders?status=all&limit=200", env.headers)
    if a_s != 200 or not isinstance(acct, dict) or p_s != 200 or not isinstance(pos, list) or o_s != 200 or not isinstance(orders, list):
        raise Abstain(f"broker state unreadable (account={a_s} positions={p_s} orders={o_s}); never treated as flat")
    if hashlib.sha256(str(acct.get("id")).encode()).hexdigest() != env.pin["account_id_sha256"]:
        raise HardStop("account id does not match the pinned paper account")
    if acct.get("status") != "ACTIVE" or acct.get("trading_blocked") or acct.get("account_blocked"):
        raise HardStop(f"account not tradable (status={acct.get('status')} blocked={acct.get('trading_blocked')})")
    if not (_finite(acct.get("equity")) and float(acct["equity"]) > 0 and _finite(acct.get("cash"))):
        raise Abstain("account equity/cash missing or non-finite")
    return {"account": acct, "equity": float(acct["equity"]), "cash": float(acct["cash"]), "positions": pos, "orders": orders}


def read_clock(env: Env) -> dict:
    s, c = env.get_json(f"{env.trading_host}/v2/clock", env.headers)
    if s != 200 or not isinstance(c, dict) or "timestamp" not in c or "is_open" not in c:
        raise Abstain(f"clock unreadable (HTTP {s})")
    ts = _parse_ts(c["timestamp"])
    skew = abs((env.now_utc() - ts).total_seconds())
    if skew > 120:
        raise HardStop(f"broker clock disagrees with the host clock by {skew:.0f}s")
    return {"is_open": bool(c["is_open"]), "timestamp": ts, "next_open": _parse_ts(c["next_open"]), "next_close": _parse_ts(c["next_close"])}


def read_calendar(env: Env, start: str, end: str) -> list[dict]:
    s, rows = env.get_json(f"{env.trading_host}/v2/calendar?start={start}&end={end}", env.headers)
    if s != 200 or not isinstance(rows, list):
        raise Abstain(f"calendar unreadable (HTTP {s}); an unreadable calendar is not a holiday")
    out = []
    for r in rows:
        try:
            date.fromisoformat(r["date"])
            _hhmm(r["open"]), _hhmm(r["close"])
        except (KeyError, TypeError, ValueError) as e:
            raise Abstain(f"malformed calendar row {r!r}") from e
        out.append({"date": r["date"], "open": r["open"], "close": r["close"]})
    return sorted(out, key=lambda r: r["date"])


# --------------------------------------------------------------------------- campaign / state
def session_times(row: dict) -> dict:
    d = date.fromisoformat(row["date"])
    oh, om = _hhmm(row["open"])
    ch, cm = _hhmm(row["close"])
    o = datetime(d.year, d.month, d.day, oh, om, tzinfo=NY)
    c = datetime(d.year, d.month, d.day, ch, cm, tzinfo=NY)
    return {"date": row["date"], "open_et": row["open"], "close_et": row["close"], "open_utc": _iso(o), "close_utc": _iso(c),
            "utc_offset_at_open": o.strftime("%z"), "early_close": (ch, cm) != (16, 0)}


def load_campaign(env: Env) -> dict:
    p = _p(env, "campaign.json")
    if not p.exists():
        raise HardStop("campaign.json missing: the four sessions were never frozen")
    try:
        c = read_json_strict(p)
    except StateCorrupt as e:
        raise HardStop(f"campaign.json corrupt: {e}") from e
    if len(c.get("sessions", [])) != env.cfg["campaign_sessions"]:
        raise HardStop("campaign.json does not hold exactly the authorized number of sessions")
    return c


def freeze_campaign(env: Env, first_date: str) -> dict:
    p = _p(env, "campaign.json")
    if p.exists():
        return {"status": "already_frozen", "campaign": read_json_strict(p)}
    end = (date.fromisoformat(first_date) + timedelta(days=21)).isoformat()
    rows = [r for r in read_calendar(env, first_date, end) if r["date"] >= first_date]
    if len(rows) < env.cfg["campaign_sessions"] or rows[0]["date"] != first_date:
        raise HardStop(f"{first_date} is not an eligible session or the calendar holds too few sessions")
    sess = [session_times(r) for r in rows[:env.cfg["campaign_sessions"]]]
    last_close = _parse_ts(sess[-1]["close_utc"])
    campaign = {"frozen_at_utc": _iso(env.now_utc()), "calendar_source": f"{env.trading_host}/v2/calendar", "anchor_session": first_date,
                "sessions": sess, "last_session": sess[-1]["date"], "last_close_utc": sess[-1]["close_utc"],
                "hold_deadline_utc": _iso(last_close + timedelta(minutes=env.cfg["hold_deadline_minutes_after_close"])),
                "note": "missed opportunities remain missed; the campaign is never extended automatically"}
    env.state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)       # write-once
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(campaign, indent=2))
        f.flush()
        os.fsync(f.fileno())
    return {"status": "frozen", "campaign": campaign}


def open_ledger(env: Env) -> PersistentIntentLedger:
    led = PersistentIntentLedger(str(_p(env, "order_ledger.sqlite3")))
    env._ledgers.append(led)
    return led


def close_ledgers(env: Env) -> None:
    for led in env._ledgers:
        try:
            led.close()
        except Exception:  # noqa: BLE001
            pass
    env._ledgers.clear()


def pilot_rows(ledger: PersistentIntentLedger) -> list[dict]:
    cur = ledger.conn.execute("SELECT client_order_id, symbol, side, qty, status, filled_qty, session_date, "
                              "COALESCE(order_class,'normal') FROM order_intents")
    keys = ["client_order_id", "symbol", "side", "qty", "status", "filled_qty", "session_date", "order_class"]
    return [r for r in (dict(zip(keys, x)) for x in cur.fetchall()) if PILOT_ID_RE.match(r["client_order_id"] or "")]


def pilot_net_filled(ledger: PersistentIntentLedger) -> dict:
    net: dict = {}
    for r in pilot_rows(ledger):
        q = float(r["filled_qty"] or 0)
        net[r["symbol"]] = net.get(r["symbol"], 0.0) + (q if r["side"] == "buy" else -q)
    return {k: int(round(v)) for k, v in net.items() if abs(v) > 1e-9}


def init_state(env: Env) -> dict:
    """Once. Reconcile real broker holdings FIRST; unexpected holdings or unexplained orders block activation."""
    campaign = load_campaign(env)
    marker = _p(env, "pilot_baseline.json")
    if marker.exists():
        return {"status": "already_initialized", "baseline": read_json_strict(marker)}
    b = read_broker(env)
    held = {p["symbol"]: int(float(p["qty"])) for p in b["positions"] if float(p.get("qty") or 0) != 0}
    if held != {k: int(v) for k, v in env.pin["expected_baseline_positions"].items()}:
        raise HardStop(f"unexpected holdings at initialization: {held}")
    known = set(env.pin["known_diagnostic_client_order_ids"])
    unexplained = [o.get("client_order_id") for o in b["orders"] if o.get("client_order_id") not in known]
    live = [o.get("client_order_id") for o in b["orders"] if o.get("status") in LIVE_STATUSES]
    if unexplained or live:
        raise HardStop(f"unexplained or open orders at initialization: {len(unexplained)} unexplained, {len(live)} open")
    led = open_ledger(env)
    seeded = broker_reconciliation.seed_ledger_from_broker_state(led, b["positions"], b["orders"], "baseline")
    now = env.now_utc()
    write_json_atomic(_p(env, "cadence_state.json"), {
        "anchor_session": campaign["anchor_session"], "session_zero_date": campaign["anchor_session"],
        "rebalance_every_n_sessions": env.cfg["rebalance_every_n_sessions"], "last_rebalanced_step": None,
        "last_evaluated_session_date": None, "abstained_sessions": [],
        "cadence_source": "exchange-calendar session index since the frozen anchor; rebalance when floor(step/N)*N > last_rebalanced_step"})
    write_json_atomic(_p(env, "peak_equity_state.json"), {"start_equity": b["equity"], "peak_equity": b["equity"],
                                                          "start_at_utc": _iso(now), "peak_at_utc": _iso(now)})
    write_json_atomic(marker, {"positions": held, "diagnostic_order_ids": sorted(known), "ledger_rows_seeded": seeded,
                               "initialized_at_utc": _iso(now), "pilot_start_equity": b["equity"]})
    return {"status": "initialized", "baseline": held, "ledger_rows_seeded": seeded, "pilot_start_equity": b["equity"]}


def load_state(env: Env) -> tuple[dict, dict, dict]:
    try:
        baseline = read_json_strict(_p(env, "pilot_baseline.json"))
        cadence = read_json_strict(_p(env, "cadence_state.json"))
        peak = read_json_strict(_p(env, "peak_equity_state.json"))
    except (StateCorrupt, OSError) as e:
        raise HardStop(f"pilot state missing or corrupt: {e}") from e
    return baseline, cadence, peak


def update_peak(env: Env, peak: dict, equity: float) -> tuple[dict, float]:
    if equity > peak["peak_equity"]:
        peak = {**peak, "peak_equity": equity, "peak_at_utc": _iso(env.now_utc())}
        write_json_atomic(_p(env, "peak_equity_state.json"), peak)
    return peak, min(0.0, (equity - peak["peak_equity"]) / peak["peak_equity"])


def halt_state(env: Env) -> dict:
    p = _p(env, "halt_state.json")
    try:
        return read_json_strict(p) if p.exists() else {"halted": False}
    except StateCorrupt as e:
        raise HardStop(f"halt_state.json corrupt: {e}") from e


def evaluate_halt(env: Env, dd: float, equity: float, peak: dict) -> dict:
    h = halt_state(env)
    if not h.get("halted") and dd <= -env.cfg["drawdown_halt_pct"] - 1e-9:
        h = {"halted": True, "halted_at_utc": _iso(env.now_utc()), "drawdown": dd, "equity": equity,
             "peak_equity": peak["peak_equity"], "threshold": env.cfg["drawdown_halt_pct"],
             "note": "latched; a trigger, not a guaranteed maximum loss; removed only by a deliberate human decision"}
        write_json_atomic(_p(env, "halt_state.json"), h)
    return h


def reconcile_and_check(env: Env, ledger: PersistentIntentLedger, baseline: dict, b: dict) -> dict:
    """Refresh every unresolved ledger row from the broker, then require every broker order and holding to be explained."""
    errors: list = []
    broker_reconciliation.reconcile_ledger(ledger, env.broker, errors)
    if errors:
        raise Abstain(f"ledger reconciliation could not reach the broker for {len(errors)} order(s)")
    known = set(baseline["diagnostic_order_ids"])
    ledger_ids = {r[0] for r in ledger.conn.execute("SELECT client_order_id FROM order_intents").fetchall()}
    unexplained = [o.get("client_order_id") for o in b["orders"]
                   if o.get("client_order_id") not in known and o.get("client_order_id") not in ledger_ids]
    if unexplained:
        raise HardStop(f"{len(unexplained)} broker order(s) are not in the pilot ledger or the known diagnostics")
    net = pilot_net_filled(ledger)
    expect = {s: int(q) + net.get(s, 0) for s, q in baseline["positions"].items()}
    for s, q in net.items():
        expect.setdefault(s, q)
    held = {p["symbol"]: int(float(p["qty"])) for p in b["positions"] if float(p.get("qty") or 0) != 0}
    mismatch = {s: {"held": held.get(s, 0), "expected": expect.get(s, 0)} for s in set(held) | set(expect)
                if held.get(s, 0) != expect.get(s, 0)}
    return {"pilot_net_filled": net, "mismatch": mismatch, "held": held}


def explained_or_stop(env: Env, ledger, baseline, b) -> tuple[dict, dict]:
    r = reconcile_and_check(env, ledger, baseline, b)
    if r["mismatch"]:                       # one re-read: a fill can land between the order read and the position read
        env.sleep(2.0)
        b = read_broker(env)
        r = reconcile_and_check(env, ledger, baseline, b)
        if r["mismatch"]:
            raise HardStop(f"holdings are not explained by baseline + pilot ledger: {r['mismatch']}")
    return r, b


# --------------------------------------------------------------------------- decision
def completed_session_before(sessions: list[str], today: str) -> str:
    prior = [s for s in sessions if s < today]
    if not prior:
        raise Abstain("no completed session before today in the calendar window")
    return prior[-1]


def prepare_decision(env: Env, today: str) -> dict:
    cfg = env.cfg
    start = (date.fromisoformat(today) - timedelta(days=cfg["lookback_calendar_days"] + 10)).isoformat()
    rows = read_calendar(env, start, today)
    sessions = [r["date"] for r in rows]
    if today not in sessions:
        raise Abstain(f"{today} is not an exchange session")
    completed = completed_session_before(sessions, today)
    t0 = env.now_utc()
    try:
        data = pilot_data.fetch_validated(env.get_json, env.headers, cfg, sessions, completed, t0)
    except pilot_data.DataInvalid as e:
        raise Abstain(f"data boundary rejected the history: {e}") from e
    bars = {s: [{"timestamp": d, "adjusted_close": c} for d, c in zip(data["dates"], data["adjusted"][s])] for s in cfg["universe"]}
    decision = control_agent.decide(LiveView(bars, {s: True for s in cfg["universe"]}), env.genome)
    return {"completed_session": completed, "data": data, "decision": {"selected": decision["selected"], "weights": decision["weights"]},
            "computed_at_utc": _iso(env.now_utc()), "retrieved_at_utc": _iso(t0), "sessions": sessions}


def plan_orders(env: Env, prep: dict, b: dict, dd: float, ledger, baseline: dict) -> tuple[list[dict], list[str]]:
    cfg, notes = env.cfg, []
    equity_c = round(b["equity"] * 100)
    current = shares_including_pending(b["positions"], [o for o in b["orders"] if o.get("status") in LIVE_STATUSES])
    eff = {"max_asset_weight": cfg["max_asset_weight"], "target_max_exposure": cfg["gross_exposure_ceiling"],
           "drawdown_halt_pct": cfg["drawdown_halt_pct"]}
    marks = prep["data"]["sizing_cents"]
    raw = compute_target_orders(prep["decision"]["weights"], equity_c, marks, current, [], eff, current_drawdown=dd)
    # the baseline (non-pilot) holding is never sold: a sell may only dispose of verified pilot-owned shares
    net = pilot_net_filled(ledger)
    committed = {}
    for o in b["orders"]:
        if o.get("status") in LIVE_STATUSES and o.get("side") == "sell":
            committed[o["symbol"]] = committed.get(o["symbol"], 0) + int(float(o["qty"]) - float(o.get("filled_qty") or 0))
    orders = []
    for o in raw:
        if o["side"] == "sell":
            allowed = max(0, net.get(o["symbol"], 0) - committed.get(o["symbol"], 0))
            if allowed < o["shares"]:
                notes.append(f"sell {o['symbol']} {o['shares']} capped to pilot-owned {allowed}")
            if allowed <= 0:
                continue
            o = {**o, "shares": min(o["shares"], allowed)}
        orders.append(o)
    orders, cash_notes = session_guards.constrain_buys_to_cash(orders, marks, round(b["cash"] * 100), equity_c, cfg["min_cash_reserve_pct"])
    notes += cash_notes
    # the 1-share baseline holding is not pilot exposure: the 18% caps measure pilot-owned shares plus these orders only
    pilot_current = {s: max(0, q - baseline["positions"].get(s, 0)) for s, q in current.items()}
    session_guards.check_post_trade_limits(orders, pilot_current, marks, equity_c,
                                           cfg["max_asset_weight"], cfg["gross_exposure_ceiling"])
    if len(orders) > cfg["max_orders_per_session"]:
        raise session_guards.OrderGuardError(f"{len(orders)} orders exceed the normal cap {cfg['max_orders_per_session']}")
    return orders, notes


# --------------------------------------------------------------------------- modes
def _common_gate(env: Env, *, need_campaign_today: bool = True) -> tuple[dict, str]:
    verify_identity(env)
    assert_trading_host_allowed(env.trading_host)
    campaign = load_campaign(env)
    today = _et(env.now_utc()).date().isoformat()
    if need_campaign_today and today not in {s["date"] for s in campaign["sessions"]}:
        raise Abstain(f"{today} is not one of the four frozen campaign sessions")
    return campaign, today


def run_preflight(env: Env) -> dict:
    rec: dict = {"validate_only": True, "submission_attempted": False}
    campaign, today = _common_gate(env)
    rec["session_date"] = today
    if hold_present(env):
        raise Abstain("HOLD in force")
    if stop_present(env):
        raise Abstain("STOP in force; new exposure blocked")
    baseline, cadence, peak = load_state(env)
    clock = read_clock(env)
    rec["clock"] = {"is_open": clock["is_open"], "broker_timestamp": clock["timestamp"].isoformat()}
    b = read_broker(env)
    led = open_ledger(env)
    r, b = explained_or_stop(env, led, baseline, b)
    peak, dd = update_peak(env, peak, b["equity"])
    h = evaluate_halt(env, dd, b["equity"], peak)
    rec.update(account_pin_verified=True, positions=r["held"], pilot_net_filled=r["pilot_net_filled"], open_orders=len([o for o in b["orders"] if o.get("status") in LIVE_STATUSES]),
               drawdown=dd, halt_latched=bool(h.get("halted")), identity=env.identity["genome_ids"]["independent_sha256"])
    step = session_calendar.session_step(_campaign_calendar(env, campaign, today), campaign["anchor_session"], today)
    if step is None:
        raise Abstain(f"{today} is not a session on/after the anchor in the exchange calendar")
    due = session_calendar.latest_due_rebalance_step(step, env.cfg["rebalance_every_n_sessions"])
    last = cadence["last_rebalanced_step"] if cadence["last_rebalanced_step"] is not None else -1
    rec.update(step=step, rebalance_due=due > last)
    if due > last and not h.get("halted"):
        prep = prepare_decision(env, today)
        try:
            orders, notes = plan_orders(env, prep, b, dd, led, baseline)
        except Exception as e:  # noqa: BLE001
            rec.update(status="preflight_decision_refused", prepared_refusal=repr(e))
            return rec
        rec["prepared"] = {"completed_session": prep["completed_session"], "weights": prep["decision"]["weights"],
                           "intended_orders": orders, "notes": notes, "retrieved_at_utc": prep["retrieved_at_utc"],
                           "computed_at_utc": prep["computed_at_utc"], "data_provenance": prep["data"]["provenance"]}
        write_json_atomic(_p(env, f"prepared_{today}.json"), {"weights": prep["decision"]["weights"], "orders": orders,
                                                              "completed_session": prep["completed_session"]})
    rec["status"] = "preflight_ok"
    return rec


def _campaign_calendar(env: Env, campaign: dict, today: str) -> list[str]:
    rows = read_calendar(env, campaign["anchor_session"], today)
    return [r["date"] for r in rows]


def run_open(env: Env) -> dict:
    cfg = env.cfg
    rec: dict = {"validate_only": False}
    campaign, today = _common_gate(env)
    rec["session_date"] = today
    if hold_present(env):
        raise Abstain("HOLD in force")
    if stop_present(env):
        raise Abstain("STOP in force; new exposure blocked")
    gate = bool(env.submission_gate())
    rec["submission_gate_open"] = gate
    baseline, cadence, peak = load_state(env)
    sh, sm = _hhmm(cfg["open_window_et"][0])
    eh, em = _hhmm(cfg["open_window_et"][1])
    d = date.fromisoformat(today)
    win_start = datetime(d.year, d.month, d.day, sh, sm, tzinfo=NY)
    win_end = datetime(d.year, d.month, d.day, eh, em, tzinfo=NY)
    # wait for Alpaca's clock to confirm the market is open, never past the window
    clock = None
    while True:
        now = env.now_utc()
        if now > win_end:
            raise Abstain("the 09:30-09:35 ET window closed without a confirmed market open; this opportunity is missed")
        if now >= win_start:
            try:
                clock = read_clock(env)
            except Abstain:
                clock = None
            if clock and clock["is_open"]:
                break
        env.sleep(5.0)
    rec["clock_confirmed_open_at_utc"] = _iso(env.now_utc())
    b = read_broker(env)
    led = open_ledger(env)
    r, b = explained_or_stop(env, led, baseline, b)
    peak, dd = update_peak(env, peak, b["equity"])
    h = evaluate_halt(env, dd, b["equity"], peak)
    rec.update(holdings_reconciled_first=True, positions=r["held"], pilot_net_filled=r["pilot_net_filled"], drawdown=dd)
    if h.get("halted"):
        raise Abstain("drawdown halt latched; the monitor handles liquidation, no new exposure")
    step = session_calendar.session_step(_campaign_calendar(env, campaign, today), campaign["anchor_session"], today)
    if step is None:
        raise Abstain(f"{today} is not a session on/after the anchor in the exchange calendar")
    due = session_calendar.latest_due_rebalance_step(step, cfg["rebalance_every_n_sessions"])
    last = cadence["last_rebalanced_step"] if cadence["last_rebalanced_step"] is not None else -1
    rec.update(step=step, rebalance_due_step=due, last_rebalanced_step=cadence["last_rebalanced_step"])
    if due <= last:
        rec["status"] = "hold_not_due"
        rec["decision"] = {"hold": True, "reason": f"no rebalance due (step {step}, last rebalanced {last})"}
        _mark_evaluated(env, cadence, today, None)
        return rec
    prep = prepare_decision(env, today)
    rec.update(completed_session_used=prep["completed_session"], data_retrieved_at_utc=prep["retrieved_at_utc"],
               decision_computed_at_utc=prep["computed_at_utc"], data_provenance=prep["data"]["provenance"],
               decision=prep["decision"])
    prepared = _p(env, f"prepared_{today}.json")
    if prepared.exists():
        pre = read_json_strict(prepared)
        if pre["weights"] != prep["decision"]["weights"] or pre["completed_session"] != prep["completed_session"]:
            raise Abstain("the opening decision differs from the preflight decision; fail closed, no order")
        rec["matches_preflight_decision"] = True
    try:
        orders, notes = plan_orders(env, prep, b, dd, led, baseline)
    except Exception as e:  # noqa: BLE001  (RiskRefusal / OrderGuardError / ValueError: same refusal class the old runner caught)
        rec.update(status="order_refused", refusal=repr(e))
        return rec
    rec.update(intended_orders=orders, order_guard_notes=notes)
    if not orders:
        rec["status"] = "decided_no_orders"          # a legitimate cash / unchanged-position decision
        _mark_evaluated(env, cadence, today, due)
        return rec
    if env.now_utc() > win_end:
        raise Abstain("decision ready only after the 09:35 ET window closed; no order")
    specs = session_guards.build_specs(orders, today, cfg["time_in_force"])
    if not gate:
        rec["status"] = "shadow_no_submission"
        rec["submission"] = {"skipped": True, "reason": "submission gate closed; ledger untouched"}
        return rec
    rec["submission_started_at_utc"] = _iso(env.now_utc())
    result = submit_orders_with_limits(env.broker, led, specs, _p(env, cfg["kill_switch_file"]), today, cfg["max_orders_per_session"])
    rec["submission"] = result
    blocked = result["blocked"] > 0 or any(x["outcome"] == "uncertain_unresolved" for x in result["results"])
    rejected = any(x["outcome"] == "rejected" for x in result["results"])
    rec["order_states_from_broker"] = {x["client_order_id"]: x["outcome"] for x in result["results"]}
    try:
        broker_reconciliation.reconcile_ledger(led, env.broker, [])
        rec["order_states_after_reconcile"] = {r["client_order_id"]: {"status": r["status"], "filled_qty": r["filled_qty"]}
                                               for r in pilot_rows(led) if r["session_date"] == today}
    except Exception:  # noqa: BLE001
        pass
    if rejected or blocked:
        rec["escalate"] = True
    _mark_evaluated(env, cadence, today, due if not (rejected or blocked) else None, consume=not (rejected or blocked))
    rec["status"] = "submitted" if not (rejected or blocked) else ("rejected" if rejected else "blocked_or_uncertain")
    return rec


def _mark_evaluated(env: Env, cadence: dict, today: str, due, consume: bool = True) -> None:
    cadence = {**cadence, "last_evaluated_session_date": today}
    if due is not None and consume:
        cadence["last_rebalanced_step"] = due
    write_json_atomic(_p(env, "cadence_state.json"), cadence)


def _cancel_pilot_orders(env: Env, led, b: dict, rec: dict, *, keep_liquidation: bool) -> None:
    """Cancel working pilot orders, then look every one up again: a cancel is never proof of no fill."""
    ids = {r["client_order_id"]: r for r in pilot_rows(led)}
    notes = []
    for o in b["orders"]:
        cid = o.get("client_order_id")
        if o.get("status") not in LIVE_STATUSES or cid not in ids:
            continue
        if keep_liquidation and cid.endswith("-liq"):
            continue
        try:
            outcome = env.broker.cancel(o["id"])
        except BrokerLookupError as e:
            outcome = f"cancel_state_unknown: {e}"
        try:
            found = env.broker.find_by_client_order_id(cid)
        except BrokerLookupError:
            found = None
        if found:
            led.update_status(cid, found["id"], found["status"], found.get("filled_qty", 0))
        notes.append({"client_order_id": cid, "cancel": outcome, "final_status": (found or {}).get("status"),
                      "filled_qty": (found or {}).get("filled_qty")})
    rec["cancellations"] = notes


def _liquidate_pilot(env: Env, led, baseline: dict, rec: dict, today: str, clock_open: bool) -> None:
    cfg = env.cfg
    fresh_b = read_broker(env)
    unresolved = [r for r in led.get_uncertain() if PILOT_ID_RE.match(r["client_order_id"])]
    if any(r["side"] == "buy" for r in unresolved):
        rec.update(halt_hold="an uncertain BUY is unresolved; never resubmitted under a halt", escalate=True)
        return
    net = pilot_net_filled(led)
    positions = []
    for p in fresh_b["positions"]:
        q = float(p.get("qty") or 0)
        sym = p["symbol"]
        owned = max(0, min(int(math.floor(q + 1e-9)), net.get(sym, 0)))
        if sym in cfg["universe"] and owned > 0:
            positions.append({"symbol": sym, "qty": str(owned)})
    open_orders = [o for o in fresh_b["orders"] if o.get("status") in LIVE_STATUSES]
    ledger_sells = [r for r in led.get_unresolved() if r["side"] == "sell"]
    orders, notes = session_guards.plan_halt_liquidation(positions, open_orders, ledger_sells, cfg["universe"])
    specs = session_guards.build_liquidation_specs(orders, today)
    rec.update(halt_liquidation_plan=orders, halt_notes=notes,
               baseline_holdings_untouched=baseline["positions"])
    if not specs:
        return
    if not clock_open:
        rec["halt_hold"] = "market closed; liquidation waits for regular hours"
        return
    if not env.submission_gate():
        rec["halt_hold"] = "submission gate closed"
        return
    rec["halt_submission"] = submit_orders_with_limits(env.broker, led, specs, _p(env, cfg["kill_switch_file"]), today,
                                                       cfg["max_liquidation_orders_per_session"], "liquidation")


def run_monitor(env: Env) -> dict:
    rec: dict = {}
    if hold_present(env):
        raise Abstain("HOLD in force")
    campaign, today = _common_gate(env)
    rec["session_date"] = today
    baseline, cadence, peak = load_state(env)
    sess = next(s for s in campaign["sessions"] if s["date"] == today)
    now = env.now_utc()
    if not (_parse_ts(sess["open_utc"]) - timedelta(minutes=1) <= now <= _parse_ts(sess["close_utc"]) + timedelta(minutes=30)):
        raise Abstain("outside regular market hours for this campaign session")
    clock = read_clock(env)
    b = read_broker(env)
    led = open_ledger(env)
    r, b = explained_or_stop(env, led, baseline, b)
    peak, dd = update_peak(env, peak, b["equity"])
    h = evaluate_halt(env, dd, b["equity"], peak)
    rec.update(equity=b["equity"], peak_equity=peak["peak_equity"], drawdown=dd, halt_latched=bool(h.get("halted")),
               positions=r["held"], pilot_net_filled=r["pilot_net_filled"],
               open_orders=[{"client_order_id": o.get("client_order_id"), "status": o.get("status")} for o in b["orders"] if o.get("status") in LIVE_STATUSES],
               stop_present=stop_present(env))
    if h.get("halted"):
        _cancel_pilot_orders(env, led, b, rec, keep_liquidation=True)
        _liquidate_pilot(env, led, baseline, rec, today, clock["is_open"])
        rec["status"] = "halted"
    else:
        rec["status"] = "monitored"
    return rec


def run_expiry(env: Env) -> dict:
    rec: dict = {}
    verify_identity(env)
    campaign = load_campaign(env)
    now = env.now_utc()
    if now < _parse_ts(campaign["last_close_utc"]):
        raise Abstain("the fourth session has not closed yet")
    write_json_atomic(_p(env, "EXPIRED"), {"expired_at_utc": _iso(now), "note": "strategy orders blocked from this moment"})
    baseline, cadence, peak = load_state(env)
    b = read_broker(env)
    led = open_ledger(env)
    _cancel_pilot_orders(env, led, b, rec, keep_liquidation=False)
    b = read_broker(env)
    errors: list = []
    broker_reconciliation.reconcile_ledger(led, env.broker, errors)
    net = pilot_net_filled(led)
    held = {p["symbol"]: int(float(p["qty"])) for p in b["positions"] if float(p.get("qty") or 0) != 0}
    rec.update(reconciliation_errors=len(errors), positions=held, pilot_owned_positions=net, baseline_positions=baseline["positions"],
               equity=b["equity"], open_orders_remaining=len([o for o in b["orders"] if o.get("status") in LIVE_STATUSES]),
               last_session=campaign["last_session"], halt_state=halt_state(env),
               automatic_trading_and_risk_liquidation="STOPPED")
    status = "HOLD_WITH_POSITIONS" if any(v > 0 for v in net.values()) else "HOLD_NO_PILOT_POSITIONS"
    write_json_atomic(_p(env, "HOLD"), {"status": status, "held_at_utc": _iso(env.now_utc()), "positions": held, "pilot_owned": net})
    rec["status"] = status
    return rec


# --------------------------------------------------------------------------- entry
def run_mode(env: Env, mode: str, arg: str | None = None) -> tuple[dict, int]:
    """Runs one mode and maps outcomes to (receipt, exit code). HardStop writes STOP and exits 3."""
    wait = 60.0 if mode == "open" else 0.0
    fd = acquire_lock(env, wait)
    if fd is None:
        return {"status": "busy_another_writer_holds_the_lock"}, EXIT_BUSY
    try:
        try:
            if mode == "freeze-campaign":
                rec = freeze_campaign(env, arg or "")
            elif mode == "init":
                verify_identity(env)
                rec = init_state(env)
            else:
                rec = {"preflight": run_preflight, "open": run_open, "monitor": run_monitor, "expiry": run_expiry}[mode](env)
            code = EXIT_OK
        except Abstain as e:
            rec, code = {"status": "abstained", "reason": str(e)}, EXIT_OK
        except OSError as e:                      # network/transport trouble reading state: nothing unsafe happened
            rec, code = {"status": "abstained", "reason": f"transient transport error: {e!r}"}, EXIT_OK
        except HardStop as e:
            if mode not in ("freeze-campaign",):
                try:
                    write_stop(env, str(e))
                except OSError:
                    pass
            rec, code = {"status": "HARD_STOP", "reason": str(e), "new_exposure_blocked": True}, EXIT_HARD_STOP
        except Exception as e:  # noqa: BLE001  unknown state is a safety failure: block new exposure, do not guess
            if mode != "freeze-campaign":
                try:
                    write_stop(env, f"unexpected error: {e!r}")
                except OSError:
                    pass
            rec, code = {"status": "HARD_STOP", "reason": f"unexpected error: {e!r}", "new_exposure_blocked": True}, EXIT_HARD_STOP
        rec["mode"] = mode
        try:
            write_receipt(env, mode, rec)
        except OSError:
            pass
        return rec, code
    finally:
        close_ledgers(env)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def build_real_env() -> Env:
    from alpaca_adapter import submission_enabled
    from alpaca_paper_broker import AlpacaPaperBroker
    from runtime_credential_loader import get_json, load_credential_headers
    cfg, sha = load_config(HERE / "kim_paper_pilot_config.json")
    headers = load_credential_headers()
    state_dir = Path(os.environ.get("TBOTS_KIM_PAPER_STATE_DIR", cfg["state_dir"]))
    return Env(cfg=cfg, cfg_sha=sha, get_json=get_json, headers=headers, broker=AlpacaPaperBroker(headers),
               now_utc=lambda: datetime.now(timezone.utc), submission_gate=submission_enabled, state_dir=state_dir,
               pin=load_pin(Path(cfg["account_pin_file"])))


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in MODES:
        print(f"usage: kim_paper_pilot.py {{{'|'.join(MODES)}}} [FIRST_DATE]", file=sys.stderr)
        return 2
    try:
        env = build_real_env()
    except HardStop as e:
        print(json.dumps({"status": "HARD_STOP", "reason": str(e)}))
        return EXIT_HARD_STOP
    rec, code = run_mode(env, argv[0], argv[1] if len(argv) > 1 else None)
    print(json.dumps(rec, indent=2, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
