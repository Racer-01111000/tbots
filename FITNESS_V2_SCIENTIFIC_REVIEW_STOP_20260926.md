# Fitness V2 pre-result scientific review and conservation stop

Authority: Rick's explicit review instruction following FINAL FORMULA DEFINITIONS.
Reviewed checkpoint: `02bc2ad0e3624bad28096c7b9541dd1a520fc246` on
`feat/node-resident-fitness-v2-autonomous-evolution-20260925`.
Only the untracked `scripts/fitness_v2_protocol.py` draft existed at review start.
It had not been committed or used for production construction. The draft is
removed rather than preserved as a falsely complete executable protocol.

## Authority and precedence

The parameter freeze and all later stop/resolution receipts through `02bc2ad`
were reread, alongside the exact subsequent instructions in this thread.
Chronological, explicit Rick amendments supersede conflicting earlier rules;
receipts are evidence of their checkpoint, not authority to undo later decisions.

| Source | Governing effect now |
|---|---|
| `FITNESS_V2_PARAMETER_FREEZE_20260925.md`, its receipt and content-addressed manifest `47aba93e…` | Preserve seeds, counts, rotation, comparator, threshold, five campaigns, expansion and immutable historical evidence. Original January source metadata is historical, superseded below. |
| Original seven-item EXECUTABLE-PROTOCOL GO in this thread | Exact H1/H2/H3 anniversary shapes; S5A episodes; transformation constraints; descriptor families; seed replacement; S5A-derived evolution; fixed19 rank1 admission; passive OC1. It delegates only mechanical non-selection choices. |
| Execution distance resolution, preserved at `008b39a` | Execution-only distance exemption; no exemption for other structural checks or families. |
| Execution warm-up GO; Shock warm-up GO, preserved by amendment `3053c6c4…` at `02bc2ad` | Both use matching real-anchor available history under frozen S5A eligibility. No missing-history fabrication. Distributional/Sequence still require378 generated same-stream bars. Shock has no whole-path generator. |
| Shock/Sequence clarifications in this thread, including the stop preserved at `69fa48d` | Shock only three authorized gap relocations, no same-asset donor restriction. Sequence distinct start-index segments, no disjoint fixed-grid restriction. |
| FINAL FORMULA DEFINITIONS in this thread | Exact log-price Pearson trend and signed OC1 zero-MAD sentinels. The stop at `02bc2ad` is resolved for those two definitions. |
| Latest scientific-review instruction in this thread | Generation/calibration exclusively `2007-02-07..2018-12-31`. January1–February6 and earlier permitted real-anchor warm-up cannot enter transformation or calibration sources. |

Historical receipts reread and preserved: implementation and parameter-freeze
receipts; executable-ambiguity, Execution-diversity, H1-warmup, Shock-warmup and
formula-definition stops; both warm-up resolution/status receipts. None is
rewritten. The older `fitness_v2.validate_calibration_source` validates the old
freeze's January metadata; it is not sufficient authorization for a new builder.
New scoped `validate_development_source` rejects earlier dates and all post-2018
dates rather than silently trimming a mixed source. No builder is enabled.

## Draft convention audit

“Unresolved” means no authoritative rule fixing that choice was found in the
reviewed sources. Existing S5A strategy/fitness semantics do not automatically
define new ex-ante world descriptors. Nothing in this table adopts an unresolved
choice. The draft's declarative strings were not implementations of all these
checks and did not constitute a completed protocol.

| Draft detail | Authority or selection assessment | Disposition |
|---|---|---|
| 2007-01-01 generation/calibration source | Contradicts latest explicit boundary. Changes donor/reference/index pools. | Corrected to February7 in new source guard; old frozen metadata retained only as evidence. |
| Eight synchronized assets; fixed H shapes; S5A episode resets | Explicit original GO item1 and item2; episode manifest `s5a_episode_c8d370…`. | Authorized. |
| Exactly378 generated bars for Distributional/Sequence; available real history for Execution/Shock | Explicit original GO plus later exceptions. | Authorized; unchanged. |
| Sample rather than population stdev for world volatility | `scripts/indicators.py` specifies sample variance for strategy volatility; S5A fitness manifest specifies sample variance for Sharpe. Neither assigns it to world descriptors. Varying reference/world horizon makes the distinction non-cancelling. | Unresolved; threshold-crossing proof below. |
| Sample rather than population cross-sectional dispersion | With exactly8 assets at every observation, the factor is the same `sqrt(8/7)` everywhere; positive common scaling cancels median/MAD z normalization, including equality/zero-MAD cases in exact arithmetic. | Outcome-neutral for this descriptor distance only, under fixed8/no missing cells. No broader estimator rule adopted. |
| Volatility annualization by sqrt252 | Fixed positive common scaling cancels in componentwise median/MAD z distances; same factor also cancels in a like-for-like volatility ratio. S5A sqrt252 is expressly authoritative for Sharpe, not automatic authority for descriptors. | Outcome-neutral only when applied identically throughout; not grounds to choose variance/return conventions. |
| Adjusted simple returns for all non-trend descriptors | Frozen strategy indicators use adjusted closes (`scripts/indicators.py`), but new GO explicitly names adjusted close for trend only. Raw, adjusted, simple and log returns can change other descriptors. | Unresolved mapping for remaining descriptors. |
| Equal-weight portfolio as daily-rebalanced mean returns compounded from1 | “Equal-weight portfolio” does not explicitly choose daily rebalancing versus buy-once/drifting weights for descriptors. The passive comparator's buy-once rule governs a different object. | Unresolved; changes vol/drawdown/duration. |
| Rolling correlation:63 returns, flatten all pairs/windows, zero variance=>0 | GO specifies median rolling63 off-diagonal correlation. Window prices versus returns, nested versus flat medians and undefined-pair treatment are not made executable. Latest constant-series zero is specific to trend. | Unresolved; zero/omit/reject are not interchangeable. Symmetric duplicate pairs alone are neutral under equal multiplicity. |
| Drawdown as compounded path with initial1; longest underwater count divided by price-session count | GO specifies max equal-weight drawdown depth plus normalized duration, not the portfolio convention, duration statistic, recovery endpoint or denominator. | Unresolved; duration/world-length alternatives are not a common affine scaling across252 references and multi-year worlds. |
| Leader momentum as adjusted close ratio over63 sessions | S5A indicator formula supplies a related strategy calculation; latest descriptor text supplies horizon and leader-change frequency but does not explicitly adopt every S5A convention for the descriptor. | Requires explicit applicability for descriptor inputs; not silently borrowed. |
| Lexicographic symbol tie-break and changes/leader transitions | S5A tie-break governs strategy selection. Turnover-opportunity descriptor tie handling can create/remove leader changes; dividing by sessions versus transitions is length-dependent. | Unresolved descriptor tie and denominator rules. |
| Gap `open[t]/close[t-1]-1`, pooled threshold, linear quantile interpolation | “Overnight gap”/“DEVELOPMENT95th percentile absolute-gap threshold” do not specify simple versus log/absolute-price measure, pooling or quantile estimator. For values1..20, nearest-rank95th=19 while linear interpolation=19.05; an observation19.025 crosses one only. | Unresolved; affects threshold, frequency, magnitude and donor relocation interpretation. |
| Gap frequency over all asset/session gaps, magnitude0 if no exceedances | GO names frequency and median magnitude; denominator, missing/invalid observations and empty exceedance treatment not executable. Median of an empty set is not automatically0. | Unresolved. |
| Every rolling252 window stride1 | “Rolling252-session” naturally includes every complete window; no subsampling authorized. | No thinning permitted. First-window return availability still depends on the source/boundary rule and must not import warm-up. |
| Median/MAD z, zero-MAD equality, RMS distance .75 | Explicit original GO item3; Execution exemption explicit later. | Authorized. Historical anchors remain immutable; any zero-MAD nonmedian anchor comparison needs a defined distance treatment rather than inventing a finite/infinite sentinel. |
| Exact latest trend and OC1 | FINAL FORMULA DEFINITIONS explicitly supplies formulas, exceptions and threshold. | Implemented/tested in a scoped module, without extending trend's zero-variance rule to other correlations. |
| Bootstrap common index, geometric mean21, boundary redraw/no wrap, final truncation | Original GO item2. | Authorized. No additional transformation may be introduced. |
| Multiplicative reconstruction, OHLC geometry, observation-linked yield-scaled dividends, no separate splits | Original GO explicitly requires these constraints for Distributional. It does not give an initial price level or a full adjusted-close reconstruction convention for every family. | Required constraints retained; missing level/adjustment initialization not invented. |
| Initial reconstructed price = first selected source predecessor close | No such base level was specified. At a fixed100-dollar target, a60-dollar price buys1 share; a120-dollar price buys0. Integer shares, cents, costs and eligibility/activity thresholds are not scale invariant. | Unresolved; not a harmless coordinate convention. |
| Sequence all complete starts, overlap allowed, sorted starts, no replacement,63 intact rows, final truncation | Explicit Rick clarification distinguishes segments by start index and rejects fixed-grid disjointness. | Authorized membership by distinct starts; no new overlap prohibition. |
| Sequence implementation `range(1,size-62)` | Draft silently excludes source start0 to accommodate a preceding-price reconstruction. For size64, complete raw63-session starts are0 and1, but draft permits only1. | Unauthorized restriction removed. Need edge/reconstruction rule that respects eligible segments without pre-February7 price imports. |
| Sequence ratio reconstruction instead of directly concatenated intact observations | “Intact segments” plus ordering is frozen, but how any segment boundary price-level adjustment interacts with intactness is not explicitly supplied by the Distributional reconstruction instruction. | Unresolved; do not quietly apply another family's reconstruction rule. |
| Shock anchor base, three distinct destinations/donors without replacement, no same-asset restriction | Exact Rick instructions. | Authorized; whole-path generators forbidden. |
| Shock simple-return gap measure; multiplying only selected day's OHLC/adjusted close/dividend, other rows unchanged | Preserving intraday geometry is required, but the measure of that geometry and handling of adjusted closes/dividends/subsequent implied gap are not made explicit. Draft additionally changes dividend amounts under a rule given to Distributional, not Shock. | Unresolved; no scalar transform or cash-action change adopted. |
| Shock whole-world extrema check including next-day implied gaps | GO says never exceed DEVELOPMENT extrema, but exact gap measure/pool is needed first. Altering destination close while retaining next day's open changes a second overnight gap. | Must be reconciled with exactly3 relocated events; no silent propagation/extra-price changes. |
| Malformed/nonpositive/uninterpretable corporate-action donor exclusion | Explicit original GO. | Authorized exclusion; implementation must use frozen action semantics, not broaden donor pool. |
| Replacement seeds, fixed five seeds,50 population,0..10, mutation bounds, control+9 elites, retirement, rotation | Original GO item4/5 explicitly adopts S5A mechanics; S5A population manifest defines control+9 policy. | Authorized; no training code retained from this unreviewed draft. |
| Fixed19, rank1 only, no sibling shopping,3of5, lowest seed | Original GO item6 plus original freeze. Generation10 final selection follows adopted S5A final-freeze rule. | Authorized; no admission run. |
| Canonical JSON/filenames/order | Serialization is delegated; identical vectors under a common component permutation have identical RMS distance. | Mechanical only when preserving values/membership. Changing random candidate pools or tie outcomes is not justified by “loop order.” |

Primary supporting code read: `scripts/indicators.py`, `scripts/execution.py`,
`scripts/s5a_evaluator.py`, frozen S5A fitness/population protocols. Governing
documents read: `experiments/world_league_design_20260923T063845Z/DESIGN.md`,
`experiments/world_league_fitness_correction_20260923T071111Z/CORRECTED_CONTRACT.md`,
`experiments/fitness_v2_prefreeze_errata_20260923T084553Z/FINAL_CONTRACT.md`.
The design's older2007–2026 extrema text cannot authorize protected observations
against later DEVELOPMENT-only rules. No such observations were accessed.

## Minimal decisive proof and required decision

Even with all other choices held fixed, the variance-estimator omission prevents
a truthful complete-protocol freeze. This descriptor-only algebra uses no market
world or genome: reference sample volatilities `[.01,.02,.03,.04,.05]`, each
from251 returns, give median `.03`, MAD `.01`. Anchor sample volatility is `.03`
and candidate is `.04414`, each from1000 returns. Put the same difference in all9
volatility components and hold the other23 components equal.

- Sample-stdev distance: `sqrt(9/32)*1.414 = 0.7498867414483336` — reject.
- Population stdev scales references by `sqrt(250/251)` and both multi-year
  worlds by `sqrt(999/1000)`. The anchor and candidate medians cancel in their
  difference. Distance becomes `0.7510092316706231` — admit.

Therefore the variance convention is outcome-material despite robust scaling.
The minimum decision for this demonstrated conflict is an explicit world-
descriptor variance estimator, including its observation-count convention.
For a complete executable protocol, Rick must also freeze the unresolved
descriptor and reconstruction details listed above (or explicitly designate an
existing exact implementation as their authority). No proposed defaults are
adopted. This audit consolidates them instead of concealing further choices.

## Durable gates and conservation

1. Checkpoint and history: `02bc2ad` verified; all earlier receipts remain unchanged.
2. Prior formula stop: resolved by Rick. Exact trend/OC1 and February7 source
   guard are preserved in `scripts/fitness_v2_formula_definitions.py` with a
   content-addressed scoped amendment; it explicitly sets complete readiness false.
3. Scientific completeness: **FAILED CLOSED** on the proof above and unresolved
   audit rows. `scripts/fitness_v2_protocol.py` removed before commit/results.
4. Validation: focused formula/source/conservation tests and full regression
   results are recorded in `experiments/fitness_v2_scientific_review_20260926/VALIDATION.json`.
5. Production generation/training/acceptance: **NOT STARTED**. No production world,
   genome, performance evaluation or worker was created. No formula choice in
   the proof is a selected protocol setting. V1/S5D/S6B and protected data untouched.

Only the scoped authorized definitions, tests, review receipt and validation
evidence may be preserved by this review commit. It is not the full protocol gate.
