"""Rick's real-anchor warm-up amendments; not a complete protocol readiness gate.

This module validates existing observations only. It generates no market path,
world, genome, or missing history, and does not implement price transformations.
"""
from __future__ import annotations

from fitness_v2 import FitnessV2Error, PARAMETER_FREEZE_ID, content_identity


WARMUP_AMENDMENT = {
    "schema_version": 1,
    "authority": "Rick",
    "extends_parameter_freeze": PARAMETER_FREEZE_ID,
    "preserved_receipt_commits": [
        "9ff6582c9798e9fad70d5e3cf666238c04cf2e78",
        "008b39ad9197b02cdf629e5818a1c0a2b94a7a06",
        "69fa48db8e791ac8be8862f16dab6060f70a4acf",
    ],
    "execution": {
        "warmup": "Same available real historical warm-up as matching immutable historical anchor",
        "eligibility": "Frozen S5A insufficient-history semantics; naturally satisfy lookback before eligibility",
        "scored_price_path": "Unchanged matching historical anchor",
        "only_stress": "Frozen deterministic skipped-fill behavior",
        "market_distance_exempt": True,
    },
    "shock": {
        "warmup": "Same available real historical warm-up as matching immutable historical anchor",
        "eligibility": "Frozen S5A insufficient-history semantics; naturally satisfy lookback before eligibility",
        "scored_base_path": "Matching immutable historical anchor",
        "only_price_transformation": "Exactly three authorized DEVELOPMENT donor-gap relocations into three distinct eligible asset/session destinations, preserving destination intraday geometry",
        "same_asset_donor_destination_restriction": False,
        "gap_relocations_create_extra_history": False,
        "market_distance_exempt": False,
    },
    "historical_warmup_exceptions": {
        "families": ["execution", "shock"],
        "applies_to": ["H1", "H2", "H3"],
        "fewer_than_378_bars_permitted": True,
        "fabrication_interpolation_backfill_bootstrap": False,
        "protected_or_post_2018_sources": False,
    },
    "distributional_sequence": {
        "generated_prior_price_bars": 378,
        "stream": "Same world transformation stream",
        "scored": False,
        "historical_warmup_exception": False,
        "market_distance_exempt": False,
    },
    "resolution_status": {
        "execution_h1_stop": "Correct under then-unspecified rule; resolved by Rick",
        "shock_h1_stop": "Correct under then-unspecified rule; resolved by Rick",
        "prior_receipts": "Preserved historical evidence, not erased or rewritten",
        "complete_executable_protocol_ready": False,
        "scope": "Warm-up amendment only; full protocol blocked on separately recorded descriptor/OC1 definitions",
    },
}
AMENDMENT_ID = content_identity("fitness_v2_real_warmup_amendment_", WARMUP_AMENDMENT)


def validate_amendment(envelope):
    if envelope != {"manifest_id": AMENDMENT_ID, "content": WARMUP_AMENDMENT}:
        raise FitnessV2Error("real historical warm-up amendment mismatch")
    return AMENDMENT_ID


def warmup_policy(family):
    if family in ("execution", "shock"):
        return "MATCH_AVAILABLE_REAL_ANCHOR"
    if family in ("distributional", "sequence"):
        return "EXACT_378_SAME_TRANSFORMATION_STREAM"
    raise FitnessV2Error("warm-up amendment requires a known synthetic family")


def validate_real_warmup(family, proposed, anchor, *, scoring_start):
    """Compare with trusted immutable anchor warm-up, with no writes/padding.

    Caller obtains the anchor from the hash-verified S5A DEVELOPMENT bundle.
    Per-asset availability is preserved, including DBC's shorter H1 history.
    Pre-DEVELOPMENT rows here are warm-up only, never transformation/calibration
    sources. This predicate cannot certify a whole world or its other checks.
    """
    from genome_control import CONTROL_GENOME

    if warmup_policy(family) != "MATCH_AVAILABLE_REAL_ANCHOR":
        raise FitnessV2Error("real-history exception cannot leak to generated families")
    if scoring_start not in ("2007-02-07", "2011-02-07", "2015-02-07"):
        raise FitnessV2Error("unknown historical shape start")
    if set(anchor) != set(CONTROL_GENOME["universe"]) or proposed != anchor:
        raise FitnessV2Error("warm-up must exactly match all eight real anchor histories")
    for rows in anchor.values():
        dates = [r["timestamp"] for r in rows]
        if len(dates) > 378 or dates != sorted(set(dates)):
            raise FitnessV2Error("invalid S5A real warm-up history")
        if any(d >= scoring_start or d > "2018-12-31" for d in dates):
            raise FitnessV2Error("warm-up contains scoring/future/protected observation")
    return True
