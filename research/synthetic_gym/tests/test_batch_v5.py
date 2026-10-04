import json
from datetime import datetime, timezone
import pytest
from gym import batch_v5 as B, evolve as EVO, evaluation_v5 as EV4, checkpoint as CK, generator_v4 as G


@pytest.fixture(autouse=True)
def small(monkeypatch, tmp_path):
    monkeypatch.setattr(EVO, "POP", 8); monkeypatch.setattr(EVO, "N_TREND0", 2); monkeypatch.setattr(EVO, "N_D0", 3); monkeypatch.setattr(EVO, "SURVIVORS", 2)
    monkeypatch.setattr(EV4, "EXP_ID", "TEST-V5-BATCH"); monkeypatch.setattr(EV4, "SPLIT_PER_FAMILY", {"training": 1, "validation": 1, "sealed_test": 1})
    adm = tmp_path / "ADM.json"; adm.write_text(json.dumps({"experiment_id": EV4.EXP_ID, "generator_admission_pass": True})); monkeypatch.setattr(B, "ADMISSION", adm)
    fz = tmp_path / "FZ.json"; fz.write_text(json.dumps({"experiment_id": EV4.EXP_ID, "files": B.code_hashes()})); monkeypatch.setattr(B, "FROZEN", fz)
    monkeypatch.setattr(B, "FROZEN_FILES", B.FROZEN_FILES)


def at(day, h=5): return datetime(2026, 10, day, h + 4, 0, tzinfo=timezone.utc)


def test_refused_without_generator_admission(tmp_path, monkeypatch):
    bad = tmp_path / "BAD.json"; bad.write_text(json.dumps({"experiment_id": EV4.EXP_ID, "generator_admission_pass": False, "failed_gates": ["x"]})); monkeypatch.setattr(B, "ADMISSION", bad)
    assert B.run(tmp_path / "s", now=at(5), gens=1)["outcome"] == "REFUSED_GENERATOR_ADMISSION_NOT_PASSED"
    assert not (tmp_path / "s" / "evolution").exists()


def test_committed_admission_record_is_a_passing_report_bound_to_this_experiment():
    rec = json.loads((B.HERE / "configs" / "ADMISSION_V5.json").read_text())
    assert rec["experiment_id"] == "SYNGYM-20261005-V5" and rec["generator_admission_pass"] is True and not rec["failed_gates"]
    import hashlib; assert hashlib.sha256((B.HERE / rec["report"]).read_bytes()).hexdigest() == rec["report_sha256"]


def test_early_batch_consumes_slot_and_scheduled_trigger_skips_without_duplicate(tmp_path):
    s = tmp_path / "s"
    r = B.run(s, now=at(4, 14), gens=2, slot="2026-10-05")                   # Sunday, early run assigned to Monday's planned slot
    assert r["outcome"] == "COMPLETED" and r["batch"]["batch_id"].endswith("-B1") and r["batch"]["evaluation_budget"]["generation_zero_included"] is True
    led = json.loads((s / "slots.json").read_text()); assert led["2026-10-05"]["early"] is True and led["2026-10-05"]["status"] == "COMPLETED"
    again = B.run(s, now=at(5), gens=2)
    assert again["outcome"] == "SKIPPED_SLOT_ALREADY_CONSUMED" and again["batch_id"] == r["batch"]["batch_id"]
    assert CK.list_generations(s / "evolution" / "checkpoints") == [0, 1]
    nxt = B.run(s, now=at(6), gens=1); assert nxt["batch"]["first_generation_this_batch"] == 2 and nxt["batch"]["batch_id"].endswith("-B2")


def test_weekly_budget_is_five_slots(tmp_path):
    s = tmp_path / "s"
    for d in range(5, 10): assert B.run(s, now=at(d), gens=1, enforce_session=False)["outcome"] in ("COMPLETED",)
    assert B.run(s, now=at(9, 6), gens=1)["outcome"] in ("SKIPPED_SLOT_ALREADY_CONSUMED", "SKIPPED_WEEKLY_BUDGET_EXHAUSTED")


def test_status_and_publication_are_reconciled_and_distinguish_local_from_remote(tmp_path):
    s = tmp_path / "s"; B.run(s, now=at(5), gens=1)
    st = json.loads((s / "status.json").read_text()); pub = json.loads((s / "publication.json").read_text())
    assert st["training_success"] is True and st["completed_evaluations_this_batch"] > 0 and st["interpreter"]["version"].startswith("3.")
    assert pub["remote_publication"].startswith("PENDING_RELAY") and pub["evaluations_repeated_on_publication_failure"] is False


def test_world_construction_budget_persists_and_resumes_deterministically(tmp_path):
    from gym import calibration as C
    cal = C.build(B.REPO / "data" / "normalized"); rec = tmp_path / "adm.json"
    with pytest.raises(EV4.InvalidWorldBank): EV4.admit_suite("training", cal, record_path=None, exp_id="TEST-V5-BUDGET", budget_s=0.0)
    a = EV4.admit_suite("training", cal, record_path=rec, exp_id="TEST-V5-BUDGET"); b = EV4.admit_suite("training", cal, record_path=rec, exp_id="TEST-V5-BUDGET")
    assert a.world_hashes() == b.world_hashes() and json.loads(rec.read_text())["admitted"]


def test_structurally_invalid_world_is_rejected_with_reason_and_next_seed_used(tmp_path, monkeypatch):
    from gym import calibration as C
    cal = C.build(B.REPO / "data" / "normalized"); real = G.generate_world; calls = {"n": 0}
    def broken(spec, cal_):
        w = real(spec, cal_); calls["n"] += 1
        if calls["n"] == 1: w.close["SPY"][5] = float("nan")
        return w
    monkeypatch.setattr(G, "generate_world", broken); rec = tmp_path / "adm.json"
    s = EV4.admit_suite("training", cal, record_path=rec, exp_id="TEST-V5-REJECT")
    r = json.loads(rec.read_text()); assert len(r["attempts"]) == 1 and r["attempts"][0]["reasons"] and len(s.worlds) == 6


# ------------------------------------------------------------------ V5: the crash windows and freeze gates found in review
def test_missing_or_mismatched_freeze_manifest_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "FROZEN", tmp_path / "absent.json")
    assert B.run(tmp_path / "s", now=at(5), gens=1)["outcome"] == "REFUSED_FROZEN_MANIFEST_MISSING"
    wrong = tmp_path / "w.json"; wrong.write_text(json.dumps({"experiment_id": "OTHER", "files": {}})); monkeypatch.setattr(B, "FROZEN", wrong)
    assert B.run(tmp_path / "s", now=at(5), gens=1)["outcome"] == "REFUSED_FROZEN_MANIFEST_IDENTITY_MISMATCH"
    h = dict(B.code_hashes()); h["gym/engine.py"] = "0" * 64; wrong.write_text(json.dumps({"experiment_id": EV4.EXP_ID, "files": h}))
    r = B.run(tmp_path / "s", now=at(5), gens=1); assert r["outcome"] == "REFUSED_FROZEN_HASH_MISMATCH" and "gym/engine.py" in r["files"]
    h = dict(B.code_hashes()); h.pop("gym/engine.py"); wrong.write_text(json.dumps({"experiment_id": EV4.EXP_ID, "files": h}))
    assert B.run(tmp_path / "s", now=at(5), gens=1)["outcome"] == "REFUSED_FROZEN_HASH_MISMATCH"        # a file missing from the manifest is also a mismatch


def test_admission_for_a_different_experiment_refuses(tmp_path, monkeypatch):
    bad = tmp_path / "OTHER.json"; bad.write_text(json.dumps({"experiment_id": "SYNGYM-20261005-V4", "generator_admission_pass": True})); monkeypatch.setattr(B, "ADMISSION", bad)
    assert B.run(tmp_path / "s", now=at(5), gens=1)["outcome"] == "REFUSED_GENERATOR_ADMISSION_NOT_PASSED"


def test_interrupted_batch_resumes_same_target_and_cannot_be_extended(tmp_path, monkeypatch):
    import os, signal
    calls = {"n": 0}; real = EV4.Evaluator.__call__
    def flaky(self, cand):
        calls["n"] += 1
        if calls["n"] == 12: os.kill(os.getpid(), signal.SIGTERM)
        return real(self, cand)
    monkeypatch.setattr(EV4.Evaluator, "__call__", flaky)
    s = tmp_path / "s"; r = B.run(s, now=at(5), gens=3, slot="2026-10-05"); assert r["outcome"] == "INTERRUPTED_CHECKPOINTED"
    led = json.loads((s / "slots.json").read_text())["2026-10-05"]; assert led["status"] == "RUNNING" and led["target_generation_exclusive"] == 3
    monkeypatch.setattr(EV4.Evaluator, "__call__", real)
    r2 = B.run(s, now=at(5, 6), gens=50, slot="2026-10-05")                        # a larger --gens on resume must NOT extend the frozen target
    assert r2["outcome"] == "COMPLETED" and r2["batch"]["generations_completed_total"] == 3
    assert CK.list_generations(s / "evolution" / "checkpoints") == [0, 1, 2]


def test_validation_only_on_friday_slot_regardless_of_final_flag(tmp_path):
    s = tmp_path / "s"; r = B.run(s, now=at(5), gens=1, final=True, slot="2026-10-05")
    assert "qualification" not in r and not (s / "validation_result.json").exists() and not (s / "phase_state.json").exists()


def test_world_build_interrupt_persists_and_resumes_identically(tmp_path):
    from gym import calibration as C
    cal = C.build(B.REPO / "data" / "normalized"); rec = tmp_path / "adm.json"; n = {"k": 0}
    def stop_after_three(): n["k"] += 1; return n["k"] > 3
    with pytest.raises(EV4.WorldBuildInterrupted): EV4.admit_suite("training", cal, record_path=rec, exp_id="TEST-V5-INT", stop_check=stop_after_three)
    saved = json.loads(rec.read_text()); assert saved["admitted"] and "in_progress" in saved
    a = EV4.admit_suite("training", cal, record_path=rec, exp_id="TEST-V5-INT"); b = EV4.admit_suite("training", cal, record_path=tmp_path / "fresh.json", exp_id="TEST-V5-INT")
    assert a.world_hashes() == b.world_hashes()


def _friday_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "PLANNED_SLOTS", ["2026-10-09"])
    return tmp_path / "s"


def test_validation_exposure_is_recorded_and_recovery_never_reselects(tmp_path, monkeypatch):
    s = _friday_ready(tmp_path, monkeypatch)
    r = B.run(s, now=at(9), gens=2)
    if "validation" not in r: pytest.skip("no eligible nominee in this tiny run (qualification unavailable path)")
    ph = json.loads((s / "phase_state.json").read_text())
    assert ph["validation_started"] and ph["validation_done"] and ph["nominee_genome_id"] and ph["selection_checkpoint_sha256"] and "never rerun selection" in ph["recovery_rule"]


def test_validation_recovery_identity_mismatch_blocks(tmp_path, monkeypatch):
    s = _friday_ready(tmp_path, monkeypatch)
    r = B.run(s, now=at(9), gens=2)
    if "validation" not in r: pytest.skip("no eligible nominee")
    ph = json.loads((s / "phase_state.json").read_text()); ph.update(validation_done=False, nominee_genome_id="gen_other"); (s / "phase_state.json").write_text(json.dumps(ph))
    led = json.loads((s / "slots.json").read_text()); led["2026-10-09"]["status"] = "RUNNING"; (s / "slots.json").write_text(json.dumps(led))
    r2 = B.run(s, now=at(9, 6), gens=2)
    assert "BLOCKED" in r2.get("qualification", "") or "VALIDATION_RECOVERY_IDENTITY_MISMATCH" in str(r2)


def test_terminal_artifacts_reconcile_without_repeating_evaluations(tmp_path):
    s = tmp_path / "s"; r = B.run(s, now=at(5), gens=1, slot="2026-10-05"); assert r["outcome"] == "COMPLETED"
    n_ck = CK.list_generations(s / "evolution" / "checkpoints")
    (s / "publication.json").unlink(); (s / "status.json").unlink()
    r2 = B.run(s, now=at(5, 6), gens=1)
    assert r2["outcome"] == "SKIPPED_SLOT_ALREADY_CONSUMED" and r2["terminal_artifacts_reconciled"] is True
    assert (s / "publication.json").exists() and (s / "status.json").exists() and CK.list_generations(s / "evolution" / "checkpoints") == n_ck
    assert json.loads((s / "status.json").read_text())["completed_evaluations_total"] > 0
