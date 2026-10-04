import json
from datetime import datetime, timezone
import pytest
from gym import batch_v4 as B, evolve as EVO, evaluation_v4 as EV4, checkpoint as CK, generator_v3 as G


@pytest.fixture(autouse=True)
def small(monkeypatch, tmp_path):
    monkeypatch.setattr(EVO, "POP", 8); monkeypatch.setattr(EVO, "N_TREND0", 2); monkeypatch.setattr(EVO, "N_D0", 3); monkeypatch.setattr(EVO, "SURVIVORS", 2)
    monkeypatch.setattr(EV4, "EXP_ID", "TEST-V4-BATCH"); monkeypatch.setattr(EV4, "SPLIT_PER_FAMILY", {"training": 1, "validation": 1, "sealed_test": 1})
    adm = tmp_path / "ADM.json"; adm.write_text(json.dumps({"generator_admission_pass": True})); monkeypatch.setattr(B, "ADMISSION", adm)
    monkeypatch.setattr(B, "FROZEN_FILES", B.FROZEN_FILES)


def at(day, h=5): return datetime(2026, 10, day, h + 4, 0, tzinfo=timezone.utc)


def test_refused_without_generator_admission(tmp_path, monkeypatch):
    bad = tmp_path / "BAD.json"; bad.write_text(json.dumps({"generator_admission_pass": False, "failed_gates": ["x"]})); monkeypatch.setattr(B, "ADMISSION", bad)
    assert B.run(tmp_path / "s", now=at(5), gens=1)["outcome"] == "REFUSED_GENERATOR_ADMISSION_NOT_PASSED"
    assert not (tmp_path / "s" / "evolution").exists()


def test_committed_admission_record_currently_blocks_scored_evolution():
    rec = json.loads((B.HERE / "configs" / "ADMISSION_V4.json").read_text())
    assert rec["generator_admission_pass"] is False and rec["failed_gates"]


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
    with pytest.raises(EV4.InvalidWorldBank): EV4.admit_suite("training", cal, record_path=None, exp_id="TEST-V4-BUDGET", budget_s=0.0)
    a = EV4.admit_suite("training", cal, record_path=rec, exp_id="TEST-V4-BUDGET"); b = EV4.admit_suite("training", cal, record_path=rec, exp_id="TEST-V4-BUDGET")
    assert a.world_hashes() == b.world_hashes() and json.loads(rec.read_text())["admitted"]


def test_structurally_invalid_world_is_rejected_with_reason_and_next_seed_used(tmp_path, monkeypatch):
    from gym import calibration as C
    cal = C.build(B.REPO / "data" / "normalized"); real = G.generate_world; calls = {"n": 0}
    def broken(spec, cal_):
        w = real(spec, cal_); calls["n"] += 1
        if calls["n"] == 1: w.close["SPY"][5] = float("nan")
        return w
    monkeypatch.setattr(G, "generate_world", broken); rec = tmp_path / "adm.json"
    s = EV4.admit_suite("training", cal, record_path=rec, exp_id="TEST-V4-REJECT")
    r = json.loads(rec.read_text()); assert len(r["attempts"]) == 1 and r["attempts"][0]["reasons"] and len(s.worlds) == 6
