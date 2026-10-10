# Operating the Kim recurring paper lifecycle (unattended)

## Layout (all on the research/pilot instance; HOST, Rick and a CC session are not runtime dependencies)
| Item | Path |
|---|---|
| Release root (hash-pinned, immutable) | `/opt/tbots-kim-recurring/releases/<commit>`, `current` symlink, `ROLLBACK_TO` |
| State | `/var/lib/tbots-kim-recurring` (ledger, cadence, peak, baseline, `continuation.json`, `receipts/`, `archive/`) |
| Account pin | `/etc/tbots-kim-recurring/account_pin.json` (hash only; root:ec2-user 0640) |
| Units | `tbots-kim-recurring-{preflight,open,monitor,close}.{service,timer}`, `tbots-kim-recurring-hardstop.service` |
| Archived four-session pilot (read-only history) | `/var/lib/tbots-kim-paper` (HOLD/EXPIRED stay in place) and `/opt/tbots-kim-paper` |

## Schedule (America/New_York, Mon-Fri; slots are *candidates* only)
preflight 09:15 · open 09:30/09:32/09:34 (one window) · monitor every 5 min 09:35-16:30 · close 13:10, 13:20, 16:10, 16:20.
Each run asks Alpaca's calendar/clock: not a session -> nothing; early close -> the 13:xx close slot does the final reconciliation and
monitoring stops 30 min after the real close; DST is handled by systemd's time zone and by `zoneinfo`. `Persistent=false`: a missed slot is never replayed.

## Safety terms (unchanged from the pilot; sealed in `kim_recurring_config.json`, whose sha256 is the submission seal)
Kim genome only; Alpaca SIP daily bars; paper host only; long-only whole-share DAY orders, extended hours off; 18% per-asset and gross
exposure caps counting the protected baseline share; 4 normal + 8 liquidation orders per session; 8% drawdown halt from the persisted peak;
duplicate-order, freshness, contiguity and identity checks; baseline share never sold.

## Look at it
`sudo journalctl -u 'tbots-kim-recurring-*' --since today`; `systemctl list-timers 'tbots-kim-recurring-*'`;
`/var/lib/tbots-kim-recurring/receipts/` (`monitor_<date>.jsonl` one line per 5-min tick, `*_open.json`, `*_close.json` daily summary, `*_preflight.json`);
`STOP`, `halt_state.json`, `close_done_<date>.json`, `open_done_<date>.json`.

## Read-only forward check (exercises the data -> decision -> plan path that first runs unattended at step 50)
`cp -a /var/lib/tbots-kim-recurring /var/tmp/scratch && TBOTS_KIM_RECURRING_STATE_DIR=/var/tmp/scratch python3.13 kim_recurring.py plan-check`
(no submission environment variables; writes only into the scratch copy).

## Rollback
`/opt/tbots-kim-recurring/ROLLBACK_TO` names the previous release (NONE for the first). To roll back: disable the four timers, repoint `current`, re-render the units for that release, re-enable. State is never rolled back.

## Housekeeping
Monitor lines are appended per session (not a file per tick). Files older than 30 days are tarred, read back and hash-compared, the tar's sha256 is recorded in a manifest, and only then are the originals removed. Nothing else is ever deleted by the software.

## Never
`systemctl start` the open or monitor service by hand (it runs the real submitting runner). Use `plan-check` on a scratch copy instead.
