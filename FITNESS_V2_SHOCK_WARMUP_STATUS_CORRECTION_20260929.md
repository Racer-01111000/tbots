# Correction: Shock warm-up status was already resolved, not open

Authority: Claude Code on NODE, 2026-09-29, correcting an error in the
2026-09-29 09:58 receipt below.

## The error

`FITNESS_V2_SHOCK_MARKET_DISTANCE_EXEMPTION_20260929.md` (commit `0f97756`)
stated: "This does NOT resolve the Shock warm-up bar conflict... Shock still
requires exactly 378 same-stream generated warm-up bars... The 378-bar
requirement remains outstanding." **That statement is wrong.** It cited only
`FITNESS_V2_SHOCK_WARMUP_CONFLICT_20260926.md` and
`FITNESS_V2_WARMUP_RESOLUTION_STATUS_20260926.md` (both from commit `69fa48d`,
08:36 +07 on 2026-09-26) without checking whether a later same-day document
superseded them.

## What is actually true

Ten minutes after `69fa48d`, commit `02bc2ad` ("Preserve real warmup
resolutions and record formula definition stop", 08:46 +07 on 2026-09-26)
resolved the Shock warm-up question by extending Shock the same
available-real-history exception already given to Execution --
`FITNESS_V2_WARMUP_RESOLVED_FORMULAS_PENDING_20260926.md` states explicitly:
"**Execution and Shock warm-up: resolved.** Both use their matching immutable
historical anchors' available real history under frozen S5A
insufficient-history eligibility... Shock's three gap relocations create no
extra history and remain its only price transformation... Shock remains
subject to the market-distance gate." That same commit is the one that
introduced `scripts/fitness_v2_warmup.py` itself -- its live
`warmup_policy("shock")` has returned `"MATCH_AVAILABLE_REAL_ANCHOR"` (not the
old 378-generated-bar requirement) since that commit, and
`WARMUP_AMENDMENT["resolution_status"]["shock_h1_stop"]` has read `"Correct
under then-unspecified rule; resolved by Rick"` the whole time.

`FITNESS_V2_HANDOFF_RECONCILIATION_20260926.md` (16:17 +07, same day)
independently confirms this: "Shock warm-up:
`FITNESS_V2_WARMUP_RESOLVED_FORMULAS_PENDING_20260926.md` explicitly
supersedes the earlier `WARMUP_RESOLUTION_STATUS`'s 'Shock: unresolved' line."

The currently-frozen complete-protocol manifest (before today's amendment,
`fitness_v2_complete_protocol_49c71da1...60e89.json`) already depended on
`real_warmup_amendment: fitness_v2_real_warmup_amendment_3053c6c4...61dfec`,
which is the exact `AMENDMENT_ID` of the resolved `WARMUP_AMENDMENT` dict as
it stands today. **The resolution was already fully incorporated into the
active manifest before this session began** -- there was nothing left to
incorporate.

## What was and wasn't confused

`FITNESS_V2_SHOCK_WARMUP_CONFLICT_20260926.md`'s 378-bar warm-up question
(resolved 2026-09-26, above) is a completely different gate from today's
market-distance amendment (`FITNESS_V2_SHOCK_MARKET_DISTANCE_EXEMPTION_20260929.md`
/ `FITNESS_V2_SHOCK_MARKET_DISTANCE_PROVENANCE_20260929.md`). The
`WARMUP_RESOLVED_FORMULAS_PENDING` doc says as much directly: "Shock remains
subject to the market-distance gate" -- warm-up resolution never touched
distance admission. Today's amendment is real, necessary, and independently
justified (see the provenance receipt); the only error was claiming the
*warm-up* conflict was still open when it had not been since 2026-09-26.

No code changes accompany this file. It is a documentation-only correction.
