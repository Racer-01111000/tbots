"""One bounded research batch of experiment SYNGYM-20261005-V3 (continuing evolution). Single writer per state store (non-blocking flock; an overlapping
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
from . import evaluation_v3 as EV3
from . import evolve as EVO
from . import scoring as SC
from . import stats as st
from . import strategies as S

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parents[1]
NY = ZoneInfo("America/New_York")
HOLIDAYS = {"2026": ["01-01", "01-19", "02-16", "04-03", "05-25", "06-19", "07-03", "09-07", "11-26", "12-25"], "2027": ["01-01", "01-18", "02-15", "03-26", "05-31", "06-18", "07-05", "09-06", "11-25", "12-24"]}
FROZEN_FILES = ["gym/generator_v2.py", "configs/generator_v2.json", "configs/experiment_v3.json", "gym/scoring.py", "gym/engine.py", "gym/strategies.py", "gym/evolve.py",
                "gym/checkpoint.py", "gym/evaluation_v3.py", "gym/evaluation.py", "gym/factors.py", "gym/world.py", "gym/sessions.py", "gym/rng.py", "gym/calibration.py", "gym/batch_v3.py"]


class Stop(RuntimeError):
    pass


def code_hashes() -> dict:
    return {f: hashlib.sha256((HERE / f).read_bytes()).hexdigest() for f in FROZEN_FILES}


def trading_day(d: date):
    ys = HOLIDAYS.get(str(d.year))
    return None if ys is None else not (d.weekday() >= 5 or d.strftime("%m-%d") in ys)


def rss_mib(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
def dir_mib(p: Path): return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1048576.0
def wj(path: Path, obj): CK.write_atomic(path, (json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n").encode())


def run(state: Path, final=False, now=None, gens=5, wall_seconds=7200.0, mem_cap=800.0, out_cap=1024.0, expires_et="2026-10-09T17:00:00", enforce_session=True) -> dict:
    state = Path(state); state.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc); et = now.astimezone(NY)
    lock = open(state / "writer.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return {"outcome": "SKIPPED_OVERLAP_ANOTHER_BATCH_HOLDS_THE_STATE_LOCK"}
    if et >= datetime.fromisoformat(expires_et).replace(tzinfo=NY):
        return {"outcome": "SKIPPED_EXPIRED"}
    if enforce_session and trading_day(et.date()) is not True:
        return {"outcome": "SKIPPED_NOT_A_TRADING_DAY"}
    log = state / "batches.jsonl"
    prior = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    if enforce_session and any(p["session_date"] == et.date().isoformat() and p["outcome"] != "SKIPPED" for p in prior):
        return {"outcome": "SKIPPED_ALREADY_RAN_THIS_SESSION"}
    # frozen-code check: the freeze file is committed in the repo BEFORE any V3 score exists
    hashes = code_hashes(); frozen = HERE / "configs" / "FROZEN_HASHES_V3.json"
    if frozen.exists():
        want = json.loads(frozen.read_text())["files"]
        diff = sorted(k for k in want if want[k] != hashes.get(k))
        if diff: return {"outcome": "REFUSED_FROZEN_HASH_MISMATCH", "files": diff}
    out = state / "evolution"; out.mkdir(exist_ok=True)
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))
    t_start = time.monotonic(); cpu0 = time.process_time()
    rec = {"session_date": et.date().isoformat(), "started_utc": now.isoformat(), "outcome": "RUNNING", "final": final}
    with open(log, "a") as f: f.write(json.dumps(rec) + "\n")
    cal = C.build(REPO / "data" / "normalized")
    train = EV3.Suite("training", cal); refs = S.load_references(); P = EV3.E_PARAMS()
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
    batch = {"experiment_id": EV3.EXP_ID, "session_date": et.date().isoformat(), "generations_completed_total": s["generation"] + 1, "first_generation_this_batch": first_gen,
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
            valid = EV3.Suite("validation", cal); entries = {"nominee": {**nom, "name": "nominee"}, **{k: {**r, "name": k} for k, r in refs.items()}}
            val = {k: EV3.evaluate(e, valid, P) for k, e in entries.items()}
            vq = st.quantile([m["excess_vs_passive18"] for m in val["nominee"]["per_world"]], .25); vpass = bool(val["nominee"]["summary"]["eligible"] and vq > 0)
            vres = {"nominee": {k: nom[k] for k in ("genome_id", "family", "name", "origin")}, "lower_quartile_excess": vq, "eligible": val["nominee"]["summary"]["eligible"], "PASS": vpass,
                    "all": {k: v["summary"] for k, v in val.items()}, "validation_world_hashes": valid.world_hashes()}
            phase.update(validation_done=True, validation_pass=vpass); wj(phase_file, phase); wj(state / "validation_result.json", vres)
            result["validation"] = {k: vres[k] for k in ("lower_quartile_excess", "eligible", "PASS")}
            if vpass:
                test = EV3.Suite("sealed_test", cal); tres = {k: EV3.evaluate(e, test, P) for k, e in entries.items()}
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
    wj(state / "latest_result.json", result)
    result["outcome"] = outcome
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True); ap.add_argument("--final", action="store_true"); ap.add_argument("--now")
    ap.add_argument("--gens", type=int, default=5); ap.add_argument("--wall-seconds", type=float, default=7200.0)
    ap.add_argument("--no-session-guard", action="store_true"); ap.add_argument("--expires", default="2026-10-09T17:00:00")
    a = ap.parse_args()
    r = run(Path(a.state), a.final, datetime.fromisoformat(a.now).astimezone(timezone.utc) if a.now else None, a.gens, a.wall_seconds, expires_et=a.expires, enforce_session=not a.no_session_guard)
    print(json.dumps({k: r[k] for k in r if k != "batch"} | ({"batch": {k: r["batch"][k] for k in ("generations_this_batch", "generations_completed_total", "n_eligible", "positive_primary_score_count", "best_training", "halt_reason")}} if "batch" in r else {}), indent=1, default=str))
