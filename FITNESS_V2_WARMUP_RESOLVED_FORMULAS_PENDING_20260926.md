# Fitness V2 current resolution status

Authority: Rick, 2026-09-26. This receipt supplements rather than rewrites the
receipts at `9ff6582`, `008b39a`, and `69fa48d`, including the earlier
`FITNESS_V2_WARMUP_RESOLUTION_STATUS_20260926.md`.

**Execution and Shock warm-up: resolved.** Both use their matching immutable
historical anchors' available real history under frozen S5A insufficient-history
eligibility. Short history is permitted; missing bars are not fabricated,
interpolated, backfilled, bootstrapped, or borrowed from protected/post-2018 data.
Assets become eligible only when their frozen lookback requirements are naturally
satisfied. This applies to H1, H2, and H3. Shock's three gap relocations create no
extra history and remain its only price transformation. No same-asset donor/
destination restriction is introduced. Execution retains its distance-only
exemption; Shock remains subject to the market-distance gate. Distributional
and Sequence retain exactly 378 generated same-stream prior bars.

The earlier warm-up stops were correct under the then-unspecified rules and
remain historical evidence. They no longer block warm-up readiness. The admission
module's stale description is updated accordingly; its distance logic is unchanged.

The executable warm-up amendment is the canonical-JSON/SHA-256 content of
`WARMUP_AMENDMENT` in `scripts/fitness_v2_warmup.py`, identified by `AMENDMENT_ID`
and stored as `evolution/protocol/<AMENDMENT_ID>.json`. Its validator checks the
exact envelope. It explicitly makes no complete-protocol readiness claim.

**Full executable protocol: stopped before results.** The trend normalization
and OC1 zero-MAD family aggregation remain unspecified. See
`FITNESS_V2_FORMULA_DEFINITION_STOP_20260926.md` for proofs and the minimal
authoritative definitions required. No formula/sentinel has been chosen.

**Recovery and conservation:** live HEAD was verified as
`69fa48db8e791ac8be8862f16dab6060f70a4acf`; the only recovered changes were the
admission docstring and the untracked warm-up module/tests named by Rick. The
missing manifest is written in this recovery. No full executable-protocol draft,
production world/genome generation, worker launch, or acceptance attempt occurred.
Focused and full validation outcomes are persisted under
`experiments/fitness_v2_warmup_resolution_20260926/VALIDATION.json`.
