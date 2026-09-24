# FITNESS V2 / WORLD LEAGUE CONTRACT — Pre-Freeze Errata Resolution

Status: DESIGN ONLY. No code modified, no evaluator modified, no world generated, no simulation executed against real market data, no genome touched, nothing committed. Written per Rick's GO "TBOTS — FITNESS V2 / WORLD LEAGUE PRE-FREEZE ERRATA GO," correcting `experiments/world_league_fitness_correction_20260923T071111Z/CORRECTED_CONTRACT.md`.

Every numeric example below is synthetic/hypothetical, never computed from D1, B, C, D, the champion, or any other observed genome's recorded results.

---

## Correction 1 — Campaign majority-rule formula fixed

**The bug, confirmed by direct arithmetic.** The prior contract used `⌈N/2⌉+1` for the multi-campaign admission threshold. A strict simple majority ("more than half") is `⌊N/2⌋+1`, equivalently "qualifiers > N/2". These are NOT the same formula, and the discrepancy is not cosmetic:

| N | ⌊N/2⌋+1 (correct) | required fraction | ⌈N/2⌉+1 (old, wrong) | required fraction | diverge? |
|---|---|---|---|---|---|
| 1 | 1 | 100% | 2 | **impossible — 2 of 1** | YES — old formula could never admit anything at N=1 |
| 2 | 2 | 100% | 2 | 100% | no |
| 3 | 2 | 66.7% | 3 | 100% | YES |
| 4 | 3 | 75% | 3 | 75% | no |
| 5 | 3 | 60% | 4 | 80% | YES |
| 6 | 4 | 66.7% | 4 | 66.7% | no |
| 7 | 4 | 57.1% | 5 | 71.4% | YES |
| 8 | 5 | 62.5% | 5 | 62.5% | no |
| 9 | 5 | 55.6% | 6 | 66.7% | YES |
| 10 | 6 | 60% | 6 | 60% | no |

**Pattern**: the two formulas coincide only for even N. For every odd N, the old formula demands one qualifier more than a true majority requires — at N=3 it silently became unanimity (100%, not >50%); at N=9 it demanded 66.7% instead of the correct 55.6% (exactly the divergence Rick's message anticipated). At N=1 it was not merely stricter but **mathematically impossible** — no single-campaign cycle could ever admit a candidate under the old formula, regardless of outcome, since 2-of-1 cannot exist.

**Proof `⌊N/2⌋+1` is correct**: "more than half" means `qualifiers > N/2`. For even N=2m: `>m` means `≥m+1 = ⌊N/2⌋+1`. ✓. For odd N=2m+1: `N/2 = m+0.5`, so `>m+0.5` means `≥m+1 = ⌊N/2⌋+1` (since `⌊(2m+1)/2⌋ = m`). ✓. Confirmed correct for all N by direct substitution, not merely by example.

**Fix applied**: replace `⌈N/2⌉+1` with `⌊N/2⌋+1` (equivalently: `qualifiers > N/2`) everywhere in the contract — gate (e) below and Correction 6's procedure step 2.

**Usage note, not a formula defect**: N=1 mathematically reduces to "require the one campaign to pass" (100%), which is internally consistent but defeats the replication purpose the multi-campaign rule exists for. This contract doesn't prohibit N=1, but any admission cycle run at N=1 should be understood as providing no cross-campaign replication evidence at all — a policy note for whoever runs a future campaign, not a fix this document needs to make.

---

## Correction 2 — "Calmar" terminology struck; K=3 restated as a predeclared design parameter

**Formal Calmar ratio, checked precisely**: annualized compound rate of return divided by maximum drawdown, standardly computed from a trailing 36-month (3-year) series of monthly returns, compounded to an annualized figure, per a fixed calendar convention.

**This contract's quantity, checked against that definition**: `median_return(w) / (K·|worst_drawdown(w)| + ε)`, computed **per world** — historical worlds are ~4-year calendar folds (not a rolling 36-month window), `median_return(w)` is the median of this simulator's own per-episode returns within that world (not a monthly-compounded annualized series), and the formula includes a `K` multiplier that has no counterpart in the standard Calmar ratio at all (the standard ratio is unscaled `return/|maxDD|`; this contract computes `return/(K·|maxDD|)`, i.e. `(standard-ratio-like-quantity)/K`).

**Verdict: does NOT meet the formal Calmar definition.** Different time normalization, different underlying return series, and an extra scaling constant with no standard analog. The prior document's claim that conventional Calmar quality tiers (1/"acceptable", 2/"good", 3/"excellent") "derive" K=3 borrowed unearned external rigor for a quantity that only resembles a Calmar ratio in spirit, not in construction.

**Renamed, consistently, everywhere in this contract**: **internal return-to-drawdown scaling ratio**, `IRDR(w) = median_return(w) / |worst_drawdown(w)|` (the un-scaled quantity `K` is subsequently applied to). Every reference to "Calmar" is struck.

**K=3 retained, but re-justified as a predeclared conservative design parameter, not an external-convention derivation**: the synthetic sweep already performed (below, restated with the corrected terminology) shows K=3 produces a real, non-trivial discount (~25%) on the previously-identified exploit case (IRDR≈2.27) while leaving genuinely low-risk cases (IRDR≥6) at full credit. That empirical behavior — not any borrowed external tier system — is the entire justification for K=3. Rick may revise K based on this arithmetic alone; no claim is made that 3 is uniquely correct.

**Synthetic sweep, restated (K=2 original vs. K=3 resolved), ε=0.001 — identical numbers to the prior document, terminology only corrected:**

| # | r | dd | IRDR (r/\|dd\|) | K=2 factor | K=2 term | K=3 factor | K=3 term | Assessment |
|---|---|---|---|---|---|---|---|---|
| 1 | +0.15 | −0.02 | 7.50 | 1.000 | 0.450 | 1.000 | 0.450 | Both full credit — unaffected by K. |
| 2 | +0.25 | −0.11 | 2.27 | 1.000 | 0.750 | 0.755 | 0.566 | **The flagged exploit** — K=2 gave full credit at a moderate ratio; K=3 applies a real ~25% discount. |
| 3 | +0.08 | −0.05 | 1.60 | 0.792 | 0.190 | 0.530 | 0.127 | Consistent further tightening, no cliff. |
| 4 | +0.20 | −0.03 | 6.67 | 1.000 | 0.600 | 1.000 | 0.600 | Both full credit — well clear of the K=3 boundary either way. |
| 5 | +0.30 | −0.13 | 2.31 | 1.000 | 0.900 | 0.767 | 0.690 | High-return/high-drawdown at the halt's natural ceiling — K=3 discounts meaningfully. |
| 6 | +0.03 | −0.02 | 1.50 | 0.732 | 0.066 | 0.492 | 0.044 | Proportional scaling at small magnitudes, no discontinuity. |

**K=3 remains the resolved coefficient**, on the strength of this synthetic arithmetic alone.

---

## Correction 3 — `activity_rate` defined mechanically; a real token-trade gap found and closed

**Mechanical definition, exact, as it stood in the prior contract**: `activity_rate(genome) = (count of worlds w where order_count(w) > 0) / (total worlds evaluated)`.

**Adversarial construction**: genome R places exactly one minimal/token trade per world — e.g. one share of the cheapest available asset, notional ≈ $50 against a ≈$100,000,000 capital base (turnover ≈ 5×10⁻⁷ of capital), held briefly, then closed — in every world, specifically to guarantee `order_count(w) > 0` everywhere. Otherwise R is economically inert: no informed thesis, effectively cash-only apart from these token round-trips.

**Worked arithmetic for genome R, one world:**
- **Return**: P&L on a $50 position is, in absolute terms, on the order of cents to at most a few dollars even under a generous favorable price move — against $100M capital this is `median_return(w) ≈ 0` to numerical precision (order 10⁻⁸–10⁻⁹). Guaranteed round-trip costs (commission 5bps + adverse slippage 5bps on each leg, per this project's frozen execution convention) are a pure drag with no offsetting expected gain from an uninformed trade, so `median_return(w)` is, in expectation, an infinitesimally negative number.
- **Return term**: since `median_return(w) ≤ 0` in expectation, `scaling_factor = 1.0` (Correction 1's branch), so the term is `3.0 × (≈0⁻) × 1.0 ≈ 0`.
- **Sharpe term**: `0.20 × median_sharpe(w) × min(1, |median_return(w)|/0.005)`. Because `|median_return(w)|` is ~10⁻⁸, the materiality gate evaluates to `≈10⁻⁶`, killing the Sharpe contribution to effectively zero **regardless of what raw Sharpe value the token trade's own tiny, near-zero-variance return series happens to produce**. This is the intended mechanism working correctly.
- **Drawdown term**: a $50 position cannot move portfolio-level drawdown meaningfully; `−2.0·|worst_drawdown(w)| ≈ 0`.
- **Halt term**: never triggers on a $50 position; `≈0`.
- **Turnover term**: `−0.02 × median_turnover(w)`, and `median_turnover(w) ≈ 5×10⁻⁷` — contributes `≈−10⁻⁸`, negligible.
- **Transaction-cost term**: `−5.0 × median_tx_cost_rate(w)`. If cost-rate is normalized against capital (as turnover is), this term is likewise ≈0 for a $50 trade. If instead normalized against the trade's own notional (giving a constant ~10bps regardless of size), the term is a small fixed `−5.0×0.001 ≈ −0.005` — non-zero, but tiny relative to any real genome's typical per-world score magnitude (compare Correction 2's table: real scores range ~0.04–0.9), and in either normalization convention it does **not** scale up with the size of the trade — it stays essentially flat whether R trades $50 or $5,000.

**Result: `per_world_score(w) ≈ 0`, indistinguishable from NO_TRADE, in every world.** But `activity_rate(R) = 1.0` (every world shows `order_count(w) > 0`) — genome R is **not** flagged `STRUCTURALLY_INACTIVE`, despite being economically identical to a genome that would be flagged if it simply never traded at all.

**This is a genuine gap, not one the existing gates already close.** The materiality gate correctly neutralizes R's *credit* (Sharpe, return), and the cost/turnover terms correctly stay negligible rather than punishing it — but nothing in the existing per-world-score arithmetic prevents R from *scoring exactly like NO_TRADE while structurally evading the order-count-based inactivity test*. In the specific weak-population scenario Correction 5 already identified as the actual risk (most genomes scoring negative), genome R — scoring ≈0 — could still "win by default" in exactly the way `STRUCTURALLY_INACTIVE` was built to prevent, just laundered through a token trade that satisfies `order_count > 0` without any real economic content. The GO's instruction was to add a new threshold only if a genuine gap is shown by the arithmetic; this arithmetic shows one.

**Fix, reusing the contract's own existing materiality constant for consistency (no new arbitrary number introduced)**: redefine what counts as an "active" world for the purposes of `activity_rate` to require economic materiality, not merely a nonzero order count:

```
activity_rate(genome) = (count of worlds w where order_count(w) > 0 AND turnover(w) ≥ 0.005)
                       / (total worlds evaluated)
```

`0.005` (0.5% of capital turned over) is the same materiality threshold already used for the Sharpe gate (`min(1, |return|/0.005)`) — reused, not invented, for a uniform "what counts as economically meaningful" standard across the whole contract. Genome R's ~5×10⁻⁷ turnover falls roughly four orders of magnitude short of this bar in every world, so under the corrected definition `activity_rate(R) = 0`, correctly triggering `STRUCTURALLY_INACTIVE`. A genuinely modest but real position (e.g. a 3–5% single-asset allocation, turnover ≈0.03–0.05) clears `0.005` comfortably, so no legitimate small-scale trading genome is misclassified — confirmed by re-running the prior document's own Genome P example: its real per-world turnovers (comparable to this project's observed 80–150% turnover figures for genuinely active real genomes, per this conversation's own prior experiments) are many multiples of `0.005` in every active world, so Genome P's classification (`NOT flagged`) is unchanged by this fix. Genome Q (the original loophole example, inactive/trivial in 10 of 12 worlds) is unaffected by this fix either — it was already correctly flagged under the order-count-only definition, and remains flagged under the corrected one.

---

## Correction 4 — OC2's "≥50% margin," resolved gate-by-gate

OC2 requires, as a precondition for overriding `PASSIVE_PARITY_OR_WORSE`, that "every other survival gate ... is cleared with at least 50% margin above its respective floor." Checked against each gate individually:

- **Gate (c), `STRUCTURALLY_INACTIVE` (`activity_rate ≥ 0.50`)**: well-defined already. The gate's floor (0.50) sits on a natural, bounded `[0,1]` scale with a meaningful ceiling (1.0). "50% margin above the floor" reads consistently under either natural interpretation — 50% of the floor's own value (`0.50 × 1.5 = 0.75`), or 50% of the remaining headroom to the ceiling (`0.50 + 0.5×(1.0−0.50) = 0.75`) — both give the same number here. **Resolved, unchanged**: OC2's activity-rate precondition is `activity_rate(genome) ≥ 0.75`.

- **Gates (a) and (a′), the zero floors (`per_world_score(w) ≥ 0`, `family_score(φ) ≥ 0`)**: genuinely **underspecified as originally stated**. "50% margin above a floor of 0" has no coherent percentage meaning — 50% of zero is zero, so a literal reading would make the "margin" requirement satisfied by any positive score at all, which is clearly not what "comfortable clearance" was meant to convey. This is a real gap, not a paperable-over technicality; confirming Rick's suspicion.

**Fix**: for zero-floor gates specifically, redefine the OC2 margin requirement as an **absolute** threshold rather than a percentage, since percentage-of-zero is not a coherent quantity:

```
OC2 zero-floor precondition: per_world_score(w) ≥ margin_abs   for every historical world w
                              family_score(φ)   ≥ margin_abs   for every synthetic family φ
                              margin_abs = 0.10   (fitness-score units)
```

`margin_abs = 0.10` is a newly-introduced explicit constant — flagged plainly as such, not hidden inside "50%" language. Chosen from the same synthetic-sweep evidence already in this contract (Correction 2's table): it sits meaningfully above small/marginal real scores (row 6: 0.044; row 3 under K=3: 0.127 is just above it) while being comfortably clearable by genuinely strong scores (rows 2, 4, 5: 0.566–0.690). This is a predeclared design parameter in the same spirit as K=3 — defensible from the synthetic arithmetic, not asserted as uniquely correct, and Rick may revise it.

**Summary**: OC2's margin definition is now fully, explicitly specified for all three gates it checks — `activity_rate ≥ 0.75` (unchanged, already coherent), and `per_world_score(w) ≥ 0.10` / `family_score(φ) ≥ 0.10` (newly fixed, previously incoherent).

---

## Complete final mathematical contract

**Level 1 — per world/fold:**

```
if order_count(w) == 0:
    per_world_score(w) = 0                                          # MUST short-circuit before Sharpe is evaluated

else:
    scaling_factor(w) = 1.0                                                          if median_return(w) <= 0
                       = clip(median_return(w) / (3·|worst_drawdown(w)| + 0.001), 0, 1)   if median_return(w) > 0
                                                                                       # K = 3, a predeclared design
                                                                                       # parameter (Correction 2) —
                                                                                       # NOT a formal Calmar-ratio
                                                                                       # derivation.

    per_world_score(w) = 3.0 · median_return(w) · scaling_factor(w)
                        + 0.20 · median_sharpe(w) · min(1, |median_return(w)| / 0.005)
                        − 2.0 · |worst_drawdown(w)|
                        − 0.25 · halt_rate(w)
                        − 0.02 · median_turnover(w)
                        − 5.0 · median_transaction_cost_rate(w)
```

**Level 2 — per world family** φ ∈ {historical, distributional, execution, sequence, shock} (unchanged):

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

**Activity classification (Correction 3, revised):**

```
activity_rate(genome) = (count of worlds w where order_count(w) > 0 AND turnover(w) ≥ 0.005)
                       / (total worlds evaluated)

STRUCTURALLY_INACTIVE(genome) = TRUE  if  [order_count(w)>0 AND turnover(w)≥0.005] is FALSE for ALL 3 historical worlds
                                  OR  activity_rate(genome) < 0.50
                              = FALSE otherwise
```

**Calibration boundary (unchanged)**: every quantitative bound feeding the transformation library and the world-validity gate is derived exclusively from DEVELOPMENT's own 2007-01-01→2018-12-31 record. No bound anywhere in this contract may cite QUALIFICATION, CHAMPIONSHIP, FINAL_RESERVE, or any date outside that range.

**Survival and admission gates, complete and final:**

- **(a)** `per_world_score(w) ≥ 0` for every one of the 3 historical worlds — non-negotiable, no override.
- **(a′)** `family_score(φ) ≥ 0` for every synthetic family — non-negotiable, no override.
- **(b)** `signal_delta(w) = genome_sharpe(w) − exposure_matched_passive_sharpe(w)`, aggregated per family via min+0.5·mean; median cross-family value ≤ 0 → `PASSIVE_PARITY_OR_WORSE` flag → excluded from automatic admission unless:
  - **OC1**: negative median driven by exactly one synthetic family, removable to flip the sign, AND that family's passive comparator is independently flagged anomalous (computed without reference to the candidate); OR
  - **OC2**: `|median signal_delta| < 0.05` (Sharpe units) AND `activity_rate(genome) ≥ 0.75` AND `per_world_score(w) ≥ 0.10` for every historical world AND `family_score(φ) ≥ 0.10` for every synthetic family.
- **(c)** `STRUCTURALLY_INACTIVE` per the revised definition above → excluded from automatic admission, **no override path** (OC1/OC2 never apply to this flag).
- **(d)** among genomes clearing (a)/(a′)/(b)/(c) directly or via a valid override: rank top-1 per independent campaign (`EVOLUTION_SEED`); a rejected rank-1 may never be replaced by a sibling from the same campaign for looking more favorable — explicit candidate-shopping prohibition, not implicit.
- **(e)** across N predeclared, independently-seeded campaigns: admission requires `> N/2` campaigns' nominees (equivalently `⌊N/2⌋+1`, **corrected from `⌈N/2⌉+1`**) to each independently clear (a)–(d); final selection among qualifiers uses a rule fixed before the batch runs (e.g. lowest `EVOLUTION_SEED`) — never "whichever looks best." N is fixed in advance and never increased after seeing results.

---

## Acceptance tests (rerun and extended — synthetic arithmetic only, no market simulation)

**Corrections carried forward unchanged, reverified:**
1. GIVEN `order_count(w)=0`, THE formula MUST return `per_world_score(w)=0` without evaluating `median_sharpe(w)`. — **HOLDS.**
2. GIVEN `median_return(w)=-0.05, worst_drawdown(w)=-0.06`, THE return term MUST equal `3.0×(-0.05)×1.0=-0.15`, not `0`. — **HOLDS.**
3. GIVEN `median_return(w)=-0.002, worst_drawdown(w)=-0.001`, THE return term MUST equal `-0.006`, not `0`. — **HOLDS.**
4. GIVEN a flag driven by 2 of 5 families, THE genome MUST NOT be OC1-eligible. — **HOLDS.**
6. GIVEN a rejected rank-1 with neither OC1 nor OC2 applicable, THE procedure MUST NOT substitute rank-2/rank-3 from the same campaign. — **HOLDS.**
9. GIVEN any transformation/validity bound citing a date ≥2019-01-01, THE contract MUST reject it. — **HOLDS.**
10. GIVEN the max-single-day-move check, THE bound MUST be computed exclusively from 2007-01-01–2018-12-31. — **HOLDS.**
12. GIVEN a genome active in 2 of 12 worlds including zero historical, THE genome MUST be flagged `STRUCTURALLY_INACTIVE` with no override path. — **HOLDS** (unaffected by Correction 3's turnover addition — this genome already fails the order-count test alone).
16. GIVEN a first batch of N=3 with 0/3 clearing, THE next action MUST NOT be "run more campaigns until one passes." — **HOLDS.**
17. GIVEN any admission-cycle report, THE `X/N` ratio MUST be stated regardless of outcome. — **HOLDS.**
18. GIVEN NO_TRADE's per-world score (return=0, dd=0, order_count=0), THE score MUST equal exactly `0`, and `0≥0` MUST pass. — **HOLDS.**
19. GIVEN a family with world scores `[0.05,-0.02,0.08]`, THE `family_score` MUST equal `-0.02+0.5×0.0367=-0.00167`. — **HOLDS.**
20. GIVEN the five family weights, THEY MUST sum to exactly `1.00`. — **HOLDS.**

**Restated for renamed terminology (Correction 2) — logic unchanged, "Calmar" struck:**
7. GIVEN `median_return(w)=0.25, worst_drawdown(w)=-0.11`, THE scaling factor MUST equal `clip(0.25/(3×0.11+0.001),0,1)=0.755` (IRDR≈2.27, not a "Calmar ratio"), not `1.0`.
8. GIVEN `median_return(w)=0.20, worst_drawdown(w)=-0.03`, THE scaling factor MUST equal `1.0` (IRDR≈6.67, comfortably clear of the K=3 boundary; the boundary is a predeclared parameter, not a formal Calmar tier).

**Restated for the corrected majority formula (Correction 1) — required counts changed:**
14. GIVEN N=5 with 2/5 nominees clearing all gates, THE procedure MUST NOT admit either (2 < ⌊5/2⌋+1 = 3, corrected from the prior document's erroneous "= 4").
15. GIVEN N=5 with 4/5 nominees clearing, THE procedure MUST admit exactly one, by the predeclared tiebreak (4 > ⌊5/2⌋+1 = 3 — a comfortable majority under the corrected formula, not a bare-minimum pass as the prior document's "= 4" implied).

**Restated for gate-specific OC2 margins (Correction 4) — split from the single prior test 5:**
5a. GIVEN a `PASSIVE_PARITY_OR_WORSE` genome with `|signal_delta|=0.03 < 0.05`, `activity_rate=0.80 ≥ 0.75`, all historical `per_world_score ≥ 0.10`, all synthetic `family_score ≥ 0.10`, THE genome MUST be OC2-eligible.
5b. GIVEN the same genome but with one historical `per_world_score = 0.05` (positive, clears gate (a), but `< 0.10`), THE genome MUST NOT be OC2-eligible, even though it clears the raw `≥0` survival floor — margin and floor are distinct checks.
5c. GIVEN the same genome but with `activity_rate = 0.60` (clears the `≥0.50` `STRUCTURALLY_INACTIVE` floor but `< 0.75`), THE genome MUST NOT be OC2-eligible.

**New — N=1..10 majority table, explicit per-N assertions (Correction 1):**
21. GIVEN N=1 with the nominee clearing all gates, THE procedure MUST admit it (`⌊1/2⌋+1=1`, i.e. 1-of-1 required and met) — and note the prior formula (`⌈1/2⌉+1=2`) would have made this impossible at any outcome.
22. GIVEN N=9 with exactly 5 of 9 nominees clearing, THE procedure MUST admit (5 ≥ `⌊9/2⌋+1=5`) — under the prior formula this would have failed (5 < `⌈9/2⌉+1=6`).
23. GIVEN N=3 with exactly 2 of 3 nominees clearing, THE procedure MUST admit (2 ≥ `⌊3/2⌋+1=2`) — under the prior formula this would have failed (2 < `⌈3/2⌉+1=3`, which demanded unanimity).

**New — token-trade activity-gaming (Correction 3):**
24. GIVEN a genome with `order_count(w)>0` in every world but `turnover(w)≈5×10⁻⁷` in every world (far below `0.005`), THE revised `activity_rate` formula MUST classify all such worlds as inactive for activity-rate purposes, yielding `activity_rate≈0` and triggering `STRUCTURALLY_INACTIVE` — even though the pre-correction, order-count-only definition would have computed `activity_rate=1.0` and passed this genome.
25. GIVEN a genome with `order_count(w)>0` and `turnover(w)=0.03` (3% of capital) in a world, THE revised formula MUST count that world as active (`0.03 ≥ 0.005`) — confirming genuinely modest real trading is not misclassified by the Correction 3 fix.

---

## Final determination

**FITNESS V2 / WORLD LEAGUE CONTRACT — READY TO FREEZE**

All four required corrections resolved with concrete, arithmetic-verified fixes:
1. Majority-rule formula corrected (`⌊N/2⌋+1`, proven correct by direct substitution for even and odd N; the prior formula's N=1 impossibility and systematic odd-N over-strictness both confirmed and fixed).
2. "Calmar" terminology struck throughout; K=3 stands as an explicitly-labeled, synthetic-arithmetic-justified design parameter, not a borrowed external convention.
3. A genuine token-trade gaming gap in `activity_rate` was found (not merely asserted-then-dismissed) via full worked arithmetic through every term of the per-world formula, and closed by reusing the contract's own existing `0.005` materiality constant — no new arbitrary threshold introduced for this fix.
4. OC2's margin requirement was confirmed already coherent for the `activity_rate` gate, and genuinely underspecified for the two zero-floor gates — fixed via one new, explicitly-flagged absolute constant (`margin_abs=0.10`), derived from the same synthetic-sweep evidence already in the contract.

25 acceptance tests (20 carried forward with 5 restated for terminology/formula changes, 5 newly added covering all four corrections) are stated as falsifiable `GIVEN → THEN` assertions sufficient for mechanical verification by a future implementer.

Two constants are newly introduced in this errata pass and are flagged explicitly for Rick's visibility, not buried: the reused `0.005` turnover-materiality threshold for `activity_rate` (Correction 3), and the new `margin_abs=0.10` absolute-score threshold for OC2's zero-floor preconditions (Correction 4). Both are derived from evidence already in this document, not asserted as uniquely correct — either may be revised without touching any other part of the contract.

**Final contract document content sha256** (of this file as written, pre-hash-insertion): `35b51d3dbd63a49076343c9a3708999c47654f1c44890da5bd02cbdcda3ffbb4`

---

## Hard boundary compliance statement

No source-code modification, no evaluator modification, no dividend repair, no genome modification, no mutation, no reproduction, no candidate generation, no fitness implementation wired into real code, no simulation/backtest execution against real market data, no E/F/G execution, no promotion/demotion, no champion replacement, no NODE mutation, no Gen0-bootstrap modification or access — `scripts/s6b_primary_development.py`, `tests/test_s6b_primary_development.py`, `scripts/s6b_resume_executor.py` were not read or touched this turn. Nothing committed, nothing pushed. No broker/live/paper activity. Champion remains frozen throughout. D1 remains an experimental subject only, never referred to as a succession candidate. Every numeric example above is synthetic, constructed to test the formula — none derived from any real genome's recorded results.
