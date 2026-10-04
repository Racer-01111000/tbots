"""World suites, the real evaluator, frozen-hash bundle and reference/robustness reports."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from . import engine as E
from . import generator as G
from . import scoring as SC
from . import strategies as S
from .world import SYMBOLS

HERE = Path(__file__).resolve().parent
EXP_ID = "SYNGYM-20261004-V1"
FAMILY_ORDER = list(G.FAMILIES)
SPLIT_PER_FAMILY = {"training": 2, "validation": 1, "sealed_test": 1}


def suite_specs(split: str, exp_id: str = EXP_ID) -> list:
    return [G.WorldSpec(exp_id, split, fam, i) for fam in FAMILY_ORDER for i in range(SPLIT_PER_FAMILY[split])]


class Suite:
    def __init__(self, split: str, cal: dict, exp_id: str = EXP_ID):
        self.split = split
        self.specs = suite_specs(split, exp_id)
        self.worlds = [G.generate_world(sp, cal) for sp in self.specs]
        self._controls = {}

    def controls(self, params: E.EngineParams):
        key = (params.cost.label, params.ceiling, params.interest, params.overlay.name if params.overlay else None)
        if key not in self._controls:
            self._controls[key] = [(E.run(w, S.passive18(), params), E.run(w, S.cash_control(), params)) for w in self.worlds]
        return self._controls[key]

    def world_hashes(self):
        return {w.world_id: w.content_hash() for w in self.worlds}


def evaluate(entry: dict, suite: Suite, params: E.EngineParams) -> dict:
    strat = S.strategy_for(entry.get("name", "x"), entry["family"], entry["genome"])
    ctrls = suite.controls(params)
    per = []
    for w, (pas, csh) in zip(suite.worlds, ctrls):
        res = E.run(w, strat, params)
        per.append(SC.world_metrics(res, pas, csh, w))
    return {"per_world": per, "summary": SC.summarize(per)}


class Evaluator:
    def __init__(self, suite: Suite, params: E.EngineParams):
        self.suite, self.params = suite, params

    def __call__(self, cand: dict) -> dict:
        return evaluate(cand, self.suite, self.params)


# ----------------------------------------------------------------------------- frozen hashes
def _h(paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(Path(p).name.encode()); h.update(Path(p).read_bytes())
    return h.hexdigest()


def code_hashes(root: Path, data_digest: str) -> dict:
    g = root / "gym"
    return {
        "spec": _h([root / "configs" / "experiment_v1.json"]),
        "scoring": SC.scoring_hash(),
        "generator": _h([g / "generator.py", g / "factors.py", g / "world.py", g / "sessions.py", g / "rng.py", g / "calibration.py", root / "configs" / "generator_v1.json"]) ,
        "engine": _h([g / "engine.py"]), "strategies": _h([g / "strategies.py"]), "evolve": _h([g / "evolve.py", g / "checkpoint.py"]),
        "calibration_data_digest": data_digest,
    }


# ----------------------------------------------------------------------------- robustness / reference reports
COST_CASES = [E.CostCase(10, 5), E.CostCase(5, 5), E.CostCase(25, 5)]


def cost_sensitivity(entries: dict, suite: Suite, base: E.EngineParams) -> dict:
    out = {}
    for case in COST_CASES:
        p = replace(base, cost=case)
        out[case.label] = {n: evaluate(e, suite, p)["summary"] | {"per_world_excess": [m["excess_vs_passive18"] for m in evaluate(e, suite, p)["per_world"]]} for n, e in entries.items()}
    return out


def native_sensitivity(entries: dict, suite: Suite, base: E.EngineParams) -> dict:
    p = replace(base, ceiling=None)
    return {n: evaluate(e, suite, p)["summary"] for n, e in entries.items()}


def by_family(per_world: list) -> dict:
    out = {}
    for m in per_world:
        fam = m["world_id"].split(":")[2]
        out.setdefault(fam, []).append(m["excess_vs_passive18"])
    return {k: sum(v) / len(v) for k, v in out.items()}


STRESS = [E.StressOverlay(partial_fill_prob=.15, reject_prob=.03, stale_feed_prob=.05, symbol_halt_prob=.02, outage_prob=.01, gap_shock_bps=40, name="moderate_stress"),
          E.StressOverlay(partial_fill_prob=.35, reject_prob=.10, stale_feed_prob=.15, symbol_halt_prob=.06, outage_prob=.03, gap_shock_bps=120, name="severe_stress")]


def stress_report(entries: dict, suite: Suite, base: E.EngineParams) -> dict:
    out = {}
    for ov in STRESS:
        p = replace(base, overlay=ov)
        out[ov.name] = {}
        for n, e in entries.items():
            strat = S.strategy_for(n, e["family"], e["genome"])
            ctrls = suite.controls(p)
            agg = {"partial_fills": 0, "rejected": 0, "stale_skips": 0, "outage_sessions": 0, "halted_symbol_blocks": 0, "deferred": 0, "violations": 0}
            per = []
            for w, (pas, csh) in zip(suite.worlds, ctrls):
                res = E.run(w, strat, p)
                m = SC.world_metrics(res, pas, csh, w); per.append(m)
                for k in agg:
                    agg[k] += (m["exec"].get(k, 0) if k != "violations" else m["violations"])
            out[ov.name][n] = {"summary": SC.summarize(per), "execution_failure_counts": agg}
    return out
