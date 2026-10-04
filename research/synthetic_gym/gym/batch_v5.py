"""One bounded research batch of experiment SYNGYM-20261005-V5.

V5 preserves V1 through V4 and closes the crash windows found during the V4
handoff review: world construction persists its current seed before work,
RUNNING slots resume under the single-writer lock, terminal publication/status
can be reconstructed without repeating evaluations, and a complete frozen-hash
manifest is mandatory.  It retains the idempotent five-slot ledger, bounded
world admission, engine_v4, and durable local-vs-remote publication record.
Single writer per state store (non-blocking flock; an overlapping
invocation is SKIPPED, never run concurrently). At most one batch per US trading session. Only TRAINING worlds feed mutation; validation is opened exactly once
(--final) and sealed only if validation passes. Offline: no network, no broker code, no credential access."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import resource
import signal
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import calibration as C
from . import checkpoint as CK
from . import engine as E
from . import evaluation_v5 as EV3
from . import evolve as EVO
from . import scoring as SC
from . import stats as st
from . import strategies as S

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parents[1]
NY = ZoneInfo("America/New_York")
HOLIDAYS = {"2026": ["01-01", "01-19", "02-16", "04-03", "05-25", "06-19", "07-03", "09-07", "11-26", "12-25"], "2027": ["01-01", "01-18", "02-15", "03-26", "05-31", "06-18", "07-05", "09-06", "11-25", "12-24"]}
FROZEN_FILES = ["gym/generator_v4.py", "configs/generator_v4.json", "configs/experiment_v5.json", "configs/calibration_targets_v4.json", "configs/ADMISSION_V5.json",
                "docs/generator_v5_validation_ENG-V5FROZEN.json", "gym/engine_v4.py", "gym/evaluation_v5.py", "gym/validation_v4.py", "gym/scoring.py", "gym/engine.py",
                "gym/strategies.py", "gym/evolve.py", "gym/checkpoint.py", "gym/evaluation.py", "gym/factors.py", "gym/world.py", "gym/sessions.py", "gym/rng.py",
                "gym/calibration.py", "gym/batch_v5.py"]


class Stop(RuntimeError):
    pass


def code_hashes() -> dict:
    return {f: hashlib.sha256((HERE / f).read_bytes()).hexdigest() for f in FROZEN_FILES}


def trading_day(d: date):
    ys = HOLIDAYS.get(str(d.year))
    return None if ys is None else not (d.weekday() >= 5 or d.strftime("%m-%d") in ys)


def status_record(state: Path, batch: dict, outcome: str) -> dict:
    """reconciled from durable state: a heartbeat without advancing evaluations is NOT reported as successful training"""
    receipts = [json.loads(p.read_text()) for p in sorted((state / "evolution").glob("batch_*.json"))]
    total = sum(int(r.get("evaluations", 0)) for r in receipts)
    ck_dir = state / "evolution" / "checkpoints"; cks = sorted(ck_dir.glob("gen_*.sha256"))
    ck = None if not cks else {"generation": CK.list_generations(ck_dir)[-1], "sha256": cks[-1].read_text().strip()}
    return {"experiment_id": batch["experiment_id"], "deployed_revision": (Path(os.environ.get("FF_RELEASE", HERE.parents[1])) / "DEPLOYED_SHA").read_text().strip() if (Path(os.environ.get("FF_RELEASE", HERE.parents[1])) / "DEPLOYED_SHA").exists() else "unknown",
            "interpreter": {"path": sys.executable, "version": sys.version.split()[0]}, "batch_id": batch["batch_id"], "generation": batch["generations_completed_total"] - 1,
            "last_progress_utc": datetime.now(timezone.utc).isoformat() if batch["evaluations"] > 0 else None, "completed_evaluations_total": total,
            "completed_evaluations_this_batch": batch["evaluations"], "rejected_worlds": batch["rejected_worlds"], "output_mib": batch["resource"]["output_mib"], "stop_reason": batch["halt_reason"] or outcome,
            "checkpoint_identity": ck, "training_success": bool(batch["evaluations"] > 0 and outcome == "COMPLETED"),
            "promotion_enabled": False, "claim": "development-training receipt only; not a qualified trading strategy"}


def rss_mib(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
def dir_mib(p: Path): return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1048576.0
def wj(path: Path, obj): CK.write_atomic(path, (json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n").encode())


def publication_record(state: Path, batch: dict) -> dict:
    ck_dir = state / "evolution" / "checkpoints"; cks = sorted(ck_dir.glob("gen_*.sha256"))
    return {"batch_id": batch["batch_id"], "local_durable_checkpoint": {
                "latest_generation": (CK.list_generations(ck_dir)[-1] if cks else None),
                "latest_checkpoint_sha256": (cks[-1].read_text().strip() if cks else None)},
            "remote_publication": "PENDING_RELAY: this instance holds no git credentials; results are preserved locally and must be relayed to HOST for publication. Local completion is NOT remote publication.",
            "evaluations_repeated_on_publication_failure": False}


def reconcile_terminal_artifacts(state: Path, entry: dict) -> bool:
    """Repair publication/status from a terminal batch receipt; never evaluate."""
    if not entry.get("batch_file"):
        return False
    p = Path(entry["batch_file"])
    if not p.is_absolute():
        p = state / p
    if not p.exists():
        return False
    batch = json.loads(p.read_text()); outcome = entry["status"]
    wj(state / "publication.json", publication_record(state, batch))
    wj(state / "status.json", status_record(state, batch, outcome))
    return True


PLANNED_SLOTS = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
ADMISSION = HERE / "configs" / "ADMISSION_V5.json"
FROZEN = HERE / "configs" / "FROZEN_HASHES_V5.json"


def run(state: Path, final=False, now=None, gens=5, wall_seconds=7200.0, mem_cap=800.0, out_cap=1024.0, expires_et="2026-10-09T17:00:00", enforce_session=True, slot=None) -> dict:
    state = Path(state); state.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc); et = now.astimezone(NY)
    lock = open(state / "writer.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return {"outcome": "SKIPPED_OVERLAP_ANOTHER_BATCH_HOLDS_THE_STATE_LOCK"}
    if et >= datetime.fromisoformat(expires_et).replace(tzinfo=NY):
        return {"outcome": "SKIPPED_EXPIRED"}
    if enforce_session and slot is None and trading_day(et.date()) is not True:
        return {"outcome": "SKIPPED_NOT_A_TRADING_DAY"}
    # Code and admission are both mandatory gates.  Absence is refusal, never
    # permission to run unfrozen code.
    frozen = FROZEN
    if not frozen.exists():
        return {"outcome": "REFUSED_FROZEN_MANIFEST_MISSING"}
    hashes = code_hashes(); freeze_rec = json.loads(frozen.read_text())
    if freeze_rec.get("experiment_id") != EV3.EXP_ID:
        return {"outcome": "REFUSED_FROZEN_MANIFEST_IDENTITY_MISMATCH"}
    wanted = freeze_rec.get("files", {})
    diff = sorted(k for k in set(wanted) | set(hashes) if wanted.get(k) != hashes.get(k))
    if diff:
        return {"outcome": "REFUSED_FROZEN_HASH_MISMATCH", "files": diff}
    adm = json.loads(ADMISSION.read_text()) if ADMISSION.exists() else {"generator_admission_pass": False}
    if adm.get("experiment_id") != EV3.EXP_ID or not adm.get("generator_admission_pass"):
        return {"outcome": "REFUSED_GENERATOR_ADMISSION_NOT_PASSED", "detail": adm.get("failed_gates", "no admission record")}
    # idempotent slot ledger: each planned session slot is consumed at most once, by whichever invocation reaches it first (early or scheduled)
    ledger_path = state / "slots.json"; ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    want = slot or et.date().isoformat()
    if want not in PLANNED_SLOTS: return {"outcome": "SKIPPED_NOT_A_PLANNED_SLOT"}
    existing = ledger.get(want)
    if existing and existing.get("status") != "RUNNING":
        repaired = reconcile_terminal_artifacts(state, existing)
        return {"outcome": "SKIPPED_SLOT_ALREADY_CONSUMED", "slot": want, "batch_id": existing["batch_id"],
                "terminal_artifacts_reconciled": repaired}
    if not existing and len(ledger) >= len(PLANNED_SLOTS): return {"outcome": "SKIPPED_WEEKLY_BUDGET_EXHAUSTED"}
    batch_id = existing["batch_id"] if existing else f"{EV3.EXP_ID}-B{PLANNED_SLOTS.index(want) + 1}"
    log = state / "batches.jsonl"
    out = state / "evolution"; out.mkdir(exist_ok=True)
    batch_path = out / f"batch_{batch_id}.json"
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))
    t_start = time.monotonic(); cpu0 = time.process_time()
    # Reserved validation is a Friday-slot boundary, not an operator flag.
    final = want == PLANNED_SLOTS[-1]
    if existing:
        ledger[want].setdefault("resume_utc", []).append(now.isoformat())
    else:
        ledger[want] = {"batch_id": batch_id, "started_utc": now.isoformat(), "early": want != et.date().isoformat(), "status": "RUNNING",
                        "batch_file": str(batch_path.relative_to(state))}
    wj(ledger_path, ledger)
    rec = {"session_date": et.date().isoformat(), "slot": want, "batch_id": batch_id, "started_utc": now.isoformat(),
           "outcome": "RESUMING" if existing else "RUNNING", "final": final}
    with open(log, "a") as f: f.write(json.dumps(rec) + "\n")
    cal = C.build(REPO / "data" / "normalized")
    try:
        train = EV3.admit_suite("training", cal, record_path=state / "world_admission_training.json", stop_check=lambda: stop["flag"])
    except EV3.WorldBuildInterrupted as e:
        ledger[want]["last_stop_reason"] = str(e); wj(ledger_path, ledger)
        result = {"outcome": "INTERRUPTED_WORLD_BUILD_RESUMABLE", "batch_id": batch_id, "slot": want, "detail": str(e)}
        wj(state / "latest_result.json", result)
        return result
    except EV3.InvalidWorldBank as e:
        rejected = 0; ap = state / "world_admission_training.json"
        if ap.exists(): rejected = len(json.loads(ap.read_text()).get("attempts", []))
        batch = {"batch_id": batch_id, "slot": want, "experiment_id": EV3.EXP_ID, "session_date": et.date().isoformat(),
                 "generations_completed_total": len(CK.list_generations(out / "checkpoints")), "evaluations": 0, "rejected_worlds": rejected,
                 "halt_reason": str(e), "resource": {"wall_s": time.monotonic() - t_start, "cpu_s": time.process_time() - cpu0,
                 "maxrss_mib": rss_mib(), "output_mib": dir_mib(out), "limits": {"wall_s": wall_seconds, "mem_mib": mem_cap, "out_mib": out_cap}}}
        wj(batch_path, batch); outcome = "BLOCKED_INVALID_WORLD_BANK"
        ledger[want].update(status=outcome, ended_utc=datetime.now(timezone.utc).isoformat()); wj(ledger_path, ledger)
        wj(state / "status.json", status_record(state, batch, outcome)); result = {"outcome": outcome, "batch": batch}; wj(state / "latest_result.json", result)
        return result
    refs = S.load_references(); P = EV3.E_PARAMS()
    world_hashes = train.world_hashes(); wj(state / "training_world_hashes.json", world_hashes)
    ck_hashes = {"experiment": EV3.EXP_ID, "code": hashes, "worlds": world_hashes}
    ck_dir = out / "checkpoints"; done = CK.list_generations(ck_dir)
    first_gen = (done[-1] + 1) if done else 0
    if "first_generation" not in ledger[want]:
        ledger[want]["first_generation"] = first_gen
        ledger[want]["target_generation_exclusive"] = first_gen + gens
        wj(ledger_path, ledger)
    batch_first_gen = int(ledger[want]["first_generation"])
    target = int(ledger[want]["target_generation_exclusive"])
    base_eval = EV3.Evaluator(train, P); n = {"evals": 0}

    def guarded(cand):
        if stop["flag"]: raise Stop("SIGTERM received: checkpointing at the last completed generation")
        if rss_mib() > mem_cap: raise Stop(f"memory {rss_mib():.0f} MiB > cap")
        if dir_mib(out) > out_cap: raise Stop("output cap")
        n["evals"] += 1
        return base_eval(cand)
    budget = EVO.Budget(wall_seconds)
    halt = None
    if first_gen >= target:
        s = CK.load_latest(ck_dir) or {"generation": -1, "rows": [], "history": []}
    else:
        try:
            s = EVO.run(EV3.EXP_ID, out, refs, guarded, S.mutate_child, lambda g: __import__("lib.ids", fromlist=["genome_id"]).genome_id(g), budget, ck_hashes,
                        max_generations=target, resume=bool(done))
        except Stop as e:
            halt = str(e); s = CK.load_latest(ck_dir) or {"generation": -1, "rows": [], "history": []}
    halt = halt or s.get("halt_reason")
    rows = s["rows"]; ordered = sorted(rows, key=SC.ordering_key)
    best = ordered[0] if ordered else None
    prior_evals = int(ledger[want].get("evaluations_completed", 0)); ledger[want]["evaluations_completed"] = prior_evals + n["evals"]; wj(ledger_path, ledger)
    batch = {"batch_id": batch_id, "slot": want, "evaluation_budget": {"generations_per_batch": target - batch_first_gen, "population": EVO.POP, "generation_zero_included": batch_first_gen == 0, "max_candidate_evaluations": (target - batch_first_gen) * EVO.POP}, "rejected_worlds": len(train.admission["attempts"]), "experiment_id": EV3.EXP_ID, "session_date": et.date().isoformat(), "generations_completed_total": s["generation"] + 1, "first_generation_this_batch": batch_first_gen,
             "generations_this_batch": s["generation"] + 1 - batch_first_gen, "evaluations": ledger[want]["evaluations_completed"], "evaluations_this_invocation": n["evals"], "halt_reason": halt,
             "n_eligible": sum(r["summary"]["eligible"] for r in rows), "population": len(rows),
             "best_training": None if best is None else {"genome_id": best["genome_id"], "family": best["family"], "primary_score": best["summary"]["primary_score"], "eligible": best["summary"]["eligible"]},
             "positive_primary_score_count": sum(1 for r in rows if r["summary"]["primary_score"] > 0),
             "resource": {"wall_s": time.monotonic() - t_start, "cpu_s": time.process_time() - cpu0, "maxrss_mib": rss_mib(), "output_mib": dir_mib(out), "limits": {"wall_s": wall_seconds, "mem_mib": mem_cap, "out_mib": out_cap}}}
    wj(batch_path, batch)
    wj(out / "leaderboard_latest.json", [{k: r[k] for k in ("genome_id", "family", "name", "origin", "summary")} for r in ordered])
    result = {"batch": batch}
    if stop["flag"] and not halt:
        halt = "SIGTERM received after a durable generation checkpoint"
        batch["halt_reason"] = halt; wj(batch_path, batch)
    # ------------------------------------------------------------- final step: validation exactly once, sealed only after a validation pass
    if final and s["generation"] + 1 >= target and not halt:
        phase_file = state / "phase_state.json"; phase = json.loads(phase_file.read_text()) if phase_file.exists() else {"validation_done": False, "test_done": False}
        nom = EVO.nominee(rows)
        if nom is None:
            result["qualification"] = "UNAVAILABLE: no eligible nominee in the final generation; validation and sealed test were NOT opened (training diagnostics only)"
        elif phase["validation_done"]:
            result["qualification"] = "validation already executed (exactly once): see validation_result.json"
        else:
            ck_files = sorted(ck_dir.glob("gen_*.sha256")); selection_ck = ck_files[-1].read_text().strip() if ck_files else None
            if phase.get("validation_started"):
                if phase.get("nominee_genome_id") != nom["genome_id"] or phase.get("selection_checkpoint_sha256") != selection_ck:
                    halt = "VALIDATION_RECOVERY_IDENTITY_MISMATCH"
                    batch["halt_reason"] = halt; wj(batch_path, batch)
                    result["qualification"] = "BLOCKED: validation was exposed under a different frozen nominee/checkpoint"
                else:
                    phase["validation_resume_count"] = int(phase.get("validation_resume_count", 0)) + 1; wj(phase_file, phase)
            else:
                phase.update(validation_started=True, validation_started_utc=datetime.now(timezone.utc).isoformat(),
                             nominee_genome_id=nom["genome_id"], selection_checkpoint_sha256=selection_ck,
                             recovery_rule="resume the same persisted validation world identities and same frozen nominee; never rerun selection or allocate new seeds")
                wj(phase_file, phase)
            if halt:
                valid = None
            else:
                valid = EV3.admit_suite("validation", cal, record_path=state / "world_admission_validation.json", stop_check=lambda: stop["flag"])
                phase["validation_world_hashes"] = valid.world_hashes(); wj(phase_file, phase)
            entries = {"nominee": {**nom, "name": "nominee"}, **{k: {**r, "name": k} for k, r in refs.items()}}
            if valid is None:
                val = None
            else:
                val = {k: EV3.evaluate(e, valid, P) for k, e in entries.items()}
            if val is None:
                vq = None; vpass = False
            else:
                vq = st.quantile([m["excess_vs_passive18"] for m in val["nominee"]["per_world"]], .25); vpass = bool(val["nominee"]["summary"]["eligible"] and vq > 0)
            if val is None:
                vres = None
            else:
                vres = {"nominee": {k: nom[k] for k in ("genome_id", "family", "name", "origin")}, "lower_quartile_excess": vq, "eligible": val["nominee"]["summary"]["eligible"], "PASS": vpass,
                        "all": {k: v["summary"] for k, v in val.items()}, "validation_world_hashes": valid.world_hashes()}
            if vres is not None:
                phase.update(validation_done=True, validation_pass=vpass, validation_done_utc=datetime.now(timezone.utc).isoformat()); wj(phase_file, phase); wj(state / "validation_result.json", vres)
                result["validation"] = {k: vres[k] for k in ("lower_quartile_excess", "eligible", "PASS")}
            if vpass:
                phase.update(sealed_started=True, sealed_started_utc=datetime.now(timezone.utc).isoformat()); wj(phase_file, phase)
                test = EV3.admit_suite("sealed_test", cal, record_path=state / "world_admission_sealed.json", stop_check=lambda: stop["flag"]); tres = {k: EV3.evaluate(e, test, P) for k, e in entries.items()}
                tq = st.quantile([m["excess_vs_passive18"] for m in tres["nominee"]["per_world"]], .25); tpass = bool(tres["nominee"]["summary"]["eligible"] and tq > 0)
                wj(state / "sealed_test_result.json", {"lower_quartile_excess": tq, "PASS": tpass, "all": {k: v["summary"] for k, v in tres.items()}})
                phase.update(test_done=True, test_pass=tpass, sealed_done_utc=datetime.now(timezone.utc).isoformat()); wj(phase_file, phase)
                result["sealed_test"] = {"lower_quartile_excess": tq, "PASS": tpass}
                result["qualification"] = "RESEARCH_CANDIDATE (research/shadow-only)" if tpass else "validation passed; sealed synthetic test NOT passed"
            else:
                result["qualification"] = "NOT QUALIFIED: nominee failed validation; sealed test stayed sealed"
    elif final:
        result["qualification"] = "UNAVAILABLE: final step skipped because the batch halted early"
    outcome = "COMPLETED" if not halt else ("INTERRUPTED_CHECKPOINTED" if "SIGTERM" in halt else "HALTED: " + halt)
    batch["outcome"] = outcome; wj(batch_path, batch)
    prior_lines = log.read_text().splitlines()
    prior_lines[-1] = json.dumps({**rec, "outcome": outcome, "ended_utc": datetime.now(timezone.utc).isoformat(), "generations_this_batch": batch["generations_this_batch"]})
    CK.write_atomic(log, ("\n".join(prior_lines) + "\n").encode())
    if outcome == "INTERRUPTED_CHECKPOINTED":
        ledger[want].update(status="RUNNING", last_stop_reason=halt, last_stop_utc=datetime.now(timezone.utc).isoformat())
    else:
        ledger[want].update(status=outcome, ended_utc=datetime.now(timezone.utc).isoformat())
    wj(ledger_path, ledger)
    wj(state / "publication.json", publication_record(state, batch))
    wj(state / "status.json", status_record(state, batch, outcome))
    result["outcome"] = outcome
    wj(state / "latest_result.json", result)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True); ap.add_argument("--final", action="store_true"); ap.add_argument("--now")
    ap.add_argument("--gens", type=int, default=5); ap.add_argument("--wall-seconds", type=float, default=7200.0)
    ap.add_argument("--no-session-guard", action="store_true"); ap.add_argument("--slot"); ap.add_argument("--expires", default="2026-10-09T17:00:00")
    a = ap.parse_args()
    r = run(Path(a.state), a.final, datetime.fromisoformat(a.now).astimezone(timezone.utc) if a.now else None, a.gens, a.wall_seconds, expires_et=a.expires, enforce_session=not a.no_session_guard, slot=a.slot)
    print(json.dumps({k: r[k] for k in r if k != "batch"} | ({"batch": {k: r["batch"][k] for k in ("generations_this_batch", "generations_completed_total", "n_eligible", "positive_primary_score_count", "best_training", "halt_reason")}} if "batch" in r else {}), indent=1, default=str))
