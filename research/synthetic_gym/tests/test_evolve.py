import json
import shutil
from pathlib import Path

import pytest

from gym import checkpoint as CK, evolve as EVO, scoring as SC
from gym.rng import derive_seed

HASHES = {"spec": "s", "scoring": "c", "generator": "g", "worlds": {}}


def make_refs():
    return {"champion": {"family": "TREND", "genome": {"v": 0, "fam": "T"}, "genome_id": "gen_ref_champ"},
            "sam": {"family": "D", "genome": {"v": 0, "fam": "D1"}, "genome_id": "gen_ref_sam"},
            "d_primary_rank3": {"family": "D", "genome": {"v": 0, "fam": "D2"}, "genome_id": "gen_ref_d3"}}


def fake_mutate(family, parent, seed):
    g = dict(parent); g["v"] = (derive_seed("m", seed) % 100000); g["seed"] = seed
    return g


def gid_of(g):
    return "gen_" + json.dumps(g, sort_keys=True)


class FakeEval:
    def __init__(self, crash_on=None):
        self.calls = 0; self.crash_on = crash_on

    def __call__(self, c):
        self.calls += 1
        if self.crash_on and self.calls == self.crash_on:
            raise RuntimeError("simulated crash mid-generation")
        base = (derive_seed("score", c["genome_id"]) % 1000) / 10000.0
        dd = -0.01 - (derive_seed("dd", c["genome_id"]) % 100) / 1000.0           # -0.01 .. -0.11
        pw = [{"world_id": f"w{i}", "net_return": .1, "ann_return": .02, "excess_vs_passive18": base - i * 1e-4, "max_drawdown": dd, "violations": 0, "turnover": 1.0 + base} for i in range(12)]
        return {"per_world": pw, "summary": SC.summarize(pw)}


def run(tmp, **kw):
    refs = make_refs()
    ev = kw.pop("ev", FakeEval())
    return EVO.run("EXP", tmp, refs, ev, kw.pop("mutate", fake_mutate), gid_of, kw.pop("budget", EVO.Budget(1e9)), HASHES, **kw), ev


def test_initial_population_has_3_references_plus_29_distinct_bounded_mutants_by_family(tmp_path):
    pop = EVO.initial_population("EXP", make_refs(), fake_mutate, gid_of)
    assert len(pop) == 32 and len({c["genome_id"] for c in pop}) == 32
    muts = [c for c in pop if c["origin"]["kind"] == "mutation"]
    assert len(muts) == 29 and sum(c["family"] == "TREND" for c in muts) == 13 and sum(c["family"] == "D" for c in muts) == 16
    assert {c["genome_id"] for c in pop if c["origin"]["kind"] == "reference"} == {"gen_ref_champ", "gen_ref_sam", "gen_ref_d3"}


def test_references_survive_every_generation_unchanged_and_children_stay_in_their_parents_family(tmp_path):
    st, _ = run(tmp_path)
    assert st["generation"] == 4 and len(st["history"]) == 5
    refs = make_refs()
    for gen in range(5):
        s = CK.load(tmp_path / "checkpoints", gen)
        by = {r["genome_id"]: r for r in s["rows"]}
        for name, r in refs.items():
            assert by[r["genome_id"]]["genome"] == r["genome"] and by[r["genome_id"]]["family"] == r["family"]
        assert len(s["rows"]) == 32 and len({r["genome_id"] for r in s["rows"]}) == 32
        for r in s["rows"]:
            if r["origin"]["kind"] == "mutation":
                everyone = [x for g2 in range(gen + 1) for x in CK.load(tmp_path / "checkpoints", g2)["rows"]]
                parent = next(x for x in everyone if x["genome_id"] == r["origin"]["parent_id"])
                assert parent["family"] == r["family"]


def test_whole_campaign_is_deterministic(tmp_path):
    a, _ = run(tmp_path / "a"); b, _ = run(tmp_path / "b")
    assert [h["ids"] for h in a["history"]] == [h["ids"] for h in b["history"]]


def test_checkpoints_are_written_atomically_with_verified_hashes(tmp_path):
    run(tmp_path)
    files = sorted(p.name for p in (tmp_path / "checkpoints").iterdir())
    assert files == [f"gen_{g:03d}.{ext}" for g in range(5) for ext in ("json", "sha256")]
    assert not [p for p in (tmp_path / "checkpoints").iterdir() if ".tmp" in p.name]
    for g in range(5):
        CK.load(tmp_path / "checkpoints", g)


def test_resume_after_a_crash_equals_an_uninterrupted_run(tmp_path):
    full, _ = run(tmp_path / "full")
    ev = FakeEval(crash_on=32 * 2 + 7)                                      # dies partway through generation 2 (after caching)
    with pytest.raises(RuntimeError):
        run(tmp_path / "crash", ev=ev)
    assert CK.list_generations(tmp_path / "crash" / "checkpoints") == [0, 1]   # the unfinished generation left no checkpoint
    resumed, _ = run(tmp_path / "crash", resume=True)
    assert [h["ids"] for h in resumed["history"]] == [h["ids"] for h in full["history"]]
    assert [r["genome_id"] for r in resumed["rows"]] == [r["genome_id"] for r in full["rows"]]


def test_a_corrupt_latest_checkpoint_is_refused_not_skipped(tmp_path):
    run(tmp_path, stop_after_generation=2)
    p = tmp_path / "checkpoints" / "gen_002.json"
    raw = bytearray(p.read_bytes()); raw[len(raw) // 2] ^= 0x01; p.write_bytes(bytes(raw))
    with pytest.raises(CK.CheckpointCorrupt):
        run(tmp_path, resume=True)
    assert CK.list_generations(tmp_path / "checkpoints") == [0, 1, 2]       # nothing was deleted or rewritten


@pytest.mark.parametrize("damage", ["truncate", "delete_sidecar", "bad_sidecar", "tamper_and_fix_sidecar"])
def test_every_kind_of_checkpoint_damage_is_detected(tmp_path, damage):
    run(tmp_path, stop_after_generation=1)
    p = tmp_path / "checkpoints" / "gen_001.json"; side = p.with_suffix(".sha256")
    if damage == "truncate":
        p.write_bytes(p.read_bytes()[:-40])
    elif damage == "delete_sidecar":
        side.unlink()
    elif damage == "bad_sidecar":
        side.write_text("0" * 64 + "\n")
    else:                                                                   # attacker fixes the sidecar but the embedded payload hash catches it
        import hashlib
        d = json.loads(p.read_text()); d["rows"][0]["summary"]["primary_score"] = 9.9
        raw = json.dumps(d, sort_keys=True, separators=(",", ":")).encode(); p.write_bytes(raw); side.write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(CK.CheckpointCorrupt):
        run(tmp_path, resume=True)


def test_resuming_under_different_hashes_is_refused(tmp_path):
    run(tmp_path, stop_after_generation=0)
    refs = make_refs()
    with pytest.raises(CK.CheckpointCorrupt):
        EVO.run("EXP", tmp_path, refs, FakeEval(), fake_mutate, gid_of, EVO.Budget(1e9), {**HASHES, "scoring": "CHANGED"}, resume=True)


def test_existing_checkpoints_are_never_overwritten_without_resume(tmp_path):
    run(tmp_path, stop_after_generation=0)
    with pytest.raises(CK.CheckpointCorrupt):
        run(tmp_path)


def test_bounded_mutation_failure_stops_the_generation_in_finite_attempts(tmp_path):
    calls = {"n": 0}
    def always_fails(family, parent, seed):
        calls["n"] += 1; raise EVO.InsufficientChildren("boom") if False else RuntimeError("mutation failed closed")
    with pytest.raises(EVO.InsufficientChildren):
        EVO.initial_population("EXP", make_refs(), always_fails, gid_of)
    assert calls["n"] == EVO.MAX_ATTEMPTS_PER_CHILD                         # one slot, exactly 4 attempts, then stop (no loop)
    st, _ = run(tmp_path, mutate=always_fails)
    assert st["generation"] == -1 and "insufficient valid children" in st["halt_reason"]


def test_failure_in_a_later_generation_halts_cleanly_and_keeps_completed_generations(tmp_path):
    state = {"n": 0}
    def flaky(family, parent, seed):
        state["n"] += 1
        if state["n"] > 29 + 5:
            raise RuntimeError("closed")
        return fake_mutate(family, parent, seed)
    st, _ = run(tmp_path, mutate=flaky)
    assert st["generation"] == 0 and "insufficient valid children" in st["halt_reason"]
    assert CK.list_generations(tmp_path / "checkpoints") == [0] and CK.load(tmp_path / "checkpoints", 0)["generation"] == 0


def test_duplicate_children_are_not_admitted(tmp_path):
    def constant(family, parent, seed):
        return {"v": 1, "same": True}
    with pytest.raises(EVO.InsufficientChildren):
        EVO.initial_population("EXP", make_refs(), constant, gid_of)


def test_budget_exhaustion_halts_at_a_generation_boundary_without_extra_seeds(tmp_path):
    t = {"now": 0.0}
    def clock():
        return t["now"]
    class TickEval(FakeEval):
        def __call__(self, c):
            t["now"] += 1.0                                                  # 1 s per candidate => ~32 s per generation
            return super().__call__(c)
    st, ev = run(tmp_path, ev=TickEval(), budget=EVO.Budget(80.0, clock=clock))
    assert st["generation"] == 1 and "budget" in st["halt_reason"] and CK.list_generations(tmp_path / "checkpoints") == [0, 1]


def test_nominee_is_the_best_eligible_or_none(tmp_path):
    st, _ = run(tmp_path)
    n = EVO.nominee(st["rows"])
    assert n is not None and n["summary"]["eligible"]
    assert all(r["summary"]["primary_score"] <= n["summary"]["primary_score"] + 1e-12 for r in st["rows"] if r["summary"]["eligible"])
    for r in st["rows"]:
        r["summary"]["eligible"] = False
    assert EVO.nominee(st["rows"]) is None


def test_evaluation_results_of_survivors_are_reused_not_recomputed(tmp_path):
    st, ev = run(tmp_path)
    assert ev.calls < 5 * 32 and ev.calls >= 32
