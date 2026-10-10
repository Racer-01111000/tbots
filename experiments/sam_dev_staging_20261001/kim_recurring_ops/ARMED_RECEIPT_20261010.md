> **SUPERSEDED by `REPAIR_RECEIPT_20261010.md`.** This first receipt described a monitor that could liquidate under a persisted STOP and had no state backups; both are corrected there.

# Kim unattended recurring PAPER lifecycle - ARMED (sanitized)

GO `TBOTS_KIM_UNATTENDED_RECURRING_PAPER_20261010`. Alpaca **paper** only. No broker/account identifiers, balances, raw market data, private state or detailed broker receipts are published here.

| | |
|---|---|
| Deployed code SHA | `beeee1fece3f60b199426e8a3f926570a5ceb010` (release tarball sha256 `526980b5deb925ec640c42d0700cd77a7ceabaf01cec516fbba018012d7ccc90`, extracted read-only under a new release root; `ROLLBACK_TO=NONE`, first release) |
| Submission seal | sha256 of `kim_recurring_config.json` = `9d7de30639f560cefc13af83160b455e384fad2a66028677eeef9e528e659a1a`; the archived pilot config can no longer arm anything |
| Migrated state identity | `8fdd665130c2a9b7c6ccab214c7110f4add33c450eda30b0e24616c1f5b45fa5` (sha256 of `continuation.json`) |
| Archived pilot | untouched: tree hash identical before and after (`3c55d9c5...`), HOLD/EXPIRED/campaign evidence intact; V5 stays HOLD, nothing re-armed |

## Continuation (explicit, audited, write-once)
Ledger (2 orders), cadence (anchor 2026-10-06, last rebalanced step 0), persisted equity peak/start anchor and baseline were carried over **byte-for-byte** (ledger via SQLite backup, row digest equal). Not copied, not cleared: peak, drawdown history, halt state, STOP, order ownership. Cleared: only the scheduled-expiry restriction (the archived pilot's HOLD/EXPIRED/campaign are not part of the new lifecycle).

Reconciliation before activation, against the live paper account: positions = protected baseline share + pilot-owned shares exactly; no open orders; every broker FILL activity equals the ledger per order; no unexplained orders; drawdown vs the persisted peak far inside the 8% limit; the existing holding sits slightly above 18% of equity from price drift (a forward `plan-check` shows the frozen rule would trim a few shares at the next rebalance - nothing is forced, no gate trips on drift).

## Effective risk terms (unchanged from the pilot)
Kim genome only (identity + frozen champion + decision-function hashes verified every run); Alpaca SIP daily bars; paper host only (independent live-endpoint rejection); long-only whole-share DAY orders, extended hours off; 18% per-asset and gross caps counting the protected baseline; 4 normal + 8 liquidation orders per session; 8% drawdown halt from the persisted peak, latched; only pilot-owned shares may be sold; duplicate-order, freshness and history-contiguity checks; 50-session rebalance cadence.

## Lifecycle
Fixed weekday ET slots as *candidates* only (systemd, `Persistent=false`, never replayed after downtime): preflight 09:15, open 09:30/09:32/09:34 (one 09:30-09:35 window, per-session done-marker), monitor every 5 min, close 13:10/13:20/16:10/16:20. Every run asks Alpaca's calendar/clock (holidays, early closes, DST). A closed market, missed window or absent operator never causes catch-up orders. **Rebalance rule:** trades only on the exact step-50 session inside its window; if missed it is recorded and consumed, never traded later (stricter than the pilot - see `RECOVERY_POLICY.md`).

## Recovery policy
See `RECOVERY_POLICY.md`: transient API/network failures retry with 1/2/4/8 s backoff (40 retries per process), uncertain order submissions are looked up by client id before any resubmission, overlap is prevented by an exclusive lock; identity/hash drift, account mismatch, unexplained holdings/orders, ledger discrepancy, credential or live-endpoint violations, and the 8% halt **latch** and are never cleared or reset automatically.

## Verification
HOST: 352 tests pass (359 including 7 unrelated lineage-D data-dependent shadow-parity tests). Instance interpreter (py3.13.5): 352 pass (the shadow-parity file is excluded there because it needs ~82 MB of historical run data that is not on the instance). 53 new regressions cover continuation, evidence immutability, sessions/holidays/early close/DST, restart, ambiguous broker responses, partial fills, duplicate starts, bounded retries, baseline protection, caps, halt, live-endpoint rejection and STOP persistence; six mutation checks were red. Production run with submission disabled: migration reconciled, identity and calendar gating pass, forward plan-check on live SIP data completes. Units verified with `systemd-analyze`, installed, enabled, and survived a full OS reboot (timers re-armed, nothing fired on boot, state hashes unchanged). No orders were created to demonstrate installation.

## Next eligible session
**Mon 2026-10-12**: preflight 09:15 ET (20:15 Hanoi), open 09:30 ET = **20:30 Hanoi** (13:30Z). The first rebalance-eligible session is **Wed 2026-12-16** (step 50), 09:30 ET = 21:30 Hanoi.
