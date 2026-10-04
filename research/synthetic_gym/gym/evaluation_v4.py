"""V4 suites and evaluator: generator gen-v3 (anchor events), engine_v4 (missing-price abstention), bounded and resumable world admission."""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import engine_v4 as E
from . import evaluation as EV
from . import generator_v3 as G
from . import scoring as SC
from . import strategies as S
from . import validation_v4 as V4
from .world import check_invariants

EXP_ID = "SYNGYM-20261005-V4"
SPLIT_PER_FAMILY = {"training": 2, "validation": 1, "sealed_test": 1}
MAX_ATTEMPTS_PER_SLOT = 3
WORLD_BUDGET_SECONDS = 600.0


class InvalidWorldBank(RuntimeError):
    pass


def _structurally_valid(w) -> list:
    return check_invariants(w) + [v for _, v in V4.structure_checks([w])["violations"]]


def admit_suite(split: str, cal: dict, record_path: Path | None = None, exp_id: str | None = None, budget_s: float = WORLD_BUDGET_SECONDS):
    """Builds a suite. A world that is STRUCTURALLY invalid (non-finite/illegal prices, calendar or corporate-action incoherence) is rejected and replaced by the next
    deterministic seed slot (index + 1000*attempt); difficulty is never a rejection reason. Attempts and reasons persist so an interrupted build resumes identically."""
    exp_id = exp_id or EXP_ID
    rec = json.loads(record_path.read_text()) if record_path and record_path.exists() else {"split": split, "attempts": [], "admitted": {}}
    t0 = time.monotonic(); worlds = []
    for fam in G.FAMILIES:
        for i in range(SPLIT_PER_FAMILY[split]):
            key = f"{fam}:{i}"; done = None
            for attempt in range(MAX_ATTEMPTS_PER_SLOT):
                if time.monotonic() - t0 > budget_s: raise InvalidWorldBank(f"world construction exceeded {budget_s}s at {key}; attempts persisted")
                spec = G.WorldSpec(exp_id, split, fam, i + 1000 * attempt)
                w = G.generate_world(spec, cal); bad = _structurally_valid(w)
                if not bad:
                    rec["admitted"][key] = {"world_id": w.world_id, "seed": spec.seed, "attempt": attempt, "hash": w.content_hash()}; worlds.append(w); done = w; break
                rec["attempts"].append({"slot": key, "world_id": w.world_id, "seed": spec.seed, "attempt": attempt, "reasons": bad[:5]})
            if done is None:
                if record_path: record_path.write_text(json.dumps(rec, indent=1))
                raise InvalidWorldBank(f"{key}: no structurally valid world in {MAX_ATTEMPTS_PER_SLOT} deterministic attempts (reasons persisted)")
    if record_path: record_path.write_text(json.dumps(rec, indent=1))
    suite = EV.Suite.__new__(EV.Suite); suite.split = split; suite.specs = [G.WorldSpec(exp_id, split, w.family, int(w.world_id.rsplit(":", 1)[1])) for w in worlds]
    suite.worlds = worlds; suite._controls = {}; suite.admission = rec
    return suite


def evaluate(entry: dict, suite, params) -> dict:
    strat = S.strategy_for(entry.get("name", "x"), entry["family"], entry["genome"])
    ctrls = suite.controls(params) if hasattr(suite, "controls") else None
    per = []
    for w, (pas, csh) in zip(suite.worlds, ctrls):
        per.append(SC.world_metrics(E.run(w, strat, params), pas, csh, w))
    return {"per_world": per, "summary": SC.summarize(per)}


class SuiteControls:
    """controls under engine_v4"""
    @staticmethod
    def attach(suite):
        def controls(params):
            key = (params.cost.label, params.ceiling, params.interest, params.participation_cap)
            if key not in suite._controls:
                suite._controls[key] = [(E.run(w, S.passive18(), params), E.run(w, S.cash_control(), params)) for w in suite.worlds]
            return suite._controls[key]
        suite.controls = controls
        return suite


class Evaluator:
    def __init__(self, suite, params): self.suite, self.params = SuiteControls.attach(suite), params
    def __call__(self, cand): return evaluate(cand, self.suite, self.params)


E_PARAMS = lambda: E.EngineParams(participation_cap=G.CFG["volume_participation_cap"])
