# Research evidence preservation (2026-09-24)

Authority: Rick, `GO — TBOTS RESEARCH EVIDENCE PRESERVATION + DIVIDEND ACCOUNTING
REPAIR / REVALIDATION`, Phase A.

- Source: HOST working tree, `/home/rick/tbots/experiments/` (untracked at the
  time of preservation).
- Authoritative source revision: `4ee5b098f73d0db3b8b97485b3fcefa3200582f3`
  (`repair/self-contained-canonical-20260901`).
- Preservation branch: `evidence/research-log-20260924` — an evidence ledger,
  not the canonical execution branch. Branched directly from the authoritative
  revision above, not from any other branch.
- Preserved: 15 research directories, 31 files, byte-for-byte (SHA-256
  verified source-to-destination for every file; `__pycache__/` build
  artifacts excluded as non-evidentiary, consistent with this repository's
  existing `.gitignore` convention). Per-file inventory and hashes are in
  `PRESERVATION_MANIFEST_20260924.json` in this directory.

## What this is

A durable copy of the existing research record as it stood on HOST at
preservation time: baseline-controls studies, holdout studies, the Sable
independent review, the passive-envelope/exposure/qualification follow-up
experiments, and the Fitness V2 / World League design-contract thread through
its "READY TO FREEZE" errata pass.

## What this is not

- **Not acceptance.** Preserving these files records that the analyses,
  numbers, and conclusions exist and were produced at the stated times — it
  is not a determination that any finding, method, or recommendation in them
  is correct, complete, or ready to act on.
- **Fitness V2 / World League remains a separately governed design.** Nothing
  in this preservation event implements, freezes, or authorizes the Fitness
  V2 contract in `experiments/fitness_v2_prefreeze_errata_20260923T084553Z/`
  or any earlier draft of it. Implementing it into the evaluator remains a
  future, separately GO'd decision.
- **Sable's audit is preserved as audit evidence**, not as an accepted
  finding beyond what it itself already distinguishes as directly verified
  versus receipt-only.
- **Gen0-bootstrap work is excluded.** `scripts/s6b_resume_executor.py`'s
  unstaged diff, `scripts/s6b_primary_development.py`, and
  `tests/test_s6b_primary_development.py` were not copied, read for content
  changes, or otherwise touched by this preservation event. They remain
  HOST-local dirty working-tree state, pending their own future GO.
- **NODE state is not modified or reconciled.** This preservation event made
  no connection to NODE and changed nothing there. It does not address the
  C-primary/D-reproduction divergence between NODE and this repository
  documented in `SNAPSHOT_RECONCILIATION.md`.

## Future batches

This preservation is append-only. A later research or audit batch is
preserved as a new, separately dated manifest (e.g.
`PRESERVATION_MANIFEST_<date>.json`) added to this same evidence branch,
never by editing or replacing `PRESERVATION_MANIFEST_20260924.json` or the
files it already covers.
