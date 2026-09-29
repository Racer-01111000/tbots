"""Shock market-distance amendment (Rick, 2026-09-29).

Concerns the market-distance admission gate only
(fitness_v2_admission.market_distance_passes). Orthogonal to, and does not
reopen, the separate Shock warm-up resolution: Shock's 378-bar warm-up
question was already resolved on 2026-09-26 by extending it the same
available-real-history exception as Execution (see
FITNESS_V2_WARMUP_RESOLVED_FORMULAS_PENDING_20260926.md, commit 02bc2ad;
codified in fitness_v2_warmup.py's WARMUP_AMENDMENT, referenced here by
extends_warmup_amendment). This amendment supersedes nothing about that
resolution -- it is a new, additional rule about a different gate.

No production world bank, campaign, or generation existed at the time this
decision was made (verified: evolution/state/ did not exist; STATUS.json
showed campaign/generation/world_bank_id all null). No threshold tuning is
performed and no prior Shock admission attempt is retroactively accepted --
the exemption applies going forward only.
"""
from __future__ import annotations

from fitness_v2 import FitnessV2Error, content_identity
from fitness_v2_warmup import AMENDMENT_ID as WARMUP_AMENDMENT_ID


SHOCK_DISTANCE_AMENDMENT = {
    "schema_version": 1,
    "authority": "Rick",
    "supersedes": None,
    "extends_warmup_amendment": WARMUP_AMENDMENT_ID,
    "rule": {
        "family": "shock",
        "exempt_from": "market_distance_passes' >=0.75 normalized-descriptor "
                       "distance threshold only",
        "still_enforced_for_shock": [
            "known-synthetic-family check",
            "non-empty candidate/references requirement",
            "per-vector length/type/finiteness structural validation",
            "generator/base-path construction "
            "(fitness_v2_world_bank Shock builder, unchanged by this amendment)",
        ],
        "not_exempt": ["distributional", "sequence"],
        "execution_unaffected": (
            "Execution's pre-existing full exemption (no descriptor validation "
            "at all) is a separate, earlier rule and is unchanged by this "
            "amendment."
        ),
    },
    "rationale": {
        "empirical_evidence": (
            "533+ distinct Shock candidates drawn against the >=0.75 threshold, "
            "0 admissions"
        ),
        "conclusion": (
            "structural incompatibility between Shock's transformation (three "
            "authorized DEVELOPMENT donor-gap relocations from a real "
            "historical anchor) and a normalized-descriptor distance gate "
            "calibrated for fully synthetic worlds -- not a threshold that "
            "needed retuning"
        ),
        "identified_pre_result": True,
    },
    "no_threshold_tuning": True,
    "no_retroactive_acceptance_of_prior_shock_attempts": True,
}
AMENDMENT_ID = content_identity(
    "fitness_v2_shock_distance_amendment_", SHOCK_DISTANCE_AMENDMENT
)


def validate_amendment(envelope):
    if envelope != {"manifest_id": AMENDMENT_ID, "content": SHOCK_DISTANCE_AMENDMENT}:
        raise FitnessV2Error("shock distance amendment mismatch")
    return AMENDMENT_ID
