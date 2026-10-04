"""Records SHA-256 of every file batch_v5 depends on (including the admission record and its report), BEFORE any V5 score exists."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent; REPO = HERE.parents[1]
for p in (str(HERE), str(REPO / "scripts"), str(REPO / "scripts" / "lib"), str(REPO / "experiments" / "sam_dev_staging_20261001")): sys.path.insert(0, p)
from gym.batch_v5 import code_hashes
adm = json.loads((HERE / "configs" / "ADMISSION_V5.json").read_text()); assert adm["generator_admission_pass"] is True, "refusing to freeze an experiment whose admission did not pass"
json.dump({"experiment_id": "SYNGYM-20261005-V5", "note": "recorded BEFORE any V5 score exists; batch_v5 refuses to run if any listed file differs or is missing", "files": code_hashes()}, open(HERE / "configs" / "FROZEN_HASHES_V5.json", "w"), indent=1)
print("frozen", len(code_hashes()))
