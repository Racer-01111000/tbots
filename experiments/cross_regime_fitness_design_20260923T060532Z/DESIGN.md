# Cross-Regime Evolutionary Fitness / Selection Contract — Design

Status: DESIGN ONLY. No code modified, no evaluator modified, no simulation executed, no genome touched, nothing committed. Written in response to Rick's GO "CROSS-REGIME EVOLUTIONARY FITNESS / SELECTION CONTRACT DESIGN."

This document is grounded in a read-only inventory of the current S5A evolutionary engine (`scripts/s5a_evolution.py`, `scripts/s5a_config.py`, `evolution/protocol/*.json`, `scripts/s5_boundary.py`) and the empirical record built across the prior experiment series in this conversation (controls comparison, SABLE review, PASSIVE_ENVELOPE DEV-gap closure, lineage decomposition, D1 exposure-isolation, D1 QUALIFICATION generalization, qualification-harness equivalence — all cited by finding, not re-derived).

---

## 1 / A. Current-system map

**Pipeline:** `genome → evaluation (12 DEV episodes) → fitness scalar → rank → select top 10 → mutate/immigrate → next generation (×10) → freeze top 10 of generation 10`

**Genome structure** (`s5a_config.py::genome_from_values`): immutable fields — `strategy_family`, `universe` (fixed 8-asset ETF set), `direction`, `leverage`, `shorting`, `drawdown_halt_pct` (12%, identical for every genome, every lineage). Mutable fields (9 genes, `MUTABLE_NAMES`) — `momentum_lookbacks` (fast/medium/slow triplet, must satisfy fast<medium<slow), `trend_filter_window`, `volatility_window`, `max_positions`, `target_max_exposure`, `max_asset_weight`, `rebalance_every_n_sessions`. Every gene lives on a discretized grid with frozen min/max/step bounds (`mutation_bounds_*.json`). `strategy_family` itself is immutable per lineage — B/C/D/etc. are different frozen families (`mean_reversion`, `fast_trend_breakout`, `defensive_rotation` per the lineage-decomposition GO), not something evolution discovers; evolution only tunes the 9 numeric genes within one family per lineage run.

**Mutation** (`s5a_config.py::mutate_genome`): 1000 attempts to find a valid bounded child — picks a random count of genes to mutate (bounded by `child_mutated_gene_count.min/max`), shifts each by a bounded grid-step delta, rejects out-of-bounds/off-grid/no-op/exact-duplicate results. Fully deterministic given the frozen `EVOLUTION_SEED` (`2026082701`) and per-slot derived seeds.

**Population/generation structure** (`population_rules_*.json`): 50 genomes/generation, 11 generations (0–10). Generation 0 = 1 fixed control anchor (the frozen S4 control genome, same across every lineage's generation 0) + 49 random immigrants. Generations 1–10 = 10 elites retained (the control anchor + the 9 highest-fitness non-control survivors from the prior generation) + 30 mutated children (parents drawn with replacement from the 10-elite pool) + 10 fresh random immigrants = 50. All 50 genome_ids in a generation must be unique (collision → reroll).

**Current fitness calculation** (`fitness_formula_*.json`, formula name `s5a_development_fitness_v1`):
```
fitness = 3.0·median_episode_return
        + 0.20·median_episode_sharpe
        − 2.0·|worst_drawdown|
        + 0.50·consistency_score
        − 0.25·halt_rate
        − 0.02·median_episode_turnover
        − 5.0·median_transaction_cost_rate
        − 0.25·performance_concentration
```
where `consistency_score = 0.5·positive_episode_fraction + 0.5·(1 − min(1, return_dispersion/0.10))`, `performance_concentration` = normalized HHI of the 12 episodes' absolute returns, `halt_rate` = drawdown-halts / 12. **Every term in this formula is computed exclusively from the same 12 DEVELOPMENT episodes** (2007-02-07 → 2018-12-31, one contiguous historical path, per `episode_manifest_*.json`). There is no external benchmark, control, or non-DEV data anywhere in the fitness path.

**Ranking/selection/discard**: `_rank_population` sorts by `(-fitness, genome_id)` (deterministic tiebreak). `_select_survivors` keeps the control anchor plus the top 9 non-control genomes by fitness — pure fitness-rank truncation, nothing else. Everyone else is permanently retired (`state='retired'`, `status='graveyard'`) and, per the frozen population rules, "may never reproduce" — discard is final, no reentry, no second chance.

**Verification** (`_verify_generation`): each generation, the champion, the worst surviving elite, and one deterministic-random other genome are re-evaluated from scratch; if the reproduction doesn't hash-match the original, the whole run raises and halts. This checks *determinism*, not independence or generalization — it proves the code is reproducible, not that a genome's fitness is meaningful outside DEV.

**Freeze**: top 10 of generation 10 by final fitness become the frozen lineage output (what later becomes e.g. "D_primary top-eight" after a further downstream narrowing not part of S5A itself).

**Risk/exposure constraints**: enforced per-episode by `execution.py`/`risk.py` at evaluation time (position caps via `max_positions`×`max_asset_weight`, whichever binds first; portfolio-level `drawdown_halt_pct`=12% halt, identical for every genome) — these are evaluation-time constraints, not fitness-shaping terms. A genome's *realized* exposure is a downstream consequence of its own mutable caps, not something fitness explicitly rewards or penalizes independent of the return/drawdown it produces.

**Boundary architecture already exists and is stronger than the fitness formula's own discipline suggests** (`s5_boundary.py`): lane manifests are content-hashed and read through `load_authorized_manifest`, which hard-rejects (`SealedLaneError`) any manifest whose date range overlaps `SEALED_INTERVALS = {CHAMPIONSHIP: 2023-01-01..2025-12-31, FINAL_RESERVE: 2026-01-01..2026-08-25}` — this is a mechanical block, not a policy note. `build_control_evaluation_plan` raises `ContaminatedFitnessInput` if given `fitness_input`, `prior_result_identifiers`, or `qualification_feedback` — i.e. the codebase already has a typed exception class dedicated to refusing exactly the kind of leakage this GO is worried about. Every authorized lane manifest carries a `feedback_policy` field; only DEVELOPMENT is marked `eligible_for_evolutionary_fitness` — QUALIFICATION is explicitly `evaluation_only_no_mutation_or_selection_feedback`. **This machinery already existed before this GO and was not built to catch D1's specific failure** — it predates the D1 experiment series and is S5-wide infrastructure. The design below extends this same pattern rather than inventing a new one.

**Where specialization to DEVELOPMENT can occur — the actual gap**: nothing above is broken or leaking. The gap is that DEVELOPMENT itself is only one historical path (2007–2018, 12 contiguous non-independent annual slices), and the fitness formula's only "robustness" terms (`consistency_score`, `performance_concentration`) measure stability *within* that one path, not across genuinely different macro regimes. A genome can score perfectly on both terms by being reliably good at "what 2007–2018 rewarded" — which is exactly consistent with, not contradictory to, D1 generalizing poorly to 2019–2022. Return also dominates the formula 15:1 over Sharpe (3.0 vs 0.20), so search pressure is overwhelmingly toward raw absolute return — the same axis the exposure-isolation experiments showed is most easily inflated by sizing rather than signal quality. And no genome is ever compared to a passive/naive baseline anywhere in fitness — only to its 49 DEV-generation siblings, all fit to the same single historical path.

---

## 2. Protected-data boundary

**Definition — what future evolution may see:** only fresh, per-episode evaluation results computed from DEVELOPMENT-lane price/fundamental data (the existing `ds_7e16896c...` dataset, restricted to dates ≤ 2018-12-31, or restricted further to whichever DEV sub-windows are used per §3), evaluated with the frozen, unmodified evaluator primitives (`execution.py`, `risk.py`, `indicators.py`, `s6a_runtime.py`'s `decide_*` functions). Nothing else.

**What must remain sealed, permanently, from every future evolutionary run** (extending the existing `SEALED_INTERVALS` pattern with two new entries — this is additive, not a replacement of the current mechanism):
- QUALIFICATION (2019-01-01 → 2022-12-31) outcomes of any genome, including D1's own already-observed result. The existing manifest already marks this `evaluation_only_no_mutation_or_selection_feedback`; this design recommends it graduate to a hard `SealedLaneError`-style block once a QUALIFICATION lane has actually been used as an exam (see §9) — currently it's soft-policy, not mechanically enforced the way CHAMPIONSHIP/FINAL_RESERVE are.
- CHAMPIONSHIP (2023-2025) and FINAL_RESERVE (2026+) — already hard-sealed by existing code, unchanged.
- D1's specific 2019–2022 failure pattern, or any narrative about *why* it failed (concentrated rotation into GLD/EEM, whipsaw, etc.) — this is exactly the kind of result-shaped knowledge that must not leak into how §3–§5 below get parameterized. This document's author (me, writing this design) already possesses that knowledge from this conversation, which is an unavoidable contamination of *this design session specifically* — flagged honestly in §9's "contaminated designer" caveat below, not hidden.
- Any future protected-stage result not yet produced.

**Uncontaminated historical data check**: DEVELOPMENT (2007-2018) is 12 years / ~3,000 trading sessions across 8 assets. That is enough raw calendar length to carve into genuinely distinct macro sub-regimes (see §3) without borrowing a single day from QUALIFICATION/CHAMPIONSHIP/FINAL_RESERVE. I did not find evidence of insufficient uncontaminated data for the walk-forward design below — DEVELOPMENT's own 12-year span already contains a real crisis (2008), a real recovery/bull run (2009-2007-2013ish), and calmer grind periods, i.e. actual regime diversity, it is simply not currently *used* as diversity by the fitness formula (see finding in §1).

---

## 3. Cross-regime DEVELOPMENT design

**Constraint acknowledged up front, per the GO**: this must be defensible without knowing D1's later QUALIFICATION result. The case below is argued from principles established *before* this experiment series existed — non-stationarity of financial regimes is a textbook concern, and the SABLE review already flagged (independently, before any QUALIFICATION result existed) that DEV's 12 episodes are "one correlated 12-year path examined at 12 checkpoints," not 12 independent trials. That critique alone — with zero knowledge of D1's QUALIFICATION outcome — already implies DEV-only fitness cannot distinguish "genuinely robust" from "fit to one path's idiosyncrasies." The design below follows from that, not from D1's specific failure mode.

**Proposed: walk-forward developmental folds, entirely inside 2007–2018.**

Partition DEVELOPMENT into K non-overlapping, contiguous folds (candidate: K=3, roughly 2007-2010, 2011-2014, 2015-2018 — each ~4 years, each containing at least one distinguishable macro character: fold 1 contains the 2008 crash and its immediate aftermath, fold 2 is a mid-cycle grind, fold 3 contains the mid-decade correction period referenced earlier in this series as "2015-16"). Exact fold boundaries should be chosen by a *content-neutral* rule stated in advance (e.g. "split into K roughly-equal-length calendar spans," not "split so a crisis falls in each fold" — the latter would be regime-content-aware tuning, which is a milder form of the same leakage this design exists to prevent). Within each fold, keep the existing 12-episode-style annual/sub-annual episode construction unchanged (episode mechanics — T+1 fill, fresh per-episode state, commission/slippage — are not being redesigned here).

A genome is evaluated once per fold (all folds always run — no adaptive stopping), producing K independent-ish episode-metric bundles per fold internally (still correlated within-fold, but folds are chosen to be less correlated with each other than adjacent years are with each other, since a multi-year fold spans a different macro chapter).

**Alternatives considered:**
- *Rolling/expanding windows* (train on an expanding prefix, test on the next slice, walk forward): more statistically standard for pure forecasting, but conceptually mismatched here — there is no "training" step in this genetic algorithm distinct from "evaluation" (fitness *is* the evaluation), so a rolling-window design would just be the fold design with unequal, overlapping folds and added complexity for no clear benefit given DEVELOPMENT's fixed, already-short 12-year span. Rejected for complexity without payoff.
- *Regime-diverse partitions selected by realized volatility/drawdown clustering*: more "principled" in one sense (folds chosen to be genuinely different regimes, not just different dates), but this requires *looking at* DEVELOPMENT outcome data to choose the fold boundaries — which is a mild, DEV-internal form of the exact overfitting concern (choosing boundaries with the benefit of hindsight about what happened). Rejected in favor of the content-neutral equal-length-calendar-span rule above, which is fully specifiable before any genome is ever run.
- *Single-fold DEV (status quo)*: rejected — this is precisely the design that produced D1's non-generalizing fitness signal.

**Preferred**: 3 content-neutral, equal-length, non-overlapping calendar folds inside 2007–2018, all three always evaluated for every genome, no fold ever dropped based on results.

---

## 4 / B (fitness). Fitness contract

**Principle**: reward the *worst* fold, not the *average* fold, subject to a floor — a genome that is merely mediocre-but-consistent across all three folds should rank above one that is spectacular in one fold and poor in the other two. This directly targets "one spectacular episode compensating for repeated failures" (explicit GO requirement) without needing a black-box composite.

**Proposed formula** (extends, does not replace, the existing per-fold building blocks — return/Sharpe/drawdown/consistency/halt-rate/turnover/cost terms are computed exactly as `s5a_fitness_v1` already does, just once per fold instead of once for all of DEV):

```
per_fold_score(f) = 3.0·median_episode_return(f) + 0.20·median_episode_sharpe(f)
                   − 2.0·|worst_drawdown(f)| − 0.25·halt_rate(f)
                   − 0.02·median_episode_turnover(f) − 5.0·median_transaction_cost_rate(f)
                   [identical per-fold building blocks to the existing formula, minus the
                    two cross-episode terms that only make sense pooled — see below]

cross_regime_fitness = min_f(per_fold_score(f))                         # worst-fold floor
                      + 0.5 · mean_f(per_fold_score(f))                  # reward broad quality too
                      − 0.25 · fold_dispersion                           # penalize fold-to-fold volatility
                      − 0.25 · performance_concentration_across_folds    # same HHI logic, now cross-fold
```
where `fold_dispersion` = standard deviation of the K `per_fold_score` values (unitless once folds' scores are on the same scale as the existing per-episode terms), and `performance_concentration_across_folds` reuses the existing HHI formula, applied to the K fold scores instead of the 12 episode returns.

**Rationale for `min + 0.5·mean` rather than `min` alone**: a pure worst-fold floor with no reward for broad quality risks selecting an "even mediocrity" that is never great anywhere, which is not obviously better than the status quo — it just moves the specialization risk rather than removing it. Weighting the floor at full strength and the mean at half strength keeps the floor dominant (a genome cannot buy its way past a catastrophic fold with brilliance elsewhere) while still letting genuinely broad quality outrank genuinely narrow mediocrity when both clear the floor. This weighting (1.0 / 0.5) is a stated, arbitrary-but-declared choice, not derived from any observed genome's results — flagged as a parameter Rick may want to revisit, not asserted as optimal.

**Consistency/HHI terms**: retained, but re-scoped from "across the 12 correlated episodes of one path" (their current, weak scope) to "across the K folds" (a meaningfully different scope, since folds are chosen to differ in regime character, not just in date).

**Do not collapse further than this.** Rick's GO explicitly asks to avoid a single opaque score "unless mathematically necessary" — it is not necessary here; the four components (worst-fold floor, mean quality, fold dispersion, cross-fold concentration) are each independently interpretable and independently loggable per genome, so a human reviewer can always see *why* a genome scored as it did, not just the final number.

**Explicitly not tuned against any observed result**: the weights above (3.0/0.20/−2.0/−0.25/−0.02/−5.0 per-fold, 1.0/0.5/−0.25/−0.25 cross-fold) are the *existing* frozen `s5a_fitness_v1` weights carried forward unchanged at the per-fold level, plus new cross-fold combination weights chosen by the stated min/mean/dispersion logic above — not fit to reproduce a desired ranking of D1 vs. anything else, and not checked against what they *would* have done to D1, B, C, champion, or any other already-observed genome (doing so would itself be a form of the leakage this design exists to prevent — see §9).

---

## 5. Survival and discard contract

**Survive**: a genome must (a) clear a minimum acceptable-behavior floor in **every** fold (not just on average) — proposed floor: `per_fold_score(f) > catastrophic_threshold` for all f, where `catastrophic_threshold` is defined independently of any genome's results as "worse than NO_TRADE's fold score" (NO_TRADE's fold score is itself deterministic and known in advance: return 0, drawdown 0, Sharpe/Sortino 0, so `catastrophic_threshold` reduces to "per_fold_score(f) > 0" under the existing formula's own units — a genome that would have been better off never trading, in even one fold, fails outright, regardless of its other folds); AND (b) rank in the top 10 by `cross_regime_fitness` among genomes that cleared (a).

**Discard**: any genome failing (a) in one or more folds is discarded regardless of aggregate fitness, *before* ranking — this directly prevents one spectacular fold from buying past a catastrophic one, addressing the GO's explicit concern. Any genome clearing (a) but not making the top-10 cut on `cross_regime_fitness` is discarded normally, same as today.

**Turnover/cost abuse, inactivity, exposure gaming**: the existing per-episode transaction-cost and turnover penalty terms are retained unchanged inside `per_fold_score`. Two additions, both deterministic and predeclared:
- **Inactivity floor**: a genome producing zero trades across an entire fold is not automatically rewarded merely for avoiding the catastrophic-failure floor (which it would trivially clear, being economically identical to NO_TRADE in that fold) — cap `per_fold_score(f)` at a small positive constant (proposed: 0.0, matching NO_TRADE's own score) for any fold with `order_count == 0`, so a genome cannot use total inactivity as a way to "pass" a fold it has no real behavior in. This must be capped, not penalized further, since refusing to trade in a genuinely bad fold is not itself a defect — see §7.
- **Exposure gaming**: addressed structurally in §7 below (the fitness formula does not need an explicit exposure-gaming penalty term if the comparison in §7's control step catches it directly).

---

## 6. Simple-control requirement

**Diagnosis (always computed, every generation, logged, never silently discarded) vs. fitness-influencing (a narrow, explicit subset):**

Every generation, alongside the 50 genomes, evaluate three fixed, non-evolving reference agents on the exact same K folds: `NO_TRADE`, `PASSIVE_ENVELOPE` (the existing 80%-exposure equal-weight buy-once control), and — critically, per the exposure-isolation experiment's finding — an **exposure-matched passive control built fresh per genome**, sized to that genome's own realized/declared exposure cap (the same construction used in the D1 exposure-isolation GO). All three are **diagnostic only** by default: they are computed, logged in every generation report (extending `_generation_report`'s existing schema), and made available to Rick for review — but do **not** enter `cross_regime_fitness` automatically.

**The one exception, and it is deliberately narrow**: the catastrophic-failure floor in §5 already uses "worse than NO_TRADE" as its threshold — this is the *only* place a control's behavior is permitted to influence survival, and it is fixed at NO_TRADE's trivially-known, zero-everything score (not tuned, not re-derived from an actual backtest of NO_TRADE that could itself drift). This avoids the GO's own warning against "turning a particular control's historical behavior into another target for overfitting" — NO_TRADE's score is a mathematical constant (0), not a fitted or observed quantity that could be gamed.

**Why not let PASSIVE_ENVELOPE or the exposure-matched control influence fitness directly**: doing so would require deciding, in the fitness formula, exactly how much credit a genome gets for beating a specific control — and that decision, made by a human designer who (in this document's case) already knows D1 lost to exactly such a control on QUALIFICATION, cannot be made without risk of being shaped by that knowledge. Keeping these controls diagnostic-only sidesteps that risk entirely while still surfacing the comparison Rick's GO §6 and the whole D1 experiment series cared about — every generation report would show, for every survivor, whether it beat its own exposure-matched passive control, without that fact ever feeding the genetic algorithm's actual selection pressure.

---

## 7. Exposure-isolation principle, built into future evaluation

**Structural mechanism, not a fitness term**: for every genome, at every fold evaluation, additionally compute the same genome's own exposure-matched passive-control comparison used diagnostically in §6 (same construction as the D1 exposure-isolation GO — equal-weight, buy-once, sized to the genome's own realized gross exposure). Log, per genome per fold: `signal_delta = genome_sharpe(f) − exposure_matched_passive_sharpe(f)`. This is reported alongside fitness in every generation report, letting a human reviewer (or a later, more principled fitness-v2 design) distinguish "this genome is good because of what it holds and when it holds it" from "this genome is good because it holds less than its siblings and dodges the drawdown halt" — exactly the distinction the exposure-isolation experiment proved matters.

**Explicit non-assumption, per the GO**: lower drawdown is not treated as evidence of better signal quality anywhere in this design. The `−2.0·|worst_drawdown|` term inside `per_fold_score` already exists in the frozen formula and is carried forward unchanged (it rewards genomes for controlling risk, which is a legitimate objective on its own terms) — but `signal_delta` above is reported *separately* precisely so a low-drawdown, low-exposure genome's fitness score cannot be misread as proof of skill without checking whether it beats a same-sized dumb allocation. This is diagnostic, not yet a fitness term, for the same overfitting-avoidance reason given in §6 — a future GO, after seeing several generations of `signal_delta` data logged this way (never from a protected-lane result), could deliberately and transparently decide to fold it into fitness v2, but that is out of scope for this design.

---

## 8. Reproduction and independence

**Established fact this design must account for**: B_primary/B_reproduction and D_primary/D_reproduction are confirmed byte-identical duplicate outcomes (same genome_ids, same metrics, every field) — this is because "reproduction" in this codebase's existing usage means *re-running the same deterministic evolutionary search from the same seed to confirm it reproduces exactly* (see `s5a_evolution.py::reproduce`), not *running an independent second search*. The system already has exactly one mechanism for genuinely independent evolutionary evidence — a different `EVOLUTION_SEED` — and it has never been exercised for B or D; every "reproduction" run to date has used the identical seed to check determinism, not to generate a second independent sample.

**Future reporting rule, predeclared**: any report on genetic-algorithm output must classify every pair of runs as exactly one of:
- **Deterministic reproduction** — same `EVOLUTION_SEED`, same code revision, same bundle revision; used only to prove the pipeline is reproducible, never cited as two units of evidence.
- **Independent evolutionary search** — different `EVOLUTION_SEED` (or different bundle/code revision), same lineage/strategy_family; this is the *only* thing that should ever be described as "two runs" or "N=2" in a generalization argument, and even then, both runs still share the same DEVELOPMENT data and folds, so they are independent *searches*, not independent *evidence about market regimes*.
- **Separate genomes within one run** — different rank-slots from the same top-10 freeze (e.g. D_primary_rank1 vs rank2 vs rank3); these are correlated by shared parentage/mutation history within one search and should never be pooled as if they were unrelated data points without saying so.
- **Separate historical observations** — different folds/windows (DEV fold 1 vs fold 2, or DEV vs QUALIFICATION); already covered by the non-independence caveat established in the SABLE reconciliation GO, carried forward here.

Any future experiment report claiming "N independent trials support X" must state which of these four categories each unit belongs to, following the precedent already set by the SABLE-review and lineage-decomposition GOs in this series.

---

## 9. Anti-overfitting protections

- **Repeated reuse of the same historical episodes**: not a new risk introduced by this design — DEVELOPMENT is still fully reused, fold-by-fold, every generation, same as today; nothing here increases the number of distinct historical windows evolution ever sees beyond the K folds fixed at design time.
- **Candidate/sibling shopping after failure**: explicitly prohibited — per §1's frozen `retirement` rule ("all non-survivors become permanent graveyard and may never reproduce"), this is already structurally blocked at the code level; this design adds no new exception to it, and any future GO proposing to "bring back" a retired genome after seeing a protected-lane failure should be treated as a violation of this same principle, not authorized here or implicitly.
- **Parameter tuning after protected results**: this document is itself written with partial knowledge of D1's QUALIFICATION and QUALIFICATION-equivalence results (unavoidable, since this design GO was issued after those experiments completed in the same conversation) — flagged honestly, not hidden. The mitigation is procedural, not something this document can self-certify: every numeric weight proposed above is the *existing* frozen formula's weight carried forward unchanged, or a small integer/half-integer combination weight (1.0, 0.5, 0.25) chosen for interpretability rather than fitted to any target outcome — but Rick should treat any future request to "adjust" these specific numbers, made after seeing what they *would have done* to D1 or any other already-observed genome, as a contamination event requiring explicit disclosure, the same standard applied throughout this experiment series.
- **Fitness-function tuning against known holdout outcomes**: explicitly out of scope for every future GO in this lineage — a fitness formula change proposed specifically because of what it would do to an already-observed protected-lane result must be labeled as such and, per §10 below, probably requires treating that protected lane as burned (see next point) rather than quietly reusing it.
- **Repeated holdout inspection**: **the existing QUALIFICATION lane (2019-2022) is now scientifically contaminated for D1's lineage and this design process** — it has been read, discussed, and used to motivate this very design document. It must not be reused as a blind test for any genome descended from or benchmarked against this design's reasoning about D1. A *fresh* protected window is required for the next blind test (see §10 — FINAL_RESERVE remains unspent for any non-champion genome, per the earlier holdout-validation GO, and is the only remaining genuinely blind window in this dataset besides CHAMPIONSHIP, which is separately burned for the champion specifically).
- **Promotion based on one favorable regime**: directly addressed by §5's per-fold catastrophic floor — a genome cannot be promoted on the strength of one fold alone.

**When does a protected window become contaminated?** The moment its numeric outcome (not just its existence) has been observed by anyone who will subsequently make a design, mutation, fitness, or selection decision. Under that standard: QUALIFICATION is now contaminated (observed and discussed at length in this conversation). CHAMPIONSHIP is contaminated for the champion specifically (it was the champion's own selection window) but was never used as a blind test for D1 — its D1 result was observed in the earlier lineage/holdout work in this conversation, so treat it as contaminated for D1 too, for the same reason as QUALIFICATION. FINAL_RESERVE has been observed for the champion and D1 specifically (in the earlier holdout-validation GO) — also contaminated for those two, but not for any genome from a *future* evolutionary run using this design, provided that future run's designers/mutators never see the D1/champion FINAL_RESERVE numbers while shaping the new run. That is a real, non-trivial process discipline requirement (see §10), not something code alone can enforce for a fresh genome that doesn't exist yet.

---

## 10. Future evaluation ladder

```
Evolutionary DEVELOPMENT (K content-neutral folds, cross_regime_fitness, §3-5)
        │  survivors only (genome_id, frozen config) — NO fold-level metrics
        ▼
Internal robustness gate (§5's per-fold catastrophic floor, already enforced pre-ranking
        │  inside DEVELOPMENT; nothing new crosses this line — it's the same DEV data)
        ▼
Sealed QUALIFICATION  (2019-2022 — NEXT usable blind window must be a genuinely fresh
        │  one; the existing 2019-2022 outcome is burned for D1's lineage per §9)
        ▼
Sealed CHAMPIONSHIP  (2023-2025 — burned for champion and D1; usable once, blind,
        │  for any genome that has never had this window's outcome observed by its designers)
        ▼
Final blind reserve / future forward evidence  (genuinely-new calendar time as it accrues,
           per §11 — the only window that can never be pre-contaminated by definition)
```

**What may flow backward at each stage — stated once, applied identically at every arrow above**: only a **pass/fail admission decision** (did this genome clear the stage) and the **frozen genome definition** of anything that passes. Never: the stage's return, Sharpe, drawdown, or any other performance number; never a narrative about *why* something passed or failed; never a suggestion for how to fix a failure. This is the existing DEVELOPMENT→QUALIFICATION policy (`feedback_policy: evaluation_only_no_mutation_or_selection_feedback`) generalized to every arrow, not just the first one.

**If a candidate fails a protected stage**: per this design's own logic, the *individual candidate* is simply discarded (it already was, permanently, by the existing retirement rule) — but the experimental *campaign* (i.e., "should we run S5A again with a different fitness design, believing the new design fixes the generalization problem") is a separate question, and per the GO's own instruction, that decision belongs to Rick, not to this document. This design's recommendation: if the *majority* of a frozen top-10 fails QUALIFICATION under the new cross-regime fitness (not just one genome, the way D1 alone failed under the old fitness), that is strong evidence the redesign itself needs revision before spending another protected window — see §14/G falsification criteria below. A single genome's failure is expected and should not by itself trigger redesign; it's what discard is for.

---

## 11. Forward-test requirement (design only)

A future frozen candidate's ultimate test is calendar time that did not exist at its freeze date — i.e. genuinely new price data arriving after the freeze, evaluated read-only, exactly as FINAL_RESERVE was used for the champion and D1. Design for this future step:
- The candidate's genome and freeze date are hash-recorded (extending the existing `evolution_frozen_top10` table/genealogy pattern) at freeze time, before any forward data exists — this is already how the system works (genome_id is content-derived and fixed before evaluation).
- No broker connection, no live order routing, no paper-trading integration is designed or implied here — this document proposes only that *historical replay* continue to be used, on data that becomes retroactively "forward" once enough calendar time has passed (exactly how FINAL_RESERVE — 2026 data evaluated after being sealed — already functions for the champion). No new infrastructure is required for this; it's an operational cadence (wait, then evaluate with the same frozen, unmodified evaluator), not a system design change.
- Explicitly out of scope, per the GO: any broker/paper/live activity of any kind.

---

## 12 / E. Known simulator issues and prerequisites

- **Dividend-accounting defect** (already established, unfixed): `execution.py`'s `Portfolio` marks equity on each session's raw close and credits no dividend cash anywhere in the codebase on any ex-dividend date. **This must be repaired and revalidated before the next evolutionary campaign begins** — not because it invalidates the design above (the design is defect-agnostic, since every genome and every control is understated by the same mechanism), but because a future genetic algorithm optimizing directly against dividend-blind returns could plausibly evolve a mild bias against dividend-heavy assets/holding patterns (e.g. penalizing IEF/TLT/GLD-rotating `defensive_rotation`-family genomes like D relative to lower-yield alternatives) that a dividend-aware fitness would not produce. Repair path (design only, not executed here): credit dividend cash to `Portfolio.cash_cents` on each held position's ex-date, sourced from the same corporate-action data already present in the dataset (SABLE's review confirmed `corporate_action` entries exist and are clean) — then revalidate by rerunning the existing frozen-evaluator DEV comparison and confirming the champion/D1/PASSIVE_ENVELOPE relative ordering is understood post-repair before trusting any new campaign's absolute-return numbers.
- **Return dominance in the existing fitness weighting** (3.0 vs 0.20 Sharpe): not a "bug" in the sense of incorrect code, but a design assumption worth flagging as a fitness-*design* prerequisite decision for Rick, addressed by this document's §4 proposal (which keeps it, at the per-fold level, but adds fold-robustness terms on top) rather than unilaterally rewriting it.
- **T+1 fill / rebalance-frequency neutrality**: not independently verified in this design pass — worth a dedicated read-only check (out of scope here) of whether the fixed 5bps/5bps commission+slippage convention advantages low-`rebalance_every_n_sessions` genomes or high ones asymmetrically; flagged as an open question, not resolved.
- **Universe survivorship**: the fixed 8-asset universe (SPY/EFA/EEM/DBC/VNQ/GLD/IEF/TLT per names appearing throughout this series) was not audited in this pass for survivorship bias (e.g. whether any constituent had a corporate action, delisting, or ticker change across 2007-2026 that the dataset might handle inconsistently) — flagged as an open question for a future dedicated review, not evaluated here.

---

## 13. Evidence preservation inventory

Every experiment artifact directory produced across this series, uncommitted, git-status-confirmed clean of any other change (per this repo's established convention of leaving experiment output uncommitted pending Rick's review):

| Experiment | Path |
|---|---|
| Controls comparison (41 agents × 12 DEV episodes) | `experiments/controls_comparison_20260921T064013Z/` |
| Controls interpretation + FINAL_RESERVE holdout | `experiments/controls_interpretation_holdout_20260921T140000Z/` |
| CHAMPIONSHIP-lane holdout | `experiments/championship_holdout_20260921T160000Z/` |
| SABLE independent review | `experiments/sable_review_20260921T104614Z/` |
| PASSIVE_ENVELOPE DEV-lane gap closure | `experiments/passive_envelope_dev_gap_20260923T045250Z/` |
| PASSIVE_ENVELOPE lineage decomposition | `experiments/passive_envelope_lineage_decomposition_20260923T045937Z/` |
| D1 exposure-isolation experiment | `experiments/d_exposure_isolation_20260923T052542Z/` |
| D1 QUALIFICATION generalization test | `experiments/d1_qualification_generalization_20260923T054829Z/` |
| Qualification-harness equivalence validation | `experiments/qualification_harness_equivalence_20260923T055652Z/` |
| This design document | `experiments/cross_regime_fitness_design_20260923T060532Z/` |

**Recommended preservation strategy (recommendation only — not executed under this GO)**: a single commit per experiment directory (10 commits, chronological order, one artifact set each), onto a new dedicated branch (e.g. `evidence/d1-generalization-series-20260923`) off the current HEAD, explicitly *not* merged into `repair/self-contained-canonical-20260901` or any deployed branch until Rick reviews. Each commit message should name the experiment and its one-line finding (e.g. "D1 exposure-isolation: Classification A, Sharpe edge survives at matched 23% exposure") so `git log --oneline` alone becomes a readable index of this series without opening any file. The two pre-existing Gen0-bootstrap dirty files remain untouched and uncommitted, exactly as every prior GO in this series required.

---

## 14. Deliverable summary (cross-references to sections above — not repeated in full)

**A. Current-system map** — §1 above.
**B. Proposed evolutionary contract** — §3 (developmental data structure: K=3 content-neutral folds), §4 (fitness formula), §5 (survival/discard), §6 (control usage: diagnostic-only + one fixed NO_TRADE floor), §7 (exposure treatment: logged `signal_delta`, not a fitness term), §8 (reproduction semantics: four-way classification).
**C. Protected evaluation ladder** — §10 above.
**D. Anti-leakage rules** — §2 (what evolution may/may not see) and §9 (contamination standard: a protected window is burned once its *numeric outcome* has been seen by anyone shaping the next run — QUALIFICATION and CHAMPIONSHIP are now burned for D1's lineage; FINAL_RESERVE is burned for champion and D1 specifically but not for a future genome no one has looked at yet).
**E. Simulator prerequisites** — §12 above; dividend repair is the one hard prerequisite before the next campaign.
**F. Migration plan** — smallest safe sequence, in order: (1) preserve/commit the 9 existing experiment artifacts per §13's strategy — pure git hygiene, zero code change; (2) repair and revalidate the dividend defect per §12, as its own isolated, reviewed change, rerun against the existing frozen DEV comparison to confirm relative orderings are understood before trusting new numbers; (3) implement the K=3 fold split and `cross_regime_fitness` formula from §3-5 as new, separately-named protocol files (new content hashes, e.g. `fitness_formula_s5a_fitness_v2_<hash>.json`) alongside the existing frozen v1 files — never overwrite the v1 files, since they are the historical record this whole series depends on; (4) run a brand-new S5A evolutionary campaign (new `EVOLUTION_SEED`, explicitly different from `2026082701`) against the new fitness v2, still DEVELOPMENT-only; (5) apply §6's diagnostic controls and §7's `signal_delta` logging to every genome in that run, reviewed by Rick before any qualification step; (6) only then, with a genuinely fresh (never-before-observed) blind window, run the qualification stage described in §10.
**G. Falsification criteria** — stated in advance, before any new campaign runs: the redesigned process should be considered to still fail to generalize if, when its own frozen top-10 (from a fresh `EVOLUTION_SEED`) is evaluated on a genuinely fresh blind window (§9's contamination standard), (i) a majority of the 10 fail the same catastrophic-floor test used to select them in DEVELOPMENT, or (ii) the population's aggregate `signal_delta` against exposure-matched passive controls is not detectably different from zero (i.e. cross-regime fitness selected for "smaller/safer" genomes without any accompanying signal edge, the same disguised-sizing story the exposure-isolation experiment already ruled out for D1's *DEV* result but which could recur under a new formula if §7's diagnostic is ignored rather than acted on), or (iii) the specific failure mode changes (e.g. instead of losing on Sharpe like D1 did, the new population loses primarily on turnover/cost, suggesting the fold-robustness pressure induced overtrading as a new artifact). Any of these three would indicate the fold/fitness redesign addressed DEV's *symptom* (one-path specialization) without addressing the deeper *cause* (fitness never being checked against a real baseline), and would argue for revisiting §6's diagnostic-only stance on controls rather than another fold-count adjustment.

---

## Hard boundary compliance statement

No source file was modified. No evaluator was modified. The dividend defect was inspected and characterized (§12) but not repaired. No genome was created, mutated, or reproduced. No simulation or backtest was executed. E/F/G lineages were not touched. No promotion or demotion occurred — the champion remains frozen and D1 remains an experimental subject throughout this document, never referred to as a succession candidate. NODE was not contacted. The Gen0-bootstrap dirty files (`scripts/s6b_primary_development.py`, `tests/test_s6b_primary_development.py`, `scripts/s6b_resume_executor.py`) were not read or touched. Nothing was committed or pushed. No broker/live/paper activity occurred.
