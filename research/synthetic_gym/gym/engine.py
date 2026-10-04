"""Daily accounting/execution engine for the synthetic gym. Integer cents throughout.

Per-session order of operations (mirrors the accepted evaluator, extended):
  1 corporate actions effective this session: splits (shares and pending orders), dividend receivables, dividend payments due
  2 execution of pending orders at the RAW OPEN: sells first (only what is held), then buys (only what cash can afford after
    earlier buys and fees; unaffordable remainder is reduced, never creates negative cash); participation cap defers the rest
  3 mark at the RAW CLOSE: equity = cash + receivables + shares x close; cash earns the world's policy rate
  4 halt latch (accepted 12% external halt) / strategy decision using ONLY as-of data -> orders for the NEXT open
Every strategy, passive18 and the cash control run through this single code path with identical costs and cash yield."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import stats as st
from .rng import Rng, derive_seed
from .world import SYMBOLS, World

INF = float("inf")


@dataclass(frozen=True)
class CostCase:
    slippage_bps: float = 10.0
    commission_bps: float = 5.0
    spread_floor: bool = False        # True: effective slippage = max(allowance, modeled half-spread) -- never added on top

    @property
    def label(self):
        return f"slip{self.slippage_bps:g}_comm{self.commission_bps:g}" + ("_spreadfloor" if self.spread_floor else "")


@dataclass(frozen=True)
class StressOverlay:
    """execution stress, kept SEPARATE from the reference case. All draws are keyed by (world seed, session, symbol), so every
    strategy meets the identical stress."""
    partial_fill_prob: float = 0.0
    reject_prob: float = 0.0
    stale_feed_prob: float = 0.0
    symbol_halt_prob: float = 0.0
    outage_prob: float = 0.0
    gap_shock_bps: float = 0.0
    name: str = "stress"


@dataclass
class EngineParams:
    start_cents: int = 10_000_000
    ceiling: float | None = 0.18
    halt_pct: float = 0.12
    participation_cap: float = INF
    max_defer_sessions: int = 5
    interest: bool = True
    cost: CostCase = field(default_factory=CostCase)
    overlay: StressOverlay | None = None


@dataclass
class Strategy:
    name: str
    genome_id: str
    due: object               # callable(step:int, idx:int, world) -> bool
    weights: object           # callable(hist:dict, step:int) -> dict|None
    window: int               # as-of adjusted bars needed
    max_asset_weight: float = 1.0
    max_total_exposure: float = 1.0
    halt: bool = True


@dataclass
class RunResult:
    name: str
    world_id: str
    start_cents: int
    n_eval: int
    equity: list = field(default_factory=list)         # cents at each eval-session close
    invested: list = field(default_factory=list)       # fraction of equity in shares at each close
    orders: int = 0
    fills: int = 0
    partial_fills: int = 0
    deferred: int = 0
    dropped_after_defer: int = 0
    unaffordable_reductions: int = 0
    rejected: int = 0
    stale_skips: int = 0
    outage_sessions: int = 0
    halted_symbol_blocks: int = 0
    commission_cents: int = 0
    slippage_cents: int = 0
    traded_notional_cents: int = 0
    dividends_cents: int = 0
    interest_cents: int = 0
    halted: bool = False
    decisions: list = field(default_factory=list)      # (idx, step, weights or None)
    fill_log: list = field(default_factory=list)       # (idx, symbol, side, qty, fill_price_cents, fee_cents) when record_fills
    cash_events: list = field(default_factory=list)    # (idx, kind, cents) interest/dividend/split-cash when record_fills
    violations: list = field(default_factory=list)
    final_shares: dict = field(default_factory=dict)
    final_cash_cents: int = 0
    final_receivables_cents: int = 0

    @property
    def ok(self):
        return not self.violations


def _cents(x: float) -> int:
    return round(x * 100)


def _scale_to_ceiling(w, ceiling):
    if w is None or ceiling is None:
        return w
    tot = sum(w.values())
    if tot <= ceiling + 1e-12 or tot <= 0:
        return w
    k = ceiling / tot
    return {s: v * k for s, v in w.items()}


def _orders(target: dict, universe, equity_cents: int, price_cents: dict, shares: dict):
    out = []
    for s in sorted(set(universe) | set(shares)):
        p = price_cents.get(s)
        tgt = 0 if p is None or p <= 0 else max(0, math.floor(target.get(s, 0.0) * equity_cents / p))
        d = tgt - shares.get(s, 0)
        if d > 0:
            out.append({"symbol": s, "side": "buy", "qty": d, "age": 0})
        elif d < 0:
            out.append({"symbol": s, "side": "sell", "qty": -d, "age": 0})
    return out


def run(world: World, strat: Strategy, params: EngineParams, record_decisions: bool = False, record_fills: bool = False) -> RunResult:
    P, cost, ov = params, params.cost, params.overlay
    N, NW = world.n, world.n_warmup
    res = RunResult(strat.name, world.world_id, P.start_cents, N - NW)
    cash = P.start_cents
    shares = {s: 0 for s in SYMBOLS}
    recv: list = []                                  # [pay_idx, cents]
    pending: list = []
    peak = P.start_cents
    halted = False
    step = 0
    for idx in range(NW, N):
        # ---------------- 1 corporate actions
        outage = bool(ov and ov.outage_prob and _u(world, "out", idx, "*") < ov.outage_prob)
        for a in world.actions_at(idx):
            if a.kind == "split":
                old = shares[a.symbol]
                new = (old * a.num) // a.den
                frac = old * a.num / a.den - new
                shares[a.symbol] = new
                if frac > 0:
                    lieu = _cents(frac * world.open[a.symbol][idx])
                    cash += lieu                                              # cash in lieu of the fractional share
                    if record_fills:
                        res.cash_events.append((idx, "split_lieu", lieu))
                for o in pending:
                    if o["symbol"] == a.symbol:
                        o["qty"] = (o["qty"] * a.num) // a.den
            elif a.kind == "dividend":
                held = shares[a.symbol]
                if held > 0:
                    amt = _cents(held * a.amount)
                    if amt > 0:
                        recv.append([a.pay_idx, amt]); res.dividends_cents += amt
        due_pay = [r for r in recv if r[0] <= idx]
        if due_pay:
            cash += sum(r[1] for r in due_pay)
            if record_fills:
                res.cash_events.append((idx, "dividend_paid", sum(r[1] for r in due_pay)))
            recv = [r for r in recv if r[0] > idx]
        pending = [o for o in pending if o["qty"] > 0]

        # ---------------- 2 execution at the raw open
        if pending and outage:
            res.outage_sessions += 1
            for o in pending:
                o["age"] += 1
        elif pending:
            keep = []

            def defer(o, qty):
                o["qty"] = qty; o["age"] += 1
                if o["age"] > P.max_defer_sessions:
                    res.dropped_after_defer += 1
                else:
                    res.deferred += 1; keep.append(o)

            for o in sorted(pending, key=lambda o: (0 if o["side"] == "sell" else 1, o["symbol"])):
                s = o["symbol"]
                if ov and ov.symbol_halt_prob and _u(world, "halt", idx, s) < ov.symbol_halt_prob:
                    res.halted_symbol_blocks += 1
                    defer(o, o["qty"]); continue                      # trading halt: nothing executes, order waits
                if ov and ov.reject_prob and _u(world, "rej", idx, s) < ov.reject_prob:
                    res.rejected += 1
                    continue                                          # rejected: gone (the next decision re-sizes)
                openp = world.open[s][idx]
                if ov and ov.gap_shock_bps:
                    openp *= 1 + ov.gap_shock_bps * (2 * _u(world, "gap", idx, s) - 1) / 1e4
                oc = max(1, _cents(openp))
                slip = cost.slippage_bps
                if cost.spread_floor:
                    slip = max(slip, world.half_spread_bps[s][idx])    # allowance already covers typical spread: never added on top
                fillp = round(oc * (1 + slip / 1e4)) if o["side"] == "buy" else round(oc * (1 - slip / 1e4))
                q0 = o["qty"]
                q1 = min(q0, shares[s]) if o["side"] == "sell" else q0   # never oversell (excess is dropped, not deferred)
                cap_q = q1
                if P.participation_cap != INF:
                    cap_q = min(cap_q, math.floor(P.participation_cap * world.volume[s][idx]))
                if ov and ov.partial_fill_prob and _u(world, "pf", idx, s) < ov.partial_fill_prob:
                    cap_q = min(cap_q, int(q1 * (0.3 + 0.6 * _u(world, "pff", idx, s))))
                    res.partial_fills += 1
                q2 = max(0, cap_q)
                deferrable = q1 - q2                                      # volume/partial-fill shortfall waits for the next session
                q3 = q2
                if o["side"] == "buy" and q2 > 0:
                    afford = max(0, math.floor(cash / (fillp * (1 + cost.commission_bps / 1e4))))
                    if afford < q2:
                        res.unaffordable_reductions += 1                  # cash-limited remainder is DROPPED (no assumed proceeds)
                        q3 = afford
                if q3 > 0:
                    notional = q3 * fillp
                    fee = round(notional * cost.commission_bps / 1e4)
                    if o["side"] == "buy":
                        cash -= notional + fee; shares[s] += q3
                    else:
                        cash += notional - fee; shares[s] -= q3
                    res.fills += 1; res.commission_cents += fee; res.traded_notional_cents += notional
                    res.slippage_cents += abs(fillp - oc) * q3
                    if record_fills:
                        res.fill_log.append((idx, s, o["side"], q3, fillp, fee))
                if deferrable > 0:
                    defer(o, deferrable)
            pending = keep

        # ---------------- 3 mark at the raw close; cash earns the policy rate
        if P.interest:
            inc = round(cash * world.policy_rate[idx] / 252.0)
            cash += inc; res.interest_cents += inc
            if record_fills:
                res.cash_events.append((idx, "interest", inc))
        pos_val = 0
        for s in SYMBOLS:
            if shares[s]:
                pos_val += shares[s] * _cents(world.close[s][idx])
        recv_val = sum(r[1] for r in recv)
        equity = cash + recv_val + pos_val
        if cash < 0:
            res.violations.append(f"negative cash {cash} at idx {idx}")
        if any(v < 0 for v in shares.values()):
            res.violations.append(f"short position at idx {idx}")
        if not math.isfinite(equity):
            res.violations.append(f"non-finite equity at idx {idx}")
        res.equity.append(equity); res.invested.append(pos_val / equity if equity > 0 else 0.0)
        if equity > peak:
            peak = equity
        dd = equity / peak - 1.0

        # ---------------- 4 halt / decision (information: this close and earlier only)
        target = None
        if outage:
            pass
        elif strat.halt and dd <= -P.halt_pct - 1e-9 and not halted:
            halted = True; target = {}
        elif not halted and strat.due(step, idx, world):
            if ov and ov.stale_feed_prob and _u(world, "stale", idx, "*") < ov.stale_feed_prob:
                res.stale_skips += 1                                # stale/missing feed: the decision is skipped, not guessed
            else:
                hist = {s: world.adjuster.history(s, idx, strat.window) for s in SYMBOLS}
                target = strat.weights(hist, step)
        if target is not None:
            target = _scale_to_ceiling({s: w for s, w in target.items() if s in SYMBOLS}, P.ceiling)
            bad = [s for s, w in target.items() if (not math.isfinite(w)) or w < 0 or w > strat.max_asset_weight + 1e-9]
            if bad or sum(target.values()) > strat.max_total_exposure + 1e-9:
                res.violations.append(f"risk gate violated at idx {idx}: {bad or sum(target.values())}")
                target = {}
            price_c = {s: _cents(world.close[s][idx]) for s in SYMBOLS}
            pending = _orders(target, SYMBOLS, equity, price_c, shares)
            res.orders += len(pending)
            if record_decisions:
                res.decisions.append((idx, step, dict(target)))
        step += 1
    res.halted = halted
    res.final_shares = {s: q for s, q in shares.items() if q}
    res.final_cash_cents = cash
    res.final_receivables_cents = sum(r[1] for r in recv)
    return res


def _u(world: World, tag: str, idx: int, sym: str) -> float:
    return Rng(derive_seed(world.seed, "ovl", tag, idx, sym)).u()
