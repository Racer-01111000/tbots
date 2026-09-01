# S6B historical run evidence preservation

This record documents a bounded, byte-identical preservation copy of
already-observed S6B evolutionary run evidence. These are the original run
artifacts as they existed on NODE; nothing was executed, continued,
regenerated, repaired, or reinterpreted to produce them, and copying them
here is not an acceptance or certification event for any lineage.

- Authority: Rick, `GO — PRESERVE S6B HISTORICAL RUN EVIDENCE INTO TBOTS — NO EXECUTION`
- Source host: `NODE`
- Source repository: `/opt/evolutionary-markets`
- Source HEAD: `74acb3666b046dd363c2b4b502ae0d0a2a1806c1`
- Source directory: `evolution/s6b_runs/`
- Destination repository: `Racer-01111000/tbots`
- Destination branch: `repair/self-contained-canonical-20260901`
- Destination starting commit: `24e46c2010d5d951daefa9143d71a5f018554673`
- Destination directory: `evolution/s6b_runs/`
- Preservation relation: NODE preserved S6B working-tree evidence -> byte-identical tbots preservation copy

## Complete artifact inventory

The full per-file inventory (relative path, byte size, source modification
timestamp, SHA-256) for all 130 preserved files is recorded in
[`evolution/s6b_runs/PRESERVATION_MANIFEST.json`](evolution/s6b_runs/PRESERVATION_MANIFEST.json),
generated directly from the NODE source tree before copy. Every entry's
source SHA-256 equals its destination SHA-256; mismatches: 0.

`evolution/s6b_runs/` is listed in `.gitignore` (it is the output root for
any future real execution). The 130 preserved files below were added with an
explicit tracked exception (`git add -f`) rather than by editing that
pattern, so a future real run's output remains ignored by default.

## Preserved run trees

Only the five run trees observed on NODE were copied. No E/F/G directories
were created; their absence remains evidence of NOT STARTED, not a reason to
synthesize scaffolding.

| Run tree | Files | Run identity | Observed classification (as preserved, not newly accepted) |
|---|---:|---|---|
| `primary/B_s6a_b_11cf933f...` | 30 | `s6a_b_11cf933f3df42f14f360338c1a834fda684eb2d58705abfd90b73ca8afe75d79` | Gen0-12 persisted, `completion.json` present |
| `reproduction/B_s6a_b_11cf933f...` | 30 | `s6a_b_11cf933f3df42f14f360338c1a834fda684eb2d58705abfd90b73ca8afe75d79` | Gen0-12 persisted, `completion.json` present |
| `primary/C_s6a_c_99e51f2f...` | 25 | `s6a_c_99e51f2f688620f2bf46c8cc1d06cfefc5ea608d19aa378ebcb65d9ef8ac9c5a` | Gen0-11 persisted, no `completion.json`, no Gen12 |
| `primary/D_s6a_d_b986e970...` | 30 | `s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca` | Gen0-12 persisted, `completion.json` present |
| `reproduction/D_s6a_d_b986e970...` | 15 | `s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca` | Gen0-6 persisted, no `completion.json`, no Gen7+ |

These classifications are the observed on-disk shape at copy time, stated
for orientation only. The authoritative classification is whatever the S6B
continuation inspector (`scripts/s6b_continuation.py`, `inspect_checkpoint`)
independently computes by reading the preserved files, recorded in the GO
receipt for this preservation task.

## What was not copied

No E/F/G run directories (none exist on NODE), no forensic databases, no
equity curves, no superseded reproductions, and nothing outside
`evolution/s6b_runs/`.

## Verification

- Source manifest generated on NODE before copy: 130 files.
- Transfer: single tar of `evolution/s6b_runs` from NODE, SHA-256-verified
  identical before and after transfer, extracted without modification.
- Post-copy comparison: 130 of 130 destination files match source in byte
  size and SHA-256; 0 missing, 0 extra, 0 mismatches.
- NODE source tree: unchanged before and after (HEAD and file count
  reverified identical both times).
- Continuation-inspector classification and full test-suite results for
  this preservation are recorded in the GO receipt for this task, not
  duplicated here.

Historical organism executions, continuation previews against this
preserved evidence, mutations, reproduction events, new generation
creation for any lineage, qualification executions, and Historical Blind
Evolution executions were all zero during this preservation.
