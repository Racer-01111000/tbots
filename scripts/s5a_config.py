"""Frozen S5A protocol and deterministic genome operations."""
import hashlib
import json
import math
import random
from pathlib import Path

from genome_control import CONTROL_GENOME
from lib.ids import canonical_json, genome_id

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_DIR = ROOT / "evolution" / "protocol"
DEVELOPMENT_LANE_HASH = "lane_a02f9a435d21bd21722356da8280a45b9454e653987174b897244ec9746d1dc2"
DATASET_REVISION = "ds_7e16896c873671fe86ac416b24a0ce74502249a8a0fc33603e0f1935e5fab131"
CONTROL_GENOME_ID = "gen_2c07fe86679157fd87c8c9c287de223af254700eb93adf3998a20841317ab251"
AUTHORIZED_DEVELOPMENT_BUNDLE_REVISION = "s5adev_98e2f764f466b90ee2bbc2532b75188bfc4fd20b4a13523f94bce65e6a1f193a"

EPISODE_HASH = "s5a_episode_c8d3705ee8306daa517fc886808db68d97814fbca0a8fc5113a9c2a70dc15946"
FITNESS_HASH = "s5a_fitness_3525d7ce8ab1c5e9b716d98ebe975aabb14e60201dc660600e6b0fb69b1b5b40"
MUTATION_HASH = "s5a_mutation_4df543a239fea6a9bdb680810a171acdd684a29c81a550c0d47048643066ebfe"
POPULATION_HASH = "s5a_population_517b6b00e318172574511d50f734471ed451fafff3d9b32221cd452152be228c"

PROTOCOL_PATHS = {
    EPISODE_HASH: PROTOCOL_DIR / f"episode_manifest_{EPISODE_HASH}.json",
    FITNESS_HASH: PROTOCOL_DIR / f"fitness_formula_{FITNESS_HASH}.json",
    MUTATION_HASH: PROTOCOL_DIR / f"mutation_bounds_{MUTATION_HASH}.json",
    POPULATION_HASH: PROTOCOL_DIR / f"population_rules_{POPULATION_HASH}.json",
}


class ProtocolError(ValueError):
    pass


def _load_protocol(expected_hash: str) -> dict:
    path = PROTOCOL_PATHS[expected_hash]
    envelope = json.loads(path.read_text())
    if set(envelope) != {"content", "manifest_hash"}:
        raise ProtocolError(f"invalid protocol envelope: {path}")
    prefix = expected_hash.rsplit("_", 1)[0] + "_"
    actual = prefix + hashlib.sha256(canonical_json(envelope["content"]).encode()).hexdigest()
    if envelope["manifest_hash"] != expected_hash or actual != expected_hash:
        raise ProtocolError(f"protocol hash mismatch: {path}")
    if expected_hash not in path.name:
        raise ProtocolError(f"protocol filename is not content addressed: {path}")
    return envelope["content"]


EPISODE_PROTOCOL = _load_protocol(EPISODE_HASH)
FITNESS_PROTOCOL = _load_protocol(FITNESS_HASH)
MUTATION_PROTOCOL = _load_protocol(MUTATION_HASH)
POPULATION_PROTOCOL = _load_protocol(POPULATION_HASH)

EVOLUTION_SEED = POPULATION_PROTOCOL["evolution_seed"]
POPULATION_SIZE = POPULATION_PROTOCOL["population_size"]
FINAL_GENERATION = POPULATION_PROTOCOL["final_generation"]
MUTABLE = MUTATION_PROTOCOL["mutable"]
MUTABLE_NAMES = tuple(sorted(MUTABLE))
MAX_LEGAL_PRICE_BARS = max(
    MUTABLE["momentum_slow"]["max"] + 1,
    MUTABLE["trend_filter_window"]["max"],
    MUTABLE["volatility_window"]["max"] + 1,
)
PREDEVELOPMENT_WARMUP_BARS = MAX_LEGAL_PRICE_BARS - 1
WARMUP_POLICY = {
    "basis": "per-asset trading bars strictly before DEVELOPMENT start",
    "maximum_slow_momentum_sessions": MUTABLE["momentum_slow"]["max"],
    "maximum_trend_filter_price_bars": MUTABLE["trend_filter_window"]["max"],
    "maximum_volatility_returns": MUTABLE["volatility_window"]["max"],
    "maximum_required_price_bars_including_current": MAX_LEGAL_PRICE_BARS,
    "maximum_predevelopment_bars_per_asset": PREDEVELOPMENT_WARMUP_BARS,
    "missing_history_policy": "INSUFFICIENT_HISTORY_NOT_ELIGIBLE",
    "fabrication_backfill_interpolation": "PROHIBITED",
}

if genome_id(CONTROL_GENOME) != CONTROL_GENOME_ID:
    raise ProtocolError("frozen S4 control genome identity changed")
if EPISODE_PROTOCOL["lane_manifest_hash"] != DEVELOPMENT_LANE_HASH:
    raise ProtocolError("episode protocol is not bound to DEVELOPMENT")
if EPISODE_PROTOCOL["dataset_revision"] != DATASET_REVISION:
    raise ProtocolError("episode protocol dataset changed")


def derive_seed(*parts) -> int:
    payload = canonical_json([EVOLUTION_SEED, *parts]).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & ((1 << 63) - 1)


def mutable_values(genome: dict) -> dict:
    fast, medium, slow = genome["momentum_lookbacks"]
    return {
        "momentum_fast": fast,
        "momentum_medium": medium,
        "momentum_slow": slow,
        "trend_filter_window": genome["trend_filter_window"],
        "volatility_window": genome["volatility_window"],
        "max_positions": genome["max_positions"],
        "target_max_exposure": genome["target_max_exposure"],
        "max_asset_weight": genome["max_asset_weight"],
        "rebalance_every_n_sessions": genome["rebalance_every_n_sessions"],
    }


def genome_from_values(values: dict) -> dict:
    immutable = MUTATION_PROTOCOL["immutable"]
    return {
        "strategy_family": immutable["strategy_family"],
        "universe": list(immutable["universe"]),
        "momentum_lookbacks": [
            values["momentum_fast"], values["momentum_medium"], values["momentum_slow"]
        ],
        "trend_filter_window": values["trend_filter_window"],
        "volatility_window": values["volatility_window"],
        "max_positions": values["max_positions"],
        "target_max_exposure": values["target_max_exposure"],
        "max_asset_weight": values["max_asset_weight"],
        "direction": immutable["direction"],
        "leverage": immutable["leverage"],
        "shorting": immutable["shorting"],
        "rebalance_every_n_sessions": values["rebalance_every_n_sessions"],
        "drawdown_halt_pct": immutable["drawdown_halt_pct"],
    }


def _on_grid(value, spec) -> bool:
    scale = 100 if spec["type"] == "decimal" else 1
    v = round(value * scale)
    lo = round(spec["min"] * scale)
    step = round(spec["step"] * scale)
    return abs(value * scale - v) < 1e-9 and (v - lo) % step == 0


def validate_genome(genome: dict) -> None:
    if genome_from_values(mutable_values(genome)) != genome:
        raise ProtocolError("genome contains a changed immutable field or unexpected structure")
    values = mutable_values(genome)
    for name, value in values.items():
        spec = MUTABLE[name]
        if not spec["min"] <= value <= spec["max"] or not _on_grid(value, spec):
            raise ProtocolError(f"{name} outside frozen bounds/grid: {value}")
    if not (values["momentum_fast"] < values["momentum_medium"] < values["momentum_slow"]):
        raise ProtocolError("momentum lookbacks must satisfy fast < medium < slow")


def _random_value(rng: random.Random, spec):
    if spec["type"] == "decimal":
        lo, hi = round(spec["min"] * 100), round(spec["max"] * 100)
        step = round(spec["step"] * 100)
        return rng.randrange(lo, hi + 1, step) / 100
    return rng.randrange(spec["min"], spec["max"] + 1, spec["step"])


def random_genome(rng: random.Random) -> dict:
    while True:
        values = {name: _random_value(rng, MUTABLE[name]) for name in MUTABLE_NAMES}
        genome = genome_from_values(values)
        try:
            validate_genome(genome)
            return genome
        except ProtocolError:
            continue


def _mutated_value(current, spec, rng: random.Random):
    scale = 100 if spec["type"] == "decimal" else 1
    current_i = round(current * scale)
    lo, hi = round(spec["min"] * scale), round(spec["max"] * scale)
    max_delta = round(spec["mutation_max_delta"] * scale)
    step = round(spec["step"] * scale)
    deltas = [d for d in range(-max_delta, max_delta + 1, step) if d and lo <= current_i + d <= hi]
    if not deltas:
        return current
    value = current_i + rng.choice(deltas)
    return value / scale if spec["type"] == "decimal" else value


def mutation_magnitude(before: dict, after: dict) -> float:
    total = 0.0
    for name in MUTABLE_NAMES:
        if before[name] != after[name]:
            span = MUTABLE[name]["max"] - MUTABLE[name]["min"]
            total += ((after[name] - before[name]) / span) ** 2
    return round(math.sqrt(total), 12)


def mutate_genome(parent: dict, rng: random.Random) -> tuple[dict, dict, float]:
    validate_genome(parent)
    before = mutable_values(parent)
    limits = MUTATION_PROTOCOL["child_mutated_gene_count"]
    for _ in range(1000):
        after = dict(before)
        names = rng.sample(list(MUTABLE_NAMES), rng.randint(limits["min"], limits["max"]))
        for name in names:
            after[name] = _mutated_value(after[name], MUTABLE[name], rng)
        child = genome_from_values(after)
        try:
            validate_genome(child)
        except ProtocolError:
            continue
        if child == parent:
            continue
        changes = {
            name: {"before": before[name], "after": after[name], "delta": after[name] - before[name]}
            for name in MUTABLE_NAMES if before[name] != after[name]
        }
        return child, changes, mutation_magnitude(before, after)
    raise ProtocolError("unable to create a valid bounded mutation")


def genome_distance(a: dict, b: dict) -> float:
    av, bv = mutable_values(a), mutable_values(b)
    distance = 0.0
    for name in MUTABLE_NAMES:
        span = MUTABLE[name]["max"] - MUTABLE[name]["min"]
        distance += abs(av[name] - bv[name]) / span
    return distance / len(MUTABLE_NAMES)


def population_diversity(genomes: list[dict]) -> float:
    if len(genomes) < 2:
        return 0.0
    distances = [
        genome_distance(genomes[i], genomes[j])
        for i in range(len(genomes)) for j in range(i + 1, len(genomes))
    ]
    return round(sum(distances) / len(distances), 12)
