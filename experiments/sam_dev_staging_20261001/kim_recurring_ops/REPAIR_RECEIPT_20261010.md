# Kim recurring PAPER lifecycle - repair receipt (sanitized): **BLOCKED on one dependency, system CONTAINED**

GO `TBOTS_KIM_RECURRING_REPAIR_TO_RESOLUTION_20261010`. Alpaca **paper** only. No broker/account identifiers, balances, raw market data, private state or detailed broker receipts appear here.

## Result
| | |
|---|---|
| Status | **BLOCKED** (not ARMED): the required automated off-instance durable backup copy cannot be created within this GO's limits. Everything else is implemented, tested, deployed and verified. |
| Containment | monitor (reconciliation + risk-halt protection), close and snapshot timers **enabled**; preflight and open timers **disabled** (no new exposure). Nothing is due before the step-50 rebalance on 2026-12-16, so no opportunity is lost. |
| Deployed SHA | `0fb0dcaeee161a17f47f6ca2ccc3889549349244` (immutable release, tree hash `97b01457...`; previous release `beeee1fe...` recorded as `ROLLBACK_TO`) |
| Submission seal | sha256 of the repaired `kim_recurring_config.json` = `8350379473bb290baddd97a9f4feaf19de96323ddac7c9dee8b879e` ... (see config; unchanged risk terms, two backup keys added) |
| State continuity | migrated-state identity unchanged (`8fdd6651...`); ledger rows digest identical before and after the repair (`80de4278...`); peak, cadence, baseline, halt and STOP files untouched; archived pilot tree hash identical (`3c55d9c5...`); V5 still HOLD. |
| Broker reconciliation | clean, read-only: holdings = protected baseline + pilot ledger, no outstanding or unexplained orders, no unresolved ledger rows, no STOP, no halt. |

## The blocker
The instance role has only SSM-core and mail-send rights, no storage rights; the instance Python has no AWS SDK; and the account has no backup plan, lifecycle policy or snapshots. A durable copy outside the instance's failure domain therefore needs a **new permission or a new paid service**, both excluded by the GO. Needed (one of): (a) a private S3 bucket plus a narrowly scoped write permission for the instance role and the SDK; (b) an EBS snapshot lifecycle policy for the root volume. Until then snapshots are verified and bounded but remain on the instance, and a manual HOST copy exists. To go to full operation after authorization or an explicit decision to accept local-only backups: `sudo /usr/local/lib/tbots-kim-recurring/rearm.sh full`.

## What changed (all tested)
- **Persisted STOP classes** (`integrity`, `credential`, `unexpected_error_streak`, `startup_integrity`, plus unknown/legacy/malformed treated as blocking). Any STOP blocks **every** broker mutation - submission, cancellation, liquidation - checked fresh from disk on each path and again at the broker boundary. A passing integrity check never overrides it; removing the cause does not lift it; existing STOP evidence is never overwritten.
- **Risk halt stays separate**: authorized liquidation of pilot-owned shares (ownership- and budget-bounded, baseline share protected) runs only when no STOP exists and integrity passes. Halt + STOP together: STOP wins.
- **Snapshots**: SQLite-safe, taken under the writer lock, covering the complete state (ledger/ownership, cadence, session budgets, peak/drawdown history, STOP/halt, unresolved order identities, receipts); hash-recorded; verified by an isolated restore on every run; private 0700; newest 60 kept; 09:10 and 16:40 ET on session weekdays.
- **Restore** goes only to a new empty directory and leaves a marker that blocks every mode until `reconcile` proves broker and ledger agree; stale snapshots show up as findings. `verify-snapshot`, `restore`, `reconcile` modes added.
- **Operator scripts**: `standdown.sh` (disable all timers, wait for - never kill - running services, prove the writer lock is free, print a read-only reconciliation of outstanding broker orders) and `rearm.sh` (refuses on STOP, unreconciled restore, active writer or an unclean reconcile).
- **Docs corrected**: STOP semantics, passive price drift above 18% (order-time enforcement only, no forced trimming), refused/missed exact-session rebalances are consumed with no catch-up, exact unexpected-error counter rules, stand-down, code recovery without rolling state back, state recovery.

## Verification
- STOP gap reproduced: the previously deployed code made 1 broker mutation in the "integrity STOP, cause removed, restart, halt" scenario; the repaired code makes **0**.
- 53 -> 100 recurring regressions: every STOP class (8) x {open, preflight, monitor with a working order and a tripped halt} -> zero submissions, cancellations or liquidations; risk-only liquidation within ownership and budget; the incorrect "liquidate under an unrelated STOP" test was replaced. Mutation checks on the new controls are red (STOP ignored, guard removed, halt path unchecked, legacy STOP non-blocking, unverified snapshot, restore-over-live).
- Test counts: HOST 398 pass (full). Instance interpreter (py3.13.5) 391 pass; the 7 lineage-D shadow-parity tests are excluded there because they need about 82 MB of historical run data that is not on the instance.
- Production validate-only (submission disabled): real snapshot taken and verified, isolated restore refused over live state and succeeded elsewhere, restored copy reconciled clean against the live broker and cleared its marker; non-session runs of every mode were quiet/not-a-session; forward plan-check completed on live SIP data.
- Units verified, installed, survived a full OS reboot (timers re-armed, nothing fired on boot, state hashes unchanged). No orders or cancellations were placed to demonstrate any of this.

## Next eligible session
**Mon 2026-10-12**: snapshot 09:10 ET (20:10 Hanoi), monitor from 09:35 ET (**20:35 Hanoi**, 13:35Z) - hold/monitor only. First rebalance session **Wed 2026-12-16**, 09:30 ET = 21:30 Hanoi (requires `rearm.sh full` first).
