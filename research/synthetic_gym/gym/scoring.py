"""Frozen scoring (score-v1). The primary objective, eligibility and tie-breaks are defined in configs/experiment_v1.json
and are implemented here; changing this file changes SCORING_HASH and therefore creates a new experiment version."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from . import stats as st

SCORING_VERSION = "score-v1"
DD_CAP = 0.08
TRAIN_WORLDS = 12


def scoring_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def annualized(final_cents: int, start_cents: int, n_eval: int) -> float:
    if final_cents <= 0:
        return -1.0
    return (final_cents / start_cents) ** (252.0 / n_eval) - 1.0


def levels_with_start(res):
    return [res.start_cents] + list(res.equity)


def world_metrics(res, passive, cash, world) -> dict:
    """metrics for one run in one world; `passive` and `cash` are the matched controls in the SAME world"""
    lv = levels_with_start(res)
    dr = [lv[i] / lv[i - 1] - 1.0 for i in range(1, len(lv))]
    rf = [world.policy_rate[world.n_warmup + i] / 252.0 for i in range(len(dr))]
    ex = [a - b for a, b in zip(dr, rf)]
    sdv = st.sd(ex)
    dd_dev = math.sqrt(sum(min(0.0, x) ** 2 for x in ex) / len(ex)) if ex else 0.0
    a = annualized(res.equity[-1], res.start_cents, res.n_eval)
    ap = annualized(passive.equity[-1], passive.start_cents, passive.n_eval)
    ac = annualized(cash.equity[-1], cash.start_cents, cash.n_eval)
    mdd = st.max_drawdown_from_levels(lv)
    years = res.n_eval / 252.0
    return {
        "world_id": res.world_id, "net_return": res.equity[-1] / res.start_cents - 1.0, "ann_return": a,
        "excess_vs_passive18": a - ap, "excess_vs_cash": a - ac, "passive18_ann_return": ap, "cash_ann_return": ac,
        "max_drawdown": mdd, "recovery_sessions": st.recovery_sessions(lv), "es5_daily": st.expected_shortfall(dr, .05),
        "sharpe": (st.mean(ex) / sdv * math.sqrt(252)) if sdv > 0 else 0.0,
        "sortino": (st.mean(ex) / dd_dev * math.sqrt(252)) if dd_dev > 0 else 0.0,
        "turnover": res.traded_notional_cents / res.start_cents, "turnover_per_year": res.traded_notional_cents / res.start_cents / years,
        "commission_cents": res.commission_cents, "slippage_cents": res.slippage_cents,
        "cost_pct_of_start": (res.commission_cents + res.slippage_cents) / res.start_cents,
        "mean_exposure": st.mean(res.invested), "time_in_cash": sum(1 for x in res.invested if x < 0.01) / len(res.invested),
        "orders": res.orders, "fills": res.fills, "halted": res.halted, "violations": len(res.violations),
        "exec": {"partial_fills": res.partial_fills, "deferred": res.deferred, "dropped_after_defer": res.dropped_after_defer,
                 "unaffordable_reductions": res.unaffordable_reductions, "rejected": res.rejected, "stale_skips": res.stale_skips,
                 "outage_sessions": res.outage_sessions, "halted_symbol_blocks": res.halted_symbol_blocks},
    }


def primary_score(per_world: list) -> float:
    """25th percentile (linear interpolation between order statistics) of annualized excess vs passive18"""
    return st.quantile([m["excess_vs_passive18"] for m in per_world], .25)


def eligibility(per_world: list, dd_cap: float = DD_CAP) -> tuple[bool, list]:
    reasons = []
    for m in per_world:
        if m["violations"]:
            reasons.append(f"{m['world_id']}: {m['violations']} integrity violation(s)")
        if not all(math.isfinite(m[k]) for k in ("net_return", "ann_return", "max_drawdown")):
            reasons.append(f"{m['world_id']}: invalid value")
        if -m["max_drawdown"] > dd_cap + 1e-12:
            reasons.append(f"{m['world_id']}: drawdown {-m['max_drawdown']:.4f} > {dd_cap}")
    return (not reasons), reasons


def summarize(per_world: list) -> dict:
    ok, reasons = eligibility(per_world)
    return {"primary_score": primary_score(per_world), "eligible": ok, "ineligibility": reasons,
            "worst_drawdown": min(m["max_drawdown"] for m in per_world),         # most negative
            "mean_turnover": st.mean([m["turnover"] for m in per_world]),
            "median_net_return": st.quantile([m["net_return"] for m in per_world], .5),
            "worst_net_return": min(m["net_return"] for m in per_world)}


def ordering_key(row: dict):
    """ascending sort => best first. eligible before ineligible; higher primary score; lower worst drawdown magnitude;
    lower turnover; genome id ascending. Floats are rounded so 'exact ties' are well defined."""
    s = row["summary"]
    return (0 if s["eligible"] else 1, -round(s["primary_score"], 12), round(-s["worst_drawdown"], 12), round(s["mean_turnover"], 12), row["genome_id"])
