# World League Fitness Contract — Final Design Correction

Status: DESIGN ONLY. No code modified, no evaluator modified, no world generated, no simulation executed, no genome touched, nothing committed. Written per Rick's GO "TBOTS — WORLD LEAGUE FINAL DESIGN CORRECTION," accepting Classification B from `experiments/world_league_design_20260923T063845Z/DESIGN.md` and applying the six required corrections below. Grounded in full reads of that document and `experiments/fitness_contract_adversarial_review_20260923T062256Z/REVIEW.md`.

Every numeric example below is synthetic/hypothetical, never computed from D1, B, C, D, the champion, or any other observed genome's recorded results.

---

## Correction 1 — Negative-return scaling + inactivity-first ordering

Both fixes were already specified in the prior design; restated here as the final, binding form.

**Inactivity-first**: `order_count(w) == 0` short-circuits the *entire* per-world computation to `per_world_score(w) = 0` before any other term — including Sharpe — is evaluated. This isn't a style preference: an all-zero-return world has `stdev(daily_returns) = 0`, so Sharpe's denominator is a real 0/0 indeterminate form if reached. Worked example: genome X, world w, `order_count(w)=0`. Under the corrected order, the implementation never calls the Sharpe function for (X, w) at all — it returns `0` immediately from the inactivity check. Under the *wrong* order (compute Sharpe first, discard after), the implementation would need to catch a NaN or divide-by-zero exception downstream — fragile, and exactly the kind of silent-failure risk this ordering requirement eliminates by construction.

**Negative-return scaling fix**: the return-scaling factor is piecewise on the sign of return, not applied uniformly:

```
scaling_factor(w) = 1.0                                                            if median_return(w) <= 0
                   = clip(median_return(w) / (K·|worst_drawdown(w)| + 0.001), 0, 1)  if median_return(w) > 0
```

(K is the coefficient resolved in Correction 3 below.) For `r ≤ 0`, `scaling_factor = 1.0` always — the full, unscaled v1 loss penalty (`3.0·r`) applies with no discount. The discount mechanism (K, ε) only ever *reduces* credit for a **positive** return earned via elevated risk; it never touches the loss side. This is the fix for the defect found in the adversarial review: as originally specified, any `r ≤ 0` produced a negative ratio that clipped to exactly 0, silently erasing the entire loss-penalty term (a 5% loss scored `0` instead of `−0.15`). That defect is closed by the piecewise form above.

---

## Correction 2 — Passive-comparison flag: objective override conditions + anti-sibling-shopping rule

**Background**: `PASSIVE_PARITY_OR_WORSE` fires when a genome's aggregate cross-family `signal_delta` (genome Sharpe minus its own exposure-matched-passive comparator's Sharpe, per §7/§11 of the prior design) has a non-positive median. Flagged genomes are excluded from automatic qualification-admission by default.

**Objective override conditions — exactly two, no others admissible.** A flagged candidate is *eligible for Rick's override consideration* only if at least one of these two conditions holds. If neither holds, the flag is final and the candidate is excluded, full stop — override is not a discretionary escape hatch:

- **OC1 — Isolated synthetic-family anomaly.** The negative median `signal_delta` is driven predominantly by exactly one world-*family*, that family is synthetic (never historical — historical-world signal_delta failures are never overridable), removing that single family from the aggregate would flip the candidate's median `signal_delta` to positive, AND the exposure-matched-passive comparator's own performance in that specific family is independently flagged as anomalous by a §14-style economic-sanity check *computed without reference to the candidate's own performance* (e.g., the comparator scored in the extreme tail relative to its own performance across every other world in the same family) — this avoids circular reasoning, since the anomaly is established from the comparator alone, not from "the candidate looks good so the comparator must have been unlucky."
- **OC2 — Small-margin tolerance with comfortable clearance elsewhere.** The median `signal_delta` is negative but its magnitude is below a predeclared tolerance `ε_signal = 0.05` (Sharpe units) AND every other survival gate (per-world floor, per-family floor, `STRUCTURALLY_INACTIVE` check per Correction 5) is cleared with at least 50% margin above its respective floor.

**Anti-sibling-shopping rule, stated explicitly, not left implicit:** if a campaign's rank-1 nominee is rejected or flagged and neither OC1 nor OC2 applies, **no other genome from that same campaign's population — rank-2, rank-3, or any other individual — may be substituted or promoted into that campaign's nomination slot**, regardless of how favorable that sibling's own benchmark comparison looks. The campaign's slot is simply empty for that admission cycle. This directly extends the already-established rank-1-only-per-independent-search rule: a rejection is a rejection of the search's one nomination, not license to keep drawing from the same population until something clears the bar. Any future proposal to substitute a sibling because its numbers look better is, by this rule, explicitly named as candidate-shopping and a contract violation — not a judgment call left open for reinterpretation.

---

## Correction 3 — The return/drawdown coefficient, resolved via synthetic sweep

**Principle used to derive the value**: the coefficient `K` sets the return:drawdown ratio (approximately a Calmar ratio, `return/|drawdown|`) at which a positive return earns *full, undiscounted* credit. This should correspond to a *genuinely excellent* risk-adjusted outcome by a conventional, external standard — not an easily-cleared bar. Calmar-ratio quality tiers are a standard, non-genome-specific reference point in trading/fund performance evaluation: roughly, Calmar ≥ 1 is "acceptable," Calmar ≥ 2 is "good," Calmar ≥ 3 is widely treated as "excellent." Full credit should require the **excellent** tier, not merely "good" — a genome earning its return by taking on risk at only a "good" (not excellent) Calmar ratio should still see some discount, since the whole point of this term is to distinguish risk-taking from risk-adjusted skill.

`K=2` (the original proposal) sets the full-credit boundary at Calmar ≈ 2 — the "good," not "excellent," tier. This is why archetype 11 (return 25%, drawdown −11%, Calmar ≈ 2.27) cleared undiscounted: it only just exceeds the "good" bar, which was too permissive for what this term is meant to filter.

**Resolution: K = 3** — ties full credit to the conventionally "excellent" Calmar tier (≥3), a value derived from an external, non-genome-specific convention, not from what would pass or fail any observed genome.

**Synthetic sweep, K=2 (original) vs. K=3 (resolved), six (return, drawdown) pairs, ε=0.001:**

| # | r | dd | Calmar (r/\|dd\|) | K=2 factor | K=2 term | K=3 factor | K=3 term | Assessment |
|---|---|---|---|---|---|---|---|---|
| 1 | +0.15 | −0.02 | 7.50 | 1.000 (clip) | 0.450 | 1.000 (clip) | 0.450 | Both full credit — genuinely excellent either way, correct. |
| 2 | +0.25 | −0.11 | 2.27 | 1.000 (clip) | 0.750 | 0.755 | 0.566 | **The flagged exploit** — K=2 gives full credit at a merely "good" Calmar; K=3 applies a real ~25% discount, appropriately short of "excellent." |
| 3 | +0.08 | −0.05 | 1.60 | 0.792 | 0.190 | 0.530 | 0.127 | Moderate risk-adjustment discounted further under K=3 — consistent tightening, not a cliff. |
| 4 | +0.20 | −0.03 | 6.67 | 1.000 (clip) | 0.600 | 1.000 (clip) | 0.600 | Both full credit — comfortably above even the K=3 "excellent" bar. |
| 5 | +0.30 | −0.13 | 2.31 | 1.000 (clip) | 0.900 | 0.767 | 0.690 | High-drawdown, high-return case at the halt's natural ceiling — K=2 rewards this fully; K=3 discounts it meaningfully, correctly treating "large return earned at the maximum tolerated risk" as good-not-excellent. |
| 6 | +0.03 | −0.02 | 1.50 | 0.732 | 0.066 | 0.492 | 0.044 | Small numbers scale proportionally under both — no discontinuity at small magnitudes. |

Every genuinely excellent case (rows 1, 4 — Calmar ≥ 6) is unaffected by the change. Every case at or near the "good but not excellent" boundary (rows 2, 3, 5) is discounted further, closing the exploit without penalizing real skill. **K=3 is the resolved coefficient.**

---

## Correction 4 — Restrict all synthetic-world calibration to DEVELOPMENT-only data

**Verified date ranges** (established earlier this conversation, re-confirmed against `REVIEW.md` §8): DEVELOPMENT = 2007-01-01→2018-12-31. QUALIFICATION = 2019-01-01→2022-12-31 (contaminated). CHAMPIONSHIP = 2023-01-01→2025-12-31 (contaminated). FINAL_RESERVE = 2026-01-01→2026-08-25 (contaminated).

**Confirmed leakage bug, one instance found and fixed:** the prior design's §14 economic-sanity check stated: *"maximum single-day move in any synthetic world must not exceed the worst single-day move actually observed anywhere in the real **2007–2026** dataset."* This spans QUALIFICATION, CHAMPIONSHIP, and FINAL_RESERVE — all three already established as contaminated under the strict contamination doctrine (`REVIEW.md` §9: a protected window's numeric information, even a single derived scalar bound, is contamination once it shapes DEVELOPMENT-side design). Using the worst single-day move from those windows to calibrate a synthetic-world validity bound is exactly this: protected-window information flowing into an active-development artifact.

**Fix, applied verbatim, replacing the flagged clause**: *"maximum single-day move in any synthetic world must not exceed the worst single-day move actually observed anywhere in DEVELOPMENT's own 2007-01-01→2018-12-31 record."*

**Audit of the remainder of the prior document for the same class of leakage** — two further instances found, both using ambiguous language ("real historical record") rather than an explicit non-DEVELOPMENT date range, but both requiring the same explicit tightening to remove any ambiguity:

- §3 item 15 (sudden correlation convergence): originally *"magnitude/duration must not exceed what the real historical record itself contains."* **Fixed**: *"...must not exceed what DEVELOPMENT's own 2007–2018 record contains."*
- §3 item 16 (asset-specific shocks): originally *"drawn from that same asset's own real historical distribution of moves."* **Fixed**: *"drawn from that same asset's own real DEVELOPMENT-period (2007–2018) distribution of moves — never from QUALIFICATION, CHAMPIONSHIP, or FINAL_RESERVE data, even for a single asset in isolation."*

No other quantitative bound, threshold, or calibration reference in the prior document (§1, §4, §6, §7, §8, §16) was found to reference a date range outside 2007–2018; §3 items 1–14 already correctly scope to "DEVELOPMENT data" explicitly. These three are the complete set of fixes required.

**Standing rule going forward**: any future addition to the transformation library or validity-gate thresholds must state its calibration source explicitly as "DEVELOPMENT 2007–2018" in the manifest/spec — a bound with no explicit source citation is malformed and must be rejected at design review, not assumed safe.

---

## Correction 5 — Resolve the inactivity loophole without penalizing legitimate defensive trading

**The actual loophole, precisely stated**: the per-world inactivity rule (`order_count(w)==0 → per_world_score(w)=0`) is mathematically necessary (Correction 1) and already treats a defensively-idle world fairly *at the per-world level* — 0 is better than a loss, worse than a genuine gain, exactly as intended. The real risk is at the **aggregate, population-selection level**: in a weak population or a genuinely hostile world-family (e.g., early generations, or a shock-heavy family), a genome that is inactive in *every* world scores exactly 0 in aggregate (`min=0, mean=0`) — tying NO_TRADE. If most *other* genomes in that population are scoring negative (genuinely struggling), a totally-inactive genome can *rank best by default*, despite demonstrating zero trading behavior of any kind, defensive or otherwise. This is different from — and must not be conflated with — a genome that trades actively and profitably in most worlds but correctly sits out one specific hostile world.

**Discriminating rule, using only information already in the cross-world structure**: define `activity_rate(genome) = (count of worlds w where order_count(w) > 0) / (total worlds evaluated)`, computed per genome across its full evaluated set (historical + synthetic).

```
STRUCTURALLY_INACTIVE(genome) = TRUE  if  order_count(w) == 0 for ALL THREE historical worlds
                                  OR  activity_rate(genome) < 0.50
                              = FALSE otherwise
```

A `STRUCTURALLY_INACTIVE` genome is excluded from automatic qualification-admission by default — same treatment, same override-eligibility structure (OC1/OC2 do **not** apply here; this flag has no override path, since "trades in fewer than half its worlds, including zero real historical ones" cannot be defensibly explained away by a single anomalous comparator the way `PASSIVE_PARITY_OR_WORSE` sometimes can — inactivity is a fact about the genome's own behavior, not a comparator artifact).

**Synthetic classification examples** (12-world bank: 3 historical + 9 synthetic, illustrative):

- **Genome P (legitimate defensive skill)**: active in all 3 historical worlds, active in 8 of 9 synthetic worlds, correctly sits out exactly 1 hostile Shock world. `activity_rate = 11/12 ≈ 0.917 ≥ 0.50`; all 3 historical worlds active. → **NOT flagged.** Its one idle world scores 0 as designed, contributes to `min_f`/`mean_f` normally, no separate penalty or exclusion.
- **Genome Q (the loophole)**: inactive in 10 of 12 worlds (including all 3 historical), trades trivially in 2 low-conviction synthetic worlds. `activity_rate = 2/12 ≈ 0.167 < 0.50`, and fails the all-historical-inactive check too. → **Flagged `STRUCTURALLY_INACTIVE`.** Excluded from automatic admission even if its raw `cross_family_fitness` happens to be the population's highest (e.g., in a generation where every other genome scored negative) — it cannot win by default.

---

## Correction 6 — One deterministic multi-campaign admission procedure, with multiple-testing correction made explicit

**The actual statistical risk**: with N independently-seeded campaigns each nominating one rank-1 candidate (already established, no within-campaign shopping), the naive rule "admit any nominee that individually clears the survival gates" has a false-discovery probability that grows with N — the same family-wise error-rate problem as running N independent hypothesis tests and reporting any single significant result. Because the admission gates here are deterministic simulation-outcome checks rather than checks with a known, calibrated null-hypothesis p-value, a literal numeric Bonferroni correction (`α/N`) cannot be computed without running forbidden simulations to characterize a null distribution — that data doesn't exist and isn't authorized to be generated under this GO. The correction below is therefore **procedural**, chosen specifically because it reduces false-discovery risk without requiring an uncomputed p-value, and is exactly the same logic underlying why science requires replication rather than accepting one positive result.

**Procedure, deterministic and predeclared before any campaign runs:**

1. **N is fixed in advance.** The number of parallel independently-seeded campaigns for a given admission cycle is declared before any of them run. N may **never** be increased after seeing any campaign's results — launching additional campaigns after early ones fail, hoping one eventually passes, is itself a form of campaign-shopping and is explicitly prohibited by this rule, not merely discouraged.
2. **Majority-consistency requirement, not any-one-passes.** No candidate from any campaign in the batch is eligible for qualification-admission unless at least `⌈N/2⌉ + 1` of the N campaigns *independently* produce a rank-1 nominee that clears the full survival-gate stack (per-world floor, per-family floor, `PASSIVE_PARITY_OR_WORSE` clear-or-overridden, `STRUCTURALLY_INACTIVE` clear) on its own. This directly reduces false-discovery risk relative to "any one campaign passing," since achieving independent agreement across a majority of N separately-seeded searches is substantially harder to produce by chance alone than a single success among N draws — the same intuition that makes replicated findings more trustworthy than a single unreplicated one.
3. **Mandatory transparency reporting, regardless of outcome.** Every admission-cycle report must state the exact ratio `X / N` (how many campaigns' nominees cleared the gates), whether or not the majority threshold in step 2 was met. A batch that narrowly clears the majority bar (e.g., 3 of 5) must be reported identically to one that clears it comfortably (e.g., 5 of 5) — the raw ratio is preserved for any future reviewer to judge how surprising the pattern is, rather than being collapsed into a bare pass/fail.
4. **Final selection among qualifying nominees uses a rule fixed before any campaign runs, never "whichever looks best."** If the majority threshold is met, exactly one final candidate is chosen from the qualifying subset by a rule declared in advance of the batch — e.g., "the qualifying nominee from the lowest-numbered `EVOLUTION_SEED`" (a fully arbitrary but pre-fixed tiebreak, deliberately *not* "highest `cross_family_fitness` among qualifiers," since that reintroduces a form of post-hoc best-of-N selection one level up). This document recommends the lowest-seed rule specifically because it is maximally arbitrary with respect to any observed performance number — any rule that references a performance metric to break the tie among qualifiers reopens exactly the risk this whole procedure exists to close.

This procedure integrates directly with Correction 5's `STRUCTURALLY_INACTIVE` flag and Correction 2's `PASSIVE_PARITY_OR_WORSE` flag: both must be clear (or validly overridden under OC1/OC2) as part of "clears the full survival-gate stack" in step 2.

---

## Complete corrected mathematical contract

**Level 1 — per world/fold:**

```
if order_count(w) == 0:
    per_world_score(w) = 0                                    # MUST short-circuit before Sharpe is evaluated

else:
    scaling_factor(w) = 1.0                                                       if median_return(w) <= 0
                       = clip(median_return(w) / (3·|worst_drawdown(w)| + 0.001), 0, 1)  if median_return(w) > 0
                                                                                    # K = 3 (Correction 3)

    per_world_score(w) = 3.0 · median_return(w) · scaling_factor(w)
                        + 0.20 · median_sharpe(w) · min(1, |median_return(w)| / 0.005)
                        − 2.0 · |worst_drawdown(w)|
                        − 0.25 · halt_rate(w)
                        − 0.02 · median_turnover(w)
                        − 5.0 · median_transaction_cost_rate(w)
```

**Level 2 — per world family** φ ∈ {historical, distributional, execution, sequence, shock} (unchanged from prior design):

```
family_score(φ) = min_{w∈φ}(per_world_score(w)) + 0.5 · mean_{w∈φ}(per_world_score(w))
```

**Level 3 — across families** (unchanged weights):

```
cross_family_fitness = 0.40·family_score(historical) + 0.20·family_score(distributional)
                      + 0.15·family_score(execution)  + 0.15·family_score(sequence)
                      + 0.10·family_score(shock)
                      − 0.25·family_dispersion − 0.25·family_concentration
```

**Calibration boundary (Correction 4)**: every quantitative bound feeding §3's transformation library and §14's validity gate — including but not limited to the maximum single-day synthetic move, correlation-convergence magnitude/duration, and asset-specific shock distributions — is derived **exclusively from DEVELOPMENT's own 2007-01-01→2018-12-31 record**. No bound anywhere in this contract may cite QUALIFICATION, CHAMPIONSHIP, FINAL_RESERVE, or any date outside that range.

**Survival and admission gates, complete and final:**

- **(a)** `per_world_score(w) ≥ 0` for every one of the 3 historical worlds — non-negotiable, no override.
- **(a′)** `family_score(φ) ≥ 0` for every synthetic family — non-negotiable, no override.
- **(b)** `signal_delta(w) = genome_sharpe(w) − exposure_matched_passive_sharpe(w)`, aggregated per family via min+0.5·mean; median cross-family value ≤ 0 → `PASSIVE_PARITY_OR_WORSE` flag → excluded from automatic admission unless OC1 or OC2 (Correction 2) holds.
- **(c)** `activity_rate(genome) < 0.50`, or inactive in all 3 historical worlds → `STRUCTURALLY_INACTIVE` flag → excluded from automatic admission, no override path (Correction 5).
- **(d)** among genomes clearing (a)/(a′)/(b)/(c) (directly or via a valid override), rank top-1 per independent campaign (`EVOLUTION_SEED`) — no within-campaign candidate-shopping; a rejected rank-1 may never be replaced by a sibling from the same campaign for looking more favorable (Correction 2's anti-sibling-shopping rule).
- **(e)** across N predeclared, independently-seeded campaigns: admission requires ≥⌈N/2⌉+1 campaigns' nominees to each independently clear (a)–(d); final selection among qualifiers uses a rule fixed before the batch runs (Correction 6) — never "whichever looks best."

---

## Acceptance tests

Falsifiable `GIVEN → THEN` assertions, sufficient to verify a future implementation mechanically against this contract.

**Correction 1 — ordering and sign:**
1. GIVEN a genome with `order_count(w) = 0` in world w, THE per-world formula MUST return `per_world_score(w) = 0` without evaluating `median_sharpe(w)` at all (not evaluate-then-discard).
2. GIVEN `median_return(w) = -0.05`, `worst_drawdown(w) = -0.06`, `order_count(w) > 0`, THE return term MUST equal `3.0 × (-0.05) × 1.0 = -0.15`, not `0`.
3. GIVEN `median_return(w) = -0.002`, `worst_drawdown(w) = -0.001`, THE return term MUST equal `3.0 × (-0.002) × 1.0 = -0.006`, not `0` (confirms the sign-fix at small magnitudes too).

**Correction 2 — override conditions and anti-shopping:**
4. GIVEN a genome flagged `PASSIVE_PARITY_OR_WORSE` where the negative median is driven by 2 of 5 families (not 1), THE genome MUST NOT be OC1-eligible (OC1 requires exactly one driving family).
5. GIVEN a genome flagged `PASSIVE_PARITY_OR_WORSE` with median `signal_delta = -0.03` and every other gate cleared with ≥50% margin, THE genome MUST be OC2-eligible (`|−0.03| < 0.05` and margins satisfied).
6. GIVEN a campaign's rank-1 nominee is rejected and neither OC1 nor OC2 applies, THE admission procedure MUST NOT allow that campaign's rank-2 or rank-3 genome to be substituted into the nomination slot, even if that sibling individually clears every gate.

**Correction 3 — coefficient:**
7. GIVEN `median_return(w) = 0.25`, `worst_drawdown(w) = -0.11`, THE scaling factor MUST equal `clip(0.25/(3×0.11+0.001), 0, 1) = 0.755`, not `1.0` (confirms K=3, not K=2).
8. GIVEN `median_return(w) = 0.20`, `worst_drawdown(w) = -0.03`, THE scaling factor MUST equal `1.0` (Calmar ≈ 6.67, comfortably above the K=3 excellent threshold).

**Correction 4 — calibration boundary:**
9. GIVEN any transformation-library bound or validity-gate threshold, THE contract MUST reject it if its stated calibration source references any date on or after 2019-01-01 (start of QUALIFICATION) — a bound citing "2007–2026" or "the full historical record" without restricting to 2007-2018 fails this test.
10. GIVEN the maximum-single-day-move validity check, THE bound value MUST be computed exclusively from DEVELOPMENT (2007-01-01 to 2018-12-31) price data.

**Correction 5 — inactivity discrimination:**
11. GIVEN a genome active (`order_count > 0`) in all 3 historical worlds and 8 of 9 synthetic worlds (activity_rate ≈ 0.917), THE genome MUST NOT be flagged `STRUCTURALLY_INACTIVE`, regardless of its one idle world's score.
12. GIVEN a genome active in only 2 of 12 total worlds (activity_rate ≈ 0.167), including zero of the 3 historical worlds, THE genome MUST be flagged `STRUCTURALLY_INACTIVE` and excluded from automatic admission — with no override path available (OC1/OC2 do not apply to this flag).
13. GIVEN a genome active in exactly 6 of 12 worlds (activity_rate = 0.50) with all 3 historical worlds active, THE genome MUST NOT be flagged (the threshold is `< 0.50`, so exactly 0.50 clears it).

**Correction 6 — multi-campaign admission:**
14. GIVEN N=5 predeclared campaigns where only 2 of 5 nominees clear all survival gates, THE admission procedure MUST NOT admit either nominee (2 < ⌈5/2⌉+1 = 4).
15. GIVEN N=5 campaigns where 4 of 5 nominees clear all gates, THE admission procedure MUST admit exactly one final candidate, selected by the predeclared fixed tiebreak rule (e.g., lowest `EVOLUTION_SEED` among qualifiers) — never by comparing qualifiers' `cross_family_fitness` values.
16. GIVEN a first batch of N=3 campaigns where 0 of 3 nominees clear the gates, THE next action MUST NOT be "run additional campaigns until one passes" — N was fixed in advance and may not be increased after seeing results.
17. GIVEN any admission-cycle report, THE report MUST state the exact `X/N` ratio explicitly, regardless of whether the majority threshold was met.

**Formula-level regression checks (confirms prior fixes remain intact):**
18. GIVEN `NO_TRADE`'s per-world score in any world (return=0, drawdown=0, order_count=0), THE score MUST equal exactly `0`, and `0 ≥ 0` MUST pass the survival floor (confirms the `≥` fix from the adversarial review is preserved, not reverted to `>`).
19. GIVEN a synthetic family with worlds scoring `[0.05, -0.02, 0.08]`, THE `family_score` MUST equal `min(0.05,-0.02,0.08) + 0.5×mean(0.05,-0.02,0.08) = -0.02 + 0.5×0.0367 = -0.00167`.
20. GIVEN `w_H=0.40, w_D=0.20, w_E=0.15, w_S=0.15, w_K=0.10`, THE weights MUST sum to exactly `1.00` (no silent renormalization elsewhere in the pipeline).

---

## Hard boundary compliance statement

No source-code modification, no evaluator modification, no dividend repair, no genome modification, no mutation, no reproduction, no candidate generation, no fitness implementation (this document is a paper specification only, never wired into actual code), no simulation/backtest execution, no E/F/G execution, no promotion/demotion, no champion replacement, no NODE mutation, no Gen0-bootstrap modification or access — `scripts/s6b_primary_development.py`, `tests/test_s6b_primary_development.py`, `scripts/s6b_resume_executor.py` were not read or touched this turn. Nothing committed, nothing pushed. No broker/live/paper activity. Champion remains frozen throughout. D1 remains an experimental subject only, never referred to as a succession candidate. Every numeric example above is synthetic, constructed to test the formula — none derived from any real genome's recorded results.
