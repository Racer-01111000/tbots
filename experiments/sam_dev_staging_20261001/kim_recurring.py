"""Kim RECURRING unattended PAPER runner (GO TBOTS_KIM_UNATTENDED_RECURRING_PAPER_20261010).

A thin lifecycle layer over the archived pilot's guard logic (kim_paper_pilot.py is imported, not forked, so the safety-critical
code -- broker reads, ledger reconciliation, holdings-explained check, order planning/caps, peak/halt, cancel, halt liquidation,
persistent-ledger submission -- exists exactly once). This module owns only: the recurring config schema, exchange-calendar session
gating, the explicit `migrate` step, failure classification with bounded retries, per-session receipts and housekeeping.

Modes (one process per systemd firing; all are no-ops on non-session days, nothing ever catches up):
  migrate     write-once, audited continuation of the archived pilot state into this lifecycle's own state dir
  preflight   (09:15 ET) validate-only: identity, account pin, clock, calendar, state, data+decision when a rebalance is due today
  open        (09:30/09:32/09:34 ET firings, one 09:30-09:35 window) decide, then submit whole-share DAY orders; done-marker per session
  monitor     (every 5 min) reconcile, track equity vs the persisted peak, latch the 8% halt, liquidate pilot-owned shares only
  close       (after the real close, incl. early close) final reconciliation + daily summary + housekeeping; done-marker per session
  plan-check  read-only: forces the data->decide->plan path for the next session without writing state or submitting anything
  snapshot    consistent, hash-verified backup of the COMPLETE state dir (SQLite-safe, under the writer lock), self-verified by an
              isolated restore, bounded retention; no broker access
  reconcile   broker<->ledger reconciliation report (never mutates the broker, never latches); the only path that clears a restore marker
  restore / verify-snapshot   verify a snapshot and restore it into a NEW isolated directory (never over live state)

Persisted STOP rule: ANY STOP file (integrity, credential, unexpected-error streak, startup, malformed or legacy/unknown) blocks EVERY broker
mutation (submit, cancel, liquidation). It is enforced at the path level and again by GuardedBroker at the broker boundary. A fresh integrity
pass never overrides it. The 8% risk-halt latch is separate: halt liquidation of pilot-owned shares runs only when no STOP exists.

Failure classes (see kim_recurring_ops/RECOVERY_POLICY.md): TRANSIENT (retry with backoff, then abstain this run, no latch) versus
LATCHED (HardStop -> STOP sentinel, exit 3, never auto-cleared, peak/halt/ledger never reset).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import kim_paper_pilot as kp
from kim_paper_pilot import (Abstain, Env, HardStop, EXIT_BUSY, EXIT_HARD_STOP, EXIT_OK, LIVE_STATUSES, NY, PILOT_ID_RE,
                             _et, _hhmm, _iso, _p, _parse_ts, session_calendar, session_guards, submit_orders_with_limits)
from alpaca_adapter import HostNotAllowedError, PAPER_TRADING_HOST, assert_trading_host_allowed
from runtime_credential_loader import CredentialTrustError
from atomic_io import StateCorrupt, read_json_strict, write_json_atomic

HERE = Path(__file__).resolve().parent
SCHEMA = "kim-recurring-paper-v1"
MODES = ("migrate", "preflight", "open", "monitor", "close", "plan-check", "snapshot", "snapshot-now", "offload", "consolidate", "reconcile",
         "restore", "verify-snapshot", "fetch-restore")
ARG_MODES = ("restore", "verify-snapshot", "fetch-restore")
OFFLINE_MODES = ("snapshot", "snapshot-now", "offload", "consolidate")      # never mutate the broker, never latch a STOP
BROKER_FREE_ENV_MODES = ("snapshot-now", "offload", "consolidate")          # scheduled `snapshot` also reads Alpaca's calendar (GET only)


def env_kind(mode: str) -> str:
    return "offline" if mode in BROKER_FREE_ENV_MODES else "real"                    # take positional args; need no broker/credentials
RESTORE_MARKER = "RESTORED_UNRECONCILED"
CONFIG_NAME = "kim_recurring_config.json"
MANIFEST_NAME = "kim_recurring_ops/continuation_manifest.json"

# ---- recovery policy constants (documented in RECOVERY_POLICY.md; pinned by tests)
RETRY_DELAYS_S = (1.0, 2.0, 4.0, 8.0)        # per request: 1 try + 4 retries, <= 15 s of sleeping
RETRY_BUDGET_PER_RUN = 40                    # total retries across one process, then abstain
TRANSIENT_STATUS = {408, 425, 429}           # plus every 5xx
UNEXPECTED_STREAK_LIMIT = 6                  # consecutive unexpected-exception runs before a latch (~30 min of monitors)


class MutationBlocked(RuntimeError):
    """A broker mutation was attempted while a persisted STOP (or an unreconciled restore) blocks all mutations."""


def kim_offload_error():
    import kim_offload
    return kim_offload.OffloadError


class Quiet(Exception):
    """Nothing to do and nothing worth a receipt (non-session monitor tick, outside hours, already done)."""


class NotSession(Quiet):
    pass


# --------------------------------------------------------------------------- config
def load_config(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    cfg = json.loads(raw)
    need = {"schema", "submission_scope", "paper_host", "strategy", "genome_id", "universe", "rebalance_every_n_sessions", "feed",
            "lookback_calendar_days", "warmup_bars", "max_orders_per_session", "max_liquidation_orders_per_session",
            "drawdown_halt_pct", "max_asset_weight", "gross_exposure_ceiling", "min_cash_reserve_pct", "time_in_force",
            "extended_hours", "kill_switch_file", "preflight_et", "open_window_et", "account_pin_file", "state_dir",
            "archived_pilot_state_dir", "frozen_champion_file", "frozen_champion_file_sha256", "decision_function_sha256",
            "forbidden_genome_ids", "close_reconcile_minutes_after_close", "monitor_grace_minutes_after_close",
            "monitor_log_keep_days", "backup_dir", "offload_config_file", "s3_region", "backup_min_free_mb", "backup_max_pending",
            "offload_max_attempts", "consolidate_stage_cap_mb", "consolidate_keep_months"}
    miss = need - set(cfg)
    if miss:
        raise HardStop(f"recurring config missing keys {sorted(miss)}")
    if cfg["schema"] != SCHEMA or cfg["paper_host"] != PAPER_TRADING_HOST:
        raise HardStop("recurring config schema/host mismatch")
    if cfg["time_in_force"] != "day" or cfg["extended_hours"] is not False:
        raise HardStop("recurring config must use DAY orders with extended hours disabled")
    if cfg["warmup_bars"] != 296 or cfg["rebalance_every_n_sessions"] != 50:
        raise HardStop("recurring config warm-up/cadence differ from the authorized terms")
    if cfg["open_window_et"] != ["09:30", "09:35"]:
        raise HardStop("recurring config must keep the 09:30-09:35 ET opening window")
    if not (0 < cfg["max_orders_per_session"] <= 4 and cfg["max_liquidation_orders_per_session"] <= 8
            and cfg["drawdown_halt_pct"] <= 0.08 and cfg["max_asset_weight"] <= 0.18 and cfg["gross_exposure_ceiling"] <= 0.18):
        raise HardStop("recurring config loosens an authorized risk term")
    return cfg, hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------- transient/latched HTTP classification
class RetryBudget:
    def __init__(self, limit=RETRY_BUDGET_PER_RUN):
        self.left = limit
        self.used = 0


def resilient_get_json(raw_get, sleep, budget: RetryBudget | None = None, delays=RETRY_DELAYS_S):
    """Wrap a (url, headers)->(status, body) GET. Transient = OSError, undecodable/truncated body (ValueError), 5xx, 408/425/429:
    bounded exponential backoff, then Abstain (nothing unsafe happened, the next scheduled run tries again). 401, and 403 from the
    PAPER host, are credential/permission failures: HardStop (latched). A 403 from the data host is a data-entitlement problem
    and is returned to the data boundary, which abstains."""
    budget = budget or RetryBudget()

    def get(url, headers):
        last = None
        for attempt in range(len(delays) + 1):
            try:
                st, body = raw_get(url, headers)
            except OSError as e:
                st, body, last = None, None, f"transport error {e!r}"
            except ValueError as e:                       # JSONDecodeError: HTML 502 page, truncated body
                st, body, last = None, None, f"undecodable response body {e!r}"
            else:
                if st == 401 or (st == 403 and url.startswith(PAPER_TRADING_HOST)):
                    raise HardStop(f"credential/permission failure: HTTP {st} from the paper API")
                if not (st >= 500 or st in TRANSIENT_STATUS):
                    return st, body
                last = f"HTTP {st}"
            if attempt == len(delays) or budget.left <= 0:
                raise Abstain(f"transient failure persisted after {attempt + 1} attempt(s) ({last}); retry limit reached")
            budget.left -= 1
            budget.used += 1
            sleep(delays[attempt])
        raise AssertionError("unreachable")
    return get


def safe_http(http):
    """Broker HTTP that never leaks a ValueError: a 200 with an undecodable body on a POST is an UNCERTAIN submission (the
    order may exist), which the broker object maps from OSError to reconcile-by-lookup before any retry."""
    def call(*a, **k):
        try:
            return http(*a, **k)
        except ValueError as e:
            raise OSError(f"undecodable broker response: {e!r}") from e
    return call


# --------------------------------------------------------------------------- sessions
def session_row(env: Env, day: str) -> dict:
    rows = kp.read_calendar(env, day, day)
    for r in rows:
        if r["date"] == day:
            return r
    raise NotSession(f"{day} is not an exchange session")


def gate(env: Env, *, identity=True) -> str:
    """Common start-of-run checks. Returns today's ET date. Latched failures raise HardStop."""
    assert_trading_host_allowed(env.trading_host)
    if identity:
        kp.verify_identity(env)
    require_continuation(env)
    if _p(env, RESTORE_MARKER).exists():
        raise Abstain("restored state has not been reconciled with the broker; run `reconcile`")
    return _et(env.now_utc()).date().isoformat()


def require_continuation(env: Env) -> dict:
    p = _p(env, "continuation.json")
    try:
        c = read_json_strict(p)
    except (StateCorrupt, OSError) as e:
        raise HardStop(f"continuation record missing or corrupt: {e}") from e
    if c.get("schema") != "kim-recurring-continuation-v1":
        raise HardStop("continuation record has an unexpected schema")
    return c


def cadence_step(env: Env, cadence: dict, today: str) -> int:
    rows = kp.read_calendar(env, cadence["anchor_session"], today)
    step = session_calendar.session_step([r["date"] for r in rows], cadence["anchor_session"], today)
    if step is None:
        raise Abstain(f"{today} is not a session on/after the cadence anchor")
    return step


def rebalance_decision(step: int, last_rebalanced, every_n: int) -> tuple[str, int]:
    """'rebalance' only on the exact due session; a due step that has passed unserved is recorded as missed and consumed,
    never traded later (no catch-up). -> (action, due_step); action in rebalance | not_due | missed."""
    due = session_calendar.latest_due_rebalance_step(step, every_n)
    last = -1 if last_rebalanced is None else last_rebalanced
    if due <= last:
        return "not_due", due
    return ("rebalance" if step == due else "missed"), due


def stop_info(env: Env) -> dict | None:
    """Persisted STOP, classified. None = no STOP. Anything unreadable or without a class is 'legacy_or_unknown' and STILL blocks."""
    p = _p(env, "STOP")
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        if isinstance(d, dict) and isinstance(d.get("class"), str) and d["class"]:
            return d
    except (OSError, ValueError):
        return {"class": "malformed", "reason": "STOP file unreadable"}
    return {"class": "legacy_or_unknown", "reason": (d.get("reason") if isinstance(d, dict) else None)}


def write_stop(env: Env, reason: str, klass: str) -> None:
    p = _p(env, "STOP")
    if not p.exists():                                      # existing STOP evidence is never overwritten
        write_json_atomic(p, {"schema": "kim-stop-v2", "class": klass, "stopped_at_utc": _iso(env.now_utc()), "reason": reason,
                              "note": "persisted STOP: blocks every broker mutation; removed only by a deliberate human decision"})


def mutation_block(env: Env) -> str | None:
    """Why broker mutations are forbidden right now, or None. Read fresh from disk on every call."""
    st = stop_info(env)
    if st:
        return f"STOP in force (class {st['class']})"
    if _p(env, RESTORE_MARKER).exists():
        return "restored state has not been reconciled with the broker"
    return None


def sentinel_blocks(env: Env) -> str | None:
    return mutation_block(env)


class GuardedBroker:
    """Last line of defence at the broker boundary: submit and cancel re-read the persisted STOP every time. Reads pass through."""

    def __init__(self, inner, env: Env):
        self._inner, self._env = inner, env

    def submit(self, spec):
        if (why := mutation_block(self._env)):
            raise MutationBlocked(f"submit refused: {why}")
        return self._inner.submit(spec)

    def cancel(self, order_id):
        if (why := mutation_block(self._env)):
            raise MutationBlocked(f"cancel refused: {why}")
        return self._inner.cancel(order_id)

    def find_by_client_order_id(self, cid):
        return self._inner.find_by_client_order_id(cid)


# --------------------------------------------------------------------------- modes
def run_preflight(env: Env) -> dict:
    rec: dict = {"validate_only": True, "submission_attempted": False}
    today = gate(env)
    rec["session_date"] = today
    session_row(env, today)
    if (why := sentinel_blocks(env)):
        raise Abstain(why)
    baseline, cadence, peak = kp.load_state(env)
    clock = kp.read_clock(env)
    rec["clock"] = {"is_open": clock["is_open"], "broker_timestamp": clock["timestamp"].isoformat()}
    b = kp.read_broker(env)
    led = kp.open_ledger(env)
    r, b = kp.explained_or_stop(env, led, baseline, b)
    peak, dd = kp.update_peak(env, peak, b["equity"])
    h = kp.evaluate_halt(env, dd, b["equity"], peak)
    rec.update(account_pin_verified=True, positions=r["held"], pilot_net_filled=r["pilot_net_filled"],
               open_orders=len([o for o in b["orders"] if o.get("status") in LIVE_STATUSES]), drawdown=dd,
               halt_latched=bool(h.get("halted")), identity=env.identity["genome_ids"]["independent_sha256"])
    step = cadence_step(env, cadence, today)
    action, due = rebalance_decision(step, cadence["last_rebalanced_step"], env.cfg["rebalance_every_n_sessions"])
    rec.update(step=step, rebalance_action=action, rebalance_due=action == "rebalance")
    if action == "rebalance" and not h.get("halted"):
        prep = kp.prepare_decision(env, today)
        try:
            orders, notes = kp.plan_orders(env, prep, b, dd, led, baseline)
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


def _done_marker(env: Env, kind: str, today: str) -> Path:
    return _p(env, f"{kind}_done_{today}.json")


def run_open(env: Env) -> dict:
    cfg = env.cfg
    rec: dict = {"validate_only": False}
    today = gate(env)
    rec["session_date"] = today
    row = session_row(env, today)
    marker = _done_marker(env, "open", today)
    if marker.exists():
        raise Quiet("open already completed for this session")
    if (why := sentinel_blocks(env)):
        raise Abstain(why)
    submit_gate = bool(env.submission_gate())
    rec["submission_gate_open"] = submit_gate
    baseline, cadence, peak = kp.load_state(env)
    sess = kp.session_times(row)
    sh, sm = _hhmm(cfg["open_window_et"][0])
    eh, em = _hhmm(cfg["open_window_et"][1])
    d = date.fromisoformat(today)
    win_start = datetime(d.year, d.month, d.day, sh, sm, tzinfo=NY)
    win_end = datetime(d.year, d.month, d.day, eh, em, tzinfo=NY)
    if _parse_ts(sess["open_utc"]) > win_start:       # a late-opening session never trades outside the window
        win_start = _parse_ts(sess["open_utc"])
    while True:
        now = env.now_utc()
        if now > win_end:
            raise Abstain("the 09:30-09:35 ET window closed without a confirmed market open; this opportunity is missed")
        if now >= win_start:
            try:
                clock = kp.read_clock(env)
            except Abstain:
                clock = None
            if clock and clock["is_open"]:
                break
        env.sleep(5.0)
    rec["clock_confirmed_open_at_utc"] = _iso(env.now_utc())
    b = kp.read_broker(env)
    led = kp.open_ledger(env)
    r, b = kp.explained_or_stop(env, led, baseline, b)
    peak, dd = kp.update_peak(env, peak, b["equity"])
    h = kp.evaluate_halt(env, dd, b["equity"], peak)
    rec.update(holdings_reconciled_first=True, positions=r["held"], pilot_net_filled=r["pilot_net_filled"], drawdown=dd)
    if h.get("halted"):
        raise Abstain("drawdown halt latched; the monitor handles liquidation, no new exposure")
    step = cadence_step(env, cadence, today)
    action, due = rebalance_decision(step, cadence["last_rebalanced_step"], cfg["rebalance_every_n_sessions"])
    rec.update(step=step, rebalance_due_step=due, last_rebalanced_step=cadence["last_rebalanced_step"], rebalance_action=action)
    if action != "rebalance":
        if action == "missed":
            rec["status"] = "missed_rebalance_not_caught_up"
            rec["decision"] = {"hold": True, "reason": f"rebalance step {due} passed unserved; consumed, never traded later"}
            kp._mark_evaluated(env, cadence, today, due)
        else:
            rec["status"] = "hold_not_due"
            rec["decision"] = {"hold": True, "reason": f"no rebalance due (step {step}, last rebalanced {cadence['last_rebalanced_step']})"}
            kp._mark_evaluated(env, cadence, today, None)
        write_json_atomic(marker, {"status": rec["status"], "at_utc": _iso(env.now_utc())})
        return rec
    prep = kp.prepare_decision(env, today)
    rec.update(completed_session_used=prep["completed_session"], data_retrieved_at_utc=prep["retrieved_at_utc"],
               decision_computed_at_utc=prep["computed_at_utc"], data_provenance=prep["data"]["provenance"], decision=prep["decision"])
    prepared = _p(env, f"prepared_{today}.json")
    if prepared.exists():
        pre = read_json_strict(prepared)
        if pre["weights"] != prep["decision"]["weights"] or pre["completed_session"] != prep["completed_session"]:
            raise Abstain("the opening decision differs from the preflight decision; fail closed, no order")
        rec["matches_preflight_decision"] = True
    try:
        orders, notes = kp.plan_orders(env, prep, b, dd, led, baseline)
    except Exception as e:  # noqa: BLE001  (RiskRefusal / OrderGuardError: refusal, nothing sent)
        rec.update(status="order_refused", refusal=repr(e))
        kp._mark_evaluated(env, cadence, today, due)
        write_json_atomic(marker, {"status": "order_refused", "at_utc": _iso(env.now_utc())})
        return rec
    rec.update(intended_orders=orders, order_guard_notes=notes)
    if not orders:
        rec["status"] = "decided_no_orders"
        kp._mark_evaluated(env, cadence, today, due)
        write_json_atomic(marker, {"status": rec["status"], "at_utc": _iso(env.now_utc())})
        return rec
    if env.now_utc() > win_end:
        raise Abstain("decision ready only after the 09:35 ET window closed; no order")
    if (why := mutation_block(env)):
        raise Abstain(f"{why}; no order")
    specs = session_guards.build_specs(orders, today, cfg["time_in_force"])
    if not submit_gate:
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
        kp.broker_reconciliation.reconcile_ledger(led, env.broker, [])
        rec["order_states_after_reconcile"] = {x["client_order_id"]: {"status": x["status"], "filled_qty": x["filled_qty"]}
                                               for x in kp.pilot_rows(led) if x["session_date"] == today}
    except Exception:  # noqa: BLE001
        pass
    if rejected or blocked:
        rec["escalate"] = True
    kp._mark_evaluated(env, cadence, today, due if not (rejected or blocked) else None, consume=not (rejected or blocked))
    rec["status"] = "submitted" if not (rejected or blocked) else ("rejected" if rejected else "blocked_or_uncertain")
    if rec["status"] in ("submitted", "rejected"):       # an uncertain outcome stays open for the next in-window firing's lookup
        write_json_atomic(marker, {"status": rec["status"], "at_utc": _iso(env.now_utc())})
    return rec


def run_monitor(env: Env) -> dict:
    rec: dict = {}
    today = gate(env)
    row = session_row(env, today)
    rec["session_date"] = today
    sess = kp.session_times(row)
    now = env.now_utc()
    grace = timedelta(minutes=env.cfg["monitor_grace_minutes_after_close"])
    if not (_parse_ts(sess["open_utc"]) - timedelta(minutes=1) <= now <= _parse_ts(sess["close_utc"]) + grace):
        raise Quiet("outside regular market hours for today's session")
    baseline, cadence, peak = kp.load_state(env)
    clock = kp.read_clock(env)
    b = kp.read_broker(env)
    led = kp.open_ledger(env)
    r, b = kp.explained_or_stop(env, led, baseline, b)
    peak, dd = kp.update_peak(env, peak, b["equity"])
    h = kp.evaluate_halt(env, dd, b["equity"], peak)
    rec.update(equity=b["equity"], peak_equity=peak["peak_equity"], drawdown=dd, halt_latched=bool(h.get("halted")),
               positions=r["held"], pilot_net_filled=r["pilot_net_filled"],
               open_orders=[{"client_order_id": o.get("client_order_id"), "status": o.get("status")} for o in b["orders"]
                            if o.get("status") in LIVE_STATUSES], stop_present=stop_info(env) is not None,
               stop_class=(stop_info(env) or {}).get("class"))
    if h.get("halted"):
        why = mutation_block(env)                       # fresh read: a persisted STOP is never overridden by a passing integrity check
        if why:
            rec["halt_liquidation"] = f"BLOCKED: {why}; no cancel, no order"
            rec["status"] = "halted_mutations_blocked"
        else:
            kp._cancel_pilot_orders(env, led, b, rec, keep_liquidation=True)
            why = mutation_block(env)                   # re-read between cancel and liquidation
            if why:
                rec["halt_liquidation"] = f"BLOCKED: {why}"
            else:
                kp._liquidate_pilot(env, led, baseline, rec, today, clock["is_open"])
            rec["status"] = "halted"
    else:
        rec["status"] = "monitored"
    return rec


def run_close(env: Env) -> dict:
    rec: dict = {}
    today = gate(env)
    row = session_row(env, today)
    rec["session_date"] = today
    sess = kp.session_times(row)
    marker = _done_marker(env, "close", today)
    if marker.exists():
        raise Quiet("close reconciliation already completed for this session")
    if env.now_utc() < _parse_ts(sess["close_utc"]) + timedelta(minutes=env.cfg["close_reconcile_minutes_after_close"]):
        raise Quiet("the session has not closed long enough for the final reconciliation")
    baseline, cadence, peak = kp.load_state(env)
    b = kp.read_broker(env)
    led = kp.open_ledger(env)
    r, b = kp.explained_or_stop(env, led, baseline, b)
    peak, dd = kp.update_peak(env, peak, b["equity"])
    h = kp.evaluate_halt(env, dd, b["equity"], peak)
    counts = {k: v for k, v in led.conn.execute("SELECT status, COUNT(*) FROM order_intents GROUP BY status").fetchall()}
    rec.update(equity=b["equity"], cash=b["cash"], peak_equity=peak["peak_equity"], drawdown=dd, halt_latched=bool(h.get("halted")),
               positions=r["held"], pilot_net_filled=r["pilot_net_filled"], baseline_positions=baseline["positions"],
               open_orders=len([o for o in b["orders"] if o.get("status") in LIVE_STATUSES]),
               unresolved_ledger_rows=len(led.get_unresolved()), ledger_status_counts=counts,
               orders_today=led.count_for_session(today), early_close=sess["early_close"], close_et=sess["close_et"],
               stop_present=stop_info(env) is not None, reconciliation="broker==ledger+baseline" if not r["mismatch"] else "MISMATCH")
    rec["housekeeping"] = archive_old_monitor_logs(env)
    rec["status"] = "closed_reconciled"
    write_json_atomic(marker, {"status": rec["status"], "at_utc": _iso(env.now_utc())})
    return rec


# --------------------------------------------------------------------------- housekeeping (verified archival before deletion)
def archive_old_monitor_logs(env: Env) -> dict:
    """Per-session monitor jsonl files older than the keep window are tarred, the tar is re-read and checked entry by entry
    against the source bytes, its sha256 recorded, and only then are the originals removed. Nothing else is ever deleted."""
    rdir = env.state_dir / "receipts"
    keep = env.cfg["monitor_log_keep_days"]
    cutoff = (_et(env.now_utc()).date() - timedelta(days=keep)).isoformat()
    old = sorted(f for f in rdir.glob("monitor_*.jsonl") if f.stem[len("monitor_"):] < cutoff) if rdir.exists() else []
    if not old:
        return {"archived": 0}
    adir = env.state_dir / "archive"
    adir.mkdir(parents=True, exist_ok=True)
    month = old[0].stem[len("monitor_"):][:7]
    tar_path = adir / f"monitor_logs_through_{old[-1].stem[len('monitor_'):]}.tar.gz"
    if tar_path.exists():
        return {"archived": 0, "note": "archive name already exists; nothing deleted"}
    with tarfile.open(tar_path, "w:gz") as t:
        for f in old:
            t.add(f, arcname=f.name)
    want = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in old}
    with tarfile.open(tar_path, "r:gz") as t:
        got = {m.name: hashlib.sha256(t.extractfile(m).read()).hexdigest() for m in t.getmembers()}
    if got != want:
        tar_path.unlink()
        return {"archived": 0, "error": "archive verification failed; originals kept"}
    sha = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    write_json_atomic(tar_path.with_suffix(tar_path.suffix + ".manifest.json"), {"archive": tar_path.name, "sha256": sha, "files": want, "month": month})
    for f in old:
        f.unlink()
    return {"archived": len(old), "archive": tar_path.name, "sha256": sha}


# --------------------------------------------------------------------------- migrate (write-once, audited)
def _file_sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def ledger_rows_digest(db_path: Path, immutable: bool = False) -> str:
    c = sqlite3.connect(f"file:{db_path}?mode=ro{'&immutable=1' if immutable else ''}", uri=True)
    try:
        rows = c.execute("SELECT client_order_id, symbol, qty, side, status, broker_order_id, filled_qty, session_date, "
                         "COALESCE(order_class,'normal') FROM order_intents ORDER BY client_order_id").fetchall()
    finally:
        c.close()
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def fills_by_order(env: Env, after: str) -> dict:
    """Sum FILL activities per broker order id (GET only). Paged defensively."""
    out, page_token = {}, None
    for _ in range(20):
        url = f"{env.trading_host}/v2/account/activities/FILL?after={after}&page_size=100&direction=asc"
        if page_token:
            url += f"&page_token={page_token}"
        s, rows = env.get_json(url, env.headers)
        if s != 200 or not isinstance(rows, list):
            raise Abstain(f"fill activities unreadable (HTTP {s})")
        for a in rows:
            out[a["order_id"]] = out.get(a["order_id"], 0.0) + float(a["qty"]) * (1 if a["side"] == "buy" else -1)
        if len(rows) < 100:
            return out
        page_token = rows[-1]["id"]
    raise Abstain("fill activity paging did not terminate")


def run_migrate(env: Env, manifest_path: Path | None = None) -> dict:
    cfg = env.cfg
    verify = kp.verify_identity
    verify(env)
    dest = env.state_dir
    if (dest / "continuation.json").exists():
        return {"status": "already_migrated", "continuation_sha256": _file_sha(dest / "continuation.json")}
    manifest = json.loads(Path(manifest_path or (HERE / MANIFEST_NAME)).read_text())
    src = Path(cfg["archived_pilot_state_dir"])
    if (src / "STOP").exists():
        raise HardStop("archived pilot state carries a STOP; refusing to continue it")
    hs = src / "halt_state.json"
    if hs.exists() and read_json_strict(hs).get("halted"):
        raise HardStop("archived pilot state has a latched drawdown halt; refusing to continue it")
    src_hashes = {}
    for name, want in manifest["source_sha256"].items():
        got = _file_sha(src / name)
        if got != want:
            raise HardStop(f"archived pilot file {name} hash {got} != preserved {want}")
        src_hashes[name] = got
    if ledger_rows_digest(src / "order_ledger.sqlite3", immutable=True) != manifest["ledger_rows_digest"]:
        raise HardStop("archived ledger rows differ from the preserved digest")
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    for name in ("pilot_baseline.json", "cadence_state.json", "peak_equity_state.json"):
        shutil.copyfile(src / name, dest / name)                      # byte-for-byte
        os.chmod(dest / name, 0o600)
    con_s = sqlite3.connect(f"file:{src / 'order_ledger.sqlite3'}?mode=ro&immutable=1", uri=True)
    con_d = sqlite3.connect(dest / "order_ledger.sqlite3")
    try:
        con_s.backup(con_d)
    finally:
        con_s.close()
        con_d.close()
    os.chmod(dest / "order_ledger.sqlite3", 0o600)
    dest_digest = ledger_rows_digest(dest / "order_ledger.sqlite3")
    if dest_digest != manifest["ledger_rows_digest"]:
        raise HardStop("migrated ledger digest differs from the source")
    for name in ("pilot_baseline.json", "cadence_state.json", "peak_equity_state.json"):
        if _file_sha(dest / name) != src_hashes[name]:
            raise HardStop(f"migrated {name} differs from the source")
    # reconcile the MIGRATED copies against the live broker before declaring the continuation valid
    baseline, cadence, peak = kp.load_state(env)
    b = kp.read_broker(env)
    led = kp.open_ledger(env)
    r, b = kp.explained_or_stop(env, led, baseline, b)
    if [o for o in b["orders"] if o.get("status") in LIVE_STATUSES]:
        raise HardStop("open broker orders at migration")
    fills = fills_by_order(env, manifest["fills_after"])
    ledger_filled = {row[0]: float(row[1] or 0) * (1 if row[2] == "buy" else -1) for row in
                     led.conn.execute("SELECT broker_order_id, filled_qty, side FROM order_intents WHERE broker_order_id IS NOT NULL")}
    fill_mismatch = {k: {"fills": fills.get(k), "ledger": ledger_filled.get(k)} for k in set(fills) | set(ledger_filled)
                     if abs(fills.get(k, 0.0) - ledger_filled.get(k, 0.0)) > 1e-9}
    if fill_mismatch:
        raise HardStop(f"broker fill activities do not equal the ledger: {fill_mismatch}")
    kp.close_ledgers(env)
    post_digest = ledger_rows_digest(dest / "order_ledger.sqlite3")
    if post_digest != manifest["ledger_rows_digest"]:
        raise HardStop("ledger rows changed during migration reconciliation; refusing to continue")
    if b["equity"] > peak["peak_equity"]:
        raise HardStop("broker equity exceeds the persisted peak at migration; peak update must happen in a normal monitored run")
    dd = min(0.0, (b["equity"] - peak["peak_equity"]) / peak["peak_equity"])
    cont = {"schema": "kim-recurring-continuation-v1", "migrated_at_utc": _iso(env.now_utc()), "source_dir": str(src),
            "source_sha256": src_hashes, "source_ledger_rows_digest": manifest["ledger_rows_digest"],
            "dest_sha256": {n: _file_sha(dest / n) for n in ("pilot_baseline.json", "cadence_state.json", "peak_equity_state.json")},
            "dest_ledger_rows_digest": dest_digest, "preserved_untouched": ["order_ledger", "peak_equity_state", "cadence_state",
                                                                          "pilot_baseline", "holdings", "order_ownership"],
            "archived_pilot_evidence_sha256": manifest["archived_pilot_evidence_sha256"],
            "cleared": ["scheduled_expiry (HOLD/EXPIRED/campaign.json of the archived pilot are NOT copied and stay intact)"],
            "not_cleared": ["STOP", "halt_state", "peak", "drawdown history", "ledger", "cadence"],
            "reconciliation": {"broker_equity": b["equity"], "positions": r["held"], "pilot_net_filled": r["pilot_net_filled"],
                               "open_orders": 0, "fills_equal_ledger": True, "orders_in_ledger": len(ledger_filled),
                               "drawdown_vs_persisted_peak": dd, "peak_equity": peak["peak_equity"], "start_equity": peak["start_equity"],
                               "cadence": {k: cadence[k] for k in ("anchor_session", "last_rebalanced_step", "last_evaluated_session_date")}}}
    write_json_atomic(dest / "continuation.json", cont)
    ident = _file_sha(dest / "continuation.json")
    return {"status": "migrated", "migrated_state_identity_sha256": ident, **{k: cont[k] for k in ("dest_sha256", "reconciliation")}}


# --------------------------------------------------------------------------- plan-check (no writes to real state, no submission)
def run_plan_check(env: Env) -> dict:
    """Forces data fetch -> frozen decision -> order planning for the NEXT session as if a rebalance were due, so the path that
    will first run unattended at step 50 is exercised now. Point TBOTS_KIM_RECURRING_STATE_DIR at a scratch COPY of the state."""
    rec: dict = {"validate_only": True, "submission_attempted": False}
    gate(env)
    today = _et(env.now_utc()).date().isoformat()
    rows = kp.read_calendar(env, today, (date.fromisoformat(today) + timedelta(days=400)).isoformat())
    sessions = [r["date"] for r in rows]
    before_open = _et(env.now_utc()) < datetime.fromisoformat(f"{today}T09:30:00").replace(tzinfo=NY)
    nxt = next(x for x in sessions if x > today or (x == today and before_open))
    baseline, cadence, peak = kp.load_state(env)
    b = kp.read_broker(env)
    led = kp.open_ledger(env)
    r, b = kp.explained_or_stop(env, led, baseline, b)
    peak, dd = kp.update_peak(env, peak, b["equity"])
    prep = kp.prepare_decision(env, nxt)
    try:
        orders, notes = kp.plan_orders(env, prep, b, dd, led, baseline)
        plan = {"intended_orders": orders, "notes": notes}
    except Exception as e:  # noqa: BLE001
        plan = {"refused": repr(e)}
    anchor_rows = kp.read_calendar(env, cadence["anchor_session"], (date.fromisoformat(today) + timedelta(days=400)).isoformat())
    all_sessions = [x["date"] for x in anchor_rows]
    n = env.cfg["rebalance_every_n_sessions"]
    cur_step = session_calendar.session_step(all_sessions, cadence["anchor_session"], nxt)
    last = -1 if cadence["last_rebalanced_step"] is None else cadence["last_rebalanced_step"]
    next_due_step = -(-cur_step // n) * n
    if next_due_step <= last:
        next_due_step += n
    due_date = next((s for s in all_sessions if session_calendar.session_step(all_sessions, cadence["anchor_session"], s) == next_due_step), None)
    gross = sum(float(p.get("qty") or 0) * prep["data"]["sizing_cents"].get(p["symbol"], 0) / 100 for p in b["positions"])
    rec.update(next_session=nxt, next_session_step=cur_step, completed_session_used=prep["completed_session"],
               forced_decision=prep["decision"], forced_plan=plan, last_rebalanced_step=cadence["last_rebalanced_step"],
               next_rebalance_due_step=next_due_step, next_rebalance_due_session=due_date,
               holdings_mark_weight_of_equity=round(gross / b["equity"], 5), data_provenance_feed=prep["data"]["provenance"]["feed"],
               status="plan_check_ok")
    return rec


# --------------------------------------------------------------------------- backups: consistent snapshot, isolated restore, reconcile
SNAP_PREFIX = "kim_recurring_state_"
SKIP_NAMES = ("writer.lock",)
SKIP_SUFFIXES = ("-wal", "-shm")
TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced"}


def _is_skipped(path: Path) -> bool:
    return path.name in SKIP_NAMES or path.name.endswith(SKIP_SUFFIXES) or ".tmp." in path.name


def _ledger_unresolved(db: Path) -> list:
    c = sqlite3.connect(db)
    try:
        return [list(r) for r in c.execute("SELECT client_order_id, status, filled_qty, qty, side FROM order_intents ORDER BY client_order_id")
                if (r[1] or "") not in TERMINAL_STATUSES]
    finally:
        c.close()


def _walk_files(root: Path):
    return sorted(p for p in root.rglob("*") if p.is_file() and not _is_skipped(p))


def sha_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _manifest_for(stage: Path, env: Env | None, extra: dict) -> dict:
    files = {str(p.relative_to(stage)): sha_file(p) for p in _walk_files(stage) if p.name != "SNAPSHOT_MANIFEST.json"}
    m = {"schema": "kim-recurring-snapshot-v1", "files": files, **extra}
    led = stage / "order_ledger.sqlite3"
    if led.exists():
        m["ledger_rows_digest"] = ledger_rows_digest(led)
        m["unresolved_orders"] = _ledger_unresolved(led)
    for name, key in (("peak_equity_state.json", "peak"), ("cadence_state.json", "cadence"), ("halt_state.json", "halt"), ("STOP", "stop")):
        f = stage / name
        if f.exists():
            try:
                m[key] = json.loads(f.read_text())
            except ValueError:
                m[key] = {"unreadable": True}
    return m


def restore_snapshot(snapshot: Path, target: Path, live_state_dir: Path | None = None, backup_dir: Path | None = None) -> dict:
    """Verify a snapshot and restore it into a NEW, isolated, empty directory. Refuses the live state dir (or anything inside it / containing
    it). The restored copy carries RESTORED_UNRECONCILED: nothing may mutate the broker from it until `reconcile` passes."""
    snapshot, target = Path(snapshot), Path(target)
    side = snapshot.with_name(snapshot.name + ".sha256")
    want = side.read_text().split()[0] if side.exists() else None
    if want is None or sha_file(snapshot) != want:
        raise HardStop(f"snapshot {snapshot.name} does not match its recorded sha256")
    t = target.resolve()
    for forbidden in filter(None, (live_state_dir, backup_dir)):
        f = Path(forbidden).resolve()
        if t == f or f in t.parents or t in f.parents:
            raise HardStop(f"refusing to restore into or over {forbidden}: restores go to a new isolated directory only")
    if target.exists() and any(target.iterdir()):
        raise HardStop(f"restore target {target} is not empty")
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(snapshot, "r:gz") as tf:
        for m in tf.getmembers():
            if not (m.isreg() or m.isdir()) or m.name.startswith(("/", "..")) or ".." in Path(m.name).parts:
                raise HardStop(f"unsafe snapshot member {m.name!r}")
        tf.extractall(target, filter="data")
    man = json.loads((target / "SNAPSHOT_MANIFEST.json").read_text())
    bad = [n for n, h in man["files"].items() if sha_file(target / n) != h]
    if bad:
        raise HardStop(f"restored files differ from the manifest: {bad[:5]}")
    c = sqlite3.connect(target / "order_ledger.sqlite3")
    try:
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        c.close()
    if ok != "ok" or ledger_rows_digest(target / "order_ledger.sqlite3") != man.get("ledger_rows_digest"):
        raise HardStop("restored ledger failed integrity/digest verification")
    for name in ("pilot_baseline.json", "cadence_state.json", "peak_equity_state.json", "continuation.json"):
        if not (target / name).exists():
            raise HardStop(f"snapshot is missing {name}")
    write_json_atomic(target / RESTORE_MARKER, {"snapshot": snapshot.name, "snapshot_sha256": want,
                                                "note": "restored state is historical; it must pass `reconcile` against the broker before any use"})
    return {"verified_files": len(man["files"]), "ledger_rows_digest": man["ledger_rows_digest"], "unresolved_orders": man["unresolved_orders"],
            "snapshot_created_utc": man.get("created_utc"), "restored_to": str(target)}


FINAL_RECON_DEADLINE_MIN = 50          # minutes after the real close by which a missing final reconciliation is reported loudly


def run_snapshot_scheduled(env: Env) -> dict:
    """Post-close snapshot: only on a real session, only after the real close (early closes included) AND after the session's final
    reconciliation (`close_done_<date>`), once per session. A missing reconciliation is reported, not silently skipped."""
    today = _et(env.now_utc()).date().isoformat()
    row = session_row(env, today)
    sess = kp.session_times(row)
    done = _done_marker(env, "snapshot", today)
    if done.exists():
        raise Quiet("the post-close snapshot for this session is already done")
    close = _parse_ts(sess["close_utc"])
    if env.now_utc() < close + timedelta(minutes=env.cfg["close_reconcile_minutes_after_close"]):
        raise Quiet("the session has not closed long enough")
    if not _done_marker(env, "close", today).exists():
        if env.now_utc() >= close + timedelta(minutes=FINAL_RECON_DEADLINE_MIN):
            raise Abstain("no final reconciliation for this session yet; snapshot not taken (close runs will keep retrying)")
        raise Quiet("waiting for the session's final reconciliation")
    rec = run_snapshot(env)
    if rec.get("status") == "snapshot_ok":
        write_json_atomic(done, {"snapshot": rec["snapshot"], "at_utc": _iso(env.now_utc())})
    return rec


def run_snapshot(env: Env) -> dict:
    """Runs under the writer lock (run_mode took it), so no writer can be mid-update. SQLite via the backup API, never a raw file copy."""
    import kim_offload
    bdir = Path(env.cfg["backup_dir"])
    bdir.mkdir(parents=True, exist_ok=True)
    os.chmod(bdir, 0o700)
    sc = kim_offload.storage_check(bdir, env.cfg["backup_min_free_mb"], env.cfg["backup_max_pending"])
    if sc["pressure"]:                   # never delete an unoffloaded copy to make room: refuse, loudly
        return {"status": "SNAPSHOT_REFUSED_STORAGE_PRESSURE", "storage": sc, "_exit": 1,
                "note": "pending local snapshots are retained; offload must succeed (or a person must act) before new snapshots are staged"}
    ts = env.now_utc().strftime("%Y%m%dT%H%M%SZ")
    snap = bdir / f"{SNAP_PREFIX}{ts}.tar.gz"
    if snap.exists():
        raise Quiet("a snapshot with this timestamp already exists")
    stage = bdir / f".stage_{ts}"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(mode=0o700)
    try:
        led_src = env.state_dir / "order_ledger.sqlite3"
        if not led_src.exists():
            raise HardStop("no ledger to snapshot")
        for p in sorted(env.state_dir.rglob("*")):
            if p.is_dir() or _is_skipped(p) or p.name == "order_ledger.sqlite3":
                continue
            dst = stage / p.relative_to(env.state_dir)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)
        src = sqlite3.connect(led_src)
        dstc = sqlite3.connect(stage / "order_ledger.sqlite3")
        try:
            src.backup(dstc)
        finally:
            src.close()
            dstc.close()
        extra = {"created_utc": _iso(env.now_utc()), "release": Path(__file__).resolve().parents[2].name, "config_sha256": env.cfg_sha,
                 "offsite_copy": {"status": "PENDING_OFFLOAD", "destination": "s3://<private bucket>/daily/"}}
        man = _manifest_for(stage, env, extra)
        write_json_atomic(stage / "SNAPSHOT_MANIFEST.json", man)
        with tarfile.open(snap, "w:gz") as tf:
            for p in sorted(stage.rglob("*")):
                tf.add(p, arcname=str(p.relative_to(stage)), recursive=False)
        os.chmod(snap, 0o600)
        digest = sha_file(snap)
        side = snap.with_name(snap.name + ".sha256")
        side.write_text(f"{digest}  {snap.name}\n")
        os.chmod(side, 0o600)
        import tempfile
        vroot = Path(tempfile.mkdtemp(prefix="kimrec-verify-"))
        vdir = vroot / "restored"
        try:
            v = restore_snapshot(snap, vdir, env.state_dir, bdir)                    # isolated restore proves the snapshot is consistent
        except Exception:
            snap.unlink(missing_ok=True)
            side.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(vroot, ignore_errors=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return {"status": "snapshot_ok", "snapshot": snap.name, "sha256": digest, "files": v["verified_files"], "ledger_rows_digest": v["ledger_rows_digest"],
            "unresolved_orders": v["unresolved_orders"], "verified_by_isolated_restore": True, "local_copy": "temporary staging until offloaded"}


def offload_target(cfg: dict) -> tuple[str, str]:
    """(bucket, region). The bucket name lives in a root-owned file on the instance, not in the published config (it embeds an account id)."""
    f = Path(cfg["offload_config_file"])
    if f.exists():
        d = json.loads(f.read_text())
        return d["bucket"], d.get("region", cfg["s3_region"])
    if cfg.get("s3_bucket"):                                   # tests / explicit override
        return cfg["s3_bucket"], cfg["s3_region"]
    raise HardStop("offload destination is not configured")


def get_store(env: Env):
    st = getattr(env, "store", None)
    if st is not None:
        return st
    import kim_offload
    return kim_offload.AwsCliStore(*offload_target(env.cfg))


def run_offload(env: Env) -> dict:
    """Upload every pending snapshot, verify the remote bytes against the local checksum, then (and only then) delete the local copy. A no-op
    when nothing is pending. Failures keep the local copy, count attempts, and make the run (and its systemd unit) fail visibly."""
    import kim_offload
    bdir = Path(env.cfg["backup_dir"])
    pend = kim_offload.pending_snapshots(bdir) if bdir.exists() else []
    if not pend:
        raise Quiet("nothing pending")
    rep = kim_offload.offload_pending(bdir, get_store(env), int(env.cfg["offload_max_attempts"]), env.now_utc())
    sc = kim_offload.storage_check(bdir, env.cfg["backup_min_free_mb"], env.cfg["backup_max_pending"])
    bad = bool(rep["failed"] or rep["stuck"] or rep["skipped_no_sidecar"])
    rec = {"status": "OFFLOAD_FAILED" if bad else "offloaded", **rep, "storage": sc, "remaining_local": len(kim_offload.pending_snapshots(bdir))}
    if sc["pressure"] and rec["remaining_local"]:
        rec["status"] = "OFFLOAD_FAILED"
        rec["storage_pressure"] = "unoffloaded snapshots remain and storage is under pressure; nothing was deleted; new snapshots are refused until resolved"
    if rec["status"] == "OFFLOAD_FAILED":
        rec["_exit"] = 1
    return rec


def run_consolidate(env: Env) -> dict:
    import kim_offload
    rep = kim_offload.consolidate(Path(env.cfg["backup_dir"]), get_store(env), env.now_utc(), int(env.cfg["consolidate_keep_months"]),
                                  int(env.cfg["consolidate_stage_cap_mb"]))
    if not rep["archives"] and not rep["refused"]:
        raise Quiet("no month is eligible for consolidation")
    rec = {"status": "consolidation_refused" if rep["refused"] else "consolidated", **rep}
    if rep["refused"]:
        rec["_exit"] = 1
    return rec


def run_reconcile(env: Env) -> dict:
    """Broker <-> ledger reconciliation. Read-only toward the broker; never latches a STOP; the only path that clears RESTORED_UNRECONCILED."""
    rec: dict = {"read_only_toward_broker": True}
    kp.verify_identity(env)
    require_continuation(env)
    baseline, cadence, peak = kp.load_state(env)
    b = kp.read_broker(env)
    led = kp.open_ledger(env)
    errors: list = []
    kp.broker_reconciliation.reconcile_ledger(led, env.broker, errors)
    known = set(baseline["diagnostic_order_ids"])
    ledger_ids = {r[0] for r in led.conn.execute("SELECT client_order_id FROM order_intents").fetchall()}
    unexplained = [o.get("client_order_id") for o in b["orders"] if o.get("client_order_id") not in known and o.get("client_order_id") not in ledger_ids]
    net = kp.pilot_net_filled(led)
    expect = {sym: int(q) + net.get(sym, 0) for sym, q in baseline["positions"].items()}
    for sym, q in net.items():
        expect.setdefault(sym, q)
    held = {x["symbol"]: int(float(x["qty"])) for x in b["positions"] if float(x.get("qty") or 0) != 0}
    mismatch = {sym: {"held": held.get(sym, 0), "expected": expect.get(sym, 0)} for sym in set(held) | set(expect) if held.get(sym, 0) != expect.get(sym, 0)}
    outstanding = [{"client_order_id": o.get("client_order_id"), "status": o.get("status"), "side": o.get("side"), "qty": o.get("qty"),
                    "filled_qty": o.get("filled_qty")} for o in b["orders"] if o.get("status") in LIVE_STATUSES]
    unresolved = [r for r in led.get_unresolved()]
    clean = not (errors or unexplained or mismatch or outstanding or unresolved)
    marker = _p(env, RESTORE_MARKER)
    rec.update(clean=clean, positions=held, expected=expect, mismatch=mismatch, unexplained_broker_orders=len(unexplained),
               outstanding_broker_orders=outstanding, unresolved_ledger_rows=[r["client_order_id"] for r in unresolved],
               lookup_errors=len(errors), stop=(stop_info(env) or {}).get("class"), restore_marker_present=marker.exists(),
               writer_lock="held by this reconcile (no other writer was active)")
    if clean and marker.exists():
        marker.unlink()
        rec["restore_marker"] = "cleared: broker and restored ledger agree"
    rec["status"] = "reconciled_clean" if clean else "reconciled_with_findings"
    return rec


# --------------------------------------------------------------------------- entry
DISPATCH = {"preflight": run_preflight, "open": run_open, "monitor": run_monitor, "close": run_close, "plan-check": run_plan_check,
            "snapshot": run_snapshot_scheduled, "snapshot-now": run_snapshot, "offload": run_offload, "consolidate": run_consolidate,
            "reconcile": run_reconcile}


def _streak_path(env: Env) -> Path:
    return _p(env, "unexpected_error_streak.json")


def _bump_streak(env: Env, reason: str) -> int:
    try:
        n = int(read_json_strict(_streak_path(env)).get("consecutive", 0)) if _streak_path(env).exists() else 0
    except (StateCorrupt, OSError, ValueError):
        n = 0
    n += 1
    write_json_atomic(_streak_path(env), {"consecutive": n, "last_reason": reason, "at_utc": _iso(env.now_utc())})
    return n


def _clear_streak(env: Env) -> None:
    try:
        if _streak_path(env).exists():
            _streak_path(env).unlink()
    except OSError:
        pass


def write_monitor_line(env: Env, rec: dict) -> None:
    d = env.state_dir / "receipts"
    d.mkdir(parents=True, exist_ok=True)
    day = _et(env.now_utc()).date().isoformat()
    line = json.dumps({**rec, "mode": "monitor", "receipt_written_at_utc": _iso(env.now_utc()), "config_sha256": env.cfg_sha},
                      default=str, separators=(",", ":")) + "\n"
    fd = os.open(d / f"monitor_{day}.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def run_mode(env: Env, mode: str, arg: str | None = None) -> tuple[dict, int]:
    wait = {"open": 60.0, "snapshot": float(env.cfg.get("snapshot_lock_wait_s", 120.0)),
            "snapshot-now": float(env.cfg.get("snapshot_lock_wait_s", 120.0))}.get(mode, 0.0)
    fd = None if mode in ("offload", "consolidate") else kp.acquire_lock(env, wait)     # they only touch the backup staging dir (own lock)
    if fd is None and mode not in ("offload", "consolidate"):
        return {"status": "busy_another_writer_holds_the_lock", "mode": mode}, EXIT_BUSY
    quiet = False
    if not isinstance(env.broker, GuardedBroker):
        env.broker = GuardedBroker(env.broker, env)         # every submit/cancel re-reads the persisted STOP
    try:
        try:
            rec = run_migrate(env) if mode == "migrate" else DISPATCH[mode](env)
            code = rec.pop("_exit", EXIT_OK)
            if mode not in ("plan-check", "reconcile", *OFFLINE_MODES):
                _clear_streak(env)
        except Quiet as e:
            rec, code, quiet = {"status": "quiet", "reason": str(e)}, EXIT_OK, True
            if isinstance(e, NotSession) and mode in ("preflight", "open", "close"):
                quiet = False                      # rare, informative: record why a weekday firing did nothing
                rec["status"] = "not_a_session"
        except kim_offload_error() as e:
            rec, code = {"status": "OFFLOAD_FAILED", "reason": str(e), "_unused": None}, 1
            rec.pop("_unused")
        except MutationBlocked as e:               # backstop fired: nothing was sent; not an "unexpected" error
            rec, code = {"status": "mutation_blocked", "reason": str(e)}, EXIT_OK
        except Abstain as e:
            rec, code = {"status": "abstained", "reason": str(e)}, EXIT_OK
        except OSError as e:
            rec, code = {"status": "abstained", "reason": f"transient transport error: {e!r}"}, EXIT_OK
        except (HardStop, HostNotAllowedError, CredentialTrustError) as e:
            if mode not in ("plan-check", "reconcile", *OFFLINE_MODES):
                try:
                    klass = "credential" if isinstance(e, CredentialTrustError) or "credential" in str(e) else "integrity"
                    write_stop(env, str(e), klass)
                except OSError:
                    pass
            rec, code = {"status": "HARD_STOP", "reason": str(e), "new_exposure_blocked": True}, EXIT_HARD_STOP
        except Exception as e:  # noqa: BLE001  unexpected: abstain with a bounded streak, then latch
            if mode in OFFLINE_MODES:              # backup modes: never silent, never a trading latch
                rec, code = {"status": "OFFLINE_MODE_FAILED", "reason": f"unexpected error {e!r}"}, 1
                rec["mode"] = mode
                try:
                    kp.write_receipt(env, mode, rec)
                except OSError:
                    pass
                return rec, code
            n = _bump_streak(env, repr(e)) if mode not in ("plan-check", "reconcile", *OFFLINE_MODES) else 0
            if n >= UNEXPECTED_STREAK_LIMIT:
                try:
                    write_stop(env, f"{n} consecutive unexpected errors; last: {e!r}", "unexpected_error_streak")
                except OSError:
                    pass
                rec, code = {"status": "HARD_STOP", "reason": f"{n} consecutive unexpected errors; last: {e!r}", "new_exposure_blocked": True}, EXIT_HARD_STOP
            else:
                rec, code = {"status": "abstained", "reason": f"unexpected error {e!r} (streak {n}/{UNEXPECTED_STREAK_LIMIT})"}, EXIT_OK
        rec["mode"] = mode
        if not quiet and mode != "plan-check":
            try:
                if mode == "monitor":                  # every monitor outcome, including repeated stops, is one jsonl line
                    write_monitor_line(env, rec)
                else:
                    kp.write_receipt(env, mode, rec)
            except OSError:
                pass
        return rec, code
    finally:
        kp.close_ledgers(env)
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def run_cli_mode(env: Env, mode: str) -> tuple[dict, int]:
    """run_mode plus the post-snapshot offload (writer lock already released: upload, verify, delete the staged copy)."""
    rec, code = run_mode(env, mode)
    if mode in ("snapshot", "snapshot-now") and rec.get("status") == "snapshot_ok":
        r2, c2 = run_mode(env, "offload")
        rec["offload"], code = r2, max(code, c2)
    return rec, code


def build_real_env() -> Env:
    from alpaca_adapter import submission_enabled
    from alpaca_paper_broker import AlpacaPaperBroker, default_http
    from runtime_credential_loader import get_json, load_credential_headers
    import time
    cfg, sha = load_config(HERE / CONFIG_NAME)
    headers = load_credential_headers()
    state_dir = Path(os.environ.get("TBOTS_KIM_RECURRING_STATE_DIR", cfg["state_dir"]))
    budget = RetryBudget()
    return Env(cfg=cfg, cfg_sha=sha, get_json=resilient_get_json(get_json, time.sleep, budget), headers=headers,
               broker=AlpacaPaperBroker(headers, http=safe_http(default_http)), now_utc=lambda: datetime.now(timezone.utc),
               submission_gate=submission_enabled, state_dir=state_dir, pin=kp.load_pin(Path(cfg["account_pin_file"])))


def build_offline_env() -> Env:
    """Env for modes that never touch the broker or credentials (snapshot)."""
    cfg, sha = load_config(HERE / CONFIG_NAME)
    state_dir = Path(os.environ.get("TBOTS_KIM_RECURRING_STATE_DIR", cfg["state_dir"]))
    def _no_broker(*a, **k):
        raise AssertionError("offline mode must not use the broker")
    return Env(cfg=cfg, cfg_sha=sha, get_json=_no_broker, headers={}, broker=object(), now_utc=lambda: datetime.now(timezone.utc),
               submission_gate=lambda: False, state_dir=state_dir, pin={})


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in MODES:
        print(f"usage: kim_recurring.py {{{'|'.join(MODES)}}} [args]", file=sys.stderr)
        return 2
    mode = argv[0]
    if mode in ARG_MODES:                                   # offline: no credentials, no broker, no live state
        try:
            cfg, _ = load_config(HERE / CONFIG_NAME)
            live = Path(os.environ.get("TBOTS_KIM_RECURRING_STATE_DIR", cfg["state_dir"]))
            if mode == "fetch-restore":
                if len(argv) != 3:
                    print("usage: kim_recurring.py fetch-restore KEY|latest NEW_EMPTY_TARGET_DIR", file=sys.stderr)
                    return 2
                import kim_offload
                import tempfile
                with tempfile.TemporaryDirectory(prefix="kimrec-fetch-") as td:
                    snap = kim_offload.fetch_snapshot(kim_offload.AwsCliStore(*offload_target(cfg)), argv[1], Path(td))
                    out = restore_snapshot(snap, Path(argv[2]), live, Path(cfg["backup_dir"]))
                    out["downloaded_from_s3"] = snap.name
            elif mode == "restore":
                if len(argv) != 3:
                    print("usage: kim_recurring.py restore SNAPSHOT NEW_EMPTY_TARGET_DIR", file=sys.stderr)
                    return 2
                out = restore_snapshot(Path(argv[1]), Path(argv[2]), live, Path(cfg["backup_dir"]))
            else:
                import tempfile
                with tempfile.TemporaryDirectory() as td:
                    out = restore_snapshot(Path(argv[1]), Path(td) / "v", live, Path(cfg["backup_dir"]))
                    out["restored_to"] = "(temporary, removed)"
        except HardStop as e:
            print(json.dumps({"status": "REFUSED", "reason": str(e)}))
            return EXIT_HARD_STOP
        except Exception as e:  # noqa: BLE001  fetch/verify failures are reported, never half-applied
            print(json.dumps({"status": "FAILED", "reason": repr(e)[:300]}))
            return 1
        print(json.dumps({"status": "restore_verified", **out}, indent=2, default=str))
        return EXIT_OK
    try:
        env = build_offline_env() if env_kind(mode) == "offline" else build_real_env()
    except Exception as e:  # noqa: BLE001  config/credential/pin failures are integrity failures
        try:
            cfg, _ = load_config(HERE / CONFIG_NAME)
            sd = Path(os.environ.get("TBOTS_KIM_RECURRING_STATE_DIR", cfg["state_dir"]))
            if mode not in ("plan-check", "reconcile", *OFFLINE_MODES) and sd.exists() and not (sd / "STOP").exists():
                write_json_atomic(sd / "STOP", {"schema": "kim-stop-v2", "class": "startup_integrity",
                                                "stopped_at_utc": datetime.now(timezone.utc).isoformat(),
                                                "reason": f"startup integrity failure: {e!r}",
                                                "note": "persisted STOP: blocks every broker mutation; removed only by a deliberate human decision"})
        except Exception:  # noqa: BLE001
            pass
        print(json.dumps({"status": "HARD_STOP", "reason": f"startup integrity failure: {e!r}"}))
        return EXIT_HARD_STOP
    rec, code = run_cli_mode(env, mode)
    print(json.dumps(rec, indent=2, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
