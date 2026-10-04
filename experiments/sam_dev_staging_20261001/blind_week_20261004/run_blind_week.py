"""Isolated single-week replay of Sam (gen_8e7a622b...) with the pilot's assumptions, on stored Alpaca bars.
No broker, no network, no orders, no state outside the output directory. Built entirely on the published pilot
candidate e81ceab (shadow_compare engine, lineage_d/s6a_runtime.decide_D, execution.Portfolio, risk.validate).

Nothing about the test week's performance is printed while it runs; results are written to files and read after.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEV = HERE.parent
REPO = DEV.parents[1]
for p in (str(DEV), str(DEV / "alpaca_adapter"), str(DEV / "kim_order_simulation_tests"), str(REPO / "scripts")):
    sys.path.insert(0, p)

import execution
import s6a_final as p6
import s6a_runtime as r
import shadow_compare as sc
import lineage_d
from lib.ids import genome_id

SAM_ID = "gen_8e7a622b48276bc68b7b70773bb9e2a5250dbb82584c53f6c67416bd6ffdf0cf"
SAM_FILE = REPO / "evolution/s6b_runs/primary/D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca/development_top_eight.json"
UNIVERSE = p6.UNIVERSE


def load_sam() -> dict:
    d = json.loads(SAM_FILE.read_text())
    g = next(e for e in d["frozen"] if e["genome_id"] == SAM_ID)["genome"]
    blob = json.dumps(g, sort_keys=True, separators=(",", ":")).encode()
    if "gen_" + hashlib.sha256(blob).hexdigest() != SAM_ID or genome_id(g) != SAM_ID:
        raise RuntimeError("Sam genome identity mismatch")
    if r.validate_genome("D", g) != SAM_ID:
        raise RuntimeError("Sam failed frozen-schema validation")
    return g


def apply_ceiling(weights, ceiling):
    """Same scaling the accepted replay harness used for its 18% overlay arm."""
    if weights is None:
        return None
    total = sum(weights.values())
    if total <= ceiling + 1e-9 or total <= 0:
        return weights
    return {s: w * ceiling / total for s, w in weights.items()}


def sam_strategy(g: dict, ceiling: float) -> sc.Strategy:
    return sc.Strategy("sam", SAM_ID, lambda h, step: apply_ceiling(lineage_d.decide(g, h, step), ceiling),
                       max_asset_weight=ceiling, max_total_exposure=ceiling, rebalance_every=g["rebalance_sessions"])


def passive_strategy(total: float, every: int = 31) -> sc.Strategy:
    w = {s: total / len(UNIVERSE) for s in UNIVERSE}
    return sc.Strategy("passive", "passive_equal_weight", lambda h, step: dict(w) if step % every == 0 else None,
                       max_asset_weight=total / len(UNIVERSE) + 1e-6, max_total_exposure=total, rebalance_every=every)


def cash_strategy() -> sc.Strategy:
    return sc.Strategy("cash", "no_trade", lambda h, step: None, 1.0, 1.0, 1)


def run_replay(rows: dict, dates: list[str], *, halt_pct: float, slippage_bps: int, ceiling: float,
               start_cents: int = sc.START_CASH_CENTS, passive_total: float | None = None, g: dict | None = None):
    """One chronological pass over `dates`; every strategy sees identical rows; history before dates[0] is warm-up."""
    g = g or load_sam()
    strategies = {"sam": sam_strategy(g, ceiling), "passive": passive_strategy(passive_total if passive_total is not None else ceiling),
                  "cash": cash_strategy()}
    books = {n: sc.new_book(s, start_cents) for n, s in strategies.items()}
    old_halt, old_slip = p6.DRAWDOWN_HALT, execution.SLIPPAGE_BPS
    p6.DRAWDOWN_HALT, execution.SLIPPAGE_BPS = halt_pct, slippage_bps      # test assumptions, restored in finally
    try:
        records = sc.run_sessions(books, strategies, rows, dates)
    finally:
        p6.DRAWDOWN_HALT, execution.SLIPPAGE_BPS = old_halt, old_slip
    return books, records


def summarize(books: dict, records: list, rows: dict, dates: list[str], start_cents: int) -> dict:
    out = {}
    last = dates[-1]
    closes = {s: sc._cents(next(x for x in rows[s] if x["timestamp"] == last)["close"]) for s in UNIVERSE}
    for name, b in books.items():
        pf = sc._load(b["portfolio"])
        end_eq = pf.equity_cents(closes)
        pos_val = sum(pf.shares_of(s) * closes[s] for s in UNIVERSE)
        recs = [x["books"][name] for x in records]
        out[name] = {
            "start_equity_cents": start_cents, "end_equity_cents": end_eq, "net_return": end_eq / start_cents - 1,
            "max_drawdown_close_marks": b["max_drawdown"], "orders_placed": b["order_count"], "fills": pf.fill_count,
            "commission_cents": pf.total_commission_cents, "slippage_cents": b["slippage_cents"],
            "total_cost_cents": pf.total_commission_cents + b["slippage_cents"],
            "traded_notional_cents": pf.total_traded_notional_cents,
            "ending_positions_shares": {s: pf.shares_of(s) for s in UNIVERSE if pf.shares_of(s)},
            "ending_exposure_fraction": pos_val / end_eq if end_eq else 0.0,
            "halted": pf.halted, "pending_orders_unfilled_at_end": b["pending"],
            "sessions": len(recs), "rebalance_sessions": [x["date"] for x in recs if x["rebalanced"]],
            "hold_sessions": [x["date"] for x in recs if not x["rebalanced"]],
            "daily": [{"date": x["date"], "equity_cents": x["equity_cents"], "drawdown": x["drawdown"],
                       "rebalanced": x["rebalanced"], "new_target": x["new_target"], "orders": x["orders_placed"],
                       "held_shares": x["held_shares"]} for x in recs]}
    return out


def build_rows(bars: dict) -> tuple[dict, dict]:
    """bars = decoded fetch payloads merged: {sym: {"all": [...], "raw": [...]}}. Returns (rows_by_symbol, data_report)."""
    rows, report = {}, {"gaps": [], "per_symbol": {}}
    date_sets = {}
    for s in UNIVERSE:
        adj = {b["t"][:10]: b for b in bars[s]["all"]["bars"]}
        raw = {b["t"][:10]: b for b in bars[s]["raw"]["bars"]}
        ds = sorted(set(adj) & set(raw))
        date_sets[s] = set(ds)
        rows[s] = [{"timestamp": d, "adjusted_close": float(adj[d]["c"]), "open": float(raw[d]["o"]),
                    "close": float(raw[d]["c"]), "dividend": None} for d in ds]
        report["per_symbol"][s] = {"adj_bars": len(adj), "raw_bars": len(raw), "joined": len(ds),
                                   "adj_only": sorted(set(adj) - set(raw)), "raw_only": sorted(set(raw) - set(adj)),
                                   "first": ds[0], "last": ds[-1]}
    union = sorted(set().union(*date_sets.values()))
    for s in UNIVERSE:
        miss = sorted(set(union) - date_sets[s])
        if miss:
            report["gaps"].append({"symbol": s, "missing_dates": miss})
    return rows, report
