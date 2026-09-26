# Fitness V2 seed-stream casing decision, 2026-09-26

Authority: Rick, decision made and recorded inside the post-freeze audit
window (ends 2026-09-26T12:29:05Z), before any Fitness V2 world was
generated.

## Decision

Rick's own decision, **not** a recovered interpretation of Sable handoff
section 48: the per-family identifiers used in the world-league seed-stream
payload are the lowercase canonical family identifiers already used
throughout the frozen `fitness_v2_parameter_freeze` manifest:

```
distributional, execution, sequence, shock
```

Section 48 does not specify a casing, and Echo could not recover Rick's
intended casing from it. Rather than guess, Rick made the call directly:
lowercase, matching every other place these four names already appear in
this project (`FAMILIES` in `fitness_v2.py`, the `synthetic_families` keys
in the parameter-freeze manifest, the `technique_seeds` bookkeeping in
`validate_world_manifest`).

This rule covers replacement seeds drawn during the initial world-bank
build and every later expansion batch alike -- one casing, one meaning,
for the life of the seed stream.

## What this does and does not change

- **Does not** touch `evolution/protocol/fitness_v2_complete_protocol_*.json`
  or its hash. This is a casing clarification for the seed-stream formula
  (operational world-generation infrastructure), not one of the 13 frozen
  descriptor/world-construction conventions in that manifest. The protocol
  manifest is not rehashed for this decision.
- **Does** confirm, unchanged, `scripts/world_seed_stream.py` as already
  shipped at commit `cd56031f5f4f04c23942dea580af033fab26443d`: its
  `FAMILIES` tuple was already exactly `("distributional", "execution",
  "sequence", "shock")` before this decision was recorded. No code change
  was required -- this receipt exists to record the decision's authority
  and provenance, and to pin it against silent future drift.

## The frozen payload template (Sable handoff section 48/49)

```
SHA256("TBOTS-FITNESS-V2-WORLD-LEAGUE-20260925|world|<family>|<stream_index>")
```
Take the first 32 bits of the digest, unsigned big-endian. If the result is
zero or already used, append `|retry|<n>` (n = 1, 2, 3, ...) and rehash
until the result is nonzero and unused. One stream per family, shared by
replacement and expansion draws, with a single monotonically advancing
index; the four frozen `PARAMETER_FREEZE` seeds are indices 1-4 and are
never derived by this formula. A rejected draw still consumes its index --
an index is never reused or skipped.

## Independent verification of the index-5 values

Rick supplied four index-5 seed values as pinned evidence that the
lowercase-casing formula above is the one intended. Recomputed
independently against the already-shipped `derive_seed` implementation
before writing this receipt:

| family          | index | seed (Rick's pinned value) | matches shipped `cd56031`? |
|------------------|-------|-----------------------------|------------------------------|
| distributional   | 5     | 3728510749                  | yes, exact match             |
| execution        | 5     | 2005964006                  | yes, exact match             |
| sequence         | 5     | 687330419                   | yes, exact match             |
| shock            | 5     | 3037866416                  | yes, exact match             |

All four matched on the first attempt, with no capitalization or namespace
changes required to `world_seed_stream.py`. These four values are now
pinned as an explicit regression test (`tests/test_world_seed_stream.py`,
`PinnedIndexFiveValues`) so any future accidental change to the namespace
string, the digest truncation, or the retry rule is caught immediately.

## Artifact check (before this receipt was written)

Confirmed via `git status --porcelain` (empty) and a filesystem search for
`world_bank*` / `fitness_v2_world*` anywhere under the repository and the
whole filesystem: **zero** Fitness V2 candidate world artifacts exist
anywhere on Node. The only filesystem matches for `*world*` are two
pre-existing design documents (`experiments/world_league_design_20260923T063845Z/DESIGN.md`,
`experiments/world_league_fitness_correction_20260923T071111Z/CORRECTED_CONTRACT.md`),
both dated 2026-09-25 and predating this session's work -- not generated
artifacts. No world of any family has been generated under this GO.

## Hold-condition checklist (Rick's four conditions, checked at commit time)

Per Rick's instruction, world generation is not authorized until **all**
of the following hold. Checked immediately before this receipt was
committed:

1. **Audit window closed** -- NOT YET. Window ends 2026-09-26T12:29:05Z;
   this receipt is being recorded before that time.
2. **No `FREEZE_AUDIT_HOLD.md` on the branch** -- confirmed absent via
   `git show origin/<branch>:FREEZE_AUDIT_HOLD.md` (fails with "does not
   exist," the clean outcome).
3. **This seed-casing decision committed** -- done by this commit.
4. **Complete original Sable handoff received and reconciled, including
   its campaign rules and stop conditions** -- NOT YET. Only fragments
   have been received directly from Rick so far (the section 48/49 seed
   formula, this casing decision). The complete handoff has not arrived.

Conditions 1 and 4 are unresolved. Per Rick's explicit instruction,
Fitness V2 remains on hold pending both -- no world generation begins on
condition 2/3 alone.
