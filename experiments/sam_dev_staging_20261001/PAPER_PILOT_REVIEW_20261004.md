# Paper-pilot review of fe72d72 — 2026-10-04

Branch `repair/paper-pilot-integration-review-20261004` (local only; not pushed, not relayed to EC2).
Submission remains OFF: `alpaca_adapter._SUBMISSION_ENABLED_CONST = False` is untouched, no timer is installed.
Tests: 91 at fe72d72 → 155 now, all pass (`pytest` from the integration venv; see "Running the tests").

## Defects found in fe72d72 (all reproduced red before the fix)

| # | Area | Defect | Fix |
|---|------|--------|-----|
| 1 | Drawdown | `peak_equity` returned drawdown **positive** (+0.15); `risk.validate` halts on `<= -halt` (negative). **The halt could never fire.** Existing tests hand-built inputs, so they passed. | `peak_equity` now returns negative-or-zero (same as `Portfolio.update_peak_and_drawdown`); pinned by a test feeding real `peak_equity` output into the order path and one comparing against `Portfolio`. |
| 1 | Drawdown | Recorded only inside the rebalance branch (1 session in 50). | Recorded on every valid post-cutoff trading session, before and independent of the rebalance gate and of bar availability. |
| 1 | Drawdown | Halt = refuse the order (positions stay held, nothing sold). Backtest: liquidate to cash and stay halted. | Persisted latch `halt_state.json`; on trigger target = cash (sells), stays flat across restarts until a human removes the file. |
| 1 | Drawdown | Non-atomic `write_text`; corrupt/missing peak file silently reset the peak to "today". | Atomic write (tmp+fsync+replace+dir fsync); corrupt → `StateCorrupt`, missing-with-history → `PeakStateMissing`; both abstain with `critical`. |
| 2 | Cadence | `sessions_elapsed` was a **run** counter: advanced on holidays, calendar-API failures (treated as "not a trading day"), pre-cutoff runs, and `Persistent=true` catch-ups; a missed day didn't count. | Step is derived from the exchange calendar (sessions since session zero). Non-trading day / too-early / calendar failure change **no** state. API failure ≠ holiday. |
| 2 | Cadence | A rebalance session that abstained (missing data) was marked evaluated → **rebalance skipped for 50 sessions**. | Rebalance is "due" while `floor(step/N)*N > last_rebalanced_step`; an abstained or refused rebalance stays pending and is taken at the next valid session without shifting the N-cycle. Abstentions counted; `escalate` at ≥3. |
| 3 | Order limit | JSON read-modify-write: lost updates under concurrency (10 of 64 increments lost/errored in test), torn reads, non-atomic. | Count is derived from the SQLite ledger and enforced in the same `BEGIN IMMEDIATE` transaction as the intent INSERT (24 racing processes vs cap 8 → exactly 8). |
| 3 | Order limit | Counter incremented **before** `submit_with_reconciliation`, so a restart replaying already-sent orders burned cap (cap 8 = universe size → legitimate orders blocked). | Replays hit the duplicate check first and never touch the cap. |
| 4 | Wiring | `main()` computed intended orders and stopped. Ledger, limits and adapter were never reached; `end_to_end_dry_run.py` tested the parts, not the runner. | `run_session(deps)` fully injected; tests drive the real runner + real `AlpacaPaperBroker` against an in-memory Alpaca. |
| 4 | Wiring | `IntendedOrderSpec` default `time_in_force="opg"`. Alpaca **rejects OPG submitted 09:28–19:00 ET**; the job runs 16:45 ET → every order rejected. (Verified against docs.alpaca.markets/docs/orders-at-alpaca.) | `day` (queued for next open when submitted after close); config loader refuses anything else. |
| 4 | Wiring | No production broker object: only a read-only `find_by_client_order_id`, where **any non-200 (incl. 5xx) returned None = "not found"** → would trigger a duplicate send. | `AlpacaPaperBroker`: timeout/5xx/429/duplicate-id-422 → `UncertainSubmissionError`; lookup 404 → None but 5xx/timeout → `BrokerLookupError` (never submit on unknown); definitive 4xx → recorded `rejected`; `submit()` re-checks the gate itself. |
| 4 | Wiring | Disabled mode must not write the ledger (else `is_first_submission_session` never becomes true and baseline seeding is skipped on enable). | Gate closed ⇒ no ledger writes (tested, then enabling later seeds from broker). |
| 4 | Wiring | Account/positions/orders non-200 was ignored → positions `None` = "flat" → buy from zero. | Any unreadable broker state abstains. |
| 4 | Wiring | `-buy`/`-sell` suffix in dry run vs `kim-DATE-SYMBOL` in code. | One convention: `kim-{session}-{symbol}`; a crash-rerun can only re-present the same id. |
| 2/4 | Cadence+wiring | (found in the second review of my own repair) A **shadow** session spent the rebalance (`last_rebalanced_step` advanced on any `decided`), so any pre-activation shadow run, including the staged timer in shadow, would have used up the only in-window entry (next due ≈ Dec 14, after expiry) → zero orders. | Only a live (gate-open, non-validate-only) decision spends the rebalance. Test: shadow session, then a gate-open session next day with **no state edits** sends the entry. |
| 4 | Wiring | (same review) A timed-out POST whose lookup returned 404 left the row `uncertain_unresolved`; the rerun treated it as `already_resolved_no_resubmit`, counted it as sent, marked the session evaluated and spent the rebalance with **no order at the broker**. | `uncertain_unresolved` is unresolved: re-look-up; found → adopt; 404 → resubmit the same `client_order_id`; lookup failure → no send, session stays retryable. Tests assert exactly one broker order per id. |
| 4 | Wiring | (same review) A definitive broker rejection (e.g. 403 insufficient buying power) spent the rebalance and left the account flat for 50 sessions; cancelled/expired day orders were never noticed. | Rejection does not spend the rebalance and sets `escalate`; reconciliation flags orders that went `canceled`/`expired`/`rejected` since the last run. |
| 4 | Wiring | (same review) `KIM_SESSION_VALIDATE_ONLY=1` with the gate open would still have sent orders, and it created/migrated the ledger file and updated rows. | Validate-only never sends and touches no state file (ledger, cadence, peak, halt); tested with the gate open. |
| – | Sizing | No cash constraint (only sizing off equity). | Buys limited to cash above a reserve; **no credit for same-batch sells**; scaled down, never borrow. Post-trade per-symbol and total limits re-checked independently. Sells submitted before buys. |

Known limits left as-is (flagged, not fixed): `alpaca_adapter.route_order` + its in-memory `PaperOrderLedger` and `OrderStatus` enum (rejects `pending_new`/`held`/`expired`) are now **unused by the runner**; they should be deleted or left unreachable rather than "fixed". Halt with *no* bars available abstains (cannot size liquidation); it retries next session and escalates.

## Candidate comparison (same replay, same cost model: 5 bps commission + slippage; ceiling = 18 % gross)

15-month primary / 21-month supplementary. "18 % passive" = the matched passive control.

| Candidate | Family | Ret 15m (10bp) | Max DD | Sharpe | Excess vs 18 % passive | 5 bp vs 10 bp | Orders / turnover / cost (15m, 10bp) |
|---|---|---|---|---|---|---|---|
| **gen_0307d23c… S5D champion (Kim)** | trend, 1 position ≤18 % | 3.76 % / 7.75 % | **−6.72 % / −5.28 %** | 0.61 / 0.89 | +0.26 pp / +1.45 pp | rank unchanged | 9 / 0.96× / 0.144 % |
| gen_d890eb9f… (D) | defensive rotation, 3 pos., cap 21 % | 3.70 % / 7.24 % | −1.97 % / −2.70 % | 1.17 / 1.65 | +0.21 pp / +1.16 pp | rank unchanged | not recorded |
| gen_8e7a622b… (D, "Sam") | defensive rotation, cap 23 % | 3.63 % / 6.68 % | −1.97 % / −2.70 % | 1.14 / 1.53 | +0.15 pp / +0.85 pp | rank unchanged | 40 / 1.59× / 0.238 % |
| 18 % passive control | – | 3.44 % / 5.17 % | −1.05 % / −1.51 % | 1.61 / 1.60 | 0 | – | 79 / 0.26× / 0.040 % |

Observations that matter more than the rank order:
* **The top three are statistically indistinguishable on return** (0.26 / 0.21 / 0.15 pp over 15 months); ranks 4–5 are exact ties. The champion's lead is ~0.05 pp/yr.
* **Risk is not close:** the champion carries ~3.4× the drawdown and about half the Sharpe of the D candidates, and a *lower* Sharpe than the passive control (0.61 vs 1.61) for +0.3 pp of 15-month return.
* **Sample size is tiny:** the champion makes ~6 single-asset decisions (9 orders) in 315 sessions. Costs are immaterial to the ordering (≤0.1 pp).
* **Selection bias favours the D rows:** they were picked *because* they ranked top in this very replay; the champion was frozen independently of it.
* "Native" (5 bp) matrix is not comparable (each candidate vs a passive matched to its nominal 21–89 % cap); the champion's effective exposure is ≤18 % because `max_positions=1`. Use the 18 %-ceiling columns.
* At 18 % exposure the genome's **12 % halt needs a ~67 % loss in the held asset** and is effectively inert.
* **Runner support:** only the champion's `control_agent.decide` path is wired. D candidates need `s6a_runtime.decide_D` wiring plus a parity test against `replay_harness.simulate(use_decide_D=True)`. Not done.

## Recommendation

**Activate the paper pilot with `gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155` (the frozen S5D champion, "Kim"), with a tighter pilot-level halt of 8 %.**
Reason: the pilot's job is to prove the execution pipeline (ledger, limits, restart safety, broker semantics), not to pick a winner; the champion is the only genome the tested runner executes and the one already run in shadow, and changing genome and integration in the same step would confound any live fault. It is *not* the best risk-adjusted candidate. **Run `gen_d890eb9f…` in parallel as shadow-only** (after wiring + parity test) and decide a genome change separately, with out-of-sample data.

## Proposed paper-only activation configuration (`pilot_config.json`, validated by `pilot_config.py`: can only tighten the genome)

| Item | Value |
|---|---|
| Genome | `gen_0307d23c…` (exact id above) |
| Max asset weight / gross exposure | 18 % / 18 % of **equity** (never buying power) |
| Cash constraint | buys ≤ cash − 10 % equity reserve; no credit for same-batch sells; no margin |
| Drawdown response | measured on account equity vs persisted peak, every trading session; at −8 % latch halted → sell to cash → stay flat until a human deletes `halt_state.json` |
| Order cap | 4 distinct order intents per session date (ledger-enforced; universe is 8, max_positions is 1) |
| Execution timing | decision 16:45 ET after the 16:15 data cutoff; market, **time_in_force=day**, queued to next open; sells before buys |
| Kill switch | `kim_shadow_pilot/KILL_SWITCH` file; checked at batch start and before every send; a blocked session stays retryable |
| Expiry | 2026-11-01 00:00 ET (runner refuses on/after; staged expiry timer also disables the unit) |
| Cadence | session zero 2026-10-02, N=50. The step-0 entry stays **pending until a live decision**; pre-activation checks on EC2 should still use `KIM_SESSION_VALIDATE_ONLY=1`. Next rebalance step 50 ≈ 2026-12-14 (after expiry) |
| **Enable step (for review, not done)** | Two independent changes, both required: (1) `_SUBMISSION_ENABLED_CONST = True` in `alpaca_adapter.py` (code change), and (2) `Environment=TBOTS_ALPACA_SUBMISSION_ENABLED=true-i-understand-the-risk` in `tbots-kim-session-recurring.service` (the staged unit has no `Environment=` line, so flipping only the constant keeps running in shadow). Then install/enable the two staged timer units. |

Decisions for Rick (not made here):
1. **Zero-order risk:** with N=50 the *only* rebalance inside the 29-day window is the first-live-session entry. If you want to exercise rebalance/rotation or the halt-liquidation path live, the window is too short for this genome.
2. Halt threshold 8 % (just above the worst observed 6.72 %) vs something tighter/looser.
3. What happens to open positions at expiry (default: timer disabled, positions remain; flatten is a separate GO).
4. Genome: stay with the champion (recommended for the pipeline pilot) or switch to a D candidate after wiring.
5. Whether the account is dedicated (drawdown is whole-account equity; the 1-share DBC test position is treated as baseline and will be sold if not in target).

## Running the tests
```
cd experiments/sam_dev_staging_20261001
PYTHONPATH=alpaca_adapter:kim_order_simulation_tests:.:../../scripts \
  <integration venv>/bin/python -m pytest -q --ignore=global_brief_feasibility --ignore=credential_installer .
```
(`global_brief_feasibility` and `credential_installer` read real credentials/hit the network and are excluded.)
