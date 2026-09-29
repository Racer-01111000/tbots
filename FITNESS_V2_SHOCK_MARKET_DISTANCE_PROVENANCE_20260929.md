# Fitness V2 Shock market-distance amendment: provenance reconciliation

Authority: Rick, 2026-09-29 ("GO — PROTOCOL PROVENANCE RECONCILIATION BEFORE
PRODUCTION"). Supersedes nothing about the code change itself
(`FITNESS_V2_SHOCK_MARKET_DISTANCE_EXEMPTION_20260929.md`, commit `0f97756`,
preserved unmodified); this receipt and its accompanying commit correct that
receipt's provenance gap and complete the freeze chain the earlier commit
deliberately left incomplete.

## What this commit does

1. **Stopped the running burn-in cleanly** (PID 1082977, SIGTERM, confirmed
   dead) before any world-bank output could exist under the old,
   provenance-incomplete state. No world, genome, campaign, or generation was
   produced by that run or by the earlier PID 1064391/1082976 lineage.
2. **Left `0f97756` and its receipt untouched.** History is not rewritten.
   The executable predicate it introduced (`market_distance_passes` exempting
   Shock from the `>=0.75` threshold only, still validating descriptors) is
   correct and unchanged by this commit.
3. **Left the original frozen amendment and manifest as untouched historical
   artifacts:** `evolution/protocol/fitness_v2_real_warmup_amendment_3053c6c4
   ...61dfec.json` and `evolution/protocol/fitness_v2_complete_protocol_
   49c71da1...60e89.json` are byte-for-byte unmodified on disk (see
   `test_fitness_v2_shock_distance_manifest_supersession.py::
   test_old_manifest_is_untouched_and_still_self_consistent`).
4. **New superseding amendment** (`scripts/fitness_v2_shock_distance_amendment.py`,
   `AMENDMENT_ID = fitness_v2_shock_distance_amendment_1c0ed706...093686`,
   persisted at `evolution/protocol/<that id>.json`) truthfully records:
   - Shock is exempt only from the `>=0.75` market-distance threshold; every
     other Shock admission gate (known-family check, structural descriptor
     validation, generator/base-path construction) remains active and
     unchanged.
   - Rationale: 533+ distinct Shock candidates drawn against the threshold,
     0 admissions -- a structural incompatibility between Shock's
     transformation (three donor-gap relocations from a real historical
     anchor) and a distance gate calibrated for fully synthetic worlds, not a
     threshold that needed retuning. `no_threshold_tuning: true`,
     `no_retroactive_acceptance_of_prior_shock_attempts: true`.
   - No production world bank, campaign, or generation existed when this
     decision was made (independently re-verified this session: `evolution/
     state/` does not exist; `STATUS.json` showed `world_bank_id`/`campaign`/
     `generation` all null and `campaigns_complete: 0` before this commit).
   - `extends_warmup_amendment` points at the current `WARMUP_AMENDMENT_ID`
     rather than reopening it -- this amendment is additive, not a
     modification of the (already separately resolved, see below) warm-up
     amendment.
5. **New complete-protocol manifest**
   (`fitness_v2_complete_protocol_a65b6007...f3c4ea`) generated through the
   project's normal deterministic path: the exact real calibration inputs
   (`tau`, `gap_extrema_bounds`, `reference` median/MAD, `h_shape_dates`,
   `h_shape_anchor_rows`, `stream_counts`, `boundary_proof`) were extracted
   field-for-field from the old frozen manifest's own content, round-tripped
   through `build_complete_protocol()` under the *pre-edit* code to confirm
   byte-exact reproduction of the old manifest (proving no drift in the
   reconstructed inputs), then passed through the *post-edit*
   `complete_protocol_manifest()` to produce the new envelope. The only
   fields that differ from the old manifest are `authority` (text addition),
   `schema_version` (3 -> 4), and `depends_on` (one key added,
   `shock_distance_amendment`; the four pre-existing keys are byte-identical).
   Full diff evidence: `experiments/fitness_v2_shock_distance_amendment_20260929/`.
6. **Code/amendment/manifest now agree:** `scripts/fitness_v2_world_bank.py`'s
   `COMPLETE_PROTOCOL_PATH` (the actual runtime load path -- see
   `load_complete_protocol()`) now points at the new manifest; `STATUS.json`'s
   `protocol_manifest` field was updated to match via `worker_status.py`'s
   validated `with_updates()`/`write_status()` helpers (not hand-edited).
   `evolution/protocol/SUPERSEDED.json` (an existing GO-Addendum-C registry
   already used once before, for `14fe15dd...` -> `49c71da1...`) is updated
   in the following commit to record `49c71da1...` -> `a65b6007...`,
   referencing this commit's own SHA as `superseded_at_commit` -- the same
   two-commit pattern the repo's own history already established for the
   prior supersession (manifest-generating commit, then a registry-update
   commit naming it), since a commit cannot cite its own SHA.
7. **Validation added, not just asserted:**
   `tests/test_fitness_v2_shock_distance_amendment.py` (content-address
   round-trip, tampering detection, rationale content, declared rule matches
   live predicate behavior) and
   `tests/test_fitness_v2_shock_distance_manifest_supersession.py` (old
   manifest self-consistency/immutability, new manifest validity and
   genuine difference, field-level diff is exactly `{authority, depends_on,
   schema_version}` with all four prior `depends_on` values byte-identical,
   and the live runtime path constant points at the new manifest). The
   pre-existing `tests/test_protocol_supersession.py` (registry consistency:
   exactly one non-superseded manifest on disk, matching `STATUS.json`)
   continues to pass once the following commit's `SUPERSEDED.json` update
   lands.

## Rationale evidence: what is and isn't independently verified this session

The "533+ distinct candidates, 0 admissions" figure is Rick's reported
empirical finding, not something regenerated or independently reproduced by
this session -- doing so would require re-running the expensive
world-descriptor computation across the killed PID 1064391 burn-in's full
attempt history, and no attempts log survived that run (`evolution/state/`
never existed; the run was terminated before any checkpoint write; no
journal entries exist for a bare `nohup`/`setsid` process). What this session
*did* independently verify: no `evolution/state/` directory exists, no
`.tbots.lock`-holding process survived either kill, `STATUS.json` shows
`campaigns_complete: 0` and every campaign/generation/world-bank field null,
and every existing test file plus the two new ones pass -- i.e. the
"identified pre-result" claim is independently confirmed even though the
exact candidate count is not.

## Separate item: Shock warm-up conflict status

See `FITNESS_V2_SHOCK_WARMUP_STATUS_CORRECTION_20260929.md`. Summary: the
378-bar Shock warm-up requirement was **already resolved** on 2026-09-26 by
commit `02bc2ad`, ten minutes after being recorded as a conflict by `69fa48d`,
via the same available-real-history exception already given to Execution.
This is independently confirmed by `FITNESS_V2_WARMUP_RESOLVED_FORMULAS_PENDING_20260926.md`
and `FITNESS_V2_HANDOFF_RECONCILIATION_20260926.md`, and by the live
`scripts/fitness_v2_warmup.py` code itself (`warmup_policy("shock") ==
"MATCH_AVAILABLE_REAL_ANCHOR"`, `resolution_status.shock_h1_stop == "Correct
under then-unspecified rule; resolved by Rick"`). The currently-frozen
`real_warmup_amendment` dependency already matches this resolved state and
required no change here. `0f97756`'s receipt incorrectly claimed this
conflict remained open; that was this session's own error, not a repo defect,
and is corrected in the file above rather than by editing the earlier receipt.

## Restart condition

Not yet met as of this commit. Restart is deferred to the follow-up commit
that lands the `SUPERSEDED.json` registry update and a clean full-suite run
at that final HEAD.

## No threshold tuning, no retroactive acceptance

Confirmed: the `>=0.75` value itself is unchanged for every family that still
enforces it (distributional, sequence). No output from any prior Shock
admission attempt (under the old, non-exempt behavior) is treated as
accepted; the exemption applies to candidates drawn after this amendment
only, and no world bank exists yet for any candidate to have been drawn into.
