# gen-v2: known generator defects, repairs attempted, and what remains

gen-v2 is a parameterised COPY of gen-v1 (`gym/generator_v2.py`, `configs/generator_v2.json`); every gen-v1 file, hash and result is untouched. Validation: `scripts/validate_generator_v2.py` on 120 fresh engineering worlds (`ENG-V2FROZEN`, indices 600-619 x 6 families; disjoint from every training/validation/sealed seed) -> `docs/generator_v2_validation_report.json`.

| # | v1 defect (GENERATOR_VALIDATION.md section 6) | Action | Result |
|---|---|---|---|
| R1 | median SPY 5-year max drawdown -0.341, outside the declared band bound -0.33; the negative-jump sign bias (70% down) over-weighted crashes | `jump_neg_prob` 0.70 -> 0.55 | **repaired**: tail checks 11/11 (v1: 10/11) |
| R2 | daily-return excess kurtosis about half of history (SPY ~6 vs 13.6, EEM ~4 vs 15.0) | probed `tail_cap` 1.5 -> 2.0 (never binding, zero effect), larger jump clamp, widening the volatility-state clamp x1.25 / x1.6 | **NOT repaired**: best case +1.0 kurtosis; the thinning comes from per-pool unit-variance normalisation and the path-speed guard, which need a structural redesign |
| R3 | equity skew too negative (SPY -0.5 vs -0.13), EEM skew sign fails (the only failing plausibility statistic, 86/87 in both versions) | none reachable by parameters (jump sign probe moved skew by < 0.05) | **NOT repaired**, carried forward |
| R4 | all event/asset relationships are assumptions; blunt guards (daily cap, path-speed guard, anchor) | unchanged | documented gap |

Method note: the repair target (R1) is a criterion declared before v1 tuning; R2/R3 probes were exploratory, run on separate engineering tags (`ENG-V2PROBE`), and only R1 was adopted. No training, validation or sealed world was used to tune the generator.
