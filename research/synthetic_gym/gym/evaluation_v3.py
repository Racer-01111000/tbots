"""V3 suites: same machinery as evaluation.py but on the repaired generator (gen-v2) and fresh experiment seeds. evaluation.py is untouched."""
from __future__ import annotations
from . import engine as E
from . import evaluation as EV
from . import generator_v2 as G
from . import scoring as SC
from . import strategies as S

EXP_ID = "SYNGYM-20261005-V3R"      # V3R: fresh seeds after a discarded smoke run touched the V3 worlds (see docs/V3_NOTES.md)
SPLIT_PER_FAMILY = EV.SPLIT_PER_FAMILY


def suite_specs(split, exp_id=None):
    exp_id = exp_id or EXP_ID
    return [G.WorldSpec(exp_id, split, fam, i) for fam in G.FAMILIES for i in range(SPLIT_PER_FAMILY[split])]


class Suite(EV.Suite):
    def __init__(self, split, cal, exp_id=None):
        exp_id = exp_id or EXP_ID
        self.split = split; self.specs = suite_specs(split, exp_id)
        self.worlds = [G.generate_world(sp, cal) for sp in self.specs]; self._controls = {}


evaluate = EV.evaluate
Evaluator = EV.Evaluator
E_PARAMS = lambda: E.EngineParams(participation_cap=G.CFG["volume_participation_cap"])
