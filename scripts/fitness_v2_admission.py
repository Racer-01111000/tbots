"""Pre-result market-distance gate; never a substitute for structural validation.

Execution's exemption applies to this predicate alone. Callers must independently
validate seeds, calendars, source isolation, and execution mechanics. The complete
world builder remains blocked on the recorded H1 warm-up contract conflict.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys
from collections.abc import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from fitness_v2 import FitnessV2Error, SYNTHETIC_FAMILIES


def market_distance_passes(family: str, candidate: Sequence[float],
                           references: Sequence[Sequence[float]]) -> bool:
    """Test a synthetic candidate against every supplied anchor/admitted world.

    Historical anchors cannot be admitted/replaced through this API. Inputs are
    already DEVELOPMENT-normalized descriptors; neither inputs nor anchors are
    modified here. Execution alone does not require market-distance admission.
    """
    if family not in SYNTHETIC_FAMILIES:
        raise FitnessV2Error("distance admission requires a known synthetic family")
    if family == "execution":
        return True
    if not candidate or not references:
        raise FitnessV2Error("distance admission requires descriptors and references")
    for vector in (candidate, *references):
        if len(vector) != len(candidate) or any(
            isinstance(x, bool) or not isinstance(x, (float, int))
            or not math.isfinite(x) for x in vector
        ):
            raise FitnessV2Error("invalid normalized descriptor vector")
    return all(
        math.sqrt(math.fsum((a - b) ** 2 for a, b in zip(candidate, reference))
                  / len(candidate)) >= 0.75
        for reference in references
    )


def unchanged_h1_warmup_coverage() -> dict:
    """Audit existing authorized observations only; never construct a world."""
    from s5a_development_bundle import load_authorized_development_bundle, assert_isolated

    bundle = load_authorized_development_bundle()
    isolation = assert_isolated(bundle)
    dates = {
        symbol: {row["timestamp"] for row in rows if row["timestamp"] < "2007-02-07"}
        for symbol, rows in bundle.per_symbol_rows.items()
    }
    common = set.intersection(*dates.values())
    return {
        "bundle_revision": bundle.bundle_revision,
        "bundle_manifest_hash": bundle.bundle_manifest_hash,
        "required_synchronized_prior_bars": 378,
        "h1_start": "2007-02-07",
        "per_asset_prior_bars_including_predevelopment": {
            symbol: len(values) for symbol, values in sorted(dates.items())
        },
        "common_prior_bars_including_predevelopment": len(common),
        "common_prior_bars_from_2007_01_01": sum(d >= "2007-01-01" for d in common),
        "common_prior_bars_from_2007_02_07": 0,
        "isolation": isolation,
        "worlds_generated": 0,
        "genomes_generated": 0,
    }
