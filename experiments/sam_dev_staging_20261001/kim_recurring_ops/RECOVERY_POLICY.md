# Kim recurring paper lifecycle: recoverable vs latched failures

Paper account only. Nothing in this document authorizes live money, a different genome, new markets or news inputs.

## Principle
A failure is **recoverable** only if nothing unsafe can have happened and the next scheduled slot can simply try again.
Everything that could mean "the account, code or books are not what we think they are" is **latched**: a `STOP` file is written to the
state directory, the run exits 3, the unit's `OnFailure` removes the open/preflight timers, and nothing in this software ever clears it.
Peak equity, drawdown history, the halt latch, the ledger, order ownership and the 50-session cadence are never reset by any recovery path.

## Recoverable (bounded retry, then abstain this run; no latch)
| Condition | Handling | Limit |
|---|---|---|
| Socket error, timeout, connection reset (`OSError`) | retry with backoff | 4 retries per request, sleeps 1 s, 2 s, 4 s, 8 s (15 s max) |
| HTTP 5xx, 408, 425, 429 | same | same |
| Undecodable/truncated/HTML body (`ValueError`) | treated as transient | same |
| Retries exhausted | `Abstain`: receipt `abstained`, exit 0; the next slot (monitor: 5 min) tries again | 40 retries per process in total |
| Data-host HTTP 403 / incomplete or invalid history (freshness, contiguity, non-finite) | abstain, no order | no retry beyond the above |
| Calendar/clock unreadable | abstain (an unreadable calendar is never a holiday) | as above |
| POST /v2/orders: timeout, 5xx, 429, duplicate-id reply, garbage body | **uncertain**: look the order up by client id before anything else; adopt it if it exists; resubmit the same client id only if the broker answers 404; if the lookup itself fails, send nothing | one resubmission per firing; at most 3 firings (09:30, 09:32, 09:34) inside the one 09:30-09:35 window |
| Service killed / instance rebooted mid-run | nothing resumes automatically; `Persistent=false`, so no slot is replayed; the next monitor reconciles ledger and broker before anything else | - |
| Another run holds the lock | exit 75 (treated as success), no overlap ever | - |
| Unexpected exception of a kind never seen | abstain with a persisted streak counter | latches on the 6th consecutive unexpected failure (about 30 minutes of monitor slots); any clean run resets the streak |

## Latched (STOP; human decision required; never auto-resumed)
- Genome/identity/hash drift: genome id, frozen champion file, decision-function source hashes, sealed-config seal.
- Account pin mismatch; account not ACTIVE, blocked, or non-positive equity.
- Any trading host other than the paper host; any credential-file trust violation; HTTP 401 anywhere; HTTP 403 from the paper API.
- Holdings not explained by `protected baseline + pilot ledger`; any broker order not in the ledger or the known diagnostics; ledger/broker disagreement after one re-read.
- Corrupt or missing state files (continuation record, cadence, peak, baseline, halt state).
- Broker clock disagreeing with the host clock by more than 120 s.
- Startup integrity failures (config unreadable, pin unreadable).

## Drawdown halt (separate latch)
At -8% from the persisted equity peak the halt is written to `halt_state.json` and never cleared automatically. The monitor then cancels
pilot orders, liquidates **pilot-owned** shares only (the protected baseline share is never sold), within the 8-order liquidation cap, and
the open step never creates exposure again. The halt keeps liquidating even while a `STOP` is present, provided the integrity checks pass.

## What a human does after a latch
Read `STOP` / `halt_state.json` and the latest receipts. Fix the cause. Remove the sentinel deliberately (`rm STOP`), re-enable the open/preflight
timers. Do not delete `peak_equity_state.json`, `cadence_state.json`, the ledger or `continuation.json`.

## Rebalance rule (no catch-up)
The frozen cadence rebalances when the session step reaches a multiple of 50 (the next one: step 50). It trades only **on that exact session,
inside the 09:30-09:35 ET window**. If the window, the session or the instance is missed, the due step is recorded as
`missed_rebalance_not_caught_up`, consumed, and the next rebalance is the following multiple of 50. This is stricter than the archived pilot's
rule (which would have traded on the next session) and follows the GO's "no catch-up orders" instruction.
