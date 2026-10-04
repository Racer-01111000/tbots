import json, os, signal, threading
from datetime import datetime, timezone
from pathlib import Path
import pytest
from gym import batch_v3 as B, evolve as EVO, evaluation_v3 as EV3, checkpoint as CK


@pytest.fixture(autouse=True)
def small(monkeypatch):
    monkeypatch.setattr(EVO, "POP", 8); monkeypatch.setattr(EVO, "N_TREND0", 2); monkeypatch.setattr(EVO, "N_D0", 3); monkeypatch.setattr(EVO, "SURVIVORS", 2)
    monkeypatch.setattr(EV3, "EXP_ID", "TEST-V3-BATCH")
    monkeypatch.setattr(EV3, "SPLIT_PER_FAMILY", {"training": 1, "validation": 1, "sealed_test": 1})
    import gym.evaluation as EV; monkeypatch.setattr(EV, "SPLIT_PER_FAMILY", {"training": 1, "validation": 1, "sealed_test": 1})
    monkeypatch.setattr(B, "FROZEN_FILES", B.FROZEN_FILES)


def at(day, h=5, m=0): return datetime(2026, 10, day, h + 4, m, tzinfo=timezone.utc)


def test_batches_continue_generations_and_one_per_session(tmp_path):
    r1 = B.run(tmp_path, now=at(5), gens=2)
    assert r1["outcome"] == "COMPLETED" and r1["batch"]["generations_completed_total"] == 2
    assert B.run(tmp_path, now=at(5, 6), gens=2)["outcome"] == "SKIPPED_ALREADY_RAN_THIS_SESSION"
    r2 = B.run(tmp_path, now=at(6), gens=2)
    assert r2["batch"]["first_generation_this_batch"] == 2 and r2["batch"]["generations_completed_total"] == 4
    assert CK.list_generations(tmp_path / "evolution" / "checkpoints") == [0, 1, 2, 3]


def test_weekend_holiday_and_expiry_skip(tmp_path):
    assert B.run(tmp_path, now=at(4))["outcome"] == "SKIPPED_NOT_A_TRADING_DAY"
    assert B.run(tmp_path, now=datetime(2026, 10, 9, 21, 1, tzinfo=timezone.utc))["outcome"] == "SKIPPED_EXPIRED"


def test_overlap_is_skipped_not_run(tmp_path):
    import fcntl
    tmp_path.mkdir(exist_ok=True); f = open(tmp_path / "writer.lock", "w"); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert B.run(tmp_path, now=at(5))["outcome"].startswith("SKIPPED_OVERLAP")


def test_interrupt_checkpoints_and_resumes(tmp_path, monkeypatch):
    calls = {"n": 0}; real = EV3.Evaluator.__call__
    def flaky(self, cand):
        calls["n"] += 1
        if calls["n"] == 12: os.kill(os.getpid(), signal.SIGTERM)          # arrives mid-generation
        return real(self, cand)
    monkeypatch.setattr(EV3.Evaluator, "__call__", flaky)
    r = B.run(tmp_path, now=at(5), gens=3)
    assert r["outcome"] == "INTERRUPTED_CHECKPOINTED"
    done = CK.list_generations(tmp_path / "evolution" / "checkpoints"); assert done and len(done) < 3
    monkeypatch.setattr(EV3.Evaluator, "__call__", real)
    r2 = B.run(tmp_path, now=at(6), gens=2); assert r2["outcome"] == "COMPLETED" and r2["batch"]["first_generation_this_batch"] == done[-1] + 1


def test_final_validation_exactly_once_and_no_feedback_into_evolution(tmp_path):
    r = B.run(tmp_path, now=at(5), gens=2, final=True)
    assert "qualification" in r
    p = json.loads((tmp_path / "phase_state.json").read_text()) if (tmp_path / "phase_state.json").exists() else {}
    if r.get("validation") is not None:
        assert p["validation_done"] is True
        before = (tmp_path / "validation_result.json").read_bytes()
        r2 = B.run(tmp_path, now=at(6), gens=1, final=True)
        assert (tmp_path / "validation_result.json").read_bytes() == before and "already executed" in r2["qualification"]
    # validation never touches the evolver's checkpoints
    assert not any("validation" in p.name for p in (tmp_path / "evolution").rglob("*") if p.is_file())


def test_hash_mismatch_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "code_hashes", lambda: {"gym/engine.py": "0" * 64})
    fz = B.HERE / "configs" / "FROZEN_HASHES_V3.json"
    if not fz.exists(): pytest.skip("freeze file not present yet")
    assert B.run(tmp_path, now=at(5), gens=1)["outcome"] == "REFUSED_FROZEN_HASH_MISMATCH"
