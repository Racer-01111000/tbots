# Fitness V2 Execution exemption preserved; H1 warm-up conflict

Authority: Rick's continuing GO, received 2026-09-26. Pre-result checkpoint:
`9ff6582c9798e9fad70d5e3cf666238c04cf2e78`, clean, on
`feat/node-resident-fitness-v2-autonomous-evolution-20260925`.
That commit and its conflict receipt remain unchanged.

## Completed independent component

The authoritative Execution-only market-distance exemption is recorded in
`evolution/protocol/fitness_v2_execution_diversity_amendment_4ef367db44be987a59e41547b485decbfb8c7d9d0b75477490f9221a59b76fef.json`.
The content identity uses existing Fitness V2 canonical JSON/SHA-256 conventions,
excluding the `manifest_id` field itself.

`scripts/fitness_v2_admission.py` implements only the market-distance predicate.
Execution bypasses it; Distributional, Sequence, and Shock still require every
reference distance to be at least 0.75. Historical anchors cannot be submitted
as replacement candidates, and reference vectors are never mutated. This is not
a whole-world admission function and does not certify Execution structural
validity. No existing historical evaluator or Fitness V1 lineage was changed.

## New selection-material conflict

The prior executable-protocol GO requires:

1. H1 scoring starts `2007-02-07`; synthetic target shapes are H1,H2,H3,H1.
2. "Warm-up is exactly 378 prior price bars generated from the same world's
   transformation stream, excluded from scoring."
3. All eight assets are synchronized on a common session index.
4. Execution: "base path unchanged"; its specified transformation changes
   otherwise-valid scheduled fills, not prices.
5. Generation/calibration is DEVELOPMENT-only; the continuing resolution
   reinforces intentionally unchanged real market paths and does not relax
   calendar/horizon or other structural checks.

The authorized, hash-verified DEVELOPMENT bundle has only **252** synchronized
real sessions before H1, even when its explicitly bounded pre-DEVELOPMENT
warm-up rows are included. DBC is the limiting asset (252 prior observations;
the other seven have 378). Restricting prior source dates to the freeze's
`2007-01-01` DEVELOPMENT lower bound leaves **24** common sessions. Restricting
to scored DEVELOPMENT beginning `2007-02-07` leaves **0**.

Consequently the unchanged real path cannot supply 378 synchronized prior bars
for the H1 Execution shape. The specified fill-skipping transformation cannot
generate the missing price observations. Replacing/repeating/relabeling a
DEVELOPMENT segment for warm-up would introduce an additional price-history
construction rule not supplied for Execution; using fewer bars changes the
exact-378 requirement. S5A's existing missing-history behavior accepts fewer
available bars and makes assets ineligible, so adopting it also changes the
new exact requirement rather than satisfying it.

The distinction is outcome-material: long momentum/trend lookbacks use these
observations to determine eligibility, ranking, and early orders. It cannot be
settled as a filename, serialization, or loop-order convention. No warm-up
padding/resampling/shortening rule was invented and no performance was observed.

Required authoritative resolution: specify how the exact synthetic warm-up
requirement applies to unchanged Execution paths whose real prior synchronized
history is shorter than 378 bars, including the permitted source and construction
if artificial warm-up is intended. The Execution diversity exemption remains
valid and does not need to be reauthorized.

## Reproducible proof and boundary

The content-addressed coverage proof is
`evolution/protocol/fitness_v2_h1_warmup_coverage_9397ef5675ecc800771026689a84b1960d40558ea34a2de9b6664732060a1f57.json`.
Recompute with `fitness_v2_admission.unchanged_h1_warmup_coverage()` using
`scripts` on the Python import path. It uses only the authorized DEVELOPMENT
bundle loader, verifies its existing hashes, counts existing timestamps, and
asserts post-DEVELOPMENT isolation. It constructs no market world or genome.

Focused tests cover the exact exemption, all-reference comparison, exact
threshold, non-leakage, immutable reference inputs, malformed market descriptors,
and real-bundle coverage/isolation. Full execution scheduling/seed/calendar
validation and its requested integration tests remain unfinished because the
complete executable protocol is blocked on this conflict. This receipt does not
claim all six requested acceptance categories are complete.

The initial focused run exposed a missing legacy `scripts/lib` import path;
the new module now supplies that existing repository import convention. The
corrected focused run passed 29/29. Final full-suite evidence is recorded in the
associated validation receipt.

No new Fitness V2 world bank, baseline, evaluation, campaign, champion, worker,
or acceptance generation was created. S5D, S6B, the original parameter freeze,
and both historical stop receipts were not modified. Protected observations and
credentials were not read.

## Invocation versus acceptance

Rick states this invocation uses no-prompt bypass because the legacy profile
selector prevented `-p` and the prior workspace sandbox blocked Git writes.
This session has unrestricted filesystem/network permissions and approval
policy `never`. Direct version check of `/home/rick/.npm-global/bin/codex`
reports `codex-cli 0.157.0`. Neither that check nor this invocation proves
installed `tbots_unattended` profile semantics, HOST independence, or full
zero-prompt generational acceptance. Those remain unverified.
