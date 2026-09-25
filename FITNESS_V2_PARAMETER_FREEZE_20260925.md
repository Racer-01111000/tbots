# GO ADDENDUM — FREEZE FITNESS V2 / WORLD LEAGUE PARAMETERS

Authority: Rick

Received and frozen before any new Fitness V2 baseline, world generation, or genome evaluation.

Recorded UTC: `2026-09-25T11:56:32+00:00`

Pre-freeze repository HEAD: `622cbb5ceaf93f60da4d2e76a3d42cbc97bc7d82`

These values are frozen before any new baseline population or genome results are observed. Do not tune them based on performance.

1. World bank: 3 fixed historical DEVELOPMENT worlds + 16 synthetic worlds, four each in Distributional, Execution, Sequence, and Shock.
   Distributional: synchronized stationary/block bootstrap across all 8 assets, geometric block length with mean 21 sessions.
   Execution: deterministic seeded skipped-fill stress, 5% skip probability, maximum one skipped session per affected fill.
   Sequence: chronology-preserving recombination of intact 63-session segments, never reordering observations within a segment.
   Shock: controlled-gap relocation using only DEVELOPMENT 2007–2018 empirical gaps, 3 relocated gap events per world, preserving sampled sign/magnitude and never exceeding DEVELOPMENT-observed bounds.
2. Synthetic seeds:
   Distributional: 2232522471, 817777979, 4071970222, 1669371075
   Execution: 2417722352, 133793614, 607285341, 780370636
   Sequence: 3101765671, 2463827558, 1949083376, 1649363392
   Shock: 2470494651, 490895778, 2160267573, 2145432915
   If a world fails structural/economic/diversity validation, reject it and advance deterministically to the next seed in the same family. Never choose replacements based on bot performance.
3. Training/withhold rule: worlds 1–3 in each family are DEVELOPMENT-training worlds. World 4 in each family is permanently withheld from evolutionary ranking and breeding.
   Every generation evaluates all 3 historical worlds plus exactly 8 synthetic training worlds, two per family.
   Rotation per family:
   Gen mod 3 = 0 → {1,2}
   Gen mod 3 = 1 → {2,3}
   Gen mod 3 = 2 → {3,1}
   Every genome in one generation receives the same subset. The four withheld worlds are evaluated only after a campaign selects its rank-1 nominee. They remain DEVELOPMENT robustness evidence, not independent historical evidence.
4. Exposure-matched passive comparator: use each genome's pre-existing declared gross-exposure cap exactly, equally distributed across the fixed eight-asset universe, buy-once, under the identical simulator/world. Never match realized exposure post-hoc and never optimize the comparator. Keep the existing 80% PASSIVE_ENVELOPE diagnostic-only.
5. World diversity: use the contract's eight ex-ante descriptors, normalized solely from DEVELOPMENT 2007–2018 using robust median/MAD scaling. Distance metric:
   distance = sqrt(mean((z_i - z_j)^2))
   Minimum pairwise distance = 0.75. Worlds failing the threshold are rejected before any strategy evaluation.
6. Campaign batch: freeze N = 5 independent campaigns using:
   2066557696
   604610261
   3608585586
   3251376561
   1894202052
   Admission requires 3 of 5 or more independently selected rank-1 nominees to clear the complete Fitness V2 gate stack. Use the existing predeclared deterministic final-selection rule; never select whichever qualifier looks best.
7. Expansion: do not alter the initial bank during the first N=5 batch. After each complete five-campaign batch, add exactly 4 synthetic worlds, one per family, using the next deterministic seeds and the same validation/diversity gates. Never remove existing worlds. Continue until the synthetic bank reaches 32 worlds, then hold pending a later Rick GO.
8. Continue original mission: after authentication and this freeze are durable, proceed through:
   Fitness V2 validation → clean baseline → complete evolutionary cycle → artifacts → Git preservation → deterministic recovery → zero-approval unattended acceptance → continuous autonomous evolution.
   The historical S5D champion remains immutable. Real-money/live brokerage remains outside authorization.

GO ADDENDUM — Rick

## Governing references

- `experiments/fitness_v2_prefreeze_errata_20260923T084553Z/FINAL_CONTRACT.md`
- `experiments/world_league_design_20260923T063845Z/DESIGN.md`
- `experiments/world_league_fitness_correction_20260923T071111Z/CORRECTED_CONTRACT.md`

The existing predeclared deterministic final-selection rule is
`lowest_EVOLUTION_SEED_among_qualifiers`, as implemented at pre-freeze revision
`4ac2de7350d75edb4100d136adec691fd18d426a`.
