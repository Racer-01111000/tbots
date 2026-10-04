"""Bounded evolution orchestrator (NEW; does not use the Fitness V2 campaign orchestrator, generation 10, or admission machinery).
Mutation only inside each family's frozen domain; no crossover; references immutable; hard caps on attempts and wall time;
atomic per-generation checkpoints; deterministic seeds derived from identity (no hidden RNG state)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from . import checkpoint as CK
from . import scoring as SC
from .rng import Rng, derive_seed

POP = 32
SURVIVORS = 8
MAX_ATTEMPTS_PER_CHILD = 4
N_TREND0, N_D0 = 13, 16


class InsufficientChildren(RuntimeError):
    pass


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class Budget:
    wall_seconds: float
    clock: object = time.monotonic
    started: float = 0.0

    def start(self):
        self.started = self.clock()

    def used(self):
        return self.clock() - self.started

    def remaining(self):
        return self.wall_seconds - self.used()


def _cand(genome_id, family, genome, name, origin):
    return {"genome_id": genome_id, "family": family, "genome": genome, "name": name, "origin": origin}


def initial_population(exp_id: str, refs: dict, mutate, gid_of) -> list:
    pop = [_cand(r["genome_id"], r["family"], r["genome"], n, {"kind": "reference"}) for n, r in refs.items()]
    seen = {c["genome_id"] for c in pop}
    plan = [("TREND", "champion")] * N_TREND0 + [("D", ("sam", "d_primary_rank3")[i % 2]) for i in range(N_D0)]
    for slot, (family, parent_name) in enumerate(plan):
        parent = refs[parent_name]
        child = None
        for attempt in range(MAX_ATTEMPTS_PER_CHILD):
            seed = derive_seed(exp_id, "mutate", 0, slot, attempt)
            try:
                g = mutate(family, parent["genome"], seed)
            except Exception:                                    # MutationFailed or a schema error: counted as a failed attempt
                continue
            gid = gid_of(g)
            if gid not in seen:
                child = _cand(gid, family, g, f"g0s{slot}", {"kind": "mutation", "parent_id": parent["genome_id"], "seed": seed, "generation": 0, "attempt": attempt})
                break
        if child is None:
            raise InsufficientChildren(f"generation 0 slot {slot} ({family}): no valid distinct child in {MAX_ATTEMPTS_PER_CHILD} attempts")
        seen.add(child["genome_id"]); pop.append(child)
    return pop


def next_population(exp_id: str, gen: int, rows: list, refs_ids: set, mutate, gid_of) -> list:
    """rows: evaluated candidates of generation gen-1 (each with 'summary')."""
    ordered = sorted(rows, key=SC.ordering_key)
    refs = [r for r in rows if r["genome_id"] in refs_ids]
    survivors = [r for r in ordered if r["genome_id"] not in refs_ids][:SURVIVORS]
    keep = refs + survivors
    pop = [dict(c) for c in ({k: r[k] for k in ("genome_id", "family", "genome", "name", "origin")} for r in keep)]
    seen = {c["genome_id"] for c in pop}
    for slot in range(POP - len(pop)):
        child = None
        for attempt in range(MAX_ATTEMPTS_PER_CHILD):
            rng = Rng(derive_seed(exp_id, "tournament", gen, slot, attempt))
            sample = rng.sample_without_replacement(ordered, 4)
            parent = min(sample, key=SC.ordering_key)
            seed = derive_seed(exp_id, "mutate", gen, slot, attempt)
            try:
                g = mutate(parent["family"], parent["genome"], seed)
            except Exception:
                continue
            gid = gid_of(g)
            if gid not in seen:
                child = _cand(gid, parent["family"], g, f"g{gen}s{slot}", {"kind": "mutation", "parent_id": parent["genome_id"], "seed": seed, "generation": gen, "attempt": attempt})
                break
        if child is None:
            raise InsufficientChildren(f"generation {gen} slot {slot}: no valid distinct child in {MAX_ATTEMPTS_PER_CHILD} attempts; generation not formed")
        seen.add(child["genome_id"]); pop.append(child)
    return pop


def _heartbeat(out: Path, payload: dict):
    CK.write_atomic(out / "heartbeat.json", json.dumps(payload, sort_keys=True, default=str).encode())


def run(exp_id: str, out_dir: Path, refs: dict, evaluator, mutate, gid_of, budget: Budget, hashes: dict, max_generations: int = 5,
        resume: bool = False, stop_after_generation: int | None = None) -> dict:
    """Runs generations 0..max_generations-1. `evaluator(candidate) -> {'per_world': [...], 'summary': {...}}`."""
    out = Path(out_dir); ck_dir = out / "checkpoints"
    refs_ids = {r["genome_id"] for r in refs.values()}
    state = None
    if resume:
        state = CK.load_latest(ck_dir)             # CheckpointCorrupt propagates: refuse, do not restart silently
        if state is not None and state["hashes"] != hashes:
            raise CK.CheckpointCorrupt("checkpoint was produced under different spec/scoring/generator/world hashes; refusing to resume")
    elif CK.list_generations(ck_dir):
        raise CK.CheckpointCorrupt("checkpoints already exist and resume was not requested; refusing to overwrite")
    budget.start()
    if state is None:
        state = {"format": 1, "experiment_id": exp_id, "hashes": hashes, "generation": -1, "rows": [], "history": [], "halt_reason": None,
                 "rng": {"scheme": "derive_seed(exp_id, 'mutate'|'tournament', generation, slot, attempt)", "stateless": True}}
    gen_times = []
    g = state["generation"] + 1
    cache = {r["genome_id"]: r for r in state["rows"]}
    while g < max_generations:
        if stop_after_generation is not None and g > stop_after_generation:
            break
        if gen_times and budget.remaining() < 1.15 * max(gen_times):
            state["halt_reason"] = f"budget: {budget.remaining():.0f}s left < expected {1.15 * max(gen_times):.0f}s for generation {g}"
            break
        t0 = budget.used()
        try:
            pop = initial_population(exp_id, refs, mutate, gid_of) if g == 0 else next_population(exp_id, g, state["rows"], refs_ids, mutate, gid_of)
        except InsufficientChildren as e:
            state["halt_reason"] = f"insufficient valid children: {e}"
            break
        rows = []
        for i, c in enumerate(pop):
            if c["genome_id"] in cache:
                r = dict(cache[c["genome_id"]]); r["name"] = c["name"]
            else:
                res = evaluator(c)
                r = dict(c); r["per_world"] = res["per_world"]; r["summary"] = res["summary"]
                cache[c["genome_id"]] = r
            rows.append(r)
            _heartbeat(out, {"generation": g, "candidate_index": i, "of": len(pop), "elapsed_s": budget.used(), "genome_id": c["genome_id"]})
        state["rows"] = rows
        state["generation"] = g
        ordered = sorted(rows, key=SC.ordering_key)
        state["history"].append({"generation": g, "elapsed_s": budget.used() - t0, "n_eligible": sum(r["summary"]["eligible"] for r in rows),
                                 "best": {"genome_id": ordered[0]["genome_id"], "primary_score": ordered[0]["summary"]["primary_score"], "eligible": ordered[0]["summary"]["eligible"]},
                                 "ids": [r["genome_id"] for r in rows]})
        CK.save(ck_dir, g, state)                     # atomic: a crash before this line loses ONLY this generation
        gen_times.append(budget.used() - t0)
        g += 1
    return state


def nominee(rows: list):
    """best ELIGIBLE genome of the final generation, or None ('no qualifying winner')"""
    elig = [r for r in rows if r["summary"]["eligible"]]
    return sorted(elig, key=SC.ordering_key)[0] if elig else None
