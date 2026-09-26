"""OC1 passive-Sharpe reference methodology.

Handoff §37: "A separate 80% PASSIVE_ENVELOPE is used for the OC1 anomaly
diagnostic ... Do not collapse [it with the primary exposure-matched passive
comparator] into one concept." Rick's 2026-09-26 freeze ruling
(FITNESS_V2_OC1_REFERENCE_STOP_20260926.md, resolved by
FITNESS_V2_OC1_REFERENCE_FREEZE_20260926.md) confirms: OC1's reference must
be this standalone 80% PASSIVE_ENVELOPE, not any per-candidate or
CONTROL_GENOME-coupled comparator.

PASSIVE_ENVELOPE mirrors the pre-Fitness-V2 PASSIVE_ENVELOPE_CONTROL exactly
(experiments/controls_interpretation_holdout_20260921T140000Z/holdout_controls.py;
construction re-declared in
experiments/d_exposure_isolation_20260923T052542Z/PREDECLARATION.md): equal-
weight across the 8-asset universe at 80% total exposure (0.10 per asset),
max_asset_weight 0.35 (never binding at 0.10/asset either way), a single
order batch at the first scored session, never rebalanced thereafter.
fitness_v2_simulator.simulate_passive_comparator already implements exactly
this construction generically -- it reads only target_max_exposure from
whatever "genome" it is given, ignoring the rest -- so this module supplies
a fixed, standalone envelope object rather than coupling the OC1 reference
to CONTROL_GENOME's identity (fitness_v2_campaign.py's prior
reference_passive_sharpes passed CONTROL_GENOME directly; numerically
identical today since CONTROL_GENOME["target_max_exposure"] is also 0.80,
but that was a coincidence of CONTROL_GENOME's own definition, not a
declared envelope -- see git history at and before commit ec59d2f for the
superseded implementation).
"""
from __future__ import annotations

from fitness_v2 import FitnessV2Error, content_identity

PASSIVE_ENVELOPE = {
    "target_max_exposure": 0.80,
    "max_asset_weight": 0.35,
}
PASSIVE_ENVELOPE_ID = content_identity("fitness_v2_oc1_passive_envelope_", PASSIVE_ENVELOPE)


class OC1ReferenceError(FitnessV2Error):
    pass


def validate_passive_envelope(envelope: dict) -> str:
    if envelope != {"manifest_id": PASSIVE_ENVELOPE_ID, "content": PASSIVE_ENVELOPE}:
        raise OC1ReferenceError("OC1 PASSIVE_ENVELOPE reference manifest mismatch")
    return PASSIVE_ENVELOPE_ID
