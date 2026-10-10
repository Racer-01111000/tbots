# Operating the Kim recurring paper lifecycle (unattended)

## Layout (all on the research/pilot instance; HOST, Rick and a CC session are not runtime dependencies)
| Item | Path |
|---|---|
| Release root (hash-pinned, immutable per commit) | `/opt/tbots-kim-recurring/releases/<commit>`, `current` symlink, `ROLLBACK_TO` (previous release) |
| State | `/var/lib/tbots-kim-recurring` (ledger, cadence, peak, baseline, `continuation.json`, `receipts/`, `archive/`) |
| Backups (private, 0700) | `/var/lib/tbots-kim-recurring-backups/kim_recurring_state_<UTC>.tar.gz` + `.sha256` |
| Account pin | `/etc/tbots-kim-recurring/account_pin.json` (hash only; root:ec2-user 0640) |
| Units | `tbots-kim-recurring-{preflight,open,monitor,close,snapshot}.{service,timer}`, `tbots-kim-recurring-hardstop.service` |
| Operator scripts (root) | `/usr/local/lib/tbots-kim-recurring/{hardstop,standdown,rearm}.sh` |
| Archived four-session pilot (read-only history) | `/var/lib/tbots-kim-paper`, `/opt/tbots-kim-paper` (HOLD/EXPIRED stay in place) |

## Schedule (America/New_York, Mon-Fri; slots are *candidates* only, `Persistent=false`, never replayed)
snapshot 09:10 and 16:40 · preflight 09:15 · open 09:30/09:32/09:34 (one window) · monitor every 5 min 09:35-16:30 · close 13:10, 13:20, 16:10, 16:20.
Every run asks Alpaca's calendar/clock (holidays, early closes - monitoring stops 30 min after the real close - and DST). A closed market, missed window or absent operator never causes catch-up orders.

## Safety terms (sealed in `kim_recurring_config.json`, whose sha256 is the submission seal)
Kim genome only (identity + frozen champion + decision-function hashes every run); Alpaca SIP daily bars; paper host only; long-only whole-share DAY orders, extended hours off;
18% per-asset and gross caps counting the protected baseline, enforced at order time (passive drift is not force-trimmed - see RECOVERY_POLICY.md); 4 normal + 8 liquidation orders
per session; 8% drawdown halt from the persisted peak; only pilot-owned shares may be sold. A persisted STOP blocks every broker mutation, liquidation included.

## Look at it
`sudo journalctl -u 'tbots-kim-recurring-*' --since today`; `systemctl list-timers 'tbots-kim-recurring-*'`; `/var/lib/tbots-kim-recurring/receipts/`
(`monitor_<date>.jsonl`, `*_open.json`, `*_close.json`, `*_preflight.json`, `*_snapshot.json`); `STOP` (with `class`), `halt_state.json`, `open_done_<date>.json`, `close_done_<date>.json`.

## Safe stand-down (disabling timers alone is NOT enough)
`sudo /usr/local/lib/tbots-kim-recurring/standdown.sh`
1. disables all five timers; 2. waits (never kills) for any running recurring service to finish, up to 10 minutes - if one is still active it reports it and exits 2 rather than killing a possibly-submitting run;
3. proves the writer lock is free (exit 3 otherwise); 4. prints a **read-only** reconciliation (`reconcile`): positions vs baseline+ledger, outstanding broker orders, unresolved ledger rows.
It cancels nothing and sells nothing. Holdings and state are untouched. Review outstanding orders yourself (DAY orders expire at the close) and cancel at the broker only on a deliberate human decision.

## Re-arm
`sudo /usr/local/lib/tbots-kim-recurring/rearm.sh contained|full` - refuses while a STOP or an unreconciled restore exists, while a writer is active, or unless a fresh `reconcile` is clean.
`contained` = monitor + close + snapshot (reconciliation and risk-halt protection, no new exposure); `full` also enables preflight + open.

## Code recovery (never rolls trading state backward)
Bad release: stand down; deploy the fix as a NEW release directory (immutable, hash-checked) or repoint `current` to the release named in `ROLLBACK_TO`; re-render and reinstall the units for that release
(`render_units.py`), `daemon-reload`, `rearm.sh`. Ledger, cadence, peak, halt, STOP and `continuation.json` are never restored from an older copy as part of a code rollback.

## State recovery (only if the state directory itself is lost or corrupt)
1. Stand down. 2. Pick a snapshot; `kim_recurring.py verify-snapshot <tar>` (hash + manifest + SQLite integrity + ledger digest, in a throwaway directory).
3. `kim_recurring.py restore <tar> /var/lib/tbots-kim-recurring-restored` (refuses live state and non-empty targets; leaves `RESTORED_UNRECONCILED`).
4. `TBOTS_KIM_RECURRING_STATE_DIR=/var/lib/tbots-kim-recurring-restored kim_recurring.py reconcile` - must report `reconciled_clean` (it then clears the marker); any finding (holdings, fills or orders newer than the snapshot) means the snapshot is stale: investigate, do not force it.
5. With timers still disabled, move the damaged directory aside (keep it), put the reconciled copy in place, then `rearm.sh`.

## Forward check without touching state
`cp -a /var/lib/tbots-kim-recurring /var/tmp/scratch && TBOTS_KIM_RECURRING_STATE_DIR=/var/tmp/scratch python3.13 kim_recurring.py plan-check` (no submission environment; writes only into the scratch copy).

## Never
`systemctl start` the open or monitor service by hand (it runs the real submitting runner). Never restore over live state. Never edit or delete the peak, cadence or ledger.
