"""Per-family deterministic seed stream for TBOTS Fitness V2 world-league
world generation (Rick, 2026-09-26 -- replacement/expansion seed formula,
Sable handoff sections 40-69).

One seed stream per family, shared by replacement and expansion draws, with
a single monotonically advancing index. The four frozen PARAMETER_FREEZE
seeds are indices 1-4 for each family and are never derived here -- this
module only derives index 5 and above. A rejected attempt (whether from a
zero/duplicate seed at draw time, or a world that later fails admission)
still consumes its index and advances the stream permanently: an index is
never reused or skipped. Slots in the world bank are filled by ACCEPTED
worlds, not by attempted seeds -- rejection here or downstream both simply
mean "try the next index."

Nothing in this module reads real market data, generates a world, or
depends on world generation being unblocked. It is pure and deterministic:
safe to build and test during the freeze audit window.
"""
import hashlib
from collections.abc import Sequence

STREAM_NAMESPACE = "TBOTS-FITNESS-V2-WORLD-LEAGUE-20260925"
FAMILIES = ("distributional", "execution", "sequence", "shock")
FROZEN_INDICES = (1, 2, 3, 4)


class SeedStreamError(ValueError):
    pass


def _digest_seed(payload: str) -> int:
    digest = hashlib.sha256(payload.encode()).digest()
    return int.from_bytes(digest[:4], "big")


def derive_seed(family: str, stream_index: int, *, used_seeds: Sequence[int] = ()) -> dict:
    """Derives the seed for `family` at `stream_index` (>=5). Retries with an
    appended "|retry|<n>" suffix, rehashing each time, until the result is
    both nonzero and absent from `used_seeds`. Returns
    {"seed": int, "attempts": [{"payload", "seed", "rejected_reason"}, ...]}
    -- the list documents every rejected hash-collision/zero attempt, always
    ending in the one accepted seed."""
    if family not in FAMILIES:
        raise SeedStreamError(f"unknown family: {family}")
    if isinstance(stream_index, bool) or not isinstance(stream_index, int) or stream_index < 5:
        raise SeedStreamError("stream_index must be an integer >= 5 (indices 1-4 are frozen)")
    seen = set(used_seeds)
    attempts = []
    retry = 0
    while True:
        payload = f"{STREAM_NAMESPACE}|world|{family}|{stream_index}"
        if retry > 0:
            payload += f"|retry|{retry}"
        seed = _digest_seed(payload)
        if seed == 0:
            rejected_reason = "zero_seed"
        elif seed in seen:
            rejected_reason = "duplicate_seed"
        else:
            rejected_reason = None
        attempts.append({"payload": payload, "seed": seed, "rejected_reason": rejected_reason})
        if rejected_reason is None:
            return {"seed": seed, "attempts": attempts}
        retry += 1


def next_stream_index(consumed_indices: Sequence[int]) -> int:
    """The next index a family's stream will draw: one past the highest
    index ever consumed (frozen, accepted, or rejected) for that family.
    The frozen four (1-4) must already be present -- a stream never starts
    anywhere else."""
    consumed = set(consumed_indices)
    if not set(FROZEN_INDICES) <= consumed:
        raise SeedStreamError(
            f"the four frozen indices {FROZEN_INDICES} must already be consumed"
        )
    return max(consumed) + 1


def draw_next(family: str, consumed_indices: Sequence[int], used_seeds: Sequence[int]) -> dict:
    """Convenience wrapper: computes the next index for `family` and derives
    its seed in one call. The caller is responsible for recording the
    result (accepted or rejected) back into `consumed_indices`/`used_seeds`
    before the next draw -- this function has no persistence of its own."""
    stream_index = next_stream_index(consumed_indices)
    result = derive_seed(family, stream_index, used_seeds=used_seeds)
    return {"stream_index": stream_index, **result}
