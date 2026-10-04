"""Side-by-side SHADOW books for the champion and lineage D on identical
completed-session data. Pure computation + its own state files.

Isolation (pinned by tests):
  * touches ONLY the directory it is constructed with; it never reads or
    writes the paper pilot's cadence_state.json, halt_state.json,
    peak_equity_state.json, order_ledger.sqlite3, KILL_SWITCH or pilot_config;
  * has no broker object, no POST, no account/positions/orders calls; the live
    entrypoint (kim_shadow_compare.py) uses market-data and calendar GETs only;
  * therefore a shadow session can never spend `last_rebalanced_step`, a
    ledger cap slot, or any other future paper-execution opportunity.

Each book is a hypothetical $100k portfolio run with the accepted evaluator's
accounting (execution.Portfolio: fills at the NEXT session's raw open with
slippage and commission, marks at the raw close, 12% external halt that
liquidates to cash and latches). The per-session order of operations below is
the order in s6b_evaluator._simulate_admitted_episode, so a book fed the same
rows reproduces the evaluator's results (see test_shadow_parity_*).

Known, deliberate divergences from the paper pilot runner: no catch-up of a
missed rebalance (the evaluator has none), and halt threshold is the
evaluator's 12%, not the pilot's proposed 8%.
"""
from __future__ import annotations

import bisect
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import execution
import risk
import s6a_final as p
from atomic_io import write_json_atomic, read_json_strict

UNIVERSE = p.UNIVERSE
START_CASH_CENTS = 10_000_000  # $100,000 hypothetical per book
PORTFOLIO_FIELDS = ("starting_cash_cents", "cash_cents", "peak_equity_cents", "realized_pl_cents", "halted",
                    "total_commission_cents", "total_traded_notional_cents", "fill_count", "total_dividend_cents")


class DataGap(RuntimeError):
    """A required symbol/session row is missing; the book must not advance past it."""


@dataclass
class Strategy:
    name: str
    genome_id: str
    decide: Callable          # (adjusted_history: {sym: [float]}, step: int) -> {sym: w} | None
    max_asset_weight: float
    max_total_exposure: float
    rebalance_every: int


# ------------------------------------------------------------------ state
def new_book(strategy: Strategy, starting_cash_cents: int = START_CASH_CENTS) -> dict:
    pf = execution.Portfolio(starting_cash_cents)
    return {"name": strategy.name, "genome_id": strategy.genome_id, "session_zero": None, "last_session": None,
            "step": 0, "portfolio": _dump(pf), "pending": None, "order_count": 0, "slippage_cents": 0,
            "last_target": {}, "last_rebalance_session": None, "max_drawdown": 0.0}


def _dump(pf) -> dict:
    d = {k: getattr(pf, k) for k in PORTFOLIO_FIELDS}
    d["positions"] = {s: dict(v) for s, v in pf.positions.items()}
    return d


def _load(d: dict):
    pf = execution.Portfolio(d["starting_cash_cents"])
    for k in PORTFOLIO_FIELDS:
        setattr(pf, k, d[k])
    pf.positions = {s: dict(v) for s, v in d["positions"].items()}
    return pf


class _HistView:
    """Minimal AgentView-compatible view over truncated adjusted-close history
    (observe/history, the two-method contract control_agent/indicators use)."""

    def __init__(self, adj_hist: dict):
        self._h = adj_hist

    def observe(self):
        return {"assets": {s: {"available": bool(self._h.get(s)), "adjusted_close": (self._h[s][-1] if self._h.get(s) else None)}
                           for s in UNIVERSE}}

    def history(self, symbol, bars):
        rows = self._h.get(symbol, [])
        return [{"adjusted_close": v} for v in (rows[-bars:] if bars > 0 else [])]


# ------------------------------------------------------------------ one session
def process_session(book: dict, strat: Strategy, date: str, adj_hist: dict, open_cents: dict, close_cents: dict,
                    dividends: dict | None = None) -> dict:
    """Advance one book by one completed session. Order of operations =
    s6b_evaluator._simulate_admitted_episode."""
    pf = _load(book["portfolio"])
    if book["session_zero"] is None:
        book["session_zero"] = date
    step = book["step"]

    for s in UNIVERSE:                                   # dividends on shares held coming into the session
        div = (dividends or {}).get(s)
        if div:
            pf.credit_dividend(s, div)

    if book["pending"]:                                  # yesterday's orders fill at today's raw open
        for o in book["pending"]:
            fill = pf.apply_fill(o["symbol"], o["side"], o["shares"], open_cents[o["symbol"]])
            book["slippage_cents"] += fill["slippage_cents"]
    book["pending"] = None

    equity = pf.equity_cents(close_cents)
    dd = pf.update_peak_and_drawdown(equity)
    book["max_drawdown"] = min(book["max_drawdown"], dd)

    target = None
    halted_now = False
    if dd <= -p.DRAWDOWN_HALT - 1e-9 and not pf.halted:
        pf.halted, target, halted_now = True, {}, True
    elif not pf.halted:
        target = strat.decide(adj_hist, step)

    orders: list = []
    if target is not None:
        target = {s: w for s, w in target.items() if s in UNIVERSE}
        risk.validate(target, UNIVERSE, max_asset_weight=strat.max_asset_weight,
                      max_total_exposure=strat.max_total_exposure,
                      drawdown_halt_pct=p.DRAWDOWN_HALT, current_drawdown=dd)
        current = {s: pf.shares_of(s) for s in UNIVERSE if pf.shares_of(s) > 0}
        orders = execution.compute_orders(target, UNIVERSE, equity, close_cents, current)
        book["pending"] = orders or None
        book["order_count"] += len(orders)

    prev = dict(book["last_target"])
    turnover_target = 0.0
    if target is not None:
        keys = set(prev) | set(target)
        turnover_target = sum(abs(target.get(k, 0.0) - prev.get(k, 0.0)) for k in keys) / 2
        book["last_target"] = dict(target)
        book["last_rebalance_session"] = date

    rec = {"date": date, "step": step, "rebalanced": target is not None, "halted": pf.halted, "halted_now": halted_now,
           "equity_cents": equity, "drawdown": dd,
           "new_target": target if target is not None else None,
           "standing_target": dict(book["last_target"]),
           "standing_exposure": round(sum(book["last_target"].values()), 12),
           "target_turnover_one_way": turnover_target,
           "orders_placed": orders,
           "traded_notional_cum_cents": pf.total_traded_notional_cents,
           "commission_cum_cents": pf.total_commission_cents, "slippage_cum_cents": book["slippage_cents"],
           "held_shares": {s: pf.shares_of(s) for s in UNIVERSE if pf.shares_of(s) > 0}}

    book["portfolio"] = _dump(pf)
    book["last_session"] = date
    book["step"] = step + 1
    return rec


def compare_records(a: dict, b: dict, name_a: str, name_b: str) -> dict:
    ta, tb = a["standing_target"], b["standing_target"]
    keys = set(ta) | set(tb)
    return {"l1_distance": round(sum(abs(ta.get(k, 0.0) - tb.get(k, 0.0)) for k in keys), 12),
            f"only_{name_a}": sorted(k for k in ta if ta[k] > 0 and tb.get(k, 0.0) <= 0),
            f"only_{name_b}": sorted(k for k in tb if tb[k] > 0 and ta.get(k, 0.0) <= 0),
            "both": sorted(k for k in ta if ta[k] > 0 and tb.get(k, 0.0) > 0),
            "same_targets": all(abs(ta.get(k, 0.0) - tb.get(k, 0.0)) < 1e-12 for k in keys),
            f"exposure_{name_a}": a["standing_exposure"], f"exposure_{name_b}": b["standing_exposure"],
            f"exposure_{name_a}_at_ceiling18": min(a["standing_exposure"], 0.18),
            f"exposure_{name_b}_at_ceiling18": min(b["standing_exposure"], 0.18)}


# ------------------------------------------------------------------ driving
def _cents(x) -> int:
    return round(float(x) * 100)


def run_sessions(books: dict, strategies: dict, rows_by_symbol: dict, dates: list[str]) -> list[dict]:
    """Process `dates` in order for every book from identical rows. Already
    processed dates are skipped (idempotent). Raises DataGap before touching a
    book if any symbol lacks a row for a date."""
    index = {s: [r["timestamp"] for r in rows_by_symbol[s]] for s in UNIVERSE}
    out = []
    for date in sorted(dates):
        if all(b["last_session"] is not None and date <= b["last_session"] for b in books.values()):
            continue
        cuts, row_at = {}, {}
        for s in UNIVERSE:
            i = bisect.bisect_left(index[s], date)
            if i >= len(index[s]) or index[s][i] != date:
                raise DataGap(f"{s} has no row for {date}")
            cuts[s], row_at[s] = i + 1, rows_by_symbol[s][i]
        adj_hist = {s: [float(r["adjusted_close"]) for r in rows_by_symbol[s][:cuts[s]]] for s in UNIVERSE}
        opens = {s: _cents(row_at[s]["open"]) for s in UNIVERSE}
        closes = {s: _cents(row_at[s]["close"]) for s in UNIVERSE}
        divs = {s: row_at[s].get("dividend") for s in UNIVERSE}
        recs = {}
        for name, book in books.items():
            if book["last_session"] is not None and date <= book["last_session"]:
                continue
            recs[name] = process_session(book, strategies[name], date, adj_hist, opens, closes, divs)
        if len(recs) == 2:
            (na, ra), (nb, rb) = list(recs.items())
            out.append({"date": date, "books": recs, "comparison": compare_records(ra, rb, na, nb)})
        else:
            out.append({"date": date, "books": recs, "comparison": None})
    return out


# ------------------------------------------------------------------ persistence
class ShadowStore:
    """state_dir/<name>/state.json per book + state_dir/sessions.jsonl."""

    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir)

    def load_book(self, strategy: Strategy, starting_cash_cents: int = START_CASH_CENTS) -> dict:
        path = self.dir / strategy.name / "state.json"
        if path.exists():
            b = read_json_strict(path)
            if b["genome_id"] != strategy.genome_id:
                raise RuntimeError(f"{path} belongs to {b['genome_id']}, not {strategy.genome_id}")
            return b
        return new_book(strategy, starting_cash_cents)

    def recorded_dates(self) -> set:
        path = self.dir / "sessions.jsonl"
        if not path.exists():
            return set()
        return {json.loads(line)["date"] for line in path.read_text().splitlines() if line.strip()}

    def commit(self, books: dict, records: list[dict]) -> None:
        """Records first (idempotent by date), then states atomically: a crash
        between the two replays the session, whose record is then skipped."""
        self.dir.mkdir(parents=True, exist_ok=True)
        have = self.recorded_dates()
        new = [r for r in records if r["date"] not in have]
        if new:
            with open(self.dir / "sessions.jsonl", "a") as f:
                for r in new:
                    f.write(json.dumps(r, sort_keys=True, default=str) + "\n")
                f.flush()
                import os
                os.fsync(f.fileno())
        for name, book in books.items():
            write_json_atomic(self.dir / name / "state.json", book)
