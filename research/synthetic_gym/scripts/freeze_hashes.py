"""Records the code/spec/scoring/generator hashes and the expected training+validation world hashes. Run ONCE, immediately before the
campaign; the campaign refuses to start if any hash differs. Sealed-test worlds are NOT generated here (their seeds are recorded only)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(REPO / "scripts"), str(REPO / "scripts" / "lib"), str(REPO / "experiments" / "sam_dev_staging_20261001")]
from gym import calibration as C, evaluation as EV, generator as G

data = REPO / "data" / "normalized"
cal = C.build(data)
hashes = EV.code_hashes(HERE, C.dev_data_digest(data))
(HERE / "configs" / "FROZEN_HASHES.json").write_text(json.dumps(hashes, indent=1, sort_keys=True))
tr, va = EV.Suite("training", cal), EV.Suite("validation", cal)
exp = {"experiment_id": EV.EXP_ID, "training": tr.world_hashes(), "validation": va.world_hashes(),
       "sealed_test_seeds_only": {sp.world_id: sp.seed for sp in EV.suite_specs("sealed_test")}}
(HERE / "configs" / "EXPECTED_WORLD_HASHES.json").write_text(json.dumps(exp, indent=1, sort_keys=True))
print(json.dumps(hashes, indent=1))
print("training worlds:", len(exp["training"]), "validation worlds:", len(exp["validation"]), "sealed seeds recorded:", len(exp["sealed_test_seeds_only"]))
