"""Reference genomes, strategy wrappers (accepted decision code, unmodified), controls, and bounded mutation.

TREND family (the champion's): decision = control_agent.decide(view, genome); schema = s5a_config.validate_genome;
mutation = s5a_config.mutate_genome (frozen S5A protocol).
D family (Sam, D-primary-rank3): decision = s6a_runtime.decide_D(genome, history, step); schema = s6a_runtime.validate_genome("D");
mutation = s6a_runtime.mutate("D", ...).  Nothing here reimplements a decision rule."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import control_agent
import s5a_config
import s6a_final as p6
import s6a_runtime as r6
from lib.ids import genome_id

from .engine import Strategy
from .world import SYMBOLS

REPO = Path(__file__).resolve().parents[3]
CHAMPION_ID = "gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155"
SAM_ID = "gen_8e7a622b48276bc68b7b70773bb9e2a5250dbb82584c53f6c67416bd6ffdf0cf"
D3_ID = "gen_d890eb9faa7bf0309773c85620eb6652ad589ab82d6fbff4423ddcfbbb460f85"
D_FILE = REPO / "evolution/s6b_runs/primary/D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca/development_top_eight.json"
CHAMPION_FILE = REPO / "experiments/sam_dev_staging_20261001/selected_development_bot.json"
MAX_LOOKBACK_BARS = max(s5a_config.MAX_LEGAL_PRICE_BARS, p6.SCHEMAS["D"]["properties"]["regime_window"]["max"] + 1)


class IdentityError(RuntimeError):
    pass


class MutationFailed(RuntimeError):
    pass


def independent_id(g: dict) -> str:
    return "gen_" + hashlib.sha256(json.dumps(g, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate(family: str, g: dict) -> str:
    """validate through the family's ACTUAL frozen schema; return the recomputed identity"""
    if family == "TREND":
        s5a_config.validate_genome(g)
    elif family == "D":
        r6.validate_genome("D", g)
    else:
        raise IdentityError(f"unsupported family {family}")
    gid = genome_id(g)
    if independent_id(g) != gid:
        raise IdentityError("independent hash disagrees with lib.ids")
    return gid


def load_references() -> dict:
    out = {}
    ch = json.loads(CHAMPION_FILE.read_text())["selected_genome_definition"]
    out["champion"] = {"family": "TREND", "genome": ch, "genome_id": CHAMPION_ID}
    d = json.loads(D_FILE.read_text())
    for name, gid in (("sam", SAM_ID), ("d_primary_rank3", D3_ID)):
        out[name] = {"family": "D", "genome": next(e for e in d["frozen"] if e["genome_id"] == gid)["genome"], "genome_id": gid}
    for name, e in out.items():
        if validate(e["family"], e["genome"]) != e["genome_id"]:
            raise IdentityError(f"{name}: recomputed identity differs from {e['genome_id']}")
    return out


class _HistView:
    def __init__(self, adj_hist):
        self._h = adj_hist

    def observe(self):
        return {"assets": {s: {"available": bool(self._h.get(s)), "adjusted_close": (self._h[s][-1] if self._h.get(s) else None)} for s in SYMBOLS}}

    def history(self, symbol, bars):
        rows = self._h.get(symbol, [])
        return [{"adjusted_close": v} for v in (rows[-bars:] if bars > 0 else [])]


def required_price_bars(g: dict) -> int:
    """same three-term rule as s5b_config.required_price_bars, restated here so this gym never imports the
    qualification-lane config (protected-lane separation)"""
    return max(g["momentum_lookbacks"][2] + 1, g["trend_filter_window"], g["volatility_window"] + 1)


def strategy_for(name: str, family: str, g: dict) -> Strategy:
    gid = genome_id(g)
    if family == "TREND":
        n = g["rebalance_every_n_sessions"]
        win = required_price_bars(g) + 1
        return Strategy(name, gid, lambda step, idx, w, n=n: step % n == 0,
                        lambda h, step, g=g: control_agent.decide(_HistView(h), g)["weights"], win,
                        g["max_asset_weight"], g["target_max_exposure"])
    if family == "D":
        n = g["rebalance_sessions"]
        win = r6.warmup("D", g) + 1

        def weights(h, step, g=g):
            w = r6.decide_D(g, h, step)
            return None if w is None else {s: v for s, v in w.items() if s in SYMBOLS}
        return Strategy(name, gid, lambda step, idx, w, n=n: step % n == 0, weights, win, g["max_asset_weight"], g["gross_exposure_cap"])
    raise IdentityError(family)


def passive18() -> Strategy:
    w = {s: 0.18 / len(SYMBOLS) for s in SYMBOLS}
    return Strategy("passive18", "passive18_equal_monthly", lambda step, idx, wd: step == 0 or idx in wd._month_starts,
                    lambda h, step: dict(w), 2, 0.18 / len(SYMBOLS) + 1e-9, 0.18 + 1e-9, halt=False)


def cash_control() -> Strategy:
    return Strategy("cash", "cash_only", lambda step, idx, wd: False, lambda h, step: None, 2, 1.0, 1.0, halt=False)


def mutate_child(family: str, parent: dict, seed: int) -> dict:
    """one bounded mutation inside the family's frozen domain; raises MutationFailed (never loops)"""
    try:
        if family == "TREND":
            child, _, _ = s5a_config.mutate_genome(parent, random.Random(seed))
            s5a_config.validate_genome(child)
            return child
        if family == "D":
            child = r6.mutate("D", parent, seed)
            r6.validate_genome("D", child)
            return child
    except (s5a_config.ProtocolError, r6.S6Error) as e:
        raise MutationFailed(f"{family}: {e}") from e
    raise MutationFailed(f"unsupported family {family}")
