# S6B deterministic resume executor — team handoff

Date: 2026-09-04
Authority: Rick
Branch: `feat/s6b-deterministic-resume-executor-20260904`
Base checkpoint: `533fb75743c06ed3d9cee1f72d34be41f8fdf2c1`

## Purpose

Restore only the missing S6B continuation capability. This work does **not**
change trading methodology, mutation, selection, fitness, historical data,
frozen identities, S5D, Historical Blind Evolution, or any future-training
surface.

The preserved historical restart points remain:

- C primary: complete through Gen11; next missing generation is Gen12.
- D reproduction: complete through Gen6; next missing generation is Gen7.
- B primary/reproduction and D primary are already complete and must refuse.
- E/F/G have no existing run and must refuse.

## What is implemented

`scripts/s6b_resume_executor.py` adds a narrowly bounded resume path that:

1. accepts only a checkpoint already classified `INTERRUPTED` by
   `s6b_continuation.inspect_checkpoint()`;
2. never creates a new lineage and never restarts Gen0;
3. builds only the immediate next generation from the frozen S6A primitives;
4. reuses the existing S6B admission, evaluator-result, verifier-agreement,
   population, identity, and DEVELOPMENT-boundary checks;
5. requires explicit `population_execution_authorized=True` for preserved
   historical `execution_class=population` checkpoints;
6. persists one generation per transaction with no-overwrite hard-link commit;
7. stages and hashes every artifact before commit;
8. leaves a recoverable transaction manifest if the process dies during commit;
9. commits Gen12 completion artifacts with `completion.json` last;
10. can resume one generation at a time or continue an already-interrupted run
    to Gen12.

No historical S6B artifact was executed or modified while building this branch.

## Synthetic proof added

`tests/test_s6b_resume_executor.py` covers:

- Gen11 -> Gen12 exact resume;
- Gen6 -> Gen7..Gen12 exact resume;
- byte-for-byte tree equality against an uninterrupted synthetic reference run;
- exactly-one-generation advancement;
- completed-run refusal;
- no-existing-run refusal;
- explicit population execution gate;
- independent Gen12 verifier requirement;
- crash between feasibility and generation commit followed by deterministic
  transaction recovery;
- cleanup of inert pre-commit staging debris without advancing the checkpoint.

## Mandatory gate tomorrow — before historical execution

Run this on a clean checkout of this branch in the normal tbots test environment:

```bash
python -m pytest -q tests/test_s6b_continuation.py tests/test_s6b_resume_executor.py
python -m pytest -q
```

Required result: **all tests pass, zero unexpected skips/failures**. Record the
branch HEAD and complete test counts in the receipt.

Then perform a read-only historical preflight. Do not evaluate any organism yet:

- inspect C primary and confirm exactly `INTERRUPTED`, highest Gen11, next Gen12,
  legacy profile `LEGACY_NODE_74ACB366`;
- inspect D reproduction and confirm exactly `INTERRUPTED`, highest Gen6, next
  Gen7, legacy profile `LEGACY_NODE_74ACB366`;
- confirm no pending `.s6b_resume_transaction.json` exists in either target;
- confirm preserved artifact hashes still match `PRESERVATION_MANIFEST.json`;
- confirm S5D champion and Historical Blind Evolution remain untouched/locked.

If any check differs, **STOP**. Do not repair, regenerate, skip, or infer.

## Crash/restart rule

Before restarting after any process/host interruption, call
`recover_pending_transaction(target)` for that exact lineage target. If it
returns a recovered generation, re-inspect the checkpoint before continuing.
A hash mismatch or artifact collision is a hard stop.

## Historical launch boundary

This branch itself does not authorize historical execution. After the mandatory
test + read-only preflight passes, Rick must issue the operational GO naming the
exact targets. The intended targets are only:

- `primary/C...`: resume from Gen12 only;
- `reproduction/D...`: resume from Gen7 and continue contiguously to Gen12.

Do not run B. Do not create E/F/G. Do not start Historical Blind Evolution. Do
not execute the frozen S5D champion. Do not connect a broker, live feed, paper
order endpoint, or real-money surface.

## Acceptance condition

Historical resume is accepted only when:

- C primary is COMPLETE through Gen12;
- D reproduction is COMPLETE through Gen12;
- generations remain contiguous with exactly 64 indexed slots each;
- same-lineage immediate-predecessor parent provenance still verifies;
- Gen12 evaluator/verifier agreement passes;
- completion artifacts are present and valid;
- pre-existing preserved bytes through C Gen11 and D reproduction Gen6 are
  unchanged;
- B/D-primary/E/F/G and S5D/future-training surfaces are unchanged.

At that point stop and issue a receipt. Do not begin the next experiment in the
same GO.
