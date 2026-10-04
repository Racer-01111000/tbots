# SYNGYM-20261004-V1: five-year synthetic market gym, first campaign

**Verdict: NO QUALIFYING CANDIDATE.** Five generations ran to completion with no halt. 25 of 32 final-generation genomes met the 8% drawdown eligibility, but **no genome (eligible or not) had a positive primary score**, the best eligible nominee failed the validation gate, and the sealed test stayed sealed. No genome receives any status; Kim, formal admission and the paper pilot are unchanged.

Hypothetical synthetic worlds only: not forecasts of any real year, not real-market evidence, one shared simulator across all splits.

## 1. What ran and what did not

| Ran | Did NOT run |
|---|---|
| 12 training + 6 validation worlds (5 synthetic years each, 400-session warm-up); 5 generations x 32 genomes (115 fresh evaluations); validation of the nominee (once); reference reports, cost/native/stress robustness | the 6 sealed-test worlds (never generated: validation failed); any event-aware lineage; any live/paper/Alpaca action; EC2 deployment of anything to the paper runner; the three economic-source calibrations (sources unverifiable, HTTP 403) |

## 2. Actual compute consumption (EC2 `i-035547b52ca11d1c3`, `venv313` interpreter 3.13.5, dedicated user, no network, 1 vCPU quota)

| Resource | Used | Cap |
|---|---|---|
| wall time | 49.5 s | 7,200 s |
| CPU time | 48.9 s | one vCPU |
| peak resident memory | 124 MiB | 800 MiB (25% of free RAM) |
| output written | 1.84 MiB | 1,024 MiB |

Phases: world generation 4.5 s, evolution 33.0 s (115 genome evaluations x 12 worlds), reference reports 1.1 s, validation 0.6 s, robustness 8.9 s. Measured throughput: 24.4 ms per genome-world (benchmark predicted ~34 s of evolution; actual 33 s). The 2-hour budget was never close to binding; no extra seeds were run.

## 3. Evolution (training worlds only)

| Gen | Eligible / 32 | Best genome | Primary score (LQ excess vs passive18) | Seconds |
|---|---|---|---|---|
| 0 | 3 | `gen_769f0118ccac1e` | -1.31 pp | 8.6 |
| 1 | 6 | `gen_5ae0c363952b9a` | -0.68 pp | 6.3 |
| 2 | 14 | `gen_81bb9383881c5f` | -0.44 pp | 5.6 |
| 3 | 18 | `gen_81bb9383881c5f` | -0.44 pp | 6.0 |
| 4 | 25 | `gen_81bb9383881c5f` | -0.44 pp | 6.5 |

Eligibility rose from 3 to 25 of 32, but the best eligible score improved only from -1.31 pp to -0.44 pp and **plateaued from generation 2** (generations 3 and 4 found nothing better). Population size 32; 13 TREND and 16 D mutants in generation 0; references never mutated.

## 4. Baselines versus evolved (training, 12 worlds, 18% gross overlay, 10 bps slippage + 5 bps commission)

Primary score = 25th percentile of annualized net-return excess over passive18. Eligible = no integrity violation and max drawdown <= 8% in **every** world.

| Genome | Primary score | Eligible | Worst drawdown | Worlds failing the 8% rule | Median net return (5y) | Worst-world net return |
|---|---|---|---|---|---|---|
| nominee | -0.44 pp | yes | -7.9% | 0 | 19.3% | +9.0% |
| champion | -0.58 pp | NO | -12.0% | 5 | 21.6% | +2.0% |
| sam | -1.98 pp | NO | -12.1% | 1 | 19.2% | -1.8% |
| d_primary_rank3 | -1.46 pp | NO | -10.1% | 2 | 19.2% | +0.1% |

passive18 (the benchmark) earned a median 3.72% a year and cash 2.56% in the same worlds.

**Disqualified candidates in the final generation (reported, not hidden):** champion (worst DD -12.0%, score -0.58 pp); g4s7 (worst DD -8.4%, score -0.89 pp); g4s19 (worst DD -9.0%, score -1.00 pp); g4s11 (worst DD -9.3%, score -1.27 pp); d_primary_rank3 (worst DD -10.1%, score -1.46 pp); g4s2 (worst DD -10.4%, score -1.48 pp); sam (worst DD -12.1%, score -1.98 pp).

**Nominee:** `gen_81bb9383881c5f723068a1f4e398403eeb7b9e8d4615ea4667899aab3cac9cb4` (family TREND, slot `g2s13`, a bounded mutation of its parent `gen_5ae0c363952b9a…` created in generation 2).

### Post-hoc uncertainty (NOT part of the frozen objective; descriptive only)

Bootstrap over worlds (5,000 resamples) of the lower-quartile excess:

| Genome | Point estimate | 90% interval | P(estimate > 0) | Worlds beating passive18 |
|---|---|---|---|---|
| nominee | -0.44 pp | [-1.29 pp, +0.18 pp] | 0.22 | 8/12 |
| champion | -0.58 pp | [-2.63 pp, +0.43 pp] | 0.07 | 6/12 |
| sam | -1.98 pp | [-2.44 pp, -0.47 pp] | 0.00 | 4/12 |
| d_primary_rank3 | -1.46 pp | [-1.92 pp, -0.77 pp] | 0.00 | 3/12 |

The nominee is indistinguishable from passive18 on this measure (it cannot be shown to beat it or to lose to it); Sam and D-primary-rank3 are clearly below it. The nominee is also the best of 32 genomes over five generations, so its training score carries selection optimism, and validation did not improve on it.

## 5. Validation (6 worlds, nominee + references, run exactly once)

**Nominee did not pass:** eligible yes, lower-quartile excess -0.39 pp (needs > 0). Per-world excess: +0.26 pp, +0.30 pp, -0.38 pp, +1.02 pp, -0.63 pp, -0.39 pp (positive in 3/6).

| Genome | Primary score (validation) | Eligible | Worst drawdown |
|---|---|---|---|
| champion | -1.76 pp | NO | -12.6% |
| d_primary_rank3 | -1.00 pp | yes | -5.2% |
| nominee | -0.39 pp | yes | -6.2% |
| sam | -0.81 pp | yes | -5.6% |

The sealed test was **not opened**: the sealed worlds were never generated (their hashes do not exist; only seeds are recorded in `world_hashes.json`).

## 6. Secondary metrics (training, median across 12 worlds unless stated)

| Genome | Median DD | Worst DD | Median recovery (sessions) | Daily ES 5% | Sharpe | Sortino | Turnover/yr | Cost % of start | Mean exposure | Time in cash |
|---|---|---|---|---|---|---|---|---|---|---|
| nominee | -3.4% | -7.9% | 308 | -0.45% | 0.25 | 0.36 | 0.90 | 0.67% | 0.175 | 0.018 |
| champion | -7.3% | -12.0% | 330 | -0.66% | 0.32 | 0.47 | 1.10 | 0.82% | 0.176 | 0.021 |
| sam | -5.1% | -12.1% | 335 | -0.47% | 0.23 | 0.31 | 1.23 | 0.91% | 0.173 | 0.025 |
| d_primary_rank3 | -5.7% | -10.1% | 370 | -0.52% | 0.11 | 0.15 | 1.31 | 0.97% | 0.175 | 0.019 |

Sharpe and Sortino use the world's own cash rate as the risk-free rate. Median-world excess vs passive18 was nominee +0.15 pp, champion +0.22 pp, sam -0.60 pp, d_primary_rank3 -0.72 pp: the champion and nominee beat passive18 in the middle of the distribution but lose in its lower quartile.

### By scenario family (mean annualized excess vs passive18, 2 worlds each)

| Genome | energy shipping squeeze | fertilizer harvest stress | normalization whipsaw | recession credit shock | stagflation rates | technology boom reversal |
|---|---|---|---|---|---|---|
| nominee | -0.36 pp | +1.10 pp | -0.63 pp | -0.06 pp | -0.53 pp | +0.72 pp |
| champion | +0.33 pp | +1.67 pp | -0.75 pp | -0.68 pp | -1.33 pp | +0.30 pp |
| sam | -0.34 pp | -0.25 pp | -1.18 pp | -0.34 pp | -2.00 pp | -0.21 pp |
| d_primary_rank3 | +0.23 pp | +0.92 pp | -1.59 pp | -1.54 pp | -1.48 pp | +0.24 pp |

Stagflation, recession/credit and whipsaw worlds were negative for **every** genome; fertilizer/harvest and technology worlds were positive for three of the four (Sam was slightly negative in both); energy/shipping was mixed. With two worlds per family this is suggestive only.

## 7. Sensitivity (training; separate from the primary score)

### Cost cases (slippage 5 / 10 / 25 bps, commission 5 bps)

| Genome | 5 bps | 10 bps (reference) | 25 bps | Eligible at 25 bps |
|---|---|---|---|---|
| nominee | -0.41 pp | -0.44 pp | -0.55 pp | NO |
| champion | -0.53 pp | -0.58 pp | -0.72 pp | NO |
| sam | -1.92 pp | -1.98 pp | -2.18 pp | NO |
| d_primary_rank3 | -1.40 pp | -1.46 pp | -1.65 pp | NO |

**Sensitivity failure:** the nominee's worst drawdown crosses the 8% line at 25 bps slippage (it becomes ineligible); its eligibility is therefore marginal rather than robust. Scores themselves move by only a few tenths of a percentage point.

### Native exposure (references only; NOT comparable with the 18%-overlay primary score)

| Genome | Primary score at native exposure | Eligible |
|---|---|---|
| champion | -0.58 pp | NO |
| d_primary_rank3 | -1.84 pp | NO |
| sam | -2.10 pp | NO |

### Execution stress overlays (modeled: partial fills, rejections, stale feeds, halts, outages, gaps; counts across 12 worlds)

| Overlay | Genome | Primary score | Eligible | Deferred | Partial fills | Rejected | Stale skips | Symbol halts | Outage sessions | Integrity violations |
|---|---|---|---|---|---|---|---|---|---|---|
| moderate_stress | champion | -0.48 pp | NO | 87 | 77 | 18 | 11 | 11 | 6 | 0 |
| moderate_stress | d_primary_rank3 | -1.57 pp | NO | 295 | 247 | 56 | 24 | 48 | 8 | 0 |
| moderate_stress | nominee | -0.71 pp | yes | 208 | 180 | 35 | 23 | 28 | 6 | 0 |
| moderate_stress | sam | -1.90 pp | NO | 306 | 255 | 61 | 23 | 51 | 7 | 0 |
| severe_stress | champion | -1.20 pp | NO | 230 | 195 | 59 | 42 | 37 | 9 | 0 |
| severe_stress | d_primary_rank3 | -1.19 pp | NO | 816 | 690 | 215 | 61 | 132 | 27 | 0 |
| severe_stress | nominee | -0.87 pp | yes | 555 | 463 | 155 | 63 | 93 | 15 | 0 |
| severe_stress | sam | -1.21 pp | NO | 795 | 676 | 213 | 61 | 125 | 27 | 0 |

Zero integrity violations under either overlay (no negative cash, no short, no oversell). The nominee stays eligible under both overlays. Daily OHLC cannot establish auction fills, order-book depth or intraday event order, so these are *modeled*, not observed.

## 8. Engineering proofs completed before the run

* 88 gym tests pass on HOST and on the instance (`venv313`, sandboxed, no network, credential directory inaccessible).
* Accounting equals the accepted evaluator **exactly** (final equity, fills, orders, costs, turnover) for champion, Sam and D-primary-rank3; dividend receivables equal the accepted ex-date credit in equity with no double credit; cash reconciles to an independent ledger rebuilt from the fill log; unaffordable gap buys are reduced and never create negative cash; sells are never oversold.
* Deterministic regeneration: the 18 training/validation worlds hash identically on HOST and EC2 (and across directories).
* Mock-broker receipts (`docs/pilot_semantics_receipts.json`) of the repaired pilot runner: two uncertain 8-share sells vs 10 held are never both sent; an uncertain sell + pending sell is held; Monday's accepted-but-invisible buy blocks Tuesday's new date-keyed order; delayed-visibility timeout leaves exactly one broker order; a six-symbol halt liquidation succeeds above the normal cap with all price bars missing.
* Checkpoint refusal (truncated, missing/forged sidecar, tampered payload, changed hashes, overwrite without resume), crash/resume equivalence, bounded mutation failure, budget halt at a generation boundary.

## 9. Realism gaps (full list: `docs/GENERATOR_VALIDATION.md`)

* Daily-return tails are thinner than the 2007-2018 history (excess kurtosis about half); equity skew is more negative; 86/87 plausibility statistics and 10/11 path-severity checks pass (EEM skew; median SPY 5-year drawdown -0.341 vs a -0.33 bound).
* Every event relationship (oil/diesel, fertilizer/crop, yields, credit, technology) and the positive stock/bond co-movement are **declared assumptions**, not calibrations: the development lane has no inflationary regime. The IEA, FAO and IMF sources could not be retrieved (HTTP 403), so nothing was taken from them and initial states carry no current-macro conditioning.
* Hard guards (daily cap, path-speed guard, price anchor) bound extremes but also truncate rare legitimate events. Worlds skew somewhat harsh.
* Winning here would not have shown a real edge: the simulator, not the market, defines the rules. No actual broker evidence exists in this experiment.

## 10. Separation of evidence types

| Kind | Contents |
|---|---|
| Verified historical calibration | development lane 2007-02-07..2018-12-31 (dataset `ds_7e16896c…`): residual shapes, regime pools, dividend templates, volatility/extreme bounds; moment-matched volatility and drift |
| Synthetic assumptions | event factors, asset betas/lags/variants, family menus, positive stock/bond intervals, spreads, price anchor and guards, news feed contents |
| Simulated performance | every number in sections 3-7 |
| Actual broker evidence | none (no broker, credential or order endpoint was reachable by the simulation) |

## 11. Next smallest useful experiment

Run the **same frozen generator and objective as experiment version 2 on a fresh, larger world bank** (for example 48 training / 24 validation / 24 sealed worlds; the whole campaign costs under a minute). Twelve worlds make the lower-quartile estimator noisy (the nominee's 90% interval is about 1.5 pp wide and straddles zero) and the first campaign cannot tell 'no edge' from 'small edge'. Keep the candidate set unchanged. Only after that is a question about lowering turnover (the active genomes trade about 4.5-6.6x their starting capital in notional over five years, against about 0.65x for passive18) worth a separate version. The event-aware lineage should wait for a reviewed plan.

## 12. Provenance

* Frozen spec/hash commit `46a753b` (2026-10-04 12:15:48 +07), before any run; spec amendment A1 (warm-up 400, TREND schema) preceded any result.
* Code run on EC2 = export of commit `2e1c8ee85677ef8a161f7f7f04dc67bd735f0989` (bundle sha256 `8866f72be326467cdbba63a885fea85f2891f76b77c2214fa3913b859f8a0d5c`); code hashes in `configs/FROZEN_HASHES.json` matched at start (the run refuses otherwise).
* Results archive pulled from `/var/lib/tbots-gym/SYNGYM-20261004-V1` (sha256 `2832a2a10650e117622799791d0bc5c6162faf7aef4dd24ca8ba2b29864da502`); installed code under `/opt/tbots-gym` (root-owned). Unit `tbots-gym-SYNGYM-20261004-V1.service` ran once and exited 0; no timer, restart loop or recurring job was created.
* The paper runner state tree, runner config files, tbots/kim systemd units and timers, and the credential file metadata were fingerprinted before and after and are identical. (My crontab probe was invalid: `crontab` is not installed on the instance and the probe hashed an error message containing a per-call id; no cron spool exists.)
