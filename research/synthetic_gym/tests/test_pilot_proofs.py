import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def test_pilot_semantics_receipts_all_pass_and_are_regenerated():
    p = subprocess.run([sys.executable, str(HERE / "scripts" / "pilot_proofs.py")], capture_output=True, text=True, cwd=str(HERE))
    assert p.returncode == 0, p.stderr
    out = json.loads((HERE / "docs" / "pilot_semantics_receipts.json").read_text())
    assert out["all_pass"] and len(out["scenarios"]) == 5
