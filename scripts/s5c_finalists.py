"""Performance-free loader for the three accepted S5C finalists."""
from dataclasses import dataclass
import json

from lib.ids import canonical_json, genome_id
from s5b_frozen import load_frozen_top10
from s5c_config import (
    FINALIST_IDS,
    S5CProtocolError,
    assert_frozen_execution_semantics,
    load_advancement_manifest,
    required_price_bars,
)


@dataclass(frozen=True)
class ChampionshipFinalist:
    selection_order: int
    agent_id: str
    genome_id: str
    _genome_json: str

    @property
    def genome(self) -> dict:
        return json.loads(self._genome_json)


def load_frozen_finalists() -> tuple[ChampionshipFinalist, ...]:
    advancement = load_advancement_manifest()
    top10 = {row.genome_id: row for row in load_frozen_top10()}
    selected = advancement["selected_finalists"]
    finalists = []
    for expected_order, identity in enumerate(selected, 1):
        genome_id_value = identity["genome_id"]
        frozen = top10.get(genome_id_value)
        if frozen is None or identity["selection_order"] != expected_order:
            raise S5CProtocolError("accepted finalist is absent from frozen top ten")
        genome = frozen.genome
        assert_frozen_execution_semantics(genome)
        if (genome_id(genome) != genome_id_value
                or genome_id_value != "gen_" + identity["genome_content_sha256"]):
            raise S5CProtocolError("finalist immutable genome identity changed")
        if required_price_bars(genome) < 2:
            raise S5CProtocolError("finalist warmup requirement is invalid")
        finalists.append(ChampionshipFinalist(
            expected_order, frozen.agent_id, genome_id_value, canonical_json(genome)
        ))
    if tuple(row.genome_id for row in finalists) != FINALIST_IDS:
        raise S5CProtocolError("frozen finalist order or membership changed")
    return tuple(finalists)
