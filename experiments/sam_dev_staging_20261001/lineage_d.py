"""Lineage-D (defensive rotation) shadow wiring for gen_d890eb9f...

Decision logic is the ACCEPTED function, called unmodified:
s6a_runtime.decide_D(genome, history, step). Nothing here reimplements it.
State semantics are the accepted evaluator's: a bare integer `step` (sessions
since this shadow book's session zero); D decides only when
step % rebalance_sessions == 0 and otherwise returns None (hold); there is no
catch-up of a missed rebalance (that is a property of the *champion pilot's*
runner, not of lineage D); the 12% external halt (s6a_final.DRAWDOWN_HALT)
latches on the shadow portfolio and liquidates to cash.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import s6a_final as p
import s6a_runtime as r

EXPECTED_ID = "gen_d890eb9faa7bf0309773c85620eb6652ad589ab82d6fbff4423ddcfbbb460f85"
GENOME_FILE = Path(__file__).resolve().parent / "lineage_d_genome_d890eb9f.json"


class GenomeIdentityError(RuntimeError):
    pass


def independent_genome_id(genome: dict) -> str:
    """sha256 of canonical JSON, written out here on purpose rather than
    imported from lib.ids, so the check does not depend on the code under test."""
    blob = json.dumps(genome, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "gen_" + hashlib.sha256(blob).hexdigest()


def load_verified_genome(path: Path = GENOME_FILE) -> dict:
    doc = json.loads(Path(path).read_text())
    g = doc["genome"]
    got = independent_genome_id(g)
    if got != EXPECTED_ID or doc["genome_id"] != EXPECTED_ID:
        raise GenomeIdentityError(f"genome hash {got} != expected {EXPECTED_ID}")
    if r.validate_genome("D", g) != EXPECTED_ID:  # accepted frozen-schema validation + Trader-A isolation
        raise GenomeIdentityError("validate_genome returned a different id")
    return g


def decide(genome: dict, adjusted_history: dict, step: int):
    """-> {symbol: weight} over the tradeable universe, or None on a non-rebalance step.
    `adjusted_history[s]` is the list of adjusted closes up to and including the
    session being decided (oldest first), exactly what the evaluator passes as `h`."""
    w = r.decide_D(genome, adjusted_history, step)
    if w is None:
        return None
    return {s: v for s, v in w.items() if s in p.UNIVERSE}  # drop the folded-in CASH residual


def warmup(genome: dict) -> int:
    return r.warmup("D", genome)


def risk_bounds(genome: dict) -> tuple[float, float]:
    return genome["max_asset_weight"], genome["gross_exposure_cap"]
