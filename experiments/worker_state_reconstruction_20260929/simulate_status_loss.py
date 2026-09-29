import sys, json, shutil
sys.path.insert(0, "scripts")
import fitness_v2_worker as worker
from worker_state_reconstruction import reconcile_status
from fitness_v2_worker import _resume_campaign_state

STATUS = worker.STATUS_PATH
BACKUP = STATUS.with_name("STATUS.json.pre-simulation-backup")

print("=== before: real STATUS.json ===")
before = json.loads(STATUS.read_text())
print(json.dumps({k: before[k] for k in (
    "world_bank_id", "synthetic_world_count", "campaign_seed", "generation", "campaigns_complete"
)}, indent=2))

wb_path = worker.CHECKPOINT_DIR / "world_bank_v1.json"
wb_mtime_before = wb_path.stat().st_mtime_ns
wb_bytes_before = wb_path.read_bytes()

print("\n=== simulating instance loss: renaming STATUS.json aside ===")
shutil.move(str(STATUS), str(BACKUP))
print("STATUS.json exists now:", STATUS.exists())

try:
    print("\n=== calling reconcile_status() directly (NOT run_one_step/main -- no campaign work triggered) ===")
    reconstructed = reconcile_status(
        repo_root=worker.REPO_ROOT, status_path=worker.STATUS_PATH,
        checkpoint_dir=worker.CHECKPOINT_DIR, campaign_seeds=worker.CAMPAIGN_SEEDS,
        final_generation=worker.FINAL_GENERATION, expansion_batch_id=worker._expansion_batch_id,
        generation_checkpoint_path=worker._generation_checkpoint_path,
        nominee_checkpoint_path=worker._nominee_checkpoint_path,
        active_protocol_manifest_id=worker._active_protocol_manifest_id,
        now_utc=worker._now_utc(),
    )
    print(json.dumps({k: reconstructed[k] for k in (
        "world_bank_id", "synthetic_world_count", "campaign_seed", "generation",
        "campaigns_complete", "protocol_manifest", "recoveries",
    )}, indent=2))

    print("\n=== proving resume point (calling _resume_campaign_state directly -- pure, no computation/checkpointing) ===")
    resume_from, population, used_ids, final_ranked = _resume_campaign_state(
        reconstructed, 0, reconstructed["campaign_seed"]
    )
    print("resume_from:", resume_from)
    real_gen2 = json.loads((worker.CHECKPOINT_DIR / "batch0_campaign_2066557696_generation_2.json").read_text())
    print("resume population matches gen2's next_population:", population == real_gen2["next_population"])
    print("resume used_genome_ids matches gen2's exactly:", sorted(used_ids) == sorted(real_gen2["used_genome_ids"]))

    print("\n=== proving no world-bank rebuild occurred ===")
    wb_mtime_after = wb_path.stat().st_mtime_ns
    wb_bytes_after = wb_path.read_bytes()
    print("mtime unchanged:", wb_mtime_before == wb_mtime_after)
    print("bytes unchanged:", wb_bytes_before == wb_bytes_after)

    print("\n=== reconciled STATUS.json now on disk (from the reconstruction's own write_status) ===")
    print(json.dumps(json.loads(STATUS.read_text()), indent=2))

finally:
    print("\n=== restoring the original real STATUS.json exactly (undoing the simulated loss) ===")
    STATUS.unlink(missing_ok=True)
    shutil.move(str(BACKUP), str(STATUS))
    restored = json.loads(STATUS.read_text())
    print("restored matches original byte-for-byte:", restored == before)
