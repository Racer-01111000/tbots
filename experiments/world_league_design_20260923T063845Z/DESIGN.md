# DEVELOPMENT World League / Adversarial Market Environment Design

Status: DESIGN ONLY. No code modified, no evaluator modified, no world generated, no simulation executed, no genome touched, nothing committed. Written per Rick's GO "DEVELOPMENT WORLD LEAGUE / ADVERSARIAL MARKET ENVIRONMENT DESIGN" plus his "Rick GO — CONDITIONAL ACCEPTANCE" follow-up, which authorized this design phase (not a frozen fitness-v2) and added two required sub-analyses (Requirements 1-2 below).

Grounded in full reads of `experiments/cross_regime_fitness_design_20260923T060532Z/DESIGN.md` and `experiments/fitness_contract_adversarial_review_20260923T062256Z/REVIEW.md`.

---

## Requirement 1 — Exposure-matched-passive gate: mandatory vs. diagnostic-only

REVIEW.md's fix #4 proposed graduating the exposure-matched-passive control from diagnostic to a mandatory binary survival gate (majority-of-folds Sharpe beat). Examined carefully as instructed, against both failure modes:

**(i) Gaming risk.** The comparator's construction rule (equal-weight, buy-once, sized to the genome's *own* exposure cap) is deterministic and fully knowable in advance. Critically, the genome's exposure-related genes (`target_max_exposure`, `max_asset_weight`, `max_positions`) simultaneously determine (a) the genome's own trading behavior and (b) the *size* of the yardstick it will be measured against — a two-way coupling that doesn't exist in the D1 exposure-isolation experiment, where the match was constructed *after the fact* by an experimenter, not chosen adaptively by the genome itself. In principle, mutation pressure could discover exposure-gene values that happen to produce a locally weak comparator in a specific world's price path (e.g., an exposure level where static equal-weight buy-and-hold happens to underperform for reasons unrelated to the genome's own skill) and clear the gate on that basis rather than on genuine signal quality. This risk is real but likely bounded in practice: clearing the gate requires the comparator to be weak in a *majority* of worlds simultaneously (not just one), and worlds in different families/regimes have different price paths, so a single "exposure sweet spot" is unlikely to simultaneously weaken the comparator across a majority by construction alone — but it cannot be ruled out analytically, only bounded operationally (see recommendation below).

**(ii) Over-rejection risk.** This is not hypothetical — it is exactly what happened, empirically, in this project's own QUALIFICATION result: PASSIVE beat D1 in 3 of 4 individual years, driven by broad market direction (a calm, grinding bull stretch), not by any demonstrated signal-quality gap. A hard, mechanical majority-of-folds/worlds Sharpe-beat gate applied to a DEV-fold analog of this exact situation would very plausibly have rejected a genuinely reasonable, risk-managed genome for reasons that are about regime luck, not skill — the project has direct evidence this pattern occurs, not just a theoretical worry.

**The two risks are in real tension, not simultaneously resolvable by tuning one gate parameter.** A margin large enough to avoid over-rejecting regime-unlucky-but-skilled genomes (risk ii) is, by construction, also large enough to let a passive-mimicry genome (REVIEW.md's archetype 8 — structurally indistinguishable from its own matched control) pass undetected, since both cases produce a small/near-zero `signal_delta`. No single-threshold binary gate escapes this trade-off.

**Recommendation: hybrid — neither mandatory binary gate nor silent diagnostic-only.** Always compute and log `signal_delta` per world/family (diagnostic, unconditional, same as REVIEW.md's original stance). But add one concrete consequence, applied only at the **qualification-admission step**, never inside the ranking arithmetic: a genome whose aggregate cross-family `signal_delta` has a non-positive median is flagged `PASSIVE_PARITY_OR_WORSE` and is **excluded from automatic admission by default** — it advances only with Rick's explicit, logged override after reviewing the full per-family pattern. This threads the needle: it forces the passive-mimicry signal to surface (closing archetype 8's gap, addressing risk i's ultimate concern without needing a mechanical threshold to catch the gaming path specifically) while keeping the skill-vs-regime-luck judgment call — which a single number genuinely cannot resolve — in human hands rather than a blind formula (addressing risk ii directly). Wired into the full aggregation pipeline in §11 below.

---

## Requirement 2 — Mathematical validation of the return/risk-scaling term

Term under test: `3.0·median_return(f)·min(1, return_to_drawdown_quality(f))`, where `return_to_drawdown_quality(f) = clip(median_return(f)/(2·|worst_drawdown(f)|+0.001), 0, 1)`.

**(a) Behavior as `dd → 0`.** The `+0.001` (ε) term correctly prevents a 0/0 blowup: as `dd→0` with `r>0`, the ratio grows quickly and clips to 1 (full credit) as soon as `r ≥ 2|dd|+ε` — i.e. any return at least ~2× its drawdown magnitude gets full, undiscounted credit. This is the intended behavior: near-zero drawdown for a real positive return should not be diluted. ε=0.001 is reasonable in scale — small relative to the realistic per-fold drawdown range (~0.02–0.13, per REVIEW.md's own Finding 2) and small relative to realistic returns (~0.02–0.20), so it doesn't materially distort any real case; its sole job is preventing literal division by zero, and it does that. **Caveat**: ε alone does not resolve the separate Sharpe 0/0 indeterminacy for a fully-inactive fold (see (b)).

**(b) Extremely low exposure.** For a genuinely near-inactive fold with tiny nonzero return/drawdown (e.g. r=0.0003, dd=-0.0002), the ratio evaluates cleanly (≈0.214) and produces a correctly tiny absolute contribution (3.0×0.0003×0.214≈0.0002) — no degenerate blowup. For the *exact* zero-trade case (order_count==0, r=0 and dd=0 exactly), the return-scaling term itself degenerates harmlessly to `clip(0/(0+0.001),0,1)=0`, correctly zeroing its own contribution without needing the separate inactivity-cap rule to intervene — **but** the Sharpe term for that same fold requires `stdev(daily_returns)` in its denominator, which is exactly 0 for an all-zero-return fold: an actual 0/0 indeterminate form, not merely a small-number edge case. **Ordering matters and must be fixed**: the inactivity-cap rule (`order_count==0 → per_fold_score=0`) must be checked and short-circuit the ENTIRE per-fold computation *before* the Sharpe term is ever evaluated — not applied as an after-the-fact correction to a NaN. This confirms and sharpens REVIEW.md's own flagged-but-unresolved "halted-fold Sharpe" open question (archetype 9): the fix is a computation-order requirement, not a formula-parameter one.

**(c) Negative return, negative drawdown (sign-algebra check) — a real defect found.** For any `r ≤ 0`, the ratio `r/(2|dd|+ε)` is negative (since the denominator is always strictly positive), so `clip(·,0,1)` floors it to exactly **0**. The full return term then becomes `3.0·r·0 = 0` for *every* losing fold, regardless of loss magnitude or drawdown. Compare to v1, where a losing fold contributed `3.0·r` directly (e.g. r=−0.05 → −0.15, a real penalty). **The proposed scaling factor, applied uniformly regardless of sign, silently erases the entire loss-penalty mechanism** — not by rewarding losses (the requirement's hypothesized "factor near 1"), but by zeroing them out, which is a comparably serious defect: a genome that loses money gets *no* return-term penalty at all under the formula as literally specified. **Required fix**, piecewise:
```
scaling_factor(f) = 1.0                                                          if median_return(f) <= 0
                   = clip(median_return(f)/(2·|worst_drawdown(f)|+0.001), 0, 1)  if median_return(f) > 0
```
This preserves v1's full linear loss-penalty unchanged (factor=1 for r≤0) while still discounting excessive-risk-driven *gains* (the original, correct intent) for r>0 only.

**(d) Can `min(1,·)` ever exceed 1 uncapped?** No — `return_to_drawdown_quality` is already internally bounded by its own `clip(·,0,1)`, so the outer `min(1,·)` can never see an input above 1 in the first place. It is mathematically harmless but redundant. Recommend simplifying to reference `return_to_drawdown_quality(f)` directly (post sign-fix) rather than double-wrapping — not a defect, a clarity note.

**(e) Worked table, six hypothetical (return, drawdown) pairs**, ε=0.001, showing the as-specified formula's actual behavior:

| # | r | dd | ratio r/(2\|dd\|+ε) | factor (as-specified) | term (as-specified) | term (v1, unscaled) | Note |
|---|---|---|---|---|---|---|---|
| 1 | +0.15 | −0.02 | 3.66 | 1 (clip) | 0.450 | 0.450 | Full credit — correct, low risk relative to return. |
| 2 | +0.25 | −0.11 | 1.13 | 1 (clip) | 0.750 | 0.750 | **Undiscounted** — archetype 11's exact exploit values clear the "2×" bar and escape discounting entirely; the coefficient is looser than needed to fully close this exploit (secondary finding, below). |
| 3 | +0.0003 | −0.0002 | 0.214 | 0.214 | 0.0002 | 0.0009 | Tiny either way — behaves fine, no blowup. |
| 4 | +0.08 | −0.05 | 0.792 | 0.792 | 0.190 | 0.240 | Reasonable partial discount for a well-but-not-perfectly risk-adjusted return. |
| 5 | −0.05 | −0.06 | −0.413 | **0 (bug)** | **0.000** | **−0.150** | **Sign-algebra defect** — a real 5% loss gets zero return-term penalty instead of −0.15. |
| 6 | −0.002 | −0.001 | −0.667 | **0 (bug)** | **0.000** | −0.006 | Same defect, smaller magnitude — principle identical. |

**Secondary finding (case #2):** the "2×|dd|" denominator coefficient is loose enough that REVIEW.md's own archetype 11 (return-via-exposure, r=0.25/dd=−0.11, ratio≈1.13) is **not** actually discounted — full credit still applies. This term was intended to help close that exploit; as parameterized it only partially does, and only for return:drawdown ratios below 2:1. This should be treated as a declared, partial, complementary mitigation (working alongside the drawdown term's own natural ceiling, not as a full closure) — or the "2×" coefficient should be tightened if full closure of archetype-11-style behavior is the goal. Flagged as an open parameter for Rick's review, not silently resolved here.

**Conclusion**: the term's *concept* is sound and (a)/(b)/(d) hold up under scrutiny (with the computation-order fix from (b)). (c) is a real, required correction — the piecewise fix above must be applied before this term is implemented anywhere. (e)/case 2 is a secondary, non-blocking open question about coefficient tightness.

---

## 1. Define a "world"

A world is a content-hashed, fully reproducible manifest with these fields:
- `source_data_identity`: the existing dataset revision (`ds_7e16896c873671fe86ac416b24a0ce74502249a8a0fc33603e0f1935e5fab131`) or an explicit reference to a transformation applied to it.
- `calendar_range_or_construction_method`: explicit `[start,end]` dates for historical worlds; a named transformation function + parameter values for synthetic worlds.
- `asset_universe`: the fixed 8-asset set (SPY/EFA/EEM/DBC/VNQ/GLD/IEF/TLT) — worlds never change the universe, only the price path within it.
- `starting_capital`: fixed, matching the existing `STARTING_CASH_CENTS` convention.
- `transaction_cost_assumptions`: the existing frozen 5bps/5bps commission/slippage, unchanged unless the world is explicitly an execution world (§8), which stresses this field within declared bounds only.
- `market_data_fields`: OHLC + adjusted_close + corporate_action, matching the existing dataset schema.
- `corporate_action_treatment`: unchanged (existing adjusted-close-for-indicators / raw-close-for-marks convention), subject to the pending dividend repair (§12/E, deferred).
- `perturbation_definition`: for synthetic worlds, the named function (from §3's approved library) plus its exact parameter values.
- `deterministic_seed`: required for any synthetic world; not applicable to pure historical worlds.
- `simulator_version`: pinned to the frozen evaluator's own version/hash.
- `world_identifier`: sha256 of the canonical serialization of every field above — a world's identity, exactly analogous to how `genome_id` is already content-derived.

**Reproducibility requirement**: given the same world hash and the same frozen simulator version, two runs of the same genome must produce byte-identical results. This generalizes the existing `_verify_generation` determinism check (already required, DESIGN.md §1) to every world in the league, not only the single DEV lane.

---

## 2. Historical core worlds

Three worlds, matching the folds DESIGN.md §3 already established: **World-H1** (≈2007-01-01→2010-12-31), **World-H2** (≈2011-01-01→2014-12-31), **World-H3** (≈2015-01-01→2018-12-31) — reusing the already-accepted content-neutral construction rule (equal calendar spans, boundaries fixed *before* looking at which genomes do well or poorly on them, no hindsight labels applied at the boundary-drawing stage). These form the league's `REAL_DEVELOPMENT` core. **They may never be removed because evolution finds them difficult** — removing a world for that reason is precisely the results-driven curation this whole design exists to prevent (see §7, §9).

---

## 3. Synthetic/perturbed DEVELOPMENT worlds — transformation library

All classifications below are argued from the economic-plausibility principles in §4, never from whether a technique would have helped or hurt D1 or any other observed genome.

| # | Technique | Classification | Reasoning |
|---|---|---|---|
| 1 | Contiguous block resampling | ACCEPTABLE WITH CONSTRAINTS | Preserves local chronology within each block. Constraint: block boundaries drawn from a fixed, pre-declared distribution (fixed block length, fixed seed) — never chosen to include/exclude specific known periods. |
| 2 | Stationary/block bootstrap | ACCEPTABLE WITH CONSTRAINTS | More principled variant of #1 (randomized block length avoids a fixed-length artifact). Constraint: resample the *same* date-index across all 8 assets simultaneously — never resample assets independently, which would destroy real cross-asset correlation. |
| 3 | Chronology-preserving segment recombination | ACCEPTABLE | Reorders whole intact multi-month segments, never reorders *within* a segment — fully preserves local trend/autocorrelation structure, varies only macro sequence. |
| 4 | Volatility scaling | ACCEPTABLE WITH CONSTRAINTS | Bounded multiplicative scaling of daily returns around a local mean (e.g. 0.7×–1.5×). Constraint: bounded range predeclared, preserve each day's direction (no sign inversion), apply via a rolling window (not i.i.d. per day) to preserve volatility clustering. |
| 5 | Correlation strengthening/weakening | ACCEPTABLE WITH CONSTRAINTS | Bounded linear blend with uncorrelated noise, applied to the joint structure only — each asset's own marginal distribution preserved. Constraint: bounded blend factor within an economically plausible correlation range. |
| 6 | Cross-asset dispersion changes | ACCEPTABLE WITH CONSTRAINTS | Same family/bounding logic as #5, targeting cross-sectional spread rather than pairwise correlation specifically. |
| 7 | Return-amplitude scaling | ACCEPTABLE WITH CONSTRAINTS | Same family as #4, applied to multi-day trend amplitude rather than daily volatility; kept as a distinct taxonomy entry since the two can target different timescales. |
| 8 | Transaction-cost stress | ACCEPTABLE | Execution-world category (§8) — bounded multiplier (e.g. up to 3×) on the existing frozen 5bps commission; doesn't touch price data at all, lowest-risk category. |
| 9 | Slippage stress | ACCEPTABLE | Same reasoning as #8, applied to the 5bps slippage convention. |
| 10 | Delayed execution | ACCEPTABLE WITH CONSTRAINTS | Extends T+1 fill to T+2/T+3 within a bounded, predeclared range. Constraint: remains fully causal — fill price still drawn from a real, later session's actual data, never lookahead. |
| 11 | Missing-liquidity/skipped-fill stress | ACCEPTABLE WITH CONSTRAINTS | Bounded, seeded, deterministic skip probability/duration, predeclared and *not* adaptively targeting a specific genome's orders (that would cross into the §4/§9 omniscient-attack boundary). |
| 12 | Controlled gaps | ACCEPTABLE WITH CONSTRAINTS | Inject a bounded overnight gap sourced from the *empirical distribution of real gaps already present in DEVELOPMENT data*, relocated — not an arbitrary invented magnitude. |
| 13 | Trend shortening/lengthening | ACCEPTABLE WITH CONSTRAINTS | Bounded time-warp of a real trend segment's duration, preserving its real shape/direction and monotonic time. |
| 14 | Sideways/choppy transformations | ACCEPTABLE WITH CONSTRAINTS | Bounded dampening of a real segment's net drift toward zero while preserving its real local volatility/autocorrelation texture. |
| 15 | Sudden correlation convergence | ACCEPTABLE WITH CONSTRAINTS | A bounded, single-event version of #5, grounded in the fact real crises (2008, present in real DEVELOPMENT data) exhibit exactly this — magnitude/duration must not exceed what the real historical record itself contains. |
| 16 | Asset-specific shocks | ACCEPTABLE ONLY WITH TIGHT CONSTRAINTS (REJECT as an unconstrained category) | Acceptable only if the shock magnitude/duration is drawn from *that same asset's own real historical distribution of moves*, relocated to a new calendar position — an arbitrary invented shock with no grounding in the asset's real behavior is REJECTED as an ungrounded mathematical attack. |

**Additional blanket rejections** (not individually numbered above, but required by §4): any transformation with no predeclared bound of any kind; and any mechanism that observes a specific genome's trades/orders and alters prices in response to defeat that genome specifically (an omniscient-market attack) — the only sanctioned form of "world responds to strategy behavior" is the population-level, parameter-bounded §9 adversarial search, never per-genome real-time counter-construction.

---

## 4. Economic plausibility boundary

Preserved properties, explicit: causal time direction (no world may use information from after the point being generated); no future leakage beyond what an approved, bounded technique explicitly allows (e.g. controlled gaps); realistic trading calendars (no world introduces a session the real calendar lacks); cross-asset synchronization (all 8 assets share one date-index within a world — any desynchronizing transformation is rejected outright); return distributions/volatility clustering/autocorrelation/cross-asset correlations preserved *within* each technique's declared §3 bounds, never eliminated; gap behavior grounded in real historical gap distributions (§3 item 12); liquidity/execution assumptions bounded per §3 items 8–11; corporate actions unchanged; price positivity enforced as a hard validity check (§14); transaction costs bounded per §3 items 8–9.

**Hard rule**: no mechanism may read a specific genome's trades and then alter world prices specifically to defeat that genome, unless separately defined and justified as the §9 adversarial process — which searches bounded *transformation parameters* for population-level difficulty, never rewrites prices in response to one genome's specific order sequence. This distinction (bounded-parameter population-level search vs. omniscient per-genome counter-construction) is the exact line this section draws and carries through to §9/§10.

---

## 5. Historical sequence integrity

**Sequence-preserving** (real chronological order untouched, only amplitude/cost/execution treatment reshaped): volatility scaling, return-amplitude scaling, transaction-cost stress, slippage stress, delayed execution, missing-liquidity stress, correlation strengthening/weakening, dispersion changes, trend shortening/lengthening, sideways/choppy.

**Sequence-reconstructing** (produces a calendar sequence that never actually happened, even though every underlying data point is real): contiguous block resampling, stationary/block bootstrap, chronology-preserving segment recombination, controlled gaps (relocated), sudden correlation convergence (relocated), asset-specific shocks (relocated).

**Labeling requirement**: every world manifest (§1) carries an explicit `provenance_class` field — `REAL_DEVELOPMENT` (§2 only) or `SYNTHETIC_DEVELOPMENT` (everything else) — propagated into every fitness/generation report. A sequence-reconstructing world must never be described as representing "what happened during [dates]" — only as "a DEVELOPMENT-derived synthetic world, technique [X], grounded in real data from [source ranges]."

---

## 6. World generation timing

| Approach | Reproducibility | Overfitting risk | Compute cost | Leakage risk | Gaming risk | Interpretability |
|---|---|---|---|---|---|---|
| Fixed world bank | Highest — fully deterministic, audit trail complete | Moderate — bank could have unanticipated blind spots | Front-loaded, one-time | Lowest | Lowest | Highest |
| Seeded sampled (drawn during evolution) | High, given seed | Similar to fixed | Spread across run | Low | Low | Moderate — requires re-derivation to audit |
| Expanding bank (predeclared schedule) | High, if schedule truly predeclared and content-neutral | Risk if the schedule itself is ever results-responsive | Incremental | Moderate — must guard the schedule itself | Low, if schedule is frozen | Moderate |
| Adversarial generation | Lowest in the naive case (worlds depend on genome state) | Highest risk of arms-race specialization | Highest | Highest | Highest — needs full §9/§10 machinery | Lowest without careful logging |

**Recommendation — hybrid**: fixed world bank (§2's 3 historical + an initial fixed synthetic set per §16) as the *primary*, always-present fitness denominator (reproducibility/auditability dominate). Plus an expanding bank on a predeclared schedule (e.g. +K synthetic worlds every 3 generations, count and technique-mix fixed *in advance*, never responsive to interim results) for long-campaign diversity without full adversarial complexity. Plus the §9 adversarial generator run as a *separate*, clearly labeled process whose output worlds are adopted into the *next* bank cycle only — never fed live into the same generation that produced the genomes it was tested against. This one-generation delay is the primary structural defense against the arms-race pathology (§10).

---

## 7. Prevent world memorization

Safeguards: (a) larger deterministic banks than any single population×generation combination could plausibly memorize; (b) generation-specific subsets — each genome evaluated on a fixed-size, seeded, deterministic *subset* of the full bank per generation, not necessarily every world every time; (c) predeclared unseen DEVELOPMENT-world subsets — a fixed portion of the synthetic bank held back from the main loop entirely, used only for a final internal-robustness check on the frozen top-N before qualification admission; (d) multiple transformation seeds per technique, so the *specific* realized perturbation varies run to run within a technique's bounds; (e) world-family balancing (§8, §15) so the bank isn't accidentally dominated by one technique family; (f) a cap on how many times, across a full campaign, any single exact world (by hash) is shown to the same lineage's population.

**Required, explicit statement**: a DEVELOPMENT world withheld from some generations is **not** equivalent to a genuine external holdout — it still shares construction rules, source data, and the same underlying 2007–2018 historical path with everything else in the league. Withholding it changes *which specific perturbed view* of 2007–2018 a genome has seen, not *whether* it has been shaped by 2007–2018 as a whole. No future report may describe passing this internal check as "passing a blind test" — only as "passing an internal DEVELOPMENT robustness check."

---

## 8. World families

| Family | v2 status | Notes |
|---|---|---|
| Historical | v2 — core, always present | §2's 3 worlds, never removable. |
| Distributional | v2 | §3 items 4–7, 15; bounded per §4. |
| Execution | v2 | §3 items 8–11; lowest-risk category (no price data touched for 8–9). |
| Sequence | v2, extra scrutiny | §3 items 1–3; requires the tightest cross-asset-synchronization enforcement (§4). |
| Shock | v2, smaller allocation | §3 items 12, 16 (and 15, shareable with distributional); higher scrutiny needs than other families, smaller share of the bank. |
| Adversarial | **Deferred to v2.1+** | Full adaptive search (§9) deferred; v2 includes only a small, fixed, non-adaptive set of manually specified "known hard" combinations from the already-approved library (e.g. one named "volatility-scaling 1.4× during a block-resampled segment" world) rather than standing up live search immediately — smallest scientifically useful set over complexity for its own sake, per the GO's stated preference. |

---

## 9. Adversarial world process (designed, not implemented; deferred to v2.1 per §8)

**Search space**: bounded transformation *parameters* within the already-approved §3/§4 library (e.g. the volatility-scale factor within [0.7,1.5], the block-bootstrap block-length distribution's own parameters, the correlation-blend factor within its bounds) — never raw prices, satisfying the GO's requirement directly.

**Objective, avoiding the degenerate "make everyone lose maximally" solution**: maximize the *dispersion* (e.g. variance) of `per_world_score` across the current genome population in the candidate world, subject to a floor requiring at least some stated fraction of the population (e.g. the top quartile by historical-core fitness) to still clear `per_world_score ≥ 0`. A world where everyone loses maximally has *low* dispersion (everyone converges to the same bad outcome) — the opposite of what this objective rewards. Maximizing dispersion under a floor specifically selects worlds that *separate* robust genomes from brittle ones, the actual scientific goal, never worlds that are simply hard for everyone.

**Bounds**: the adversary searches *within* the fixed §3/§4 box for the life of a campaign; it can never expand the box. Its own search process is itself seeded/deterministic and content-hash-recorded per §1's convention, like any other world generator.

---

## 10. Arms-race protection

Concrete safeguards against trader-evolution ↔ world-generator co-adaptation: (a) frozen transformation laws — the bounded §3/§4 parameter box is fixed for a campaign's life, so "the adversary invents a fundamentally new attack" is structurally impossible mid-campaign; (b) bounded adversarial parameters, stated as a hard numeric constraint, not policy; (c) historical core worlds present in *every* genome's evaluation regardless of what the adversary produces, anchoring fitness to something the world-generator can never touch; (d) non-adversarial worlds mixed in every generation from the fixed/expanding bank, so no genome is evaluated only against adversarially-chosen worlds; (e) independent seeds — the world-generator's RNG stream is seeded separately from the trader population's `EVOLUTION_SEED`, avoiding spurious coupling; (f) diversity as the adversary's own objective (§9) is itself structurally opposed to "everyone loses"; (g) world-generator retirement — a used adversarial world is either promoted into the fixed/expanding bank (§6's delayed-adoption rule) or retired; it never iterates live against the same generation's population it was tested against.

**No unrestricted co-evolution is permitted.** The one-generation-cycle delay from §6 is the primary safeguard, converting a potential live two-way race into a supervised, reviewed, one-directional pipeline (world → bank → next generation's traders).

---

## 11. Fitness integration (incorporates Requirements 1 and 2)

**Level 1 — metric within world**, using the Requirement-2-corrected per-world formula, with the inactivity short-circuit applied *first*:

```
if order_count(w) == 0:
    per_world_score(w) = 0                          # short-circuits before Sharpe is ever computed

else:
    scaling_factor(w) = 1.0                                                          if median_return(w) <= 0
                       = clip(median_return(w)/(2·|worst_drawdown(w)|+0.001), 0, 1)  if median_return(w) > 0

    per_world_score(w) = 3.0·median_return(w)·scaling_factor(w)
                        + 0.20·median_sharpe(w)·min(1, |median_return(w)|/0.005)
                        − 2.0·|worst_drawdown(w)|
                        − 0.25·halt_rate(w)
                        − 0.02·median_turnover(w)
                        − 5.0·median_transaction_cost_rate(w)
```

**Level 2 — score within world family** φ ∈ {historical, distributional, execution, sequence, shock}:

```
family_score(φ) = min_{w∈φ}(per_world_score(w)) + 0.5·mean_{w∈φ}(per_world_score(w))
```

Same worst-plus-half-mean logic DESIGN.md already established for folds, now applied *within* each family first. This is the exact mechanism preventing a numerically larger synthetic family from outvoting the smaller historical family by sheer count — each family always reduces to exactly one scalar, regardless of internal world count.

**Level 3 — score across world families**, with historical structurally weighted higher than any single synthetic family:

```
cross_family_fitness = w_H·family_score(historical) + w_D·family_score(distributional)
                      + w_E·family_score(execution)  + w_S·family_score(sequence)
                      + w_K·family_score(shock)
                      − 0.25·family_dispersion − 0.25·family_concentration

w_H + w_D + w_E + w_S + w_K = 1;  proposed v2 starting point:
w_H = 0.40, w_D = 0.20, w_E = 0.15, w_S = 0.15, w_K = 0.10
```

`w_H = 0.40` guarantees historical worlds a fixed, non-negotiable minimum share of total fitness weight regardless of how many synthetic worlds exist — closing the "numerical overwhelm" loophole by construction, not by accident of counting, directly per the GO's explicit requirement. `family_dispersion`/`family_concentration` reuse the existing stdev/HHI logic, now applied to the (up to 5) active `family_score` values.

**Survival gate**, extending DESIGN.md §5 / REVIEW.md §12's corrected inequality:

- (a) `per_world_score(w) ≥ 0` for **every** historical world w — non-negotiable; the 3 historical worlds are the only genuinely real data in the league and carry the hard catastrophic floor unconditionally.
- (a′) for synthetic worlds, the floor applies at `family_score(φ) ≥ 0` (per-family, not per-individual-synthetic-world) — a single unlucky/extreme synthetic perturbation shouldn't carry the same all-or-nothing veto power as a real historical fold. This is a deliberate, stated asymmetry favoring real data's authority over synthetic data's, implementing the GO's training/stress-vs-genuine-evidence distinction one level further down the hierarchy.
- (b) rank top-N (N=1 per independent search, §18) by `cross_family_fitness` among genomes clearing (a)/(a′).
- (c) **Requirement 1's hybrid**: `signal_delta(w) = genome_sharpe(w) − exposure_matched_passive_sharpe(w)`, aggregated per family via the same min+0.5·mean logic. A genome whose aggregate cross-family `signal_delta` has a non-positive median is flagged `PASSIVE_PARITY_OR_WORSE` and excluded from automatic qualification admission by default — advances only via Rick's explicit override after review. This never enters the `cross_family_fitness` ranking arithmetic — it is strictly a downstream admission-gate check, applied after (a)/(b).

---

## 12. World independence

A hundred transformations of 2007–2018 are not 100 independent historical observations. Every world shares: (i) 100% source-history dependence — no world contains information from outside the real 12-year, 8-asset record; (ii) transformation-family dependence — worlds within one family are correlated through shared transformation *logic*, independent of seed; (iii) seed dependence — different seeds vary *which* draw is realized, not *what kind* of perturbation is applied; (iv) shared-period dependence — any world touching, say, the 2008 sub-period inherits some of its character however transformed.

**Required reporting rule**, extending DESIGN.md §8's four-way classification: any future report must classify evidence as either (a) genuine historical-window evidence — the §2 worlds, or future genuinely-new calendar time — the *only* category countable toward "independent market history" claims, or (b) synthetic/perturbed-world evidence — counts only as "robustness-to-transformation" evidence, never pooled with (a), never described as adding to the effective count of independent market observations regardless of quantity. A report claiming "genome X passed N=47 worlds" must state exactly how many are (a) (almost always 3) vs. (b), and must never imply N=47 independent market samples.

---

## 13. Control strategies across worlds

**NO_TRADE**: computed in every world (deterministically 0/0/0/0 by construction) — remains the hard historical-world floor reference per §11(a), using the corrected `≥0` inequality (REVIEW.md fix #1).
**PASSIVE_ENVELOPE (80%)**: diagnostic-only across all worlds, logged per family — a world where even this dumb, high-exposure control does terribly is informative about that world's severity/plausibility, feeding into §14's validity checks, never into fitness.
**Exposure-matched passive**: per Requirement 1's hybrid — diagnostic plus the `PASSIVE_PARITY_OR_WORSE` qualification-admission flag (§11c), never a ranking or hard survival-gate input, applied identically across historical and synthetic worlds.

**Explicit anti-gaming statement**: synthetic-world transformation parameters must never be tuned specifically to make evolved agents beat controls more easily — the bounded ranges in §3/§4 are fixed by economic-plausibility reasoning alone, never by observing how the evolved-vs-control fitness gap changes as parameters are adjusted. If the §9 adversarial generator's variance-maximizing search incidentally produces worlds where evolved agents beat controls by a wide margin, that is a byproduct of maximizing discriminatory power, never a design objective — any future proposal to make "beats controls" the adversary's own objective should be treated as a violation of this section.

---

## 14. World validity tests

**Structural/mandatory (hard reject on failure)**: no NaN/invalid prices; no negative or zero prices; valid, monotonically increasing timestamps; synchronized asset calendars (all 8 assets share one date-index — any desynchronization rejected); realized output within the *generating technique's own* declared §3 bounds (a generator bug producing an out-of-bounds result is rejected and flagged, never silently clipped); preserved, resolvable source provenance back to real DEVELOPMENT data; no lookahead (automated check that the transformation never reads data timestamped later than the synthetic day being constructed); deterministic reconstruction (rebuild twice from the manifest, require byte-identical output); valid corporate-action handling (no double-counted or dropped actions introduced by the transformation); evaluator schema compatibility (version mismatch is a hard reject, never a silent best-effort attempt).

**Economic sanity (softer, automatic, predeclared)**: annualized aggregate portfolio volatility within [0.3×, 3×] of the real DEVELOPMENT period's own realized volatility — a defense-in-depth check against multiple bounded-but-stacked transformations compounding into something implausible in aggregate; maximum single-day move in any synthetic world must not exceed the worst single-day move actually observed anywhere in the real 2007–2026 dataset — grounds the synthetic generator's absolute worst case in something that has actually happened, directly enforcing §4's boundary at the automated-gate level, not only at design-intent level.

---

## 15. Diversity measurement

Ex-ante descriptors, computed from a world's price data *before* any genome sees it, never from strategy performance: realized volatility (aggregate and per-asset), trend strength, cross-sectional dispersion, cross-asset correlation (rolling), drawdown structure (depth/duration), turnover opportunity (a regime-change-frequency proxy), return autocorrelation, gap frequency/magnitude.

**Method preventing masquerading diversity**: compute this descriptor vector for every candidate world; require a minimum pairwise distance (e.g. normalized Euclidean or correlation-based) between any two worlds admitted to the same bank. A candidate too similar to an already-admitted world (below the distance threshold) is rejected or deduplicated before counting toward the bank's stated size. This prevents "100 nearly-identical synthetic worlds masquerading as diversity" using only pre-performance, ex-ante characterization — never strategy results — per the GO's explicit requirement.

---

## 16. World-bank size

- **Real historical**: exactly 3 (§2) — minimum = preferred; not a tunable knob, this is the full content-neutral partition of the uncontaminated DEVELOPMENT span.
- **Synthetic, minimum viable**: 3–4 worlds per active family × 4 active families (distributional, execution, sequence, shock) = **12–16 total**.
- **Synthetic, preferred first-pass**: ~8–10 per family ≈ **30–40 total**, balanced per §8's allocation and §15's diversity-distance check.
- **Transformation families active in v2**: 4 (adversarial deferred per §8).
- **Seeds per technique**: minimum 2 (per §7's anti-memorization requirement), preferred 3–4.
- **Adversarial worlds**: 0 in v2; when introduced in v2.1, recommend a small fixed allocation (4–6) reviewed manually before any adaptive search is authorized.

**Compute-cost estimate** (order-of-magnitude only, no simulations run): `population(50) × generations(11) × worlds_seen_per_genome_per_generation × per-world simulation cost`. Using §7's generation-specific-subset safeguard — e.g. 3 historical + a rotating ~8-world synthetic subset per genome per generation ≈ 11 world-evaluations/genome/generation × 50 × 11 ≈ **~6,050 world-evaluations for one campaign**, each roughly comparable in cost to one single-episode simulation already run throughout this series (same order of magnitude as the earlier 528-backtest controls-comparison GO, scaled ~12×).

**Recommendation**: start with the minimum viable bank (3 historical + 12–16 synthetic, 2 seeds/technique) to expose meaningful brittleness cheaply, before scaling to the 30–40-world preferred target — per the GO's own "smallest bank likely to expose meaningful brittleness before scaling larger" guidance.

---

## 17. Multiple evolutionary campaigns

World League v2 = one frozen world-generation contract + one frozen fitness contract, hosting N independently-seeded campaigns (A, B, C, D...), each with a distinct `EVOLUTION_SEED`, otherwise identical infrastructure. Repeated discovery across campaigns (e.g. campaigns A and C converging on similar genome characteristics) is evidence about **search stability/reproducibility** — the algorithm reliably finds similar solutions from different starting conditions — **explicitly not independent market evidence**: every campaign still evaluates against the identical world bank, itself still ultimately derived from the same 2007–2018 real record (§12). Any future report must characterize such convergence explicitly as a search-stability finding, never as "N independent pieces of market evidence."

---

## 18. Candidate admission rule

Integrates REVIEW.md §6 directly: exactly one candidate (rank-1 by `cross_family_fitness`, among genomes clearing all survival gates in §11) per independent campaign is admitted to qualification — no within-campaign candidate-shopping, ever. If N campaigns run, N candidates are admitted, and their pass/fail must be read **simultaneously, in one single terminal read** — never a rolling re-look letting a later campaign's failure be quietly dropped after an earlier campaign's candidate already passed. If Rick wants to eventually select one candidate among several campaigns' rank-1's for a single forward-test slot, that selection rule must be **predeclared before any qualification results are read** (e.g. "best DEVELOPMENT-only `cross_family_fitness`, decided in advance") — never "whichever does best at qualification," which would simply reintroduce candidate-shopping one level up, across campaigns instead of within one.

---

## 19. Genuine future evidence boundary

Hard line: everything produced by DEVELOPMENT — the 3 historical worlds, every synthetic world from every family, every eventual adversarial world — remains DEVELOPMENT, permanently, regardless of which specific genome did or didn't sample a specific instance. A world never graduates to QUALIFICATION/CHAMPIONSHIP/FINAL_RESERVE/forward-evidence status merely because a particular genome's run didn't happen to sample it. Only genuinely new calendar time — data that did not exist at DEVELOPMENT-construction time — can occupy those tiers. Per REVIEW.md §8's finding, this project's *entire* existing named historical inventory (QUALIFICATION/CHAMPIONSHIP/FINAL_RESERVE) is already contaminated for this lineage — meaning a World-League-produced candidate has **no existing historical tier left to graduate into**; its only path to genuine out-of-sample evidence is §20's forward-test interface, on data that does not exist as of this design.

---

## 20. Prospective forward-test interface (design only)

Extends REVIEW.md §10's already-designed minimum protocol (candidate identity immutable at freeze; protocol immutable/hash-recorded before any forward day is observed; only post-freeze calendar days qualify; minimum 12-month observation; automatic fail on catastrophic stop; single pre-committed automated read date to prevent quiet early-peeking). This design's addition: exactly what a World-League-produced candidate preserves at freeze time — genome identity (content hash, §1); simulator identity (frozen evaluator version/hash); strategy code revision; **full world-league protocol hash** (bank composition, transformation-library version, aggregation-formula version — so a future auditor can verify the exact league configuration that selected this candidate, not just the candidate itself); selection receipt (which campaign, generation, its `cross_family_fitness`/survival-gate values, its `PASSIVE_PARITY_OR_WORSE` status and Rick's override decision if any); evaluator version; asset universe (confirmed unchanged); data-source contract; forward-test start timestamp (the freeze date, recorded automatically). After freeze, fresh observations accumulate automatically; no evolutionary feedback of any kind flows back. No broker/paper/live activity designed or implied.

---

## 21. Falsification criteria, refined into objective, predeclared tests

- **Search failure**: after a full minimum-viable-bank campaign, zero genomes across all campaigns clear the §11(a)/(a′) survival gates — an infrastructure/search problem, not yet a hypothesis-test result.
- **Robustness failure**: a campaign's rank-1, checked against the §7 predeclared-unseen-DEVELOPMENT-world subset, fails that internal check.
- **Synthetic overfitting**: `family_score` for any synthetic family improves across generations while `family_score(historical)` simultaneously deteriorates or stays flat — directly measurable given §11's family-separated scoring.
- **Exposure gaming**: aggregate `signal_delta` (§11c) is not detectably positive despite the candidate clearing `cross_family_fitness` — winning via the return/drawdown terms' interaction with low exposure, not via real signal margin.
- **Co-evolution failure** (once §9 is introduced, v2.1+): a candidate passing World League fitness performs no better than chance against the §7 predeclared-unseen subset — specialization to specific adversarial worlds, not genuine robustness.
- **Generalization failure**: on the eventual §20 forward test — the *only* remaining genuinely blind evidence per §19 — the candidate fails its own predeclared pass/fail criteria.
- **Simulator/evaluation failure**: the dividend defect was not actually repaired before campaign start (REVIEW.md §11 sequencing requirement); a §14 validity-gate defect let a non-compliant world into the bank undetected; or the halted-world Sharpe edge case (Requirement 2b here) produced unreliable values in practice — any of these mean conclusions are measurement artifacts, not genuine League-design findings.

---

## 22. Deliverable

**A. World League architecture**
```
DEVELOPMENT source data (real 2007-2018, 8-asset universe)
        │
        ├──► Historical worlds (§2, fixed, 3, always present)
        │
        └──► Synthetic world generator (§3, bounded transformation library)
                    │
                    └──► [deferred v2.1] Adversarial generator (§9, searches bounded params only)
                              │
                              ▼
                    World validity gate (§14 — structural + economic sanity checks)
                              │
                              ▼
                    World League bank (§6 hybrid: fixed core + predeclared-schedule expansion)
                              │
                              ▼
                    Evolutionary campaigns (§17, N independent EVOLUTION_SEEDs,
                    §11's 3-level fitness aggregation, §13 diagnostic controls)
                              │
                              ▼
                    Predeclared candidate admission (§18, rank-1 per campaign,
                    simultaneous single-read across campaigns)
                              │
                              ▼
                    Freeze (§20 — genome, protocol, receipt all hash-recorded)
                              │
                              ▼
                    Forward-data evaluation (§20/§19 — the only remaining genuinely
                    blind evidence tier for this lineage; no feedback returns)
```

**B. Approved transformation library** — §3's ACCEPTABLE / ACCEPTABLE WITH CONSTRAINTS rows (items 1–15, 16 conditionally), with the stated bound for each.

**C. Rejected transformations** — §3's rejected case (unconstrained asset-specific shocks with no grounding in real historical behavior) plus the two blanket rejections: any technique with no predeclared bound, and any omniscient per-genome price-counter-construction mechanism.

**D. World-generation contract** — §1 (manifest schema, reproducibility requirement) + §6 (hybrid fixed/expanding/adversarial-delayed timing).

**E. Fitness integration** — §11's complete 3-level math, incorporating Requirement 2's sign-fix and inactivity-ordering fix, and Requirement 1's hybrid admission-flag mechanism.

**F. Anti-gaming protections** — §9 (discriminatory-power objective, prevents degenerate maximal-loss adversary) + §10 (frozen laws, bounded params, historical anchor, independent seeds, delayed adoption — prevents trader/world-generator co-adaptation).

**G. Compute estimate** — §16: ~6,050 world-evaluations for one minimum-viable campaign, order-of-magnitude only, no simulations run.

**H. Minimum viable v2 league** — 3 historical + 12–16 synthetic worlds across 4 families (distributional, execution, sequence, shock), 2 seeds/technique, adversarial generation deferred to v2.1, fixed-bank-plus-predeclared-expansion timing, Requirement-1/2-corrected fitness formula.

**I. Future expansion path** — scale synthetic bank toward the 30–40-world preferred target (§16); introduce §9's adversarial generator under the same one-generation-delay discipline (§6/§10) once the fixed/expanding bank has been run and reviewed; none of this invalidates v2's initial protocol, since the family-weighted aggregation (§11) and the historical-priority weighting (`w_H=0.40`) absorb additional synthetic worlds/families without restructuring.

**J. Falsification contract** — §21's seven refined, objective, predeclared conditions.

**Classification: B — REVISE BEFORE IMPLEMENTATION.**

Not A: Requirement 2 found a real, required correction (the sign-algebra defect that silently zeroes the loss penalty for every losing world/fold) that must be fixed before this fitness formula is implemented anywhere, plus a secondary open question (the "2×" coefficient's looseness against archetype-11-style exploits) that needs Rick's judgment call. Requirement 1's conclusion is itself a genuine revision to REVIEW.md's own fix #4 (hybrid admission-flag, not a binary survival gate) and needs explicit sign-off since it changes the prior review's stated recommendation.

Not C: the overall architecture — world/family/campaign hierarchy, historical-priority-weighted aggregation, one-generation-delayed adversarial adoption, family-separated overfitting detection, the admission and forward-test protocols — is sound, internally consistent, and does not require a fundamentally different approach. The problems found are specific, fixable formula/parameter defects, not structural ones.

**Required before implementation, in order**: (1) apply Requirement 2's piecewise sign-fix to the return-scaling term; (2) apply Requirement 2's inactivity-cap-first computation ordering; (3) Rick's explicit decision on the "2×" coefficient (accept as a declared partial mitigation, or tighten); (4) Rick's explicit sign-off on Requirement 1's hybrid admission-flag mechanism (replacing REVIEW.md's binary-gate proposal); (5) the dividend-accounting repair, sequenced before any campaign per REVIEW.md §11.

---

## Hard boundary compliance statement

No code changes, no market simulation/backtest execution, no synthetic data generation, no world generation, no evaluator changes, no dividend repair, no genome evaluation, no mutation, no reproduction, no evolution, no candidate generation, no protected-window execution, no promotion/demotion, no E/F/G execution, no NODE mutation, no Gen0-bootstrap access or modification — `scripts/s6b_primary_development.py`, `tests/test_s6b_primary_development.py`, `scripts/s6b_resume_executor.py` were not read or touched. Nothing committed or pushed. No broker/live/paper activity. Champion remains frozen throughout; D1 remains an experimental subject, never referred to as a succession candidate. This document designs the developmental laboratory; it does not open it.
