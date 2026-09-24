# Snapshot reconciliation (2026-09-24)

Reconciles the sanitized import on `main` (`e00af4a`) against the newer TBOTS
work already pushed to this repository. Read-only review: no branch other than
`claude/loving-davinci-wbfut7` was changed.

**Correction (2026-09-24), per Rick's GO "TBOTS PR #2 CORRECTION, RETARGET, AND
DOCUMENTATION-ONLY MERGE":** the original "Access to physical NODE" section
below misattributed HOST-local paths and HOST-local uncommitted work to NODE.
That section has been corrected below. No other finding in this document was
changed.

## Branches

| Branch | Head | Relation to `main` |
|---|---|---|
| `main` | `e00af4a` Import sanitized NODE laboratory snapshot | root commit, import only |
| `import/sanitized-node-snapshot-20260901` | `e00af4a` | same as `main` |
| `repair/self-contained-canonical-20260901` | `9e57332` Merge PR #1 (S6B resume executor) | `main` + 13 commits; newest TBOTS work |
| `feat/s6b-deterministic-resume-executor-20260904` | `f7ac06a` | merged into the repair branch |
| `feat/historical-evolution-v1-20260901` | `51a01ea` Initialize tbots research laboratory | unrelated root; README only |

`main` has not received the repair branch. Treat the repair branch, not the
import, as current work.

## Differences: `main` -> `repair/self-contained-canonical-20260901`

188 files, +1,275,138 lines, no deletions. The missing development bundle is
one of several differences:

- `data/development_bundles/s5adev_98e2f764…/`: the 9-file S5A DEVELOPMENT
  bundle (commit `4e42daf`).
- `data/raw/*.json` (8) and `data/normalized/*.csv` (8): the accepted source
  dataset `ds_7e16896c…`.
- `reports/s5c_championship_08325c0_result.json`: the S5C result behind the
  frozen champion chain.
- `evolution/s6b_runs/` (151 files): S6B primary and reproduction run
  evidence plus `PRESERVATION_MANIFEST.json`.
- `scripts/s6b_continuation.py`, `scripts/s6b_resume_executor.py` and their
  tests `tests/test_s6b_continuation.py`, `tests/test_s6b_resume_executor.py`.
- `tests/test_replay.py`: adds a test that stops a `TEST_FIXTURE_ONLY` file
  from passing as accepted data.
- `.gitignore`: adds narrow allow-list rules for the files above.
- `.github/workflows/s6b-resume-validation.yml`: CI for the resume executor.
- Records: `HISTORICAL_PRESERVATION_MIGRATION.md`,
  `S6B_HISTORICAL_PRESERVATION.md`, `CHECKPOINT_S6B_20260901.md`,
  `HANDOFF_S6B_RESUME_EXECUTOR_20260904.md`.

## Development bundle

Exact path:
`data/development_bundles/s5adev_98e2f764f466b90ee2bbc2532b75188bfc4fd20b4a13523f94bce65e6a1f193a/`

- NODE source: `/opt/evolutionary-markets/<same path>` at source HEAD
  `74acb3666b046dd363c2b4b502ae0d0a2a1806c1`.
- In this repository: on `repair/self-contained-canonical-20260901`, added by
  `4e42daf` (Restore accepted historical preservation evidence).
- Files: `DBC EEM EFA GLD IEF SPY TLT VNQ` `.csv` and
  `manifest_s5adev_98e2f764….json` (SHA-256 `98c566cd…c3361b`).
- Check: the 26 files listed in `HISTORICAL_PRESERVATION_MIGRATION.md` were
  re-hashed on the repair branch: 26 of 26 match in size and SHA-256.

## Tests

- `main` (bundle absent): `cd tests && python3 -m unittest discover -p
  'test_*.py'` ran 259 tests with 7 errors. Each error was "authorized
  DEVELOPMENT bundle manifest is unreadable". The 252 other tests passed.
- Repair branch `9e57332` (bundle present): the same command ran 330 tests,
  all passing (`OK`, 33.5 s).
- `main` with the bundle alone was not run. No claim is made that 259 of 259
  pass on `main`.

## Access to physical NODE

Reconciling against GitHub needs no NODE access.

Two items in this repository's history are HOST-local, not NODE-local, and
were readable directly from HOST (`/home/rick/tbots`) without touching NODE:
`/home/rick/tbots/experiments/…` (untracked research directories) and the
uncommitted Gen0-bootstrap work (`scripts/s6b_primary_development.py`,
`tests/test_s6b_primary_development.py`, and an unstaged edit to
`scripts/s6b_resume_executor.py`). NODE's repository
(`/opt/evolutionary-markets`) has no `experiments/` directory at all, and
never received either of these HOST-local changes.

NODE access is needed only for evidence that is actually stored or observed
on NODE — for example, checking this repository's copy against its origin
again, or reading anything under `/opt/evolutionary-markets` that has not
been preserved into `tbots`. That needs:

- a shell on the NODE host, either sitting at it or through SSH over the
  tailnet from a device already on it. The cloud session cannot join the
  tailnet.
- read access to `/opt/evolutionary-markets`.
- a Claude session started there (`claude remote-control` in the target
  folder) or manual commands. For the development bundle the check is
  read-only: `sha256sum` of the 9 files compared with the table in
  `HISTORICAL_PRESERVATION_MIGRATION.md`.
- push rights to `Racer-01111000/tbots` from NODE only if new artifacts need
  to be committed.

One confirmed divergence between NODE and this repository, verified live via
SSH on 2026-09-24: `evolution/s6b_runs/` on NODE still shows C primary
interrupted after Gen11 and D reproduction interrupted after Gen6 (no
`completion.json`, no further generations) — the pre-resume state. This
repository's preserved copy shows both lineages resumed to COMPLETE through
Gen12. The resume executor was run only against the preserved copy inside
this repository, per its own governing GO, and NODE's source tree was never
touched. This is intentional historical state, not a data-integrity problem,
and it stays as-is unless a later Rick GO specifically authorizes NODE
reconciliation.
