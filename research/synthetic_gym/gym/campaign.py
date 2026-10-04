"""Bounded campaign driver. One worker, hard budgets, atomic checkpoints, exactly-once validation / sealed-test phases.
Runs offline (the process needs no network; the EC2 launcher additionally denies it) and never reads any broker credential."""
from __future__ import annotations

import json
import os
import resource
import sys
import time
from dataclasses import replace
from pathlib import Path

from . import calibration as C
from . import checkpoint as CK
from . import engine as E
from . import evaluation as EV
from . import evolve as EVO
from . import scoring as SC
from . import stats as st
from . import strategies as S
from .rng import derive_seed

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parents[1]
CRED = Path("/home/ec2-user/.config/tbots/alpaca-paper.json")


class BudgetStop(RuntimeError):
    pass


def rss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def dir_mib(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1048576.0


def read_meminfo() -> dict:
    out = {}
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":")
            out[k] = int(v.split()[0]) / 1024.0
    except OSError:
        pass
    return out


def preflight(out: Path) -> dict:
    mi = read_meminfo()
    st_ = os.statvfs(out.parent if not out.exists() else out)
    free_disk = st_.f_bavail * st_.f_frsize / 1048576.0
    cred_readable = False
    try:
        with open(CRED):
            cred_readable = True
    except OSError:
        pass
    return {"python": sys.version.split()[0], "cpus": os.cpu_count(), "mem_available_mib": mi.get("MemAvailable"),
            "free_disk_mib": free_disk, "broker_credential_readable_by_this_process": cred_readable,
            "uid": os.getuid(), "pid": os.getpid(), "env_has_alpaca_vars": any(k.startswith(("APCA", "ALPACA")) for k in os.environ)}


def write_json(path: Path, obj):
    CK.write_atomic(path, json.dumps(obj, indent=1, sort_keys=True, default=str).encode())


def frozen_hashes(cal_digest: str) -> dict:
    return EV.code_hashes(HERE, cal_digest)


def run(out_dir: str, smoke: bool = False, resume: bool = False, wall_seconds: float = 7200.0, mem_cap_mib: float = 800.0, out_cap_mib: float = 1024.0,
        benchmark_only: bool = False) -> dict:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    t_start = time.monotonic(); cpu0 = time.process_time()
    exp_id = EV.EXP_ID if not smoke else f"SYNGYM-SMOKE-{derive_seed('smoke', out.name) % 10**6}"
    receipt = {"experiment_id": exp_id, "smoke": smoke, "preflight": preflight(out), "phases": {}, "limits": {"wall_seconds": wall_seconds, "mem_cap_mib": mem_cap_mib, "out_cap_mib": out_cap_mib}}
    if receipt["preflight"]["broker_credential_readable_by_this_process"] or receipt["preflight"]["env_has_alpaca_vars"]:
        raise SystemExit("REFUSING: a broker credential is readable by / present in this simulation process")
    data_dir = REPO / "data" / "normalized"
    cal = C.build(data_dir)
    hashes = frozen_hashes(C.dev_data_digest(data_dir))
    frozen_file = HERE / "configs" / "FROZEN_HASHES.json"
    if not smoke and frozen_file.exists():
        want = json.loads(frozen_file.read_text())
        diff = {k: (want[k], hashes[k]) for k in want if k in hashes and want[k] != hashes[k]}
        if diff:
            raise SystemExit(f"REFUSING: code/spec hashes differ from FROZEN_HASHES.json: {sorted(diff)}")
    # ------------------------------------------------------------------ worlds (training + validation only; sealed stays unopened)
    t0 = time.monotonic()
    train = EV.Suite("training", cal, exp_id); valid = EV.Suite("validation", cal, exp_id)
    if smoke:
        train.worlds = train.worlds[:2]; valid.worlds = valid.worlds[:2]
    world_hashes = {"training": train.world_hashes(), "validation": valid.world_hashes(),
                    "sealed_test": {"opened": False, "seeds": {sp.world_id: sp.seed for sp in EV.suite_specs("sealed_test", exp_id)}}}
    write_json(out / "world_hashes.json", world_hashes)
    write_json(out / "frozen_hashes_observed.json", hashes)
    receipt["phases"]["worlds"] = {"seconds": time.monotonic() - t0, "n_training": len(train.worlds), "n_validation": len(valid.worlds)}
    from . import generator as _G
    P = E.EngineParams(participation_cap=_G.CFG["volume_participation_cap"])    # frozen spec: fills capped at 5% of session volume, remainder deferred
    refs = S.load_references()

    # ------------------------------------------------------------------ throughput benchmark (small, measured)
    t0 = time.monotonic()
    n = 0
    for name, r in refs.items():
        EV.evaluate({**r, "name": name}, train, P); n += len(train.worlds)
    bench = {"genome_world_evaluations": n, "seconds": time.monotonic() - t0}
    bench["seconds_per_genome_world"] = bench["seconds"] / n
    full_evals = EVO.POP * 5 * 12                               # upper bound: every candidate fresh in every generation
    bench["worst_case_evolution_seconds"] = full_evals * bench["seconds_per_genome_world"]
    bench["expected_evolution_seconds_given_survivor_cache"] = (32 + 4 * 21) * 12 * bench["seconds_per_genome_world"]
    write_json(out / "benchmark.json", bench); receipt["benchmark"] = bench
    if benchmark_only:
        write_json(out / "resource_receipt.json", receipt)
        return receipt

    # ------------------------------------------------------------------ evolution
    budget = EVO.Budget(wall_seconds - (time.monotonic() - t_start))
    base_eval = EV.Evaluator(train, P)
    n_calls = {"n": 0}

    def guarded_eval(cand):
        if rss_mib() > mem_cap_mib:
            raise BudgetStop(f"memory {rss_mib():.0f} MiB > cap {mem_cap_mib}")
        if dir_mib(out) > out_cap_mib:
            raise BudgetStop(f"output {dir_mib(out):.0f} MiB > cap {out_cap_mib}")
        n_calls["n"] += 1
        return base_eval(cand)

    max_gen = 5 if not smoke else 2
    pop_backup = EVO.POP
    if smoke:
        EVO.POP, EVO.N_TREND0, EVO.N_D0, EVO.SURVIVORS = 8, 2, 3, 2
    t0 = time.monotonic()
    ck_hashes = {"spec": hashes["spec"], "scoring": hashes["scoring"], "generator": hashes["generator"], "engine": hashes["engine"],
                 "worlds": {**world_hashes["training"]}}
    try:
        state = EVO.run(exp_id, out, refs, guarded_eval, S.mutate_child, lambda g: __import__("lib.ids", fromlist=["genome_id"]).genome_id(g),
                        budget, ck_hashes, max_generations=max_gen, resume=resume)
    except BudgetStop as e:
        state = CK.load_latest(out / "checkpoints") or {"generation": -1, "rows": [], "history": []}
        state["halt_reason"] = f"BudgetStop: {e}"
    finally:
        EVO.POP = pop_backup
    receipt["phases"]["evolution"] = {"seconds": time.monotonic() - t0, "generations_completed": state["generation"] + 1, "evaluations": n_calls["n"], "halt_reason": state.get("halt_reason")}
    write_json(out / "leaderboard_final_generation.json", [{k: r[k] for k in ("genome_id", "family", "name", "origin", "summary")} for r in sorted(state["rows"], key=SC.ordering_key)])
    nom = EVO.nominee(state["rows"]) if state["rows"] else None
    result = {"experiment_id": exp_id, "generations_completed": state["generation"] + 1, "halt_reason": state.get("halt_reason"),
              "nominee": None if nom is None else {k: nom[k] for k in ("genome_id", "family", "name", "origin", "summary")},
              "n_eligible_final": sum(r["summary"]["eligible"] for r in state["rows"]), "population": len(state["rows"])}

    # ------------------------------------------------------------------ reference reports on TRAINING (always)
    t0 = time.monotonic()
    entries = {n: {**r, "name": n} for n, r in refs.items()}
    if nom is not None:
        entries["nominee"] = {**nom, "name": "nominee"}
    result["training_references"] = {}
    for n, e in entries.items():
        ev = EV.evaluate(e, train, P)
        result["training_references"][n] = ev["summary"] | {"by_family": EV.by_family(ev["per_world"]), "per_world": ev["per_world"]}
    receipt["phases"]["reference_reports"] = {"seconds": time.monotonic() - t0}

    # ------------------------------------------------------------------ nomination -> validation (exactly once) -> sealed test (only if validation passes)
    phase_file = out / "phase_state.json"
    phase = json.loads(phase_file.read_text()) if phase_file.exists() else {"validation_done": False, "test_done": False}
    if nom is None:
        result["outcome"] = "NO QUALIFYING WINNER: no genome met eligibility in the final generation; validation and sealed test were NOT run"
    elif not phase["validation_done"]:
        t0 = time.monotonic()
        val_entries = {n: e for n, e in entries.items()}
        val = {n: EV.evaluate(e, valid, P) for n, e in val_entries.items()}
        vs = val["nominee"]["summary"]
        vq = st.quantile([m["excess_vs_passive18"] for m in val["nominee"]["per_world"]], .25)
        vpass = bool(vs["eligible"] and vq > 0)
        result["validation"] = {"nominee_lower_quartile_excess": vq, "nominee_eligible": vs["eligible"], "PASS": vpass,
                                "all": {n: v["summary"] for n, v in val.items()}, "per_world_nominee": val["nominee"]["per_world"]}
        phase.update(validation_done=True, validation_pass=vpass); write_json(phase_file, phase)
        write_json(out / "validation_result.json", result["validation"])
        receipt["phases"]["validation"] = {"seconds": time.monotonic() - t0}
        if vpass and not phase["test_done"]:
            t0 = time.monotonic()
            test = EV.Suite("sealed_test", cal, exp_id)
            if smoke:
                test.worlds = test.worlds[:2]
            world_hashes["sealed_test"] = {"opened": True, "hashes": test.world_hashes()}; write_json(out / "world_hashes.json", world_hashes)
            tres = {n: EV.evaluate(e, test, P) for n, e in entries.items()}
            tq = st.quantile([m["excess_vs_passive18"] for m in tres["nominee"]["per_world"]], .25)
            tpass = bool(tres["nominee"]["summary"]["eligible"] and tq > 0)
            result["sealed_test"] = {"nominee_lower_quartile_excess": tq, "PASS": tpass, "all": {n: v["summary"] for n, v in tres.items()}, "per_world_nominee": tres["nominee"]["per_world"]}
            phase.update(test_done=True, test_pass=tpass); write_json(phase_file, phase)
            write_json(out / "sealed_test_result.json", result["sealed_test"])
            receipt["phases"]["sealed_test"] = {"seconds": time.monotonic() - t0}
            result["outcome"] = "RESEARCH_CANDIDATE" if tpass else "nominee passed validation but did NOT pass the sealed synthetic test"
        else:
            result["outcome"] = "nominee did NOT pass validation; sealed test stayed sealed" if not vpass else "validation passed"
    else:
        result["outcome"] = "validation already executed in an earlier invocation (exactly-once): see validation_result.json"

    # ------------------------------------------------------------------ robustness (references + nominee): cost cases, native exposure, execution stress
    if not benchmark_only and not smoke:
        t0 = time.monotonic()
        rob_entries = entries
        result["robustness_training"] = {"cost_cases": {k: {n: {"primary_score": v["primary_score"], "eligible": v["eligible"], "worst_drawdown": v["worst_drawdown"]} for n, v in d.items()}
                                                        for k, d in EV.cost_sensitivity(rob_entries, train, P).items()},
                                         "native_exposure_sensitivity": EV.native_sensitivity({n: {**r, "name": n} for n, r in refs.items()}, train, P),
                                         "execution_stress": EV.stress_report(rob_entries, train, P)}
        receipt["phases"]["robustness"] = {"seconds": time.monotonic() - t0}

    receipt["totals"] = {"wall_seconds": time.monotonic() - t_start, "cpu_seconds": time.process_time() - cpu0, "peak_rss_mib": rss_mib(), "output_mib": dir_mib(out)}
    write_json(out / "campaign_result.json", result)
    write_json(out / "resource_receipt.json", receipt)
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("--smoke", action="store_true"); ap.add_argument("--resume", action="store_true")
    ap.add_argument("--benchmark-only", action="store_true"); ap.add_argument("--wall-seconds", type=float, default=7200.0)
    ap.add_argument("--mem-cap-mib", type=float, default=800.0); ap.add_argument("--out-cap-mib", type=float, default=1024.0)
    a = ap.parse_args()
    r = run(a.out, a.smoke, a.resume, a.wall_seconds, a.mem_cap_mib, a.out_cap_mib, a.benchmark_only)
    print(json.dumps({k: r[k] for k in r if k in ("experiment_id", "outcome", "generations_completed", "halt_reason", "benchmark")}, indent=1, default=str))
