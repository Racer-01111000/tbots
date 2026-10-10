# Kim recurring paper lifecycle: recoverable vs latched failures (repaired 2026-10-10)

Paper account only. Nothing here authorizes live money, a different genome, new markets or news inputs.

## Principle
A failure is **recoverable** only if nothing unsafe can have happened and the next scheduled slot can simply try again.
Anything that could mean "the account, code or books are not what we think they are" is **latched** by a persisted `STOP`.
Peak equity, drawdown history, the halt latch, the ledger, order ownership and the 50-session cadence are never reset by any recovery path.

## STOP: persisted, classed, absolute
`STOP` is a JSON file in the state directory: `{"schema":"kim-stop-v2","class":...,"stopped_at_utc":...,"reason":...}`. Existing STOP evidence is never overwritten.

| Class | Written when |
|---|---|
| `integrity` | identity/hash drift, account-pin mismatch, unexplained holdings, a broker order not in the ledger, ledger/broker disagreement, state corruption, broker-clock skew over 120 s, any non-paper trading host |
| `credential` | HTTP 401 anywhere, HTTP 403 from the paper API, credential-file trust violation |
| `unexpected_error_streak` | the 6th consecutive unexpected failure (below) |
| `startup_integrity` | config/credential/pin could not be loaded at process start |
| `legacy_or_unknown`, `malformed` | any STOP file with no recognised class, or unreadable (older software, hand-edited, partial write) - **treated as blocking** |

**A persisted STOP of any class blocks EVERY broker mutation: new orders, cancellations and risk liquidation.**
Enforcement is layered: (1) each path checks the file fresh from disk (preflight, open - twice, monitor - before cancelling and again before liquidating);
(2) `GuardedBroker` wraps the broker in every mode and refuses `submit` and `cancel` at the boundary (reads still pass). A fresh integrity check that happens to
pass never overrides a persisted STOP, and removing the original cause does not lift it. While a STOP exists the monitor still reads, reconciles ledger
to broker, keeps updating the peak and the halt latch, and writes its observation (`stop_present`, `stop_class`, `halted_mutations_blocked`), but sends and cancels nothing.
The unit's `OnFailure` also removes the open/preflight timers. The software never clears a STOP.

## Risk halt (separate from STOP)
At -8% from the persisted peak, `halt_state.json` latches (never cleared automatically). **If and only if no STOP exists and the integrity checks pass in that run**, the monitor
cancels working pilot orders and liquidates **pilot-owned** shares only (the protected baseline share is never sold), bounded by the 8-order liquidation budget
(stable client ids `kim-<date>-<SYM>-liq`, so repeated ticks never spend extra budget). The open step never creates exposure again once halted.
With a halt and a STOP together, the STOP wins: nothing is sent until a person has investigated and removed the STOP.

## Recoverable (bounded retry, then abstain this run; no latch)
| Condition | Handling | Limit |
|---|---|---|
| Socket error, timeout, connection reset | retry with backoff | 4 retries per request, sleeps 1, 2, 4, 8 s |
| HTTP 5xx, 408, 425, 429; undecodable/truncated/HTML body | same | same |
| Retries exhausted | `abstained` receipt, exit 0; the next slot (monitor: 5 min) tries again | 40 retries per process |
| Data-host 403 / incomplete or invalid history; calendar/clock unreadable | abstain, no order (an unreadable calendar is never a holiday) | as above |
| POST /v2/orders timeout, 5xx, 429, duplicate-id reply, garbage body | **uncertain**: look the order up by client id first; adopt it if it exists; resubmit the same client id only if the broker answers 404; if the lookup fails, send nothing | at most 3 firings (09:30, 09:32, 09:34) in the one 09:30-09:35 window |
| Service killed / instance rebooted mid-run | nothing resumes; `Persistent=false`, no slot is replayed; the next monitor reconciles first | - |
| Another run holds the lock | exit 75 (success), never an overlap | - |

### Unexpected-error counter (exact rules)
An exception of a kind the code has never classified increments a counter persisted in `unexpected_error_streak.json` (shared by all modes, survives reboots).
Errors 1-5: the run abstains (exit 0), nothing is sent. **On the 6th consecutive**: a `STOP` of class `unexpected_error_streak` is written, the run exits 3
with a `HARD_STOP` record, the open/preflight timers are removed, and (like every STOP) all broker mutations are blocked until a person clears it.
**Reset:** only a run that completes normally (e.g. `monitored`, `preflight_ok`, `hold_not_due`, `closed_reconciled`, `submitted`) deletes the counter file.
Quiet ticks (non-session/outside hours), abstains, `plan-check`, `snapshot` and `reconcile` neither increment nor reset it.

## Passive price drift and the 18% caps
The 18% per-asset and gross limits are enforced **at order time** (planner caps plus an independent post-trade check): no order may leave a position or the account above
18% of equity at the sizing prices, and the protected baseline share counts. They are **not** a continuous mark-to-market ceiling: if prices drift a holding above 18%
between rebalances (currently DBC is about 18.2%), nothing is sold, no gate trips and no STOP is written - the position simply waits for the next exact-session rebalance,
which trims toward target. There is no continuous forced trimming and no manufactured sell/rebuy. If a rebalance's order set cannot satisfy the post-trade check it is refused
(`order_refused`), nothing is sent, and that rebalance is consumed (below).

## Rebalance rule (exact session only; refused or missed rebalances are consumed)
The frozen cadence rebalances at session steps that are multiples of 50 (next: step 50, Wed 2026-12-16). It trades **only on that exact session, inside 09:30-09:35 ET**.
If the window, the session or the instance is missed, the step is recorded as `missed_rebalance_not_caught_up` and consumed; a refused rebalance (`order_refused`) or a
decided-no-orders result is likewise consumed. Nothing is ever traded later to catch up; the next rebalance is the following multiple of 50. (Stricter than the archived pilot.)

## Backups and recovery (see OPERATIONS.md for commands)
A consistent `snapshot` of the complete state directory (SQLite ledger via the backup API under the writer lock, ownership, cadence, session budgets/markers, peak and drawdown
history, STOP/halt state, unresolved order identities, receipts) is taken after each real session closes and its final reconciliation completes (early closes included), verified by an
isolated restore, **uploaded to a private S3 bucket and verified byte-for-byte against the local checksum before the local staging copy is deleted**. A failed upload keeps the pending copy,
is retried hourly (48 attempts), and is visible as a failed unit plus a failure receipt; under storage pressure new snapshots are refused rather than deleting an unoffloaded copy.
Daily objects are consolidated into verified monthly tar archives after three calendar months; automation can delete neither final archives nor anything outside `daily/`.
Restoration never writes over live state: it goes to a new empty directory carrying `RESTORED_UNRECONCILED`, which blocks every mode and every broker mutation until `reconcile` proves
broker and ledger agree. A stale snapshot is never trusted blindly.
**Residual risks:** the instance role can delete `daily/` objects (an instance compromise or bug could remove recent daily snapshots, never archives); there is no bucket versioning (it would keep
deleted snapshots as billable noncurrent copies, defeating the cleanup); S3 and the instance share an AWS account, so account-level loss is not covered.

## What a human does after a STOP or halt
Read `STOP` / `halt_state.json` and the latest receipts. Fix the cause. Run the stand-down procedure if the situation is unclear. Remove the sentinel deliberately (`rm STOP`) only when satisfied,
then `rearm.sh`. Never delete the peak, cadence, ledger or `continuation.json`.
