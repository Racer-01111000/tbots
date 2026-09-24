# Fitness Contract Adversarial Review

Status: DESIGN/ANALYSIS ONLY. No code, evaluator, or genome touched. No simulation executed. Nothing committed. Written against `experiments/cross_regime_fitness_design_20260923T060532Z/DESIGN.md` per Rick's GO "FITNESS CONTRACT ADVERSARIAL REVIEW."

Units grounded by reading `scripts/s5a_evaluator.py` and `evolution/protocol/fitness_formula_s5a_fitness_3525...json` directly, not assumed from DESIGN.md's prose.

---

## 1. Mathematical units and scaling audit

**Confirmed units from source** (`s5a_evaluator.py`):
- `total_return` — fraction (`final_equity_cents/STARTING_CASH_CENTS - 1.0`), e.g. `0.03` = 3%.
- `max_drawdown`/`worst_drawdown` — signed negative fraction; `abs()` applied before weighting.
- `sharpe` — `mean(daily_returns)/stdev(daily_returns) * sqrt(252)`, i.e. **annualized**, dimensionless, naturally ranging roughly −2.5 to +2.5 for real strategies observed this series.
- `consistency_score`, `performance_concentration` — dimensionless, bounded [0, 1].
- `halt_rate` — dimensionless, bounded [0, 1].
- `turnover` — ratio of traded notional to starting capital, **not** a percentage in [0,1] — can exceed 1.0 (values as high as several multiples are structurally possible for a churning strategy).
- `transaction_cost_rate` — fraction of starting capital.

**Finding 1 (corrects DESIGN.md's own claim)**: DESIGN.md §1 states "Return also dominates the formula 15:1 over Sharpe (3.0 vs 0.20)." This compares *coefficients only* and is misleading. Sharpe's natural value range (annualized, order ~1–2) is roughly 10–15× larger than return's natural fractional range (order ~0.02–0.15), which largely **cancels** the coefficient ratio. Worked example, per-fold, hypothetical:

| Metric | Coefficient | Realistic range | Contribution range |
|---|---|---|---|
| median_return | 3.0 | −0.15 to +0.20 | −0.45 to +0.60 |
| median_sharpe | 0.20 | −2.5 to +2.5 | −0.50 to +0.50 |
| \|worst_drawdown\| | −2.0 | 0.02 to 0.14 | −0.28 to −0.04 |
| consistency_score | +0.50 | 0 to 1 | 0 to +0.50 |
| halt_rate | −0.25 | 0 to 1 | −0.25 to 0 |
| median_turnover | −0.02 | 0.1 to 5.0 (up to ~38 for degenerate churn) | −0.10 to −0.002 (up to −0.76 degenerate) |
| median_tx_cost_rate | −5.0 | 0.0001 to 0.02 (up to ~0.05 degenerate) | −0.10 to −0.0005 (up to −0.25 degenerate) |
| performance_concentration | −0.25 | 0 to 1 | −0.25 to 0 |

Return, Sharpe, and consistency_score are all **comparable order of magnitude** contributors under realistic ranges — not a 15:1 dominance. In fact `consistency_score` (weight 0.50, range [0,1]) can be the single largest positive contributor in the formula, larger than a typical return contribution. This is a real correction to the design document under review, not a defense of it.

**Finding 2**: `max_drawdown` has a natural ceiling near −0.12 to −0.13 imposed by the frozen 12% portfolio drawdown halt itself (the simulator liquidates before drawdown can run further) — so the `−2.0·|worst_drawdown|` term is naturally bounded near −0.26 at its worst. **Turnover and transaction-cost terms have no analogous ceiling** — nothing in the frozen risk architecture caps how much notional a genome can churn relative to capital. This makes turnover/cost the only terms with structurally unbounded downside exposure, and (see §3) the least reliably deterring in the moderate-abuse range precisely because their coefficients (−0.02, −5.0) were sized assuming "reasonable" turnover, not audited against the achievable range.

---

## 2. Adversarial synthetic strategies

All figures below are hypothetical/synthetic, constructed to stress-test the formula — none are computed from any real genome's recorded results. Per-fold score formula used (DESIGN.md §4, K=3 folds):

`per_fold_score(f) = 3.0·ret + 0.20·sharpe − 2.0·|dd| − 0.25·halt_rate − 0.02·turnover − 5.0·cost`

| # | Archetype | Fold scores (synthetic) | min | Survives floor (`>0`)? | Note |
|---|---|---|---|---|---|
| 1 | Extremely low exposure / near-inactive | +0.012, −0.001, −0.011 | −0.011 | **No** | Tiny noise-level loss in one fold reads as "catastrophic failure," identical treatment to a genuinely bad strategy. |
| 2 | NO_TRADE | 0, 0, 0 | 0 | **No** (0 is not `>0`) | **Severe finding — see below.** |
| 3 | Steady moderate performer | ~0.07–0.10 | 0.07 | Yes | Behaves as intended. |
| 4 | High-return/high-drawdown | n/a — drawdown capped near −0.13 by the frozen halt itself | — | — | See Finding 2 above; this archetype is naturally self-limiting. |
| 5 | One spectacular fold + two weak | +1.30, −0.087, (not reached) | −0.087 | **No** | Correctly discarded — floor works as designed. |
| 6 | Two strong + one catastrophic | +0.9-ish, +0.7-ish, −1.34 | −1.34 | **No** | Correctly discarded. |
| 7 | High-turnover small-profit | ≈ −0.056 (thin-profit variant); ≈ 0.00 (boundary variant at turnover≈3, ret≈0.015) | ~0 | Borderline | Extreme churn is caught; **moderate** churn (turnover 2–4×) is barely penalized (~−0.06 to −0.09 combined turnover+cost drag) relative to what consistency/Sharpe can add elsewhere. |
| 8 | Low-turnover passive-like | ≈ 0.096 | 0.096 | Yes | **No mechanism flags that this genome is structurally indistinguishable from a dumb allocation** — see §3. |
| 9 | Halt-exploiting (early escape to cash) | +0.75-ish, **−1.11**, +0.70-ish | −1.11 | **No** | Halt_rate penalty stacks with drawdown penalty, so deliberately tripping the halt is not rewarded. One open numerical question flagged, not resolved: Sharpe's behavior over a fold containing a long stretch of exact-zero post-halt daily returns is a genuine edge case that needs an actual computation, not hypothetical arithmetic, before trusting halted-fold Sharpe values in v2. |
| 10 | Sharpe-maximizing, economically trivial return | ≈ **+0.597** (ret 0.08%, sharpe 3.0) | 0.597 | Yes, ranks near top | **Severe exploit — see below.** |
| 11 | Raw-return via exposure | ≈ **+0.591** (ret 25%, sharpe 0.5, dd −11%) | 0.591 | Yes, ranks near top | **Severe exploit, comparable magnitude to #10 — see below.** |
| 12 | Good aggregate, unstable fold-to-fold | +great, **−0.11**, +great | −0.11 | **No** | Correctly discarded — floor works as designed. |

### Severe finding A — the survival floor contradicts itself

DESIGN.md defines the floor as `per_fold_score(f) > catastrophic_threshold`, and separately derives `catastrophic_threshold = 0` from "worse than NO_TRADE's fold score" (NO_TRADE scores exactly 0 in every fold). Combined literally, **NO_TRADE itself fails its own floor** (0 is not `> 0`), and so does DESIGN.md's own "inactivity cap" rule (any fully-inactive fold is capped at exactly 0, which also fails `>0`). This is not a hypothetical edge case — it is baked into the design's own stated definitions and would misfire on the very reference point the rule is built from. **This is the single most important correctness finding in this review.**

### Severe finding B — two independent, comparably-sized exploits survive easily

- **Archetype 10** (Sharpe-triviality): a genome earning an economically meaningless ~0.08% return with very low variance scores **+0.597** per fold — higher than the steady moderate performer (archetype 3, ~0.09). The `0.20·sharpe` term rewards *low variance*, independent of whether the underlying return is real money.
- **Archetype 11** (return-via-exposure): a genome taking outsized risk for outsized raw return, with only mediocre Sharpe, scores **+0.591** — nearly identical magnitude to archetype 10, via a completely different mechanism (the `3.0·return` term isn't scaled by how much risk was taken to earn it, only offset by an independently-additive, naturally-capped drawdown term).

Both archetypes clear the survival floor easily and would rank near the top of a real population, crowding out genomes with genuine, risk-appropriate edge. DESIGN.md's own text flags return-dominance as a *concern* but doesn't quantify it; this review shows it is at minimum matched in severity by an entirely separate Sharpe-side exploit DESIGN.md does not mention at all.

### Moderate finding — moderate overtrading is weakly deterred

Extreme churn (turnover ~38, i.e. RANDOM_SIGNAL-scale) is punished hard (~−0.76 turnover + further cost drag). But moderate overtrading (turnover 2–4×) costs only ~−0.06 to −0.09 combined — small relative to what consistency/Sharpe/return can add elsewhere. The formula deters obvious churn abuse but not moderate overtrading.

### Structural finding — passive-mimicry is invisible to fitness

Archetype 8 (low-turnover, passive-like genome) scores well and has no distinguishing signature in the fitness formula from a genuinely skilled low-turnover genome. Nothing in `cross_regime_fitness` as proposed can tell the two apart, because `signal_delta` (§7 of DESIGN.md) is diagnostic-only.

---

## 3. Exposure gaming

Mathematically confirmed exploitable paths: shrinking exposure to near-zero risks failing the floor on noise alone (archetype 1) rather than being rewarded, so pure "shrink to nothing" is *not* obviously profitable under the current floor — but **archetype 8** shows a genome can shrink toward passive-like behavior and still score *well*, with no mechanism distinguishing "smart and small" from "dumb and small." The zero-trade cap (proposed at exactly `0.0`) is **not sufficient as stated** — it is inconsistent with the survival floor's strict `>0` (see Severe Finding A), and even once that inequality is fixed (§12 below), a capped-at-floor inactive genome and a genuinely-bad genome both simply fail identically; there's no separate signal for "this genome chose not to trade" vs. "this genome tried and failed."

**On adding `signal_delta` to fitness directly** (DESIGN.md leaves this diagnostic-only; the GO asks whether doing so would itself be exploitable): yes, a naive implementation would be. A genome could inflate `signal_delta` by concentrating in a single lucky asset in one fold while its exposure-matched passive comparator stays diversified — the same "one spectacular fold" problem (archetype 5) recurring one level up, at the signal_delta layer, if `signal_delta` is pooled/averaged rather than subjected to the same worst-fold discipline as the rest of the formula. Recommendation (carried into §12): if `signal_delta` becomes a survival gate at all, it must require beating the exposure-matched passive control's Sharpe in a **majority of folds**, not on a single pooled number — otherwise it inherits exactly the loophole this whole redesign exists to close.

---

## 4. Worst-fold rule stress test

Using `min(fold_score) + 0.5·mean(fold_score)`:

- **One fold slightly negative**: fails the floor identically to a catastrophic fold — the floor is a hard binary cutoff, so "slightly negative" and "catastrophically negative" are treated the same at the discard stage (only the continuous `min+0.5·mean` scoring formula would distinguish them, but a genome failing the floor never reaches scoring). This may be intentional strictness, but it should be a stated choice, not an artifact of borrowing `0` from NO_TRADE without considering the boundary.
- **One catastrophically negative fold**: correctly discarded (archetypes 6, 12).
- **One extraordinary fold**: correctly cannot rescue an otherwise-weak population (archetype 5) — behaves as intended.
- **All three mediocre / all three moderate**: both survive and rank reasonably (archetype 3) — no issue.

**Boundary gaming near zero**: real risk. Because the floor is a hard cutoff with no margin, genomes whose true expected fold score clusters near 0 pass or fail almost at random on noise, and evolutionary pressure will push mutations toward "clear +epsilon in my worst fold" rather than toward genuine quality — a classic GA pathology (threshold-clearing convergence, population huddling just above the cutoff rather than optimizing for real quality). Recommend a small positive margin (not zero) once the inequality itself is fixed — see §12.

**Is `per_fold_score(f) > 0` too permissive, too restrictive, or right?** As literally stated: **broken** (Severe Finding A). Once fixed to `>= 0`: still fairly restrictive at the margin (treats "harmless noise" and "genuinely bad" identically), but defensible as a conservative default given this system's stated priorities (no single catastrophic fold should ever be forgivable).

---

## 5. Controls — revised recommendation

In light of §2–4's findings, DESIGN.md's diagnostic-only stance for all three controls is **insufficient** if the goal is to actually prevent gaming, not just observe it after the fact:

- **NO_TRADE**: keep as the survival floor's reference (fix the inequality, §12).
- **80%-exposure PASSIVE_ENVELOPE**: keep diagnostic-only — its fixed absolute exposure level isn't naturally comparable to most genomes, so it shouldn't gate survival directly. No change from DESIGN.md.
- **Per-genome exposure-matched passive control**: **recommend graduating this from diagnostic-only to a binary survival gate** (majority-of-folds, per §3's finding above) — not a fitness-score input (avoiding DESIGN.md's legitimate worry about turning a control's absolute historical numbers into a fitted target), but a pass/fail comparison, which is far less exploitable as a tuning target while directly closing archetype 8's gap. This is a genuine, substantive revision from DESIGN.md's original position, justified by this section's synthetic findings — not by any observed genome's outcome.

---

## 6. Blind-candidate multiplicity

With N genomes admitted to one blind window, the probability that *at least one* looks good purely by chance rises with N — a standard multiple-comparisons risk, sharpened here because (per §8 below) there is currently no more than one truly scarce blind resource available at a time.

Alternatives assessed:
- **Rank-1-only per search**: most conservative — exactly one "shot" per independent search, eliminates within-search candidate-shopping entirely (no "rank-1 failed, but rank-3 passed" after-the-fact promotion).
- **Fixed top-N**: more statistical signal, but reintroduces the exact multiplicity risk this section exists to prevent. Notably, this conversation's own earlier work informally examined D1/D2/D3 as a trio across multiple non-DEV windows without a predeclared "only the single best counts" rule — a mild real instance of exactly this risk, worth naming honestly rather than pretending the system has always been disciplined about it.
- **One per independent search**: generalizes rank-1-only across multiple parallel seeded runs — genuine breadth, one blind "vote" per independent unit of evidence.
- **Family-based**: reasonable as an *additional* diversity constraint layered on top, not a replacement for search-level discipline.

**Recommended protocol**: exactly one candidate (rank-1) per independent evolutionary search (distinct `EVOLUTION_SEED`), admitted to qualification. If multiple searches run in parallel (e.g., 3 seeds × N strategy families), each search contributes exactly one candidate. All admitted candidates' pass/fail must be determined in a single, simultaneous, one-time read — never a rolling re-look that lets a later failure be quietly dropped after an earlier pass was already banked. This must be frozen now, before any next campaign's genomes exist, per the GO's explicit instruction — no existing genome was used to choose this rule.

---

## 7. Independent-search semantics

A different-seed independent search **provides**: confirmation that a result isn't a single-seed artifact of one particular mutation path — robustness of the *search process*, and genuine diversity in which specific grid points get selected.

It **does not provide**: independent evidence about *market regimes*. Every search, regardless of seed count, evaluates against the same DEVELOPMENT 2007–2018 historical path (or the same fold split within it). N independently-seeded searches are collectively N samples of "what works well fit to one 12-year stretch," never N samples of "what works well across N different market histories." "We ran it 5 times with 5 seeds and got similar answers" is evidence of algorithmic stability, not evidence of regime-generalization — the latter can only come from genuinely different historical windows or genuinely new calendar time. Multiple seeds and multiple folds/windows answer different questions and must never be pooled into one "N independent trials" claim.

---

## 8. Historical data budget

| Window | Status |
|---|---|
| DEVELOPMENT (2007–2018) | Usable for future evolutionary DEVELOPMENT — its designated role, was never meant to be blind. |
| QUALIFICATION (2019–2022) | **Contaminated** for D1's lineage and this design process — its numeric Classification-B outcome was observed and directly motivated the cross-regime redesign. |
| CHAMPIONSHIP (2023–2025) | **Contaminated** for the champion (its own original selection window) *and* for D1 (numeric result observed in the championship-holdout GO earlier this conversation). |
| FINAL_RESERVE (2026-01-01 → 2026-08-25) | **Contaminated** for champion *and* D1 (both observed in the controls-interpretation-holdout GO). |
| Other named periods | None found beyond these four (`s5_boundary.py`'s `SEALED_INTERVALS` only names CHAMPIONSHIP/FINAL_RESERVE as hard-sealed; QUALIFICATION is the S5B lane). A ~4-week sliver of calendar time has newly accrued since 2026-08-25 (today is 2026-09-23) but is almost certainly too short to serve as a meaningful episode-shaped test window on its own. |

**Direct answer to the GO's question: No.** Every existing named historical window has had its D1 and/or champion numeric outcome observed by the same people who would be shaping any next campaign's design, fitness v2, or admission protocol. There is currently no historical window in TBOTS that is both long enough to serve as a real test and genuinely untouched by anyone involved in this design process. **This is arguably the single most important finding in this whole review** — it determines what's actually possible next more than any formula fix does. Going forward, genuinely new calendar time (§10) is not a fallback option; it is the only remaining scientifically valid path to a real blind test for this lineage.

---

## 9. Strict contamination doctrine, applied to DESIGN.md's ladder

The stated rule: *numeric result OR pass/fail outcome, once it influences subsequent fitness/architecture/mutation/selection/candidate-choice/design, contaminates that window for those subsequent claims.*

DESIGN.md's ladder claims only a "pass/fail admission decision" and the frozen genome flow backward, and treats that as clean. Tested against this stricter rule: DESIGN.md's own §10 prose explicitly proposes to let a *future* qualification pass/fail *pattern* ("if the majority of a frozen top-10 fails QUALIFICATION under the new fitness... that is strong evidence the redesign itself needs revision") inform whether the fitness formula needs another redesign — a decision that shapes the next campaign, based on qualification's pass/fail outcome. **Under the strict doctrine as stated, this is contamination**, even though it never touches a single raw performance number.

**Honest answer**: the scientifically clean position under this stricter standard is that **nothing — not even a pass/fail bit — may flow backward** from a protected stage into anything shaping a *future* campaign's design, mutation, or candidate selection, if that future campaign will later be tested on the same or an overlapping protected window. The only fully clean use of a protected-stage pass/fail is a one-time, terminal, non-actionable historical record, filed away and never read by anyone designing the next campaign — an extremely demanding operational discipline, arguably harder to enforce in practice than to state. **Recommend striking DESIGN.md's "majority-failure triggers redesign" heuristic** and replacing it with a falsification rule pre-committed *before* campaign 1 even qualifies anything (§13), rather than one that reacts to campaign 1's pass/fail pattern.

---

## 10. Forward evidence — minimum protocol design

Given §8's finding, this is not optional. Design:

- **Candidate identity immutable**: at DEVELOPMENT-freeze time (per §6's admission rule), the genome's content hash is fixed — already how the system works. No parameter changes after this point, ever, for the life of the forward test.
- **Protocol immutable**: cadence, minimum duration, pass/fail thresholds, and termination conditions (below) must be written and hash-recorded *before* any forward calendar day is observed — mirroring how episode manifests are already content-hashed before use.
- **What future data qualifies**: only calendar days strictly after the candidate's freeze timestamp, extending the FINAL_RESERVE precedent (evaluate only once sealed and fully elapsed) indefinitely forward rather than to a fixed cutoff.
- **Minimum observation logic**: DEVELOPMENT folds and QUALIFICATION were each ~4 calendar years; a forward test shorter than ~12 months risks being dominated by a single regime snapshot — exactly the single-fold problem this review exists to avoid. Recommend a minimum of 12 months of genuinely new data before any pass/fail read, same episode conventions (T+1 fill etc.) as elsewhere.
- **Termination**: either (a) the minimum period elapses and a single, one-time pass/fail read is taken, or (b) a predeclared catastrophic-stop (the same 12% drawdown halt) fires early — early termination on catastrophic stop is an automatic FAIL, not "insufficient data, retry."
- **Failure defined**: same structural logic as §5/§12 applied once — fails its own exposure-matched passive control (majority basis) or the NO_TRADE floor, or trips the catastrophic stop.
- **Preventing repeated inspection**: the hardest part, and the one most likely violated informally ("let me just check how it's doing"). Raw market data may be observed anytime (public, not contamination); the *candidate's own* interim performance must not be computed or viewed before the minimum period elapses. A single pre-committed read date, generated automatically rather than chosen by a human, is the only structural way to prevent optional early-stopping/quiet-retry. This is a tooling/process requirement (an automated, date-gated report generator), not something a policy statement alone achieves — flagged as an implementation prerequisite for any future migration plan, not something authorized to build here.

No broker/paper/live activity designed or implied anywhere in this section.

---

## 11. Dividend defect interaction

**Yes, plausibly** — repairing dividend accounting could shift relative rankings enough to require re-validating any *results* produced under v2, for a concrete reason: D's `defensive_rotation` family specifically favors IEF/TLT/GLD, the highest-yielding assets in the 8-asset universe (SABLE's review already quantified ~2–2.5% cumulative understatement over just a 162-day continuous hold). A repair would disproportionately raise measured return (and, since Sharpe scales roughly with mean return at fixed volatility, measured Sharpe too) for dividend-heavy holders relative to equity-heavy families. Since v2's weights here were derived analytically (§12), not fitted to any genome's post-repair numbers, **the formula itself does not need to change after a repair** — but **any specific ranking produced by a campaign run before the repair is not comparable to one run after it**, and should not be treated as informative about what the repaired accounting would produce for the same genomes. This is a validation-of-*results* requirement, not a validation-of-*formula* requirement — worth stating precisely rather than conflating the two.

**Simulator assumptions that must be frozen/documented before campaign start** (compiling DESIGN.md §12 plus this review's own findings):
1. Dividend accounting — must be repaired (DESIGN.md).
2. T+1/rebalance-frequency neutrality — flagged unverified in DESIGN.md, still unverified here.
3. 8-asset universe survivorship bias — flagged unverified in DESIGN.md, still unverified here.
4. **New**: Sharpe-annualization behavior over halt-induced zero-return stretches (§2, archetype 9) — needs an actual numeric check before trusting halted-fold Sharpe in v2.
5. **New**: turnover has no evaluation-time ceiling analogous to the drawdown halt (§1, Finding 2) — worth deciding whether a hard turnover cap belongs in `risk.py` (a future GO's scope, not authorized here), since §2/§3 found the fitness-coefficient-only disincentive is weak at moderate churn.

---

## 12. Fitness v2 recommendation

**B — REVISE BEFORE FREEZE.** The walk-forward-fold + worst-fold-floor architecture from DESIGN.md is structurally sound and should not be discarded (not C); but the specific scoring terms and the survival boundary contain real, identified defects that must not be frozen as-is (not A). Every change below is derived from a stated design principle and the scale analysis in §1 — none from what would make D1, B, C, the champion, or any other already-observed genome pass or fail.

**Changes, smallest-first:**

1. **Fix the floor inequality**: `per_fold_score(f) >= 0` (not `> 0`). Principle: "no worse than doing nothing" naturally means `>=`, not `>`; the strict inequality was an unstated transcription error from DESIGN.md's English description, not a deliberate choice. Resolves Severe Finding A (NO_TRADE self-contradiction) cleanly.

2. **Gate Sharpe's contribution on return materiality**: replace `0.20·median_sharpe(f)` with `0.20·median_sharpe(f)·min(1, |median_return(f)|/0.005)`. Principle: Sharpe should reward risk-adjustment *on top of* an economically real return, not stand as an independent, unconditional bonus. `0.005` (half a percent) is a stated, declared constant — an approximate cost-drag floor below which a return isn't distinguishable from noise — not fit to any genome's result. Directly closes archetype 10 without penalizing archetype 3 or any genuinely good strategy.

3. **Scale return credit by risk taken to earn it**: replace `3.0·median_return(f)` with `3.0·median_return(f)·min(1, return_to_drawdown_quality(f))`, where `return_to_drawdown_quality(f) = clip(median_return(f)/(2·|worst_drawdown(f)|+0.001), 0, 1)`. Principle: return shouldn't be credited independently of the risk spent to earn it, given drawdown's own contribution is naturally ceiling-capped (§1, Finding 2) while return's is not. **This is the most novel, least battle-tested component of this v2 proposal** — flagged explicitly as deserving its own dedicated stress-test pass (more synthetic archetypes) before being trusted, separate from items 1–2 and 4–5 which are more confidently correct.

4. **Add an exposure-matched-passive survival gate** (§3, §5): a genome must beat its own freshly-computed exposure-matched passive control's Sharpe in a **majority of folds** (≥2 of 3) to survive — additive to, not replacing, the NO_TRADE floor.

5. **Turnover**: keep the existing `−0.02·turnover`/`−5.0·cost_rate` terms unchanged at the per-fold level (they do bind correctly at extreme churn) — recommend (not authorized to implement here) a future GO consider a hard evaluation-time turnover cap in `risk.py`, since the fitness-coefficient-only disincentive is weak at moderate churn (§1 Finding 2, §2).

**Complete v2 per-fold formula:**

```
per_fold_score(f) = 3.0·median_return(f)·min(1, return_to_drawdown_quality(f))
                   + 0.20·median_sharpe(f)·min(1, |median_return(f)|/0.005)
                   − 2.0·|worst_drawdown(f)|
                   − 0.25·halt_rate(f)
                   − 0.02·median_turnover(f)
                   − 5.0·median_transaction_cost_rate(f)

return_to_drawdown_quality(f) = clip(median_return(f) / (2·|worst_drawdown(f)| + 0.001), 0, 1)
```

**Cross-fold formula: unchanged from DESIGN.md** —
```
cross_regime_fitness = min_f(per_fold_score(f)) + 0.5·mean_f(per_fold_score(f))
                      − 0.25·fold_dispersion − 0.25·performance_concentration_across_folds
```

**Complete v2 survival rules:**
- (a) `per_fold_score(f) >= 0` in every fold — **[changed from `>` to `>=`]**
- (b) beats its own exposure-matched passive control's Sharpe in ≥2 of 3 folds — **[new]**
- (c) ranks top-N (recommend N=1, rank-1-only per independent search, §6) among genomes clearing (a) and (b)

---

## 13. Predeclare falsification

- **Failure of optimization**: v2's selected genomes, re-evaluated, don't actually show better `return_to_drawdown_quality` or materiality-gated Sharpe than a matched sample of what v1 would have selected — i.e. the reformulation didn't change which genomes rise to the top in a way traceable to the stated principles.
- **Failure of robustness**: a fresh top-N still shows the single-fold-dominance pattern (archetypes 5/6/12) when inspected — the floor+worst-fold logic didn't filter what it was built to filter, likely an implementation defect.
- **Failure against simple controls**: fewer than roughly half of otherwise-fit genomes clear the new exposure-matched-passive gate (item 4) — evidence the population lacks genuine signal beyond exposure level, an important finding about the search itself, not necessarily proof the fitness formula is broken.
- **Failure of generalization**: on the one real forward-test window (§10 — the only remaining valid blind test per §8), a majority of admitted rank-1 candidates fail the criteria that selected them. This replaces DESIGN.md's own §14/G criterion (i), correctly rescoped to forward data only, per §9's stricter finding that QUALIFICATION/CHAMPIONSHIP/FINAL_RESERVE are all burned for this lineage.
- **Simulator/evaluation failure** (a measurement problem, not a fitness-design problem): the dividend defect wasn't actually repaired before the next campaign; the halted-fold Sharpe edge case (§2, archetype 9) produces nonsensical values in practice; or the recommended `risk.py` turnover cap (item 5) was silently never implemented, contaminating conclusions drawn from the run.

---

## Hard boundary compliance statement

No source file modified. No evaluator modified. No dividend repair performed. No genome created, mutated, evaluated, or reproduced. No simulation or backtest executed. E/F/G untouched. No promotion/demotion — champion remains frozen, D1 remains an experimental subject only. NODE not contacted. Gen0-bootstrap dirty files (`scripts/s6b_primary_development.py`, `tests/test_s6b_primary_development.py`, `scripts/s6b_resume_executor.py`) not read or touched. Nothing committed or pushed. No broker/live/paper activity. Every numeric example in §1–4 is hypothetical/synthetic, not computed from any real genome's recorded results.
