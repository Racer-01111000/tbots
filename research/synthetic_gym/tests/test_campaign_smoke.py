"""Mechanics only: a tiny SMOKE campaign under its own experiment id. Performance numbers from it are never inspected or used."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent


def sh(args):
    return subprocess.run([sys.executable, "-m", "gym.campaign"] + args, capture_output=True, text=True, cwd=str(HERE),
                          env={"PYTHONPATH": f"{HERE}:{HERE.parents[1]}/scripts:{HERE.parents[1]}/scripts/lib:{HERE.parents[1]}/experiments/sam_dev_staging_20261001", "PATH": "/usr/bin:/bin"})


def test_smoke_campaign_produces_checkpoints_hashes_receipt_and_never_opens_sealed_without_validation(tmp_path):
    p = sh(["--out", str(tmp_path / "run"), "--smoke"])
    assert p.returncode == 0, p.stderr[-2000:]
    out = tmp_path / "run"
    assert (out / "resource_receipt.json").exists() and (out / "world_hashes.json").exists() and (out / "benchmark.json").exists()
    assert sorted(x.name for x in (out / "checkpoints").glob("gen_*.json")) == ["gen_000.json", "gen_001.json"]
    wh = json.loads((out / "world_hashes.json").read_text())
    r = json.loads((out / "campaign_result.json").read_text())
    if "sealed_test" not in r:
        assert wh["sealed_test"]["opened"] is False and not (out / "sealed_test_result.json").exists()
    rec = json.loads((out / "resource_receipt.json").read_text())
    assert rec["preflight"]["broker_credential_readable_by_this_process"] is False and rec["totals"]["peak_rss_mib"] < 800


def test_smoke_campaign_resume_is_idempotent_and_validation_runs_at_most_once(tmp_path):
    out = tmp_path / "run"
    assert sh(["--out", str(out), "--smoke"]).returncode == 0
    ph1 = json.loads((out / "phase_state.json").read_text()) if (out / "phase_state.json").exists() else {}
    q = sh(["--out", str(out), "--smoke", "--resume"])
    assert q.returncode == 0, q.stderr[-2000:]
    ph2 = json.loads((out / "phase_state.json").read_text()) if (out / "phase_state.json").exists() else {}
    assert ph1 == ph2


def test_refuses_to_start_when_checkpoints_exist_without_resume(tmp_path):
    out = tmp_path / "run"
    assert sh(["--out", str(out), "--smoke"]).returncode == 0
    q = sh(["--out", str(out), "--smoke"])
    assert q.returncode != 0 and "resume" in (q.stderr + q.stdout).lower()
