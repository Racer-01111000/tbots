# Fitness V2 OC1 reference-passive-Sharpe stop

Authority: 2026-09-26 GO ("CONDITION 4 CLEARED / CONTINUE AUTONOMY CLOSURE") — "freeze the
currently implemented OC1 passive-Sharpe methodology as an explicit pre-result protocol
addendum ... If the existing implementation contradicts an already-frozen authoritative
rule, HOLD and report instead of choosing a new outcome-dependent definition."

## What is already frozen and matches the code exactly

Handoff §26 ("EXACT OC1 ZERO-MAD DEFINITION") gives the anomaly formula verbatim:

    passive_robust_z = (passive_sharpe - reference_median) / (1.4826 * reference_MAD)
    zero-MAD sentinel: passive_sharpe != reference_median -> anomalous

This is implemented byte-for-byte in `scripts/fitness_v2_formula_definitions.py`'s
`DEFINITIONS["oc1"]` (a content-addressed, `validate_definitions`-checked manifest,
`checkpoint 02bc2ad0e3624bad28096c7b9541dd1a520fc246`) and its `passive_robust_z`/
`family_oc1` functions. No contradiction here; no freeze needed beyond what already exists.

## What is NOT resolved: which passive Sharpes are the reference

The frozen formula above takes `reference_median`/`reference_MAD` as given inputs. Where
those numbers come from is a separate question, and the current implementation
(`fitness_v2_campaign.py::reference_passive_sharpes`) is explicit in its own docstring that
it is filling this in as a "delegated-authority implementation decision, not found frozen
anywhere in the handoff or existing manifests":

    Computed once, from CONTROL_GENOME's own exposure-matched passive comparator run on
    the three real historical worlds -- a fixed reference baseline reused by every
    candidate's OC1 check.

This predates today's session (the `02bc2ad`-era OC1 formula resolution addressed the
z-score formula only — see `FITNESS_V2_EXECUTABLE_AMBIGUITY_STOP_20260925.md` item 7,
"OC1 anomaly computation ... no exact candidate-independent anomaly statistic or threshold
is frozen" — which was about the *formula*, now resolved by §26; the *reference input* to
that formula was never separately re-examined).

## The candidate contradiction

Handoff §37 ("PASSIVE COMPARATOR") distinguishes two different benchmarks:

    Core design: ... exposure-aware passive benchmark ... not post-hoc matched to
    whichever exposure makes a candidate look good.
    A separate 80% PASSIVE_ENVELOPE is used for the OC1 anomaly diagnostic.
    Read the frozen contract for the exact distinction between the primary passive
    comparator and the 80% diagnostic. Do not collapse them into one concept.

`PASSIVE_ENVELOPE_CONTROL` is an established concept from the pre-Fitness-V2 (S5A/S5D)
system: a fixed, non-adaptive, 80%-gross-exposure equal-weight buy-and-hold control,
**not** matched to any individual candidate's own exposure (see
`experiments/controls_interpretation_holdout_20260921T140000Z/interpretation.md` and
`experiments/d_exposure_isolation_20260923T052542Z/PREDECLARATION.md` for its exact prior
construction and why a fixed vs. exposure-matched control are treated as answering
different questions, not interchangeable).

§37 reads as saying Fitness V2's OC1 diagnostic specifically should use that same
fixed-80%-envelope-style reference, as distinct from the exposure-matched "primary passive
comparator" used elsewhere (signal-delta / `PASSIVE_PARITY_OR_WORSE`). The current
`reference_passive_sharpes` implementation instead uses CONTROL_GENOME's own
**exposure-matched** comparator for OC1 -- the same kind of construction §37 describes as
the *primary* comparator, not the *separate 80% envelope* it says OC1 specifically needs.

I checked for a resolving "frozen contract" as §37 directs (searched the complete protocol
manifest `fitness_v2_complete_protocol_49c71da1...json` and
`FITNESS_V2_COMPLETE_PROTOCOL_FREEZE_20260926.md` for "passive", "envelope", "OC1" — zero
matches in either) and found no artifact that defines an 80%-envelope-style OC1 reference
for Fitness V2, or that reconciles §37 with the current CONTROL_GENOME-exposure-matched
choice. I cannot rule out that §37's "frozen contract" pointer refers to something I have
not located, but nothing in the repository as checked resolves it.

## Why this isn't a freeze-and-move-on

`family_oc1`'s anomaly flag (`passive_comparator_anomalous`) feeds directly into
`evaluate_candidate`'s eligibility gate (`fitness_v2.py`) — which reference is used changes
which genomes get flagged anomalous, which is exactly the kind of selection-changing choice
AGENTS.md says to stop on rather than invent, and exactly the kind of "outcome-dependent
definition" this GO says not to freeze without checking first.

## Stop state and conservation

- No freeze addendum was written for the OC1 reference methodology.
- The existing `fitness_v2_formula_definitions.py` OC1 formula freeze is untouched and
  remains valid on its own terms.
- No world, genome, or campaign was generated or evaluated as part of this check.

## Required decision

Rick must confirm one of:

1. CONTROL_GENOME's own exposure-matched passive comparator (current code) is the correct
   OC1 reference, and §37's "separate 80% PASSIVE_ENVELOPE" either means the same thing in
   different words or was superseded — in which case say so and this becomes a one-line
   freeze addendum recording that decision; or
2. OC1 should instead use a fixed 80%-exposure `PASSIVE_ENVELOPE`-style reference (matching
   its S5A/S5D-era construction) — in which case `reference_passive_sharpes` needs to change
   before any real campaign runs, since every campaign's admission-eligibility outcome
   depends on it.

Until one of these is confirmed, do not generate a real world bank or run a real campaign
against production DEVELOPMENT data using the current `reference_passive_sharpes`.
