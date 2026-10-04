"""One bounded research batch of experiment SYNGYM-20261005-V4 (continuing evolution; supersedes V3R, which is preserved untouched).
Additions over batch_v3: code-level generator-admission gate, idempotent slot ledger (an early batch consumes its slot; the scheduled trigger then skips), bounded resumable world admission,
engine_v4, reconciled status.json, durable local-vs-remote publication record. Single writer per state store (non-blocking flock; an overlapping
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
from . import evaluation_v4 as EV3
from . import evolve as EVO
from . import scoring as SC
from . import stats as st
from . import strategies as S

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parents[1]
NY = ZoneInfo("America/New_York")
HOLIDAYS = {"2026": ["01-01", "01-19", "02-16", "04-03", "05-25", "06-19", "07-03", "09-07", "11-26", "12-25"], "2027": ["01-01", "01-18", "02-15", "03-26", "05-31", "06-18", "07-05", "09-06", "11-25", "12-24"]}
FROZEN_FILES = ["gym/generator_v3.py", "configs/generator_v3.json", "configs/experiment_v4.json", "configs/calibration_targets_v4.json", "gym/engine_v4.py", "gym/evaluation_v4.py", "gym/validation_v4.py", "gym/scoring.py", "gym/engine.py", "gym/strategies.py", "gym/evolve.py",
                "gym/checkpoint.py", "gym/evaluation.py", "gym/factors.py", "gym/world.py", "gym/sessions.py", "gym/rng.py", "gym/calibration.py", "gym/batch_v4.py"]


class Stop(RuntimeError):
    pass


def code_hashes() -> dict:
    return {f: hashlib.sha256((HERE / f).read_bytes()).hexdigest() for f in FROZEN_FILES}


def trading_day(d: date):
    ys = HOLIDAYS.get(str(d.year))
    return None if ys is None else not (d.weekday() >= 5 or d.strftime("%m-%d") in ys)


def status_record(state: Path, batch: dict, outcome: str) -> dict:
    """reconciled from durable state: a heartbeat without advancing evaluations is NOT reported as successful training"""
    prev = json.loads((state / "status.json").read_text()) if (state / "status.json").exists() else {"completed_evaluations_total": 0}
    total = prev.get("completed_evaluations_total", 0) + batch["evaluations"]
    return {"experiment_id": batch["experiment_id"], "deployed_revision": (Path(os.environ.get("FF_RELEASE", HERE.parents[1])) / "DEPLOYED_SHA").read_text().strip() if (Path(os.environ.get("FF_RELEASE", HERE.parents[1])) / "DEPLOYED_SHA").exists() else "unknown",
            "interpreter": {"path": sys.executable, "version": sys.version.split()[0]}, "batch_id": batch["batch_id"], "generation": batch["generations_completed_total"] - 1,
            "last_progress_utc": datetime.now(timezone.utc).isoformat() if batch["evaluations"] > 0 else prev.get("last_progress_utc"), "completed_evaluations_total": total,
            "completed_evaluations_this_batch": batch["evaluations"], "rejected_worlds": batch["rejected_worlds"], "output_mib": batch["resource"]["output_mib"], "stop_reason": batch["halt_reason"] or outcome,
            "training_success": bool(batch["evaluations"] > 0 and outcome == "COMPLETED"), "claim": "development-training receipt only; not a qualified trading strategy"}


def rss_mib(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
def dir_mib(p: Path): return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1048576.0
def wj(path: Path, obj): CK.write_atomic(path, (json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n").encode())


PLANNED_SLOTS = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
ADMISSION = HERE / "configs" / "ADMISSION_V4.json"


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
    # code-level generator-admission gate: scored evolution cannot start unless the recorded admission report passed every declared gate
    adm = json.loads(ADMISSION.read_text()) if ADMISSION.exists() else {"generator_admission_pass": False}
    if not adm.get("generator_admission_pass"):
        return {"outcome": "REFUSED_GENERATOR_ADMISSION_NOT_PASSED", "detail": adm.get("failed_gates", "no admission record")}
    # idempotent slot ledger: each planned session slot is consumed at most once, by whichever invocation reaches it first (early or scheduled)
    ledger_path = state / "slots.json"; ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    want = slot or et.date().isoformat()
    if want not in PLANNED_SLOTS: return {"outcome": "SKIPPED_NOT_A_PLANNED_SLOT"}
    if want in ledger: return {"outcome": "SKIPPED_SLOT_ALREADY_CONSUMED", "slot": want, "batch_id": ledger[want]["batch_id"]}
    if len(ledger) >= len(PLANNED_SLOTS): return {"outcome": "SKIPPED_WEEKLY_BUDGET_EXHAUSTED"}
    batch_id = f"{EV3.EXP_ID}-B{PLANNED_SLOTS.index(want) + 1}"
    log = state / "batches.jsonl"
    prior = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    # frozen-code check: the freeze file is committed in the repo BEFORE any V3 score exists
    hashes = code_hashes(); frozen = HERE / "configs" / "FROZEN_HASHES_V4.json"
    if frozen.exists():
        want = json.loads(frozen.read_text())["files"]
        diff = sorted(k for k in want if want[k] != hashes.get(k))
        if diff: return {"outcome": "REFUSED_FROZEN_HASH_MISMATCH", "files": diff}
    out = state / "evolution"; out.mkdir(exist_ok=True)
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))
    t_start = time.monotonic(); cpu0 = time.process_time()
    final = final or want == PLANNED_SLOTS[-1]
    ledger[want] = {"batch_id": batch_id, "started_utc": now.isoformat(), "early": want != et.date().isoformat(), "status": "RUNNING"}; wj(ledger_path, ledger)
    rec = {"session_date": et.date().isoformat(), "slot": want, "batch_id": batch_id, "started_utc": now.isoformat(), "outcome": "RUNNING", "final": final}
    with open(log, "a") as f: f.write(json.dumps(rec) + "\n")
    cal = C.build(REPO / "data" / "normalized")
    train = EV3.admit_suite("training", cal, record_path=state / "world_admission_training.json"); refs = S.load_references(); P = EV3.E_PARAMS()
    world_hashes = train.world_hashes(); wj(state / "training_world_hashes.json", world_hashes)
    ck_hashes = {"experiment": EV3.EXP_ID, "code": hashes, "worlds": world_hashes}
    ck_dir = out / "checkpoints"; done = CK.list_generations(ck_dir)
    first_gen = (done[-1] + 1) if done else 0; target = first_gen + gens
    base_eval = EV3.Evaluator(train, P); n = {"evals": 0}

    def guarded(cand):
        if stop["flag"]: raise Stop("SIGTERM received: checkpointing at the last completed generation")
        if rss_mib() > mem_cap: raise Stop(f"memory {rss_mib():.0f} MiB > cap")
        if dir_mib(out) > out_cap: raise Stop("output cap")
        n["evals"] += 1
        return base_eval(cand)
    budget = EVO.Budget(wall_seconds)
    halt = None
    try:
        s = EVO.run(EV3.EXP_ID, out, refs, guarded, S.mutate_child, lambda g: __import__("lib.ids", fromlist=["genome_id"]).genome_id(g), budget, ck_hashes,
                    max_generations=target, resume=bool(done))
    except Stop as e:
        halt = str(e); s = CK.load_latest(ck_dir) or {"generation": -1, "rows": [], "history": []}
    halt = halt or s.get("halt_reason")
    rows = s["rows"]; ordered = sorted(rows, key=SC.ordering_key)
    best = ordered[0] if ordered else None
    batch = {"batch_id": batch_id, "slot": want, "evaluation_budget": {"generations_per_batch": gens, "population": EVO.POP, "generation_zero_included": True, "max_candidate_evaluations": gens * EVO.POP}, "rejected_worlds": len(train.admission["attempts"]), "experiment_id": EV3.EXP_ID, "session_date": et.date().isoformat(), "generations_completed_total": s["generation"] + 1, "first_generation_this_batch": first_gen,
             "generations_this_batch": s["generation"] + 1 - first_gen, "evaluations": n["evals"], "halt_reason": halt,
             "n_eligible": sum(r["summary"]["eligible"] for r in rows), "population": len(rows),
             "best_training": None if best is None else {"genome_id": best["genome_id"], "family": best["family"], "primary_score": best["summary"]["primary_score"], "eligible": best["summary"]["eligible"]},
             "positive_primary_score_count": sum(1 for r in rows if r["summary"]["primary_score"] > 0),
             "resource": {"wall_s": time.monotonic() - t_start, "cpu_s": time.process_time() - cpu0, "maxrss_mib": rss_mib(), "output_mib": dir_mib(out), "limits": {"wall_s": wall_seconds, "mem_mib": mem_cap, "out_mib": out_cap}}}
    wj(out / f"batch_{et.date().isoformat()}.json", batch)
    wj(out / "leaderboard_latest.json", [{k: r[k] for k in ("genome_id", "family", "name", "origin", "summary")} for r in ordered])
    result = {"batch": batch}
    # ------------------------------------------------------------- final step: validation exactly once, sealed only after a validation pass
    if final and s["generation"] + 1 >= target and not halt:
        phase_file = state / "phase_state.json"; phase = json.loads(phase_file.read_text()) if phase_file.exists() else {"validation_done": False, "test_done": False}
        nom = EVO.nominee(rows)
        if nom is None:
            result["qualification"] = "UNAVAILABLE: no eligible nominee in the final generation; validation and sealed test were NOT opened (training diagnostics only)"
        elif phase["validation_done"]:
            result["qualification"] = "validation already executed (exactly once): see validation_result.json"
        else:
            valid = EV3.admit_suite("validation", cal, record_path=state / "world_admission_validation.json"); entries = {"nominee": {**nom, "name": "nominee"}, **{k: {**r, "name": k} for k, r in refs.items()}}
            val = {k: EV3.evaluate(e, valid, P) for k, e in entries.items()}
            vq = st.quantile([m["excess_vs_passive18"] for m in val["nominee"]["per_world"]], .25); vpass = bool(val["nominee"]["summary"]["eligible"] and vq > 0)
            vres = {"nominee": {k: nom[k] for k in ("genome_id", "family", "name", "origin")}, "lower_quartile_excess": vq, "eligible": val["nominee"]["summary"]["eligible"], "PASS": vpass,
                    "all": {k: v["summary"] for k, v in val.items()}, "validation_world_hashes": valid.world_hashes()}
            phase.update(validation_done=True, validation_pass=vpass); wj(phase_file, phase); wj(state / "validation_result.json", vres)
            result["validation"] = {k: vres[k] for k in ("lower_quartile_excess", "eligible", "PASS")}
            if vpass:
                test = EV3.admit_suite("sealed_test", cal, record_path=state / "world_admission_sealed.json"); tres = {k: EV3.evaluate(e, test, P) for k, e in entries.items()}
                tq = st.quantile([m["excess_vs_passive18"] for m in tres["nominee"]["per_world"]], .25); tpass = bool(tres["nominee"]["summary"]["eligible"] and tq > 0)
                wj(state / "sealed_test_result.json", {"lower_quartile_excess": tq, "PASS": tpass, "all": {k: v["summary"] for k, v in tres.items()}})
                phase.update(test_done=True, test_pass=tpass); wj(phase_file, phase)
                result["sealed_test"] = {"lower_quartile_excess": tq, "PASS": tpass}
                result["qualification"] = "RESEARCH_CANDIDATE (research/shadow-only)" if tpass else "validation passed; sealed synthetic test NOT passed"
            else:
                result["qualification"] = "NOT QUALIFIED: nominee failed validation; sealed test stayed sealed"
    elif final:
        result["qualification"] = "UNAVAILABLE: final step skipped because the batch halted early"
    outcome = "COMPLETED" if not halt else ("INTERRUPTED_CHECKPOINTED" if "SIGTERM" in halt else "HALTED: " + halt)
    prior_lines = log.read_text().splitlines()
    prior_lines[-1] = json.dumps({**rec, "outcome": outcome, "ended_utc": datetime.now(timezone.utc).isoformat(), "generations_this_batch": batch["generations_this_batch"]})
    CK.write_atomic(log, ("\n".join(prior_lines) + "\n").encode())
    ledger[want].update(status=outcome, ended_utc=datetime.now(timezone.utc).isoformat()); wj(ledger_path, ledger)
    cks = sorted(ck_dir.glob("gen_*.sha256")); latest_ck = cks[-1].read_text().strip() if cks else None
    wj(state / "publication.json", {"batch_id": batch_id, "local_durable_checkpoint": {"latest_generation": s["generation"], "latest_checkpoint_sha256": latest_ck},
        "remote_publication": "PENDING_RELAY: this instance holds no git credentials; results are preserved locally and must be relayed to HOST for publication. Local completion is NOT remote publication.",
        "evaluations_repeated_on_publication_failure": False})
    wj(state / "status.json", status_record(state, batch, outcome))
    wj(state / "latest_result.json", result)
    result["outcome"] = outcome
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True); ap.add_argument("--final", action="store_true"); ap.add_argument("--now")
    ap.add_argument("--gens", type=int, default=5); ap.add_argument("--wall-seconds", type=float, default=7200.0)
    ap.add_argument("--no-session-guard", action="store_true"); ap.add_argument("--slot"); ap.add_argument("--expires", default="2026-10-09T17:00:00")
    a = ap.parse_args()
    r = run(Path(a.state), a.final, datetime.fromisoformat(a.now).astimezone(timezone.utc) if a.now else None, a.gens, a.wall_seconds, expires_et=a.expires, enforce_session=not a.no_session_guard, slot=a.slot)
    print(json.dumps({k: r[k] for k in r if k != "batch"} | ({"batch": {k: r["batch"][k] for k in ("generations_this_batch", "generations_completed_total", "n_eligible", "positive_primary_score_count", "best_training", "halt_reason")}} if "batch" in r else {}), indent=1, default=str))
