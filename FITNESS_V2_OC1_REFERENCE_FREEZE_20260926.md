# Fitness V2 OC1 reference freeze

Supersedes: `FITNESS_V2_OC1_REFERENCE_STOP_20260926.md` (not deleted — retained as the
investigation record and provenance for this decision).

Authority: Rick, 2026-09-26 GO ("RESOLVE FINAL PRE-RESULT FITNESS V2 STOPS / AUTHORIZE
NODE ACCEPTANCE"), item 1.

## Ruling

Handoff §37 governs. Fitness V2 has two distinct passive concepts that must never be
collapsed into one:

- the primary passive comparator, exposure-matched to each candidate's own declared
  exposure (used for `family_signal_deltas` / `PASSIVE_PARITY_OR_WORSE`);
- a separate, standalone 80% `PASSIVE_ENVELOPE`, used specifically as the OC1 anomaly
  diagnostic's reference.

OC1 must use the 80% `PASSIVE_ENVELOPE`, not the prior `CONTROL_GENOME`-exposure-matched
implementation.

## What changed

- `scripts/fitness_v2_oc1_reference.py` (new): a standalone, content-addressed
  `PASSIVE_ENVELOPE` manifest (`target_max_exposure: 0.80, max_asset_weight: 0.35`),
  mirroring the pre-Fitness-V2 `PASSIVE_ENVELOPE_CONTROL` construction exactly
  (`experiments/controls_interpretation_holdout_20260921T140000Z/holdout_controls.py`,
  re-declared in `experiments/d_exposure_isolation_20260923T052542Z/PREDECLARATION.md`),
  plus `validate_passive_envelope`.
- `scripts/fitness_v2_campaign.py::reference_passive_sharpes` now calls
  `simulate_passive_comparator` with `PASSIVE_ENVELOPE` instead of `CONTROL_GENOME`.
  `simulate_passive_comparator` already implements the envelope construction generically
  (it reads only `target_max_exposure` from whatever "genome" it is given), so no simulator
  change was needed.
- The per-candidate `exposure_matched_passive_sharpe` path in
  `evaluate_genome_against_worlds` is untouched — it still calls
  `simulate_passive_comparator` with the candidate's own genome. The two are proven
  independent in `tests/test_fitness_v2_campaign.py::ReferencePassiveSharpes::
  test_per_candidate_exposure_matched_sharpe_uses_the_candidates_own_genome`.

## Numerical note (provenance, not a loophole)

`CONTROL_GENOME["target_max_exposure"]` is also `0.80`, so this change produces
numerically identical OC1 reference Sharpes to the prior implementation today. That is a
coincidence of `CONTROL_GENOME`'s own definition, not a declared envelope — the prior
implementation was coupled to `CONTROL_GENOME`'s identity (would have silently drifted if
`CONTROL_GENOME` ever changed) and was never content-addressed or validated. The new
`PASSIVE_ENVELOPE` is independent of `CONTROL_GENOME` and is proven so in
`tests/test_fitness_v2_campaign.py::ReferencePassiveSharpes::
test_uses_the_standalone_passive_envelope_not_control_genome` (swaps `CONTROL_GENOME`'s
exposure and confirms the OC1 reference is unaffected).

## Provenance preserved, not rewritten

The prior `reference_passive_sharpes` implementation (calling `simulate_passive_comparator`
with `CONTROL_GENOME` directly) remains in git history up to and including commit
`ec59d2f`. `FITNESS_V2_OC1_REFERENCE_STOP_20260926.md` is retained, unedited, as the record
of the ambiguity that led to this ruling.

## Tests

`tests/test_fitness_v2_oc1_reference.py` (manifest identity, drift/stale-id rejection) and
the three additions to `tests/test_fitness_v2_campaign.py::ReferencePassiveSharpes` listed
above. Full suite green (see commit for exact count) before this freeze was crossed.
