# Operating the Kim recurring paper lifecycle (unattended)

## Layout (all on the research/pilot instance; HOST, Rick and a CC session are not runtime dependencies)
| Item | Path |
|---|---|
| Release root (hash-pinned, immutable per commit) | `/opt/tbots-kim-recurring/releases/<commit>`, `current` symlink, `ROLLBACK_TO` (previous release) |
| State | `/var/lib/tbots-kim-recurring` (ledger, cadence, peak, baseline, `continuation.json`, `receipts/`, `archive/`) |
| Backup STAGING (private, 0700; **temporary only**) | `/var/lib/tbots-kim-recurring-backups/` - a snapshot lives here only until its upload is verified, then it is deleted |
| Off-instance backups | private S3 bucket (name in the root-owned `/etc/tbots-kim-recurring/offload.json`, not in Git), prefixes `daily/` and `archives/` |
| Account pin | `/etc/tbots-kim-recurring/account_pin.json` (hash only; root:ec2-user 0640) |
| Units | `tbots-kim-recurring-{preflight,open,monitor,close,snapshot,offload,consolidate}.{service,timer}`, `tbots-kim-recurring-hardstop.service` |
| Operator scripts (root) | `/usr/local/lib/tbots-kim-recurring/{hardstop,standdown,rearm}.sh` |
| Archived four-session pilot (read-only history) | `/var/lib/tbots-kim-paper`, `/opt/tbots-kim-paper` (HOLD/EXPIRED stay in place) |

## Schedule (America/New_York, Mon-Fri; slots are *candidates* only, `Persistent=false`, never replayed)
preflight 09:15 · open 09:30/09:32/09:34 (one window) · monitor every 5 min 09:35-16:30 · close 13:10, 13:20, 16:10, 16:20 ·
**snapshot 13:25/13:40/13:55/16:25/16:40/16:55** (candidates; one snapshot per real session, only after the real close and that session's final reconciliation, so early closes snapshot at 13:xx) ·
**offload hourly (UTC :20)** retries pending uploads · **consolidate Sundays 14:00 UTC**.
Every run asks Alpaca's calendar/clock (holidays, early closes - monitoring stops 30 min after the real close - and DST). A closed market, missed window or absent operator never causes catch-up orders.

## Safety terms (sealed in `kim_recurring_config.json`, whose sha256 is the submission seal)
Kim genome only (identity + frozen champion + decision-function hashes every run); Alpaca SIP daily bars; paper host only; long-only whole-share DAY orders, extended hours off;
18% per-asset and gross caps counting the protected baseline, enforced at order time (passive drift is not force-trimmed - see RECOVERY_POLICY.md); 4 normal + 8 liquidation orders
per session; 8% drawdown halt from the persisted peak; only pilot-owned shares may be sold. A persisted STOP blocks every broker mutation, liquidation included.

## Look at it
`sudo journalctl -u 'tbots-kim-recurring-*' --since today`; `systemctl list-timers 'tbots-kim-recurring-*'`; `/var/lib/tbots-kim-recurring/receipts/`
(`monitor_<date>.jsonl`, `*_open.json`, `*_close.json`, `*_preflight.json`, `*_snapshot.json`); `STOP` (with `class`), `halt_state.json`, `open_done_<date>.json`, `close_done_<date>.json`.

## Backups: offload, retention and the archive cycle
1. After a session's final reconciliation the `snapshot` unit takes a consistent snapshot (SQLite backup API under the writer lock, complete state, self-verified by an isolated restore), then **uploads it to `s3://.../daily/YYYY/MM/`** (SSE-S3, create-only/no overwrite) with a `.sha256` sidecar.
2. The object is downloaded back and compared byte-for-byte with the local file. **Only then** is the local copy deleted. Local files are temporary staging; there is no local history.
3. Failure: the pending file is kept, the attempt is counted, a visible receipt (`*_offload.json`, `OFFLOAD_FAILED`) is written and the unit fails (`systemctl --failed`). The hourly `offload` timer retries up to 48 times; after that the file is `stuck` (still kept, still reported).
4. Storage pressure: if 14 snapshots are pending or free space is under 512 MB, **new snapshots are refused** with `SNAPSHOT_REFUSED_STORAGE_PRESSURE`. Nothing unoffloaded is ever deleted to make room.
5. Three-month cycle: daily objects stay in S3 until the whole month ended at least three calendar months ago (October snapshots become eligible on 1 February). The weekly `consolidate` run downloads that month (bounded staging, 256 MB cap), checks each member against its sidecar, builds an **uncompressed** tar (the members are already compressed) plus manifest and checksum, uploads them to `archives/`, re-downloads and re-verifies the tar and every member, and only then deletes exactly those verified daily objects. The instance role cannot delete under `archives/` (and the bucket policy denies it), so automation can never remove a final archive.
6. No S3 expiration rule exists except aborting incomplete multipart uploads. Final archives are kept until a separate retention decision.
**Cost/size honesty:** consolidation reduces object count, not bytes. Each daily snapshot re-includes the whole receipts history, so snapshot size grows slowly and total stored bytes grow roughly with sessions x receipts size. At measured sizes this is cents per month, but it is **not** bounded; measure with `aws s3api list-objects-v2` per prefix.

## Safe stand-down (disabling timers alone is NOT enough)
`sudo /usr/local/lib/tbots-kim-recurring/standdown.sh`
1. disables all seven timers; 2. waits (never kills) for any running recurring service to finish, up to 10 minutes - if one is still active it reports it and exits 2 rather than killing a possibly-submitting run;
3. proves the writer lock is free (exit 3 otherwise); 4. prints a **read-only** reconciliation (`reconcile`): positions vs baseline+ledger, outstanding broker orders, unresolved ledger rows.
It cancels nothing and sells nothing. Holdings and state are untouched. Review outstanding orders yourself (DAY orders expire at the close) and cancel at the broker only on a deliberate human decision.

## Re-arm
`sudo /usr/local/lib/tbots-kim-recurring/rearm.sh contained|full` - refuses while a STOP or an unreconciled restore exists, while a writer is active, or unless a fresh `reconcile` is clean.
`contained` = monitor + close + snapshot + offload + consolidate (reconciliation and risk-halt protection, no new exposure; **preflight and open are NOT running**); `full` also enables preflight + open - the complete lifecycle requires `full`.

## Code recovery (never rolls trading state backward)
Bad release: stand down; deploy the fix as a NEW release directory (immutable, hash-checked) or repoint `current` to the release named in `ROLLBACK_TO`; re-render and reinstall the units for that release
(`render_units.py`), `daemon-reload`, `rearm.sh`. Ledger, cadence, peak, halt, STOP and `continuation.json` are never restored from an older copy as part of a code rollback.

## State recovery (only if the state directory itself is lost or corrupt)
1. Stand down. 2. Get a snapshot: `kim_recurring.py fetch-restore latest|<daily/... key> /var/lib/tbots-kim-recurring-restored` downloads from S3, checks the sidecar, verifies hashes/manifest/SQLite integrity/ledger digest and restores into that NEW empty directory (refuses live state and non-empty targets; leaves `RESTORED_UNRECONCILED`). For a local file use `restore <tar> <dir>`; `verify-snapshot <tar>` only verifies.
3. `TBOTS_KIM_RECURRING_STATE_DIR=/var/lib/tbots-kim-recurring-restored kim_recurring.py reconcile` - must report `reconciled_clean` (it then clears the marker); any finding (holdings, fills or orders newer than the snapshot) means the snapshot is stale: investigate, do not force it.
4. With timers still disabled, move the damaged directory aside (keep it), put the reconciled copy in place, then `rearm.sh`. Months already consolidated live in `archives/kim_state_snapshots_YYYY-MM.tar`: extract the wanted member, verify against `MANIFEST.json`, then use `restore`.

## Forward check without touching state
`cp -a /var/lib/tbots-kim-recurring /var/tmp/scratch && TBOTS_KIM_RECURRING_STATE_DIR=/var/tmp/scratch python3.13 kim_recurring.py plan-check` (no submission environment; writes only into the scratch copy).

## Never
`systemctl start` the open or monitor service by hand (it runs the real submitting runner). Never restore over live state. Never edit or delete the peak, cadence or ledger.
