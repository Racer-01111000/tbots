# Kim recurring PAPER lifecycle - final receipt (sanitized): **ARMED**

GO `TBOTS_KIM_OFFLOAD_ARCHIVE_AND_REARM_20261010`. Alpaca **paper** only. No broker/account identifiers, bucket name, balances, raw market data, private state or detailed broker receipts appear here.

| | |
|---|---|
| Status | **ARMED** (full lifecycle) |
| Deployed SHA | `8dad53b6f2d41afc71e4d664e79612228921070d` (immutable release, tree hash `ef32138f...`, previous release `dcfb141d...` recorded as `ROLLBACK_TO`) |
| Submission seal | sha256 of `kim_recurring_config.json` = `d64de24d0408c822614af158c38c38d0b6e0db8630259d11d1f3f22126c0fba8` (risk terms unchanged) |
| State continuity | migrated-state identity `8fdd6651...` unchanged; ledger rows digest `80de4278...` unchanged in every snapshot taken today; peak, cadence, baseline, halt and STOP state untouched; archived pilot tree hash `3c55d9c5...` unchanged; V5 still HOLD |
| Reconciliation | fresh read-only reconcile clean immediately before each rearm and again from an S3-restored copy: holdings = protected baseline + pilot ledger, no outstanding/unexplained orders, no unresolved ledger rows, **no STOP, no halt** |

## Challenges raised before implementing (per the standing preference)
- **Premise:** the GO said to use the "existing authorized root helper" for S3/IAM. That helper is a HOST utility (packages, services, power) with no AWS capability. I used my own scoped IAM identity instead. A memory note also holds long-lived root account keys in plaintext; they were **not used** and should be removed from the notes and rotated.
- **Contradiction/cost:** consolidating already-compressed snapshots into a tar saves object count, not bytes; each daily snapshot re-includes the whole receipts history, so total stored bytes grow roughly quadratically with sessions (small, but not bounded). Built as specified and reported honestly below.
- **Tradeoff:** the instance role must be able to delete `daily/` objects for the cleanup you asked for; a bug or compromise could remove recent daily snapshots (never archives). No bucket versioning (it would retain deleted snapshots as billable noncurrent copies).

## IAM / bucket scope
Private bucket in ap-southeast-1: all four public-access blocks on, TLS required (deny on insecure transport), SSE-S3 default encryption, object ownership enforced, **lifecycle rule only aborts incomplete multipart uploads** (no expiration of anything). Bucket policy additionally denies `DeleteObject` under `archives/` for everyone.
Instance role gets one new inline policy, nothing else changed: list only with prefix `daily/*` or `archives/*`; put/get/delete/abort under `daily/*`; put/get/abort (**no delete**) under `archives/*`. IAM policy simulation: other prefixes, other buckets, bucket/policy/IAM administration and archive deletion are all denied. Uploads are create-only (`If-None-Match: *`), SSE-S3, with a SHA-256 checksum.

## Offload and deletion evidence (real S3)
- The scheduled-but-never-offloaded snapshot taken earlier today and two further snapshots were uploaded, **downloaded back and compared byte-for-byte with the local checksum**, and only then deleted locally (earlier snapshot's hash `84a2e704...` equals the hash recorded when it was created). Local staging now holds no snapshot files.
- Failure handling is tested: remote mismatch, outage, retry, bounded attempts then "stuck", corrupt local file, identical-vs-different pre-existing object, storage pressure (snapshots refused, nothing deleted), each producing a visible failure receipt and a failed unit.
- Snapshots run once per real session **after the real close and the session's final reconciliation** (13:xx slots on early-close days); hourly offload retry; weekly consolidation.

## Restore evidence
`fetch-restore latest` downloaded from S3, verified the sidecar, hashes, manifest, SQLite integrity and ledger digest (17 files), restored into a new isolated directory (the same command refused the live state directory), the copy was blocked until `reconcile` and then reconciled clean against the live broker and cleared its marker. Live state was never restored over, and trading history was never rolled back.

## Three-month archive cycle (isolated fixtures, no waiting)
A month becomes eligible once it ended three calendar months ago (October -> 1 February). Fixtures prove: members re-verified against sidecars, uncompressed tar + manifest + checksum uploaded, the tar and **every member** re-verified from S3, then only those verified daily objects deleted; archives are never deleted; idempotent re-run; staging removed; and in six fault cases (corrupt member, late archive corruption, upload failure, missing sidecar, staging cap, conflicting existing archive) **nothing is deleted**. Of ten mutations of the verification/deletion logic, nine turned the suite red after two targeted tests were added; the tenth (sidecar-presence check) is equivalent because a missing sidecar also fails on download. A real `consolidate` run today correctly reported nothing eligible.

## Measured size and cost estimate (SUPERSEDED: see `VERIFICATION_RECEIPT_20261010.md` for the complete model; the year-one and year-five figures below came from a coarser 5 KB/session assumption)
Snapshots measured today: 3.7 KB, 4.8 KB, 5.1 KB (about 14 KB in S3 including sidecars). Receipts add about 5 KB compressed per session (measured from four archived pilot sessions), so a snapshot is roughly `3.7 KB + 5 KB x sessions`. One snapshot per session: about 10 MB cumulative per quarter, about 160 MB by the end of year one, about 5 GB by year five. At S3 Standard rates (about $0.025/GB-month in this region plus fractions of a cent in requests) that is **under $0.01/month now, roughly $0.004/month at the end of year one, roughly $0.15/month by year five**. Final archives are not deleted and storage is **not** bounded; consolidation does not shrink compressed data.

## Operational record corrected
The earlier statement that nothing is lost before December was inaccurate. **Monitoring and risk-only liquidation continued during containment, but preflight and opening runs were disabled; the complete lifecycle required rearming.** Evidence: preflight and open were disabled from the repair stand-down (about 05:03Z) until full rearm (about 06:39Z) on Saturday 2026-10-10, with brief stand-downs for the cutovers; the only slots in that period fell on a non-session Saturday and the first scheduled slots are Monday 2026-10-12. The journal and receipts show **no timer-triggered run has been missed or has occurred yet**; the only runs are manual validate-only checks.

## Timers and runners
All seven timers enabled after the final reboot (preflight, open, monitor, close, snapshot, offload, consolidate); no active service, no failed unit, writer lock free (single-writer enforcement demonstrated: a second writer got exit 75 / waited); no blocking STOP. V5 and the archived pilot timers remain disabled. Known cosmetic nit: `standdown.sh` prints "all five timers" although it handles seven.

## Next eligible session
**Mon 2026-10-12**: preflight 09:15 ET (**20:15 Hanoi**), open 09:30 ET (**20:30 Hanoi**) - a hold (step 4, no rebalance due), monitoring from 09:35 ET; first post-close snapshot and upload at 16:25 ET = 03:25 Hanoi on Tue 2026-10-13. First rebalance **Wed 2026-12-16**, 09:30 ET = 21:30 Hanoi.
