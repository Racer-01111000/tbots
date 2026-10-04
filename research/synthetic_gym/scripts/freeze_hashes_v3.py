"""Records the SHA-256 of every file the V3 batch depends on, BEFORE any V3 score exists."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent; REPO = HERE.parents[1]
for p in (str(HERE), str(REPO / "scripts"), str(REPO / "scripts" / "lib"), str(REPO / "experiments" / "sam_dev_staging_20261001")): sys.path.insert(0, p)
from gym.batch_v3 import code_hashes
json.dump({"experiment_id": "SYNGYM-20261005-V3R", "note": "recorded BEFORE any V3 score exists; batch_v3 refuses to run if any listed file differs", "files": code_hashes()}, open(HERE / "configs" / "FROZEN_HASHES_V3.json", "w"), indent=1)
print("frozen", len(code_hashes()))
