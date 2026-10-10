# Kim recurring PAPER lifecycle - final verification receipt (sanitized): **PASS / ARMED**

GO `TBOTS_KIM_FINAL_VERIFICATION_20261010`. Alpaca **paper** only. No credentials, account/bucket identifiers, balances, raw market data or private state appear here. No demonstration trades, forced liquidation, live-state restoration or cadence advancement occurred.

| | |
|---|---|
| Deployed SHA | `8dad53b6f2d41afc71e4d664e79612228921070d` (tree `ef32138f...`; previous release `dcfb141d...` as `ROLLBACK_TO`). **No redeployment was needed**: every runtime file (`kim_recurring.py`, `kim_offload.py`, config, unit renderer, pilot guard module) is byte-identical to the published tree; only tests and documentation changed afterwards. The deployed release tarball carries the older copies of the test files, which do not run in production. |
| Published SHA | recorded in the branch head `ops/kim-unattended-recurring-paper-20261010` (verified remote == local at publication) |
| Seal | sha256 of `kim_recurring_config.json` = `d64de24d0408c822614af158c38c38d0b6e0db8630259d11d1f3f22126c0fba8` |

## 1. Mutation results (all ten, against the final code and tests)
Method: each mutation applied alone, the three recurring suites (123 tests, unmutated baseline 123 pass) run, the mutation reverted. Expected for every row: the suite goes red.

| # | Mutation | Actual | Caught by |
|---|---|---|---|
| M1 | upload not verified against the local checksum | red (2 failed) | remote-mismatch keeps local copy; existing-object-must-be-identical |
| M2 | archive members not compared with the manifest before deleting | red (1) | member-content-must-match-manifest |
| M3 | staging cap not enforced | red (1) | fault matrix (stage_cap) |
| M4 | local copy deleted even when the upload failed | red (6) | outage-retained, stuck-retained, corrupt-local, and others |
| M5 | consolidation deletes the final archive | red (2) | verified-archive test; consolidate-mode test |
| M6 | missing-sidecar pre-check removed | red (1) | fault matrix (missing_sidecar) |
| M7 | independent post-upload archive checksum re-read removed | red (1) | late-corruption test |
| M8 | local snapshot not checked against its sidecar before upload | red (1) | corrupt-local-snapshot-never-uploaded |
| M9 | snapshot ignores the session's final-reconciliation marker | red (2) | scheduled-snapshot ordering and deadline tests |
| M10 | storage-pressure guard disabled | red (2) | backlog and low-free-space tests |

**The earlier survivor (M6)** was not equivalent: without the pre-check a missing sidecar still ended in a refusal with nothing deleted, but only after downloading members, with a different reason. The test asserted only "nothing deleted", which both paths satisfy, so the fail-fast contract was unpinned. Fixed by asserting the pre-check's reason and that **zero objects were downloaded** (same for the staging cap); M6 is now red. The two others that had survived earlier (M2, M7) were fixed the same way last round. Added in this pass: two DST mutations (open window pinned to fixed UTC-4; session times pinned to fixed UTC-4) - both red (11 and 6 failures). Test code only; no assertion was weakened.

## 2. Complete cost estimate
Prices verified today from the AWS Price List API for Singapore (S3 Standard): **storage $0.025 per GB-month (first 50 TB); PUT/COPY/POST/LIST $0.005 per 1,000; GET and other requests $0.0004 per 1,000**; DELETE is free per AWS (the API returned no separate DELETE price; even if billed at the GET rate it is immaterial). Same-region S3-to-instance transfer and transfer in are free per AWS pricing documentation (not separately returned by the price API; sensitivity: even $0.12/GB internet egress on under 1 GB per year is under $0.12).

Assumptions (measured where stated): 252 sessions/year (21/month); one snapshot per session = 2 objects (tar.gz + sidecar); **measured** first snapshot 3.7 KB (today's five test snapshots average 5.3 KB because manual validation runs added receipts); **measured** receipt growth 3.1 KB compressed per session (monitor lines 1.85 KB measured from four real sessions + about 1.2 KB of other receipts); each snapshot re-includes all receipts, so snapshot size is about `3.7 KB + 3.1 KB x sessions` (about 0.8 MB after one year, 2.3 MB after three, 3.9 MB after five); tar consolidation adds about 1.8 KB per archived snapshot and saves no bytes. Retained: about 168 daily objects at steady state (three full months plus the current month) and **3 archive objects per month (36/year, 180 after five years), never deleted**. Requests per year: PUT/LIST about 592, GET about 1,056 (verification downloads, consolidation downloads), DELETE about 504 (free). No hourly S3 calls (the offload timer is a local no-op when nothing is pending).

| Horizon | Stored | Monthly charge at that point | Cumulative spend |
|---|---|---|---|
| Month 1 | 0.8 MB | $0.0003 | $0.0003 |
| Year 1 | 100 MB | $0.0028 | $0.015 |
| Year 3 | 891 MB | $0.023 | $0.29 |
| Year 5 | 2.5 GB | $0.062 | $1.28 |
| Year 5, if receipts grow 2x faster | 4.9 GB | $0.124 | $2.55 |

Requests are about $0.0003 per month throughout; storage dominates. **Archive growth is indefinite and quadratic in sessions** (every snapshot re-includes the full receipts history, and archives are never deleted), so storage is not bounded; the figures above are a model refined by the first month of real data. The earlier statements (about $0.004/month at year one and $0.15/month at year five) came from a coarser 5 KB/session assumption and are superseded. No retention redesign was made.

## 3. Verification pass
- **Test totals:** HOST **437 pass** (full tree, at the published head). Instance interpreter (py3.13.5) **430 pass** (same head). The 7-test difference is the lineage-D shadow-parity file, excluded on the instance because it needs about 82 MB of historical run data that is not there. Recurring suites: 131 tests (123 used for the mutation baseline, plus 8 DST fixtures added afterwards; the two DST mutations were run against those).
- **By requirement (isolated fixtures / fake broker):** calendar, holidays, early closes and DST (both transitions, real UTC instants) 15; missed windows, cadence and no-catch-up 5; restart and reconciliation recovery 9; **every STOP class blocks every broker mutation 31**; risk-only liquidation bounded by ownership and budget 5; ambiguous submission recovery and partial fills 4; single writer and duplicate starts 2 (+ live demonstration); snapshot/offload/S3-restore 44; archive eligibility, verify-before-delete, idempotence and fault cases 10; retry classification, credentials, live-endpoint rejection 17. All pass.
- **Real calendar (read-only):** early closes in the window are 2026-11-27 and 2026-12-24 (13:00); no non-09:30 opens; Mon 2026-10-12 is a session. systemd expands the 09:30 New York slot to 13:30Z until 2026-10-30 and 14:30Z from 2026-11-02, and to 14:30Z then 13:30Z around the March 2027 transition.
- **Sandbox check:** the installed preflight, close, snapshot and offload units (none carries a submission flag or seal) were started under their real systemd sandbox on the non-session Saturday and succeeded; the real offload unit uploaded and verified a staged snapshot. The submitting open and monitor units were not started by hand.
- **End to end on the deployed release:** snapshot taken (ledger digest `80de4278...`), uploaded create-only with SSE-S3, downloaded, local staging left empty, **independent download with the AWS CLI whose SHA-256 matched**, restored from S3 into a new directory (32 files verified; same command refuses the live directory), blocked until reconciled, then reconciled clean against the live paper account and the marker cleared. S3 now holds 5 snapshots (10 objects, about 27 KB); no archives yet (none eligible; a real consolidate run reports nothing to do). No real retained object was deleted for testing.
- **Production state:** Kim identity verified fail-closed (genome ids agree, frozen champion file and 4 decision-function files match); risk terms unchanged (4 normal / 8 liquidation orders, 8% halt, 18% caps, DAY orders, extended hours off, 50-session cadence, SIP feed); baseline, cadence and peak files unchanged from the migration record; migrated-state identity `8fdd6651...`; fresh read-only reconcile **clean** (holdings = protected baseline + pilot ledger, no outstanding or unexplained orders, no lookup errors); **no STOP, no halt, no restore marker**; archived pilot tree hash `3c55d9c5...` unchanged; V5 HOLD, V5 and old-pilot timers disabled.
- **Timers and runners:** all seven recurring timers enabled and active; 0 failed units; 0 active recurring services; no trading process; writer lock free (a second writer gets exit 75 or waits).

## Unresolved (explicitly)
**The plaintext root-account access keys stored in a HOST memory note remain in place and are not remediated.** They were not used here, nothing in this verification resolves it, and no credential is reproduced in this receipt. Please remove them from the notes and rotate them.

## Next runs
**Mon 2026-10-12** (New York is on daylight time, 11 hours behind Hanoi): preflight **09:15 ET = 20:15 Hanoi**; opening **09:30 ET = 20:30 Hanoi** (13:30Z) - a hold, since step 4 is not rebalance-due; monitoring from 09:35 ET. Final reconciliation 16:10 ET (03:10 Hanoi Tue). **First post-close backup 16:25 ET = 03:25 Hanoi on Tue 2026-10-13**, uploaded and verified immediately. First rebalance Wed 2026-12-16, 09:30 ET = 21:30 Hanoi. The lifecycle is left armed; no further discretionary work.
