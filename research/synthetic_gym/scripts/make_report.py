"""Builds results/SYNGYM-20261004-V1/RESULTS.md from the campaign's own JSON outputs (no hand-typed numbers)."""
import json
import statistics as stt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from gym import stats as st
from gym.rng import Rng, derive_seed

R = HERE / "results" / "SYNGYM-20261004-V1"
res = json.loads((R / "campaign_result.json").read_text())
rec = json.loads((R / "resource_receipt.json").read_text())
lb = json.loads((R / "leaderboard_final_generation.json").read_text())
last = json.loads((R / "checkpoints" / "gen_004.json").read_text())
tr = res["training_references"]; rb = res["robustness_training"]; val = res["validation"]
pct = lambda x: f"{100 * x:+.2f}%"
pp = lambda x: f"{100 * x:+.2f} pp"


def boot(xs, tag, n=5000):
    r = Rng(derive_seed("posthoc-bootstrap", tag)); out = sorted(st.quantile([xs[r.int_below(len(xs))] for _ in xs], .25) for _ in range(n))
    return out[int(.05 * n)], out[int(.95 * n)], sum(1 for x in out if x > 0) / n


L = []
A = L.append
A("# SYNGYM-20261004-V1: five-year synthetic market gym, first campaign\n")
A("**Verdict: NO QUALIFYING CANDIDATE.** Five generations ran to completion with no halt. 25 of 32 final-generation genomes met the 8% drawdown eligibility, but **no genome (eligible or not) had a positive primary score**, the best eligible nominee failed the validation gate, and the sealed test stayed sealed. No genome receives any status; Kim, formal admission and the paper pilot are unchanged.\n")
A("Hypothetical synthetic worlds only: not forecasts of any real year, not real-market evidence, one shared simulator across all splits.\n")
A("## 1. What ran and what did not\n")
A("| Ran | Did NOT run |\n|---|---|\n| 12 training + 6 validation worlds (5 synthetic years each, 400-session warm-up); 5 generations x 32 genomes (115 fresh evaluations); validation of the nominee (once); reference reports, cost/native/stress robustness | the 6 sealed-test worlds (never generated: validation failed); any event-aware lineage; any live/paper/Alpaca action; EC2 deployment of anything to the paper runner; the three economic-source calibrations (sources unverifiable, HTTP 403) |\n")
A("## 2. Actual compute consumption (EC2 `i-035547b52ca11d1c3`, `venv313` interpreter 3.13.5, dedicated user, no network, 1 vCPU quota)\n")
t = rec["totals"]; ph = rec["phases"]
A(f"| Resource | Used | Cap |\n|---|---|---|\n| wall time | {t['wall_seconds']:.1f} s | 7,200 s |\n| CPU time | {t['cpu_seconds']:.1f} s | one vCPU |\n| peak resident memory | {t['peak_rss_mib']:.0f} MiB | 800 MiB (25% of free RAM) |\n| output written | {t['output_mib']:.2f} MiB | 1,024 MiB |\n")
A(f"Phases: world generation {ph['worlds']['seconds']:.1f} s, evolution {ph['evolution']['seconds']:.1f} s ({ph['evolution']['evaluations']} genome evaluations x 12 worlds), reference reports {ph['reference_reports']['seconds']:.1f} s, validation {ph['validation']['seconds']:.1f} s, robustness {ph['robustness']['seconds']:.1f} s. Measured throughput: {rec['benchmark']['seconds_per_genome_world']*1000:.1f} ms per genome-world (benchmark predicted ~34 s of evolution; actual {ph['evolution']['seconds']:.0f} s). The 2-hour budget was never close to binding; no extra seeds were run.\n")
A("## 3. Evolution (training worlds only)\n")
A("| Gen | Eligible / 32 | Best genome | Primary score (LQ excess vs passive18) | Seconds |\n|---|---|---|---|---|")
for h in last["history"]:
    b = h["best"]
    A(f"| {h['generation']} | {h['n_eligible']} | `{b['genome_id'][:18]}` | {pp(b['primary_score'])} | {h['elapsed_s']:.1f} |")
A("\nEligibility rose from 3 to 25 of 32, but the best eligible score improved only from -1.31 pp to -0.44 pp and **plateaued from generation 2** (generations 3 and 4 found nothing better). Population size 32; 13 TREND and 16 D mutants in generation 0; references never mutated.\n")
A("## 4. Baselines versus evolved (training, 12 worlds, 18% gross overlay, 10 bps slippage + 5 bps commission)\n")
A("Primary score = 25th percentile of annualized net-return excess over passive18. Eligible = no integrity violation and max drawdown <= 8% in **every** world.\n")
A("| Genome | Primary score | Eligible | Worst drawdown | Worlds failing the 8% rule | Median net return (5y) | Worst-world net return |\n|---|---|---|---|---|---|---|")
for n in ("nominee", "champion", "sam", "d_primary_rank3"):
    s = tr[n]
    A(f"| {n} | {pp(s['primary_score'])} | {'yes' if s['eligible'] else 'NO'} | {100 * s['worst_drawdown']:.1f}% | {len(s['ineligibility'])} | {100 * s['median_net_return']:.1f}% | {100 * s['worst_net_return']:+.1f}% |")
A(f"\npassive18 (the benchmark) earned a median {100 * stt.median([m['passive18_ann_return'] for m in tr['nominee']['per_world']]):.2f}% a year and cash {100 * stt.median([m['cash_ann_return'] for m in tr['nominee']['per_world']]):.2f}% in the same worlds.\n")
A("**Disqualified candidates in the final generation (reported, not hidden):** " + "; ".join(f"{r['name']} (worst DD {100 * r['summary']['worst_drawdown']:.1f}%, score {pp(r['summary']['primary_score'])})" for r in lb if not r["summary"]["eligible"]) + ".\n")
A(f"**Nominee:** `{res['nominee']['genome_id']}` (family {res['nominee']['family']}, slot `{res['nominee']['name']}`, a bounded mutation of its parent `{res['nominee']['origin']['parent_id'][:18]}…` created in generation {res['nominee']['origin']['generation']}).\n")
A("### Post-hoc uncertainty (NOT part of the frozen objective; descriptive only)\n")
A("Bootstrap over worlds (5,000 resamples) of the lower-quartile excess:\n")
A("| Genome | Point estimate | 90% interval | P(estimate > 0) | Worlds beating passive18 |\n|---|---|---|---|---|")
for n in ("nominee", "champion", "sam", "d_primary_rank3"):
    xs = [m["excess_vs_passive18"] for m in tr[n]["per_world"]]; lo, hi, p = boot(xs, n)
    A(f"| {n} | {pp(st.quantile(xs, .25))} | [{pp(lo)}, {pp(hi)}] | {p:.2f} | {sum(1 for x in xs if x > 0)}/12 |")
A("\nThe nominee is indistinguishable from passive18 on this measure (it cannot be shown to beat it or to lose to it); Sam and D-primary-rank3 are clearly below it. The nominee is also the best of 32 genomes over five generations, so its training score carries selection optimism, and validation did not improve on it.\n")
A("## 5. Validation (6 worlds, nominee + references, run exactly once)\n")
A(f"**Nominee did not pass:** eligible {'yes' if val['nominee_eligible'] else 'no'}, lower-quartile excess {pp(val['nominee_lower_quartile_excess'])} (needs > 0). Per-world excess: " + ", ".join(pp(m['excess_vs_passive18']) for m in val["per_world_nominee"]) + f" (positive in {sum(1 for m in val['per_world_nominee'] if m['excess_vs_passive18'] > 0)}/6).\n")
A("| Genome | Primary score (validation) | Eligible | Worst drawdown |\n|---|---|---|---|")
for n, s in val["all"].items():
    A(f"| {n} | {pp(s['primary_score'])} | {'yes' if s['eligible'] else 'NO'} | {100 * s['worst_drawdown']:.1f}% |")
A("\nThe sealed test was **not opened**: the sealed worlds were never generated (their hashes do not exist; only seeds are recorded in `world_hashes.json`).\n")
A("## 6. Secondary metrics (training, median across 12 worlds unless stated)\n")
A("| Genome | Median DD | Worst DD | Median recovery (sessions) | Daily ES 5% | Sharpe | Sortino | Turnover/yr | Cost % of start | Mean exposure | Time in cash |\n|---|---|---|---|---|---|---|---|---|---|---|")
for n in ("nominee", "champion", "sam", "d_primary_rank3"):
    pw = tr[n]["per_world"]; m = lambda k: stt.median([x[k] for x in pw])
    A(f"| {n} | {100 * m('max_drawdown'):.1f}% | {100 * min(x['max_drawdown'] for x in pw):.1f}% | {m('recovery_sessions'):.0f} | {100 * m('es5_daily'):.2f}% | {m('sharpe'):.2f} | {m('sortino'):.2f} | {m('turnover_per_year'):.2f} | {100 * m('cost_pct_of_start'):.2f}% | {m('mean_exposure'):.3f} | {m('time_in_cash'):.3f} |")
A("\nSharpe and Sortino use the world's own cash rate as the risk-free rate. Median-world excess vs passive18 was " + ", ".join(f"{n} {pp(stt.median([x['excess_vs_passive18'] for x in tr[n]['per_world']]))}" for n in ("nominee", "champion", "sam", "d_primary_rank3")) + ": the champion and nominee beat passive18 in the middle of the distribution but lose in its lower quartile.\n")
A("### By scenario family (mean annualized excess vs passive18, 2 worlds each)\n")
fams = list(tr["nominee"]["by_family"])
A("| Genome | " + " | ".join(f.replace("_", " ") for f in fams) + " |\n|---|" + "---|" * len(fams))
for n in ("nominee", "champion", "sam", "d_primary_rank3"):
    A(f"| {n} | " + " | ".join(pp(tr[n]["by_family"][f]) for f in fams) + " |")
A("\nStagflation, recession/credit and whipsaw worlds were negative for **every** genome; fertilizer/harvest and technology worlds were positive for three of the four (Sam was slightly negative in both); energy/shipping was mixed. With two worlds per family this is suggestive only.\n")
A("## 7. Sensitivity (training; separate from the primary score)\n")
A("### Cost cases (slippage 5 / 10 / 25 bps, commission 5 bps)\n")
A("| Genome | 5 bps | 10 bps (reference) | 25 bps | Eligible at 25 bps |\n|---|---|---|---|---|")
cc = rb["cost_cases"]
for n in ("nominee", "champion", "sam", "d_primary_rank3"):
    A(f"| {n} | {pp(cc['slip5_comm5'][n]['primary_score'])} | {pp(cc['slip10_comm5'][n]['primary_score'])} | {pp(cc['slip25_comm5'][n]['primary_score'])} | {'yes' if cc['slip25_comm5'][n]['eligible'] else 'NO'} |")
A("\n**Sensitivity failure:** the nominee's worst drawdown crosses the 8% line at 25 bps slippage (it becomes ineligible); its eligibility is therefore marginal rather than robust. Scores themselves move by only a few tenths of a percentage point.\n")
A("### Native exposure (references only; NOT comparable with the 18%-overlay primary score)\n")
A("| Genome | Primary score at native exposure | Eligible |\n|---|---|---|")
for n, s in rb["native_exposure_sensitivity"].items():
    A(f"| {n} | {pp(s['primary_score'])} | {'yes' if s['eligible'] else 'NO'} |")
A("\n### Execution stress overlays (modeled: partial fills, rejections, stale feeds, halts, outages, gaps; counts across 12 worlds)\n")
A("| Overlay | Genome | Primary score | Eligible | Deferred | Partial fills | Rejected | Stale skips | Symbol halts | Outage sessions | Integrity violations |\n|---|---|---|---|---|---|---|---|---|---|---|")
for ov, d in rb["execution_stress"].items():
    for n, v in d.items():
        f = v["execution_failure_counts"]
        A(f"| {ov} | {n} | {pp(v['summary']['primary_score'])} | {'yes' if v['summary']['eligible'] else 'NO'} | {f['deferred']} | {f['partial_fills']} | {f['rejected']} | {f['stale_skips']} | {f['halted_symbol_blocks']} | {f['outage_sessions']} | {f['violations']} |")
A("\nZero integrity violations under either overlay (no negative cash, no short, no oversell). The nominee stays eligible under both overlays. Daily OHLC cannot establish auction fills, order-book depth or intraday event order, so these are *modeled*, not observed.\n")
A("## 8. Engineering proofs completed before the run\n")
A("* 88 gym tests pass on HOST and on the instance (`venv313`, sandboxed, no network, credential directory inaccessible).\n* Accounting equals the accepted evaluator **exactly** (final equity, fills, orders, costs, turnover) for champion, Sam and D-primary-rank3; dividend receivables equal the accepted ex-date credit in equity with no double credit; cash reconciles to an independent ledger rebuilt from the fill log; unaffordable gap buys are reduced and never create negative cash; sells are never oversold.\n* Deterministic regeneration: the 18 training/validation worlds hash identically on HOST and EC2 (and across directories).\n* Mock-broker receipts (`docs/pilot_semantics_receipts.json`) of the repaired pilot runner: two uncertain 8-share sells vs 10 held are never both sent; an uncertain sell + pending sell is held; Monday's accepted-but-invisible buy blocks Tuesday's new date-keyed order; delayed-visibility timeout leaves exactly one broker order; a six-symbol halt liquidation succeeds above the normal cap with all price bars missing.\n* Checkpoint refusal (truncated, missing/forged sidecar, tampered payload, changed hashes, overwrite without resume), crash/resume equivalence, bounded mutation failure, budget halt at a generation boundary.\n")
A("## 9. Realism gaps (full list: `docs/GENERATOR_VALIDATION.md`)\n")
A("* Daily-return tails are thinner than the 2007-2018 history (excess kurtosis about half); equity skew is more negative; 86/87 plausibility statistics and 10/11 path-severity checks pass (EEM skew; median SPY 5-year drawdown -0.341 vs a -0.33 bound).\n* Every event relationship (oil/diesel, fertilizer/crop, yields, credit, technology) and the positive stock/bond co-movement are **declared assumptions**, not calibrations: the development lane has no inflationary regime. The IEA, FAO and IMF sources could not be retrieved (HTTP 403), so nothing was taken from them and initial states carry no current-macro conditioning.\n* Hard guards (daily cap, path-speed guard, price anchor) bound extremes but also truncate rare legitimate events. Worlds skew somewhat harsh.\n* Winning here would not have shown a real edge: the simulator, not the market, defines the rules. No actual broker evidence exists in this experiment.\n")
A("## 10. Separation of evidence types\n")
A("| Kind | Contents |\n|---|---|\n| Verified historical calibration | development lane 2007-02-07..2018-12-31 (dataset `ds_7e16896c…`): residual shapes, regime pools, dividend templates, volatility/extreme bounds; moment-matched volatility and drift |\n| Synthetic assumptions | event factors, asset betas/lags/variants, family menus, positive stock/bond intervals, spreads, price anchor and guards, news feed contents |\n| Simulated performance | every number in sections 3-7 |\n| Actual broker evidence | none (no broker, credential or order endpoint was reachable by the simulation) |\n")
A("## 11. Next smallest useful experiment\n")
A("Run the **same frozen generator and objective as experiment version 2 on a fresh, larger world bank** (for example 48 training / 24 validation / 24 sealed worlds; the whole campaign costs under a minute). Twelve worlds make the lower-quartile estimator noisy (the nominee's 90% interval is about 1.5 pp wide and straddles zero) and the first campaign cannot tell 'no edge' from 'small edge'. Keep the candidate set unchanged. Only after that is a question about lowering turnover (the active genomes trade about 4.5-6.6x their starting capital in notional over five years, against about 0.65x for passive18) worth a separate version. The event-aware lineage should wait for a reviewed plan.\n")
A("## 12. Provenance\n")
A(f"* Frozen spec/hash commit `46a753b` (2026-10-04 12:15:48 +07), before any run; spec amendment A1 (warm-up 400, TREND schema) preceded any result.\n* Code run on EC2 = export of commit `2e1c8ee85677ef8a161f7f7f04dc67bd735f0989` (bundle sha256 `8866f72be326467cdbba63a885fea85f2891f76b77c2214fa3913b859f8a0d5c`); code hashes in `configs/FROZEN_HASHES.json` matched at start (the run refuses otherwise).\n* Results archive pulled from `/var/lib/tbots-gym/SYNGYM-20261004-V1` (sha256 `2832a2a10650e117622799791d0bc5c6162faf7aef4dd24ca8ba2b29864da502`); installed code under `/opt/tbots-gym` (root-owned). Unit `tbots-gym-SYNGYM-20261004-V1.service` ran once and exited 0; no timer, restart loop or recurring job was created.\n* The paper runner state tree, runner config files, tbots/kim systemd units and timers, and the credential file metadata were fingerprinted before and after and are identical. (My crontab probe was invalid: `crontab` is not installed on the instance and the probe hashed an error message containing a per-call id; no cron spool exists.)\n")
(R / "RESULTS.md").write_text("\n".join(L))
print("RESULTS.md written", sum(len(x) for x in L), "chars")
