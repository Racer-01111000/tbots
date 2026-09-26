# Fitness V2 executable-protocol conflict before results

Recorded: 2026-09-26. Authority: Rick's FITNESS V2 NODE TRAINING START /
EXECUTABLE-PROTOCOL GO, including its instruction to stop before results for a
genuinely selection-material conflict.

Direct checkpoint verification: clean HEAD
`d176ac256866bdac45145b2fbb2af619d1439a8f`, branch
`feat/node-resident-fitness-v2-autonomous-evolution-20260925`.
The existing ambiguity receipt and parameter freeze remain unchanged.
The exact executable `/home/rick/.npm-global/bin/codex --version` reports
`codex-cli 0.157.0`; this is not unattended acceptance evidence.

## Exact conflicting requirements

- Resolution 1 assigns synthetic slots the calendar shapes H1,H2,H3,H1.
- Resolution 2 specifies for Execution: "base path unchanged". Its randomness
  affects scheduled strategy fills only.
- Resolution 3 specifies exclusively market-path descriptors and requires
  "every synthetic candidate >=.75 from all historical anchors and all admitted
  synthetic worlds, before any strategy sees it."
- The initial bank requires four admitted Execution worlds.

An Execution world on an unchanged historical path has the same market-path
descriptor as its historical anchor when measured on the same scored horizon.
Independently, Execution slots 1 and 4 have the same H1 calendar and unchanged
base path. Fill-skipping seeds cannot change any of the eight market descriptor
families before strategy evaluation. Their descriptor vectors are identical.
Identical vectors remain identical under the same DEVELOPMENT median/MAD
normalization. Zero MAD either preserves equality or rejects the candidate;
it cannot create positive separation. Thus their pairwise distance is exactly
`sqrt(mean((z-z)^2)) = 0`, below `0.75`, for every replacement seed.

This is a structural incompatibility, not observed strategy failure or a seed
search problem. The prescribed initial bank cannot be completed. Exempting
Execution worlds, changing descriptors to measure fill stress, or altering the
base path would change explicit selection/admission rules and is outside the
delegated mechanical conventions. No such repair was chosen.

## Verification and state

A Python 3 algebra check asserted zero RMS distance for identical finite
descriptor vectors and passed. It created no world, genome, or market result.
The first attempt used unavailable command `python` (exit 127); rerunning with
`python3` passed. No suite or unattended acceptance is claimed by this receipt.

No executable addendum was declared frozen, no world or baseline was generated,
and no campaign or continuous worker was started in this invocation. Historical
S5D and S6B artifacts were not modified. No credentials were read or exposed.

Required resolution: Rick must reconcile unchanged Execution paths with the
mandatory market-path diversity comparison, including comparisons against
historical anchors and between repeated calendar shapes. Until that rule is
resolved, world admission and dependent evolution remain stopped before results.
