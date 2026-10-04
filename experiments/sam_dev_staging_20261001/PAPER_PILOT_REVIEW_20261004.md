# Paper-pilot review of fe72d72 — 2026-10-04

Branch `repair/paper-pilot-integration-review-20261004` (local only; not pushed, not relayed to EC2).
Submission remains OFF: `alpaca_adapter._SUBMISSION_ENABLED_CONST = False` is untouched, no timer is installed.
Tests: 91 at fe72d72 → 247 now (155 at the first review commit, 173 after the lineage-D shadow, 205 after quantity liquidation, 219 after the review fixes), all pass (`pytest` from the integration venv; see "Running the tests").

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
* **Runner support:** only the champion's `control_agent.decide` path is wired. D candidates need `s6a_runtime.decide_D` wiring plus a parity test against `replay_harness.simulate(use_decide_D=True)`. *Done afterwards, shadow-only: see the addendum.*

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

---

# Addendum (second brief, same day): lineage-D shadow wiring, delayed visibility, halt semantics

Status: SHADOW ONLY. Nothing here can place or reserve an order. EC2 deployment, timer activation and enabling submission are all still pending. The 8 % halt and the 2026-11-01 expiry are **proposed, not accepted**.

## 1. `gen_d890eb9f…` (D_primary_rank3) wiring
* **Identity.** Genome copied verbatim from the primary run's `development_top_eight.json` into `lineage_d_genome_d890eb9f.json`. `lineage_d.load_verified_genome()` recomputes the hash with its own `hashlib` (not `lib.ids`) and refuses anything else; also checked: `lib.ids.genome_id`, the identical genome in the *reproduction* run's file, and `s6a_runtime.validate_genome("D", g)` (frozen schema + Trader-A isolation). A tampered copy is rejected (test).
* **Decision logic.** Not reimplemented: `lineage_d.decide` calls the accepted `s6a_runtime.decide_D(genome, history, step)` unmodified and only drops the folded-in `CASH` residual, as the evaluator does.
* **State semantics (the accepted evaluator's).** A bare `step` (sessions since the book's own session zero); D acts only when `step % 31 == 0`, otherwise `None` (hold); **no catch-up of a missed rebalance** (the champion pilot runner has catch-up, lineage D does not); external 12 % halt (`s6a_final.DRAWDOWN_HALT`) latches on the book's own hypothetical portfolio and liquidates to cash; fills at the next session's raw open with slippage + commission via `execution.Portfolio`.
* **Where it lives.** `shadow_compare.py` (engine), `shadow_strategies.py`, `lineage_d.py`, `kim_shadow_compare.py` (live entrypoint, **not scheduled by any unit**). Each book persists to its own `state.json` under `kim_shadow_pilot/shadow_compare/<name>/`, plus an append-only `sessions.jsonl`.

## 2. Parity proof (exact equality, identical inputs) — `shadow_compare_evidence_20261004.json`
Reference = the accepted evaluator path: `replay_harness.simulate(... use_decide_D=True)` using the accepted primitives (`decide_D`, `execution.Portfolio`, `risk.validate`), on the accepted normalized dataset (`data/normalized`, last session 2026-08-25). I did **not** run `s6b_evaluator._simulate_admitted_episode` itself: it needs the sealed-lane bundle and admission token, and the previous GO deliberately kept this work out of the sealed S5 system.

| Window | Steps | Inputs + targets exactly equal | Final equity (accepted vs shadow, cents) | Orders | Turnover | Result |
|---|---|---|---|---|---|---|
| 2024-01-02 → 2026-08-25 | 664 | 664 / 664 | 112,377,006 = 112,377,006 | 76 = 76 | 2.82637156 = 2.82637156 | PASS |
| 2025-07-01 → 2026-08-25 | 290 | 290 / 290 | 104,019,225 = 104,019,225 | 35 = 35 | 1.70564415 = 1.70564415 | PASS |

"Inputs equal" compares the last `regime_window+1` adjusted closes handed to `decide_D` at every step in both paths. The champion book is held to the same standard (final equity, order count, fill count and rebalance count equal to the accepted harness). Restart parity: a book stopped at session 60 and resumed equals an uninterrupted run, with no duplicate records.

## 3. Head-to-head on identical completed sessions (hypothetical $100k books, 5 bp slippage + 5 bp commission, evaluator accounting)
| | Window | Return | Max DD | Rebalances | Orders | Traded notional / start | Mean standing exposure |
|---|---|---|---|---|---|---|---|
| champion | 2024-01 → 2026-08-25 | 10.83 % | −5.58 % | 14 | 20 | 2.52× | 18 % |
| lineage D | same | 12.38 % | −3.30 % | 22 | 76 | 2.83× | 21 % |
| champion | 2025-07 → 2026-08-25 | 4.09 % | −6.71 % | 6 | 7 | 0.59× | 18 % |
| lineage D | same | 4.02 % | −2.26 % | 10 | 35 | 1.71× | 21 % |

Decision differences (664 sessions): **0** sessions with identical standing targets, 441 with no symbol in common, mean L1 weight distance 0.34, D carries +3 pp more exposure (21 % vs 18 %; at the pilot's 18 % ceiling they would be equal). D trades ~3–5× more orders for ~2.9× the turnover on the short window. The full per-rebalance target table is in the evidence file. These books ignore cash dividends (the live bars carry none); both are understated equally. **The live recorder has not run**: HOST has no Alpaca credentials and EC2 deployment is pending, so the numbers above are historical replays of the same code, not live sessions.

## 4. Neither shadow can consume a paper-execution opportunity (tests: `test_shadow_live_isolation_20261004.py`)
* **Structural:** shadow sources contain none of: `cadence_state`, `halt_state`, `peak_equity_state`, `order_ledger`, `KILL_SWITCH`, `pilot_config`, the broker class, `alpaca_adapter` imports, `submit`, POST, `/v2/orders`, `/v2/account`, `/v2/positions`.
* **Request log:** a full live-path run makes only calendar and market-data GETs, zero POSTs; the only files written are `shadow_compare/{champion,lineage_d}/state.json` and `sessions.jsonl`.
* **Interleaving, both orders:** shadow first, then the real pilot runner with the gate open → the pilot still places its entry and pilot state files are byte-identical before/after; pilot first, then shadow → unaffected.
* **Robustness:** first run = session zero (never a backfill); missed sessions are processed in order; a data gap stops the books *before* advancing and escalates; holiday / too early / calendar failure change nothing; reruns are idempotent.

## 5. Timeout recovery with delayed broker visibility (`test_delayed_visibility_*`)
The POST times out **after** the broker accepted it; the order is invisible to `by_client_order_id` lookups for the next four checks (and absent from the order list). Result: run 1 → `uncertain_unresolved`; run 2 → lookups 404, same-id resubmit is refused by the broker as a duplicate (422 → mapped to *uncertain*, not *rejected*) → still unresolved; **the broker holds exactly one order throughout**; the session stays retryable and the rebalance is not spent. Run 3, once visible, the order is adopted with no further POST, the session is marked evaluated and the rebalance consumed. Safety rests on the deterministic `client_order_id = kim-{session}-{symbol}`; a lookup *failure* (5xx/timeout) blocks the send entirely.

## 6. Halt: monitoring frequency and behaviour with no prices
* **Frequency.** Once per valid trading session, at the ~16:45 ET timer run after the 16:15 data cutoff. Drawdown = broker **account equity** vs the persisted maximum of **end-of-session snapshots**. It is **not intraday**: an intraday breach that recovers by 16:45 is never seen, and the peak is the highest daily snapshot, not the intraday high. Pre-cutoff catch-up runs and holidays record nothing. Sessions the job never ran are not backfilled into the peak history.
* **Breach response.** Latched at the snapshot; liquidation orders are `day` market orders submitted that evening and queued for the next open, so worst-case exposure after a breach is one more overnight plus the open gap.
* **Prices unavailable.** *Superseded by the next section (Rick's GO of the same day):* a latched halt now liquidates by share quantity and needs no price.


---

# Addendum 2: quantity-based liquidation under a latched halt (Rick's GO, same day)

Authorizes the paper-account repair only. It does **not** authorize deployment, timer activation or enabling submission; `_SUBMISSION_ENABLED_CONST` is still `False`, and the 8 % halt and Nov-1 expiry remain proposed.

## Behaviour
Once the halt is latched (persisted `halt_state.json`, never cleared by code), each valid session runs `_halt_liquidation` **before and independent of the bar fetch**, so missing or stale prices cannot strand the account:

1. **Hold conditions (nothing is submitted, `escalate: true`, the session is *not* marked evaluated so it retries):**
   * ledger reconciliation could not reach the broker (a lookup returned 5xx/timeout);
   * an earlier **BUY** submission is unresolved: it is **never** resubmitted under a halt;
   * an earlier **SELL** submission is unresolved and cannot be safely recovered: recovery is lookup, then (only on a definitive 404) a resubmit of the **same** `client_order_id`; it is refused if its quantity exceeds what is now held (`oversell_risk`) or if broker state cannot be read;
   * fresh positions or open orders cannot be read.
2. **Sizing (`session_guards.plan_halt_liquidation`, pure):** per universe symbol, `sell = floor(position_qty) − ceil(committed)`, where `committed` is the remaining quantity (`qty − filled_qty`) of every non-terminal sell known to the broker plus every non-terminal sell known only to our ledger (accepted but not yet visible), de-duplicated by `client_order_id`. Never negative, never a buy, never a short; a short, a non-universe holding, a fractional residual, or a pending buy is **reported and escalated, not acted on** (cancelling a pending buy is a different broker action this path does not take).
3. **Submission:** through the unchanged `submit_orders_with_limits`, so the persistent ledger, deterministic duplicate protection (`kim-{session}-{symbol}-liq`), the per-session order cap and the kill switch all apply. The kill switch blocks liquidation too. Sell-only is asserted in code (`build_liquidation_specs` raises on anything else).
4. **After submission:** positions and open orders are re-read and **flat status is confirmed from the broker** (`halt_strategy_universe_flat` / `halt_account_flat` (see Addendum 3; the original single `halt_flat_confirmed` was misleadingly named). `unflat_sessions` is tracked in `halt_state.json`; two consecutive unflat sessions escalate. The latch is preserved through partial fills, expired orders and flat; recovery of equity never un-halts and never causes a buy.
5. **Not live:** gate closed or validate-only plans and logs the sells but sends nothing and writes no ledger (validate-only writes no state file at all).

## Tests (`test_halt_liquidation_20261004.py`, 34 tests; the 9 runner-level behaviour tests fail on the previous commit)
Planner: full quantity, never a buy/short, non-universe, pending sells, partial-fill remainder, oversell prevention (pending ≥ position), ledger-only sells, de-duplication, pending buys, terminal orders, fractional residual, sell-only assertion. Runner: **missing prices** (all eight symbols unavailable → still sells by quantity), **partial fills** (remaining 25 already committed → no new order), **pending sells** from before the halt, expired order replaced next session, **timeouts** (never reached the broker → same-id retry yields exactly one order; accepted-then-invisible for several checks → still exactly one order, no double sell), **restart recovery** (crash after the send, before state is persisted → no second order), unreadable/failed fresh reconciliation, ledger lookup failure, unresolved earlier buy, stale oversize sell, kill switch, order cap with remainder next session, latch + flat confirmation, shadow and validate-only.

## Limits that code cannot remove — read before relying on the halt
* **Queued market orders have no guaranteed execution price and no maximum loss.** A halt breach is detected at the ~16:45 ET snapshot; the liquidation is a `day` market order submitted that evening and queued for the next regular open. It fills at whatever prints (overnight and open gaps, wide spreads in a stressed ETF, partial fills across the session), so realised loss can exceed the trigger threshold by an amount that is **unbounded in principle**. The 8 % figure is a trigger, not a stop-loss and not a loss cap.
* **A queued order can fail to execute** (trading halt in the symbol, rejection, expiry): the position then remains, the unflat state is flagged, and the next session retries with a new order id.
* **Monitoring is once per session on end-of-day snapshots**, not intraday.
* **The per-session cap still applies**: more held symbols than the cap means the remainder waits for the next session (flagged).
* The sell is sized from `/v2/positions` + open orders at ~16:45 ET; fills between that read and the order's execution are the broker's responsibility (an oversize sell is rejected by the broker, not partially honoured by us).


---

# Addendum 3: two activation-blocking defects in 67fe142 (found by an independent review; reproduced and fixed)

Both were reproduced on my own copy first (`oversold: 16 committed against 10 held`; Tuesday created a second 290-share order) and the new regression tests are red on 67fe142. Submission stays disabled; nothing deployed.

| # | Defect in 67fe142 | Fix |
|---|---|---|
| 1 | **Halt recovery could oversell.** Each unresolved SELL was compared with the whole position independently, and confirmed pending sells were ignored. With 10 TLT held, two uncertain 8-share sells were both submitted (16 committed); an uncertain 8 plus an existing pending 8 did the same. | `session_guards.reserve_sell_recoveries`: the worst case is that **every** uncertain sell already exists at the broker, so all of them plus every confirmed pending sell (broker-visible, and ledger-only accepted ones, de-duplicated by id) are reserved against the position **per symbol** before any is recovered. All-or-nothing: if the worst case does not fit, nothing is recovered and the session holds with `oversell_risk`. |
| 2 | **An unresolved earlier submission did not block new orders in the normal path.** Monday's accepted-but-invisible 290-share buy stayed `uncertain_unresolved`; Tuesday computed a fresh order under a new date-based `client_order_id`, bypassing same-id duplicate protection → two broker orders. | Any uncertain ledger row from an **earlier session** now holds the session (`held_unresolved_prior_submission`, escalate, not marked evaluated) before any decision or order. It is never recovered by resubmitting a stale intent. The start-of-run reconciliation still adopts it if the broker has become able to show it. Same-session uncertain rows keep the same-id recovery path. |
| 3 | **"Flat confirmed" only checked the eight symbols;** 10 AAPL shares returned `True`, and non-universe holdings were not an escalation condition. | Two explicitly scoped fields: `halt_strategy_universe_flat` (the eight symbols + live orders in them) and `halt_account_flat` (nothing held or working anywhere). A non-universe holding or live non-universe order escalates; `halt_state.json` stores both. The old `halt_flat_confirmed` name is gone. |
| 4 | Order cap can delay liquidation. | Not a defect; unchanged. **Pilot configuration decision for Rick** (see below). |

**Resolving a hold (operator procedure).** A prior-session unresolved row holds the account until it is resolved. **A `by_client_order_id` lookup returning "not found" is NOT sufficient evidence to abandon an uncertain order**: the delayed-visibility case (Addendum 1, section 5) is exactly an order that exists at the broker but 404s on lookup for a while. Do not abandon on a 404 alone. Independent evidence is required, for example: the broker's order history / orders list for that symbol and time window showing no such order, the account's positions and cash being consistent with it never having executed, and enough elapsed time that visibility lag is not a plausible explanation. Record what you checked in the `reason`. If the order does exist, no action is needed: the next run's reconciliation adopts it. Retire a row only with `PersistentIntentLedger(<path>).abandon("<id>", "<the independent evidence>")`; it refuses anything that is not currently unresolved and requires a reason. The row becomes the terminal status `abandoned_by_operator`. Until then the hold stays in place by design.

**Pilot configuration decision (cap vs liquidation).** `max_orders_per_session = 4` applies to liquidation too, as instructed. With the 8-symbol universe and `max_positions=1` the account normally holds one or two symbols, so 4 is ample; it only delays liquidation if more than four distinct symbols are held, and the remainder then waits for the next session (escalated). Options: keep as is, or exempt `-liq` orders from the cap (lower safety against a runaway loop, faster exit).

**Verification limits.** Tests were run on the local Python 3.13.5 venv (`/home/rick/tbots_integration_checkout_venv`), the same interpreter version EC2's `venv313` was built from, but **not** on EC2's venv313; that would require copying code to the instance, which is a deployment step and has not been authorized. No brokerage calls were made; all broker behaviour is the in-memory `fake_alpaca`.


---

# Addendum 4: bounded liquidation-cap exemption (Rick's GO; implemented for review, NOT accepted for activation)

**Policy implemented.** Normal trading keeps `max_orders_per_session = 4`. A latched-halt liquidation may exit more than four held symbols in one session under its own explicit finite limit, `max_liquidation_orders_per_session` (committed value **8**). This authorizes implementation for review only: it does not accept the liquidation policy, the 8 % halt, or the Nov-1 expiry for activation. Submission is still hardcoded off; nothing is deployed.

**How the limits are bounded**
* **Separate, atomic accounting.** The ledger has an `order_class` (`normal` | `liquidation`). Each class is counted inside the same `BEGIN IMMEDIATE` transaction that inserts the intent, per session date, so concurrent processes cannot exceed either limit (24 racing processes against a limit of 7 → exactly 7). Neither class consumes the other's allowance; the structural worst case per session is 4 normal + 8 liquidation = 12 distinct orders, and a halted session runs only the liquidation path.
* **Finite by construction.** The config key is mandatory and validated as an integer in `[max_orders_per_session, universe size]` (so 4 ≤ limit ≤ 8; booleans, floats, strings, 0, negatives and > 8 are refused). Independently of the cap, liquidation ids are `kim-{session}-{symbol}-liq`: one order per symbol per session, so 8 is also the structural maximum; the cap is a belt-and-braces bound if the id scheme ever changes.
* **No duplicate sells.** The deterministic id plus ledger duplicate protection means a rerun, restart, or second pass in the same session cannot re-sell a symbol; across sessions, the quantity already committed to working sells (broker-visible and ledger-only) is subtracted before sizing.
* **No overselling.** Unchanged and tested at six symbols: `floor(position) − ceil(committed)`, partial fills reduce only their remainder, and the aggregate per-symbol reservation still gates recovery of uncertain sells.
* **All unresolved-order protections preserved.** An earlier unresolved BUY or an unrecoverable unresolved SELL still holds the entire liquidation; a prior-session unresolved order still holds normal trading; the kill switch still blocks every send (tested with six positions); a lookup 404 is still not grounds to abandon an uncertain order.
* **Sells only.** `build_liquidation_specs` still raises on anything but positive-quantity sells; shorts and non-universe holdings are never touched.

**Migration.** Ledgers created before this change gain the `order_class` column with default `normal`; liquidation orders that an earlier build recorded under that default are counted as normal for the session they were created in. Only matters for a ledger that already holds a same-day halt liquidation, which none do (nothing has been enabled).

**Tests** (`test_liquidation_cap_20261004.py`, 28): six symbols exit in one session despite the normal cap of 4; normal trading still capped; finite limit enforced with the remainder flagged and finished next session without re-selling; boundary (limit == held succeeds, one fewer blocks one); class independence; atomic races (spawned processes: 24 racers vs a limit of 7 → exactly 7; normal and liquidation classes raced together → exactly 4 and 8); legacy-ledger migration; no double sell across rerun/restart/second pass; partial fills never oversell; pending sells reduce only their symbols; no buys even with a short present; kill switch; unresolved buy hold; aggregate reservation with many symbols; timeout/delayed-visibility with six symbols (one order per symbol); config boundaries 0/3/9/-1/"8"/4.5/missing refused and 4/5/8 accepted. 17 of these failed before the implementation.

**Test-infrastructure finding while verifying this (not a ledger defect).** The first version of the new race test failed when run after the other tests in its file (11-12 "successes" against a limit of 7) yet passed alone. Cause: the parent pytest process still held an open SQLite connection to the race database when it `fork()`ed the workers. Forking with an open SQLite connection is a documented hazard (the child's `close()` of an inherited descriptor releases the parent's POSIX locks), so mutual exclusion silently breaks. Production never forks (one process per run), but this exposed two real hygiene gaps, both fixed: `PersistentIntentLedger` had no `close()`, and `run_session` never closed the connection it opened (it now closes every ledger it opens, including on exceptions). All race tests (including the earlier `test_concurrent_processes_never_exceed_cap`, which had the same latent fork pattern) now use `spawn`, via the import-light `race_helpers.py`, so no worker can inherit a connection. 15 consecutive repeats of the race tests, and 3 full-suite runs, were all green.
