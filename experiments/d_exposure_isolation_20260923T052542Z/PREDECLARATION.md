# D-Lineage Exposure-Isolation Experiment — Predeclaration

Written BEFORE any new simulation code is executed or any exposure-matched result is inspected, per GO "D-LINEAGE EXPOSURE-ISOLATION EXPERIMENT" (2026-09-23).

## Frozen subjects

**D_primary_rank1** (frozen, not rerun in this experiment — its own signal logic and DEV results are reused verbatim):
- `genome_id`: `gen_8e7a622b48276bc68b7b70773bb9e2a5250dbb82584c53f6c67416bd6ffdf0cf`
- `lineage`: D, `development_rank`: 1, `original_fitness`: 0.548480762518
- Source file: `evolution/s6b_runs/primary/D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca/development_top_eight.json`
  sha256 `373879a9842f05385d41c67fab5a6208c7d0d7c5cbab323363173a04bd2c60e4`
- Genome config: `allocation_rule=equal_weight_rotation, strategy_family=defensive_rotation, signal_model=risk_regime_drawdown_breadth, selection_count=3, rebalance_sessions=31, regime_window=122, risk_off_drawdown=0.08, breadth_threshold=0.35, max_asset_weight=0.47, gross_exposure_cap=0.23, direction=long_only, shorting=none, leverage=none, universe=[SPY,EFA,EEM,IEF,TLT,GLD,DBC,VNQ]`
- Already-recorded 12-episode DEV results: `experiments/controls_comparison_20260921T064013Z/results.json`, key `D_primary_rank1`
  results.json sha256 `bfbee4dac750cfcaea3a21a72e1f320721fb406e232397729a98b52fa19707ed`

**PASSIVE_ENVELOPE_CONTROL** (original, 80% exposure — retained as reference only, NOT rerun, NOT rewritten):
- Implementation: `experiments/controls_interpretation_holdout_20260921T140000Z/holdout_controls.py` (sha256 `391ab66b1c8c25741d971fc04e0a8fa3c5e4098ee101e305731c708dde57abbc`)
- Already-recorded 12-episode DEV results: `experiments/passive_envelope_dev_gap_20260923T045250Z/passive_envelope_dev_results.json` (sha256 `0e3435494581f07237e0ca0d4e0cb193b9b5e8713bcf20d449d911339296d673`)

**Dataset / evaluator identity (unmodified, verified by hash before use):**
- Dataset revision: read from `bundle.dataset_revision` at load time (expected `ds_7e16896c873671fe86ac416b24a0ce74502249a8a0fc33603e0f1935e5fab131`, matching every prior GO in this series)
- `scripts/execution.py` sha256 `3de725fcf5b29788e140a6b999e8a4bcb7d83c30f54c6c629c5c84b9a4ba0c14`
- `scripts/risk.py` sha256 `526d2f7923b55fa7b6a27cc09548b575c4cbe88cec2c64c72d6004f24d72cf9c`
- `scripts/s6b_evaluator.py` sha256 `d306f6236149c6445e8ca21ccaae4d03339708284db547f8179d533fd9fae048`
- `scripts/s6a_runtime.py` sha256 `8e76e14138152037e429886f8a5997f38f8a64ccc7527f1eb6a23f1dbb1ab1b8`
- `scripts/s6a_final.py` sha256 `1db538092853d232ca8bd4ec4a70e291de7ddf147a37683023bc5b8cc19d98e4`
- `scripts/s5a_config.py` sha256 `18872624b4c189c0833f5539e7ed4ce217475bfbd952c3faafa01c7bc514f257`
- `scripts/lib/replay.py` sha256 `7b50c5d31b191a19c230c72e87d47e9ecf3db9a2bda759a42a975c81d2289b08`
- tbots HEAD: `9e573323501684ef8adfcbfbe357aa314cfdbdb4`, branch `repair/self-contained-canonical-20260901`

None of the above files are modified by this experiment.

## Binding exposure cap — read from the genome, not assumed

Per `s6b_evaluator.risk_bounds(code, genome)` (scripts/s6b_evaluator.py:50-59): for D genomes, `max_total_exposure = genome["gross_exposure_cap"]` and `max_asset_weight = genome["max_asset_weight"]` directly. D_primary_rank1's `gross_exposure_cap = 0.23`, `max_asset_weight = 0.47`. With `selection_count=3` (at most 3 simultaneous positions) and `allocation_rule=equal_weight_rotation`, an equal split of the 0.23 total across up to 3 positions gives ~0.0767 per position — well under the 0.47 per-asset cap, so **`gross_exposure_cap=0.23` is the binding ceiling**, confirming (not merely assuming) the ~23% figure informally traced in the prior lineage-decomposition GO.

**Target exposure for the isolation control: 0.23 (23%).**

## Predeclared normalization method

**PASSIVE_ENVELOPE_EXPOSURE_MATCHED**: identical logic to the existing `PASSIVE_ENVELOPE_CONTROL` (equal-weight allocation across the same 8-asset universe, single order batch at step 0, no rebalancing thereafter, no data read, no RNG, same T+1 fill / commission / slippage / drawdown-halt conventions via the unmodified frozen evaluator) with ONLY the sizing parameters changed:
- `max_total_exposure = 0.23` (was 0.80) — set equal to D_primary_rank1's own frozen `gross_exposure_cap`, not tuned.
- Per-asset weight = `0.23 / 8 = 0.02875` (was `0.80 / 8 = 0.10`).
- `max_asset_weight = 0.35` unchanged from the original control (not binding at 0.02875/asset either way).

No change to D's signal-generation logic. No optimization of the control against observed outcomes — the target value (0.23) is fixed entirely by D's already-frozen, already-recorded genome field, read before this control's performance is inspected.

### Alternatives considered and rejected

**(a) Scale D's exposure UP to match PASSIVE's 80%.** Rejected — would require modifying D_primary_rank1's frozen genome/position-sizing, violating both the freeze on D and the GO's explicit "do not modify D's signal-generation logic" constraint.

**(b) Post-hoc linear rescaling of the existing 80%-exposure PASSIVE_ENVELOPE_CONTROL return series down to 23%, without rerunning the simulator.** Rejected — mathematically invalid here. The prior DEV-gap experiment (`experiments/passive_envelope_dev_gap_20260923T045250Z/`) established that PASSIVE_ENVELOPE_CONTROL was halted in 2/12 episodes (the 2008-09 and 2015-16 stress windows) specifically because of its 80% exposure crossing the 12%-portfolio drawdown threshold. A linearly-scaled-down return series computed from the 80%-exposure equity curve would still embed those two halt events and their post-halt cash-lock behavior, which a genuinely smaller position would very plausibly have avoided (lower exposure → smaller drawdown magnitude → halt threshold may never be crossed). Only rerunning the simulator with the smaller weights, through the same halt-check logic, produces a valid counterfactual.

**(c) Match D's specific position-count/rotation pattern (3-of-8 rotating selection) instead of just total dollar exposure.** Rejected — this would introduce a new strategy design choice (which subset, which rotation cadence) beyond pure size-scaling, moving toward a second bespoke "informed-ish" strategy rather than the simplest exposure-only isolation. The GO explicitly asks for "the smallest valid control" and warns against "introducing a new optimized strategy." Equal-weight-across-the-full-universe-at-reduced-size (the original PASSIVE_ENVELOPE_CONTROL's own design, just re-scaled) is the simplest method that isolates total exposure level without adding any selection logic.

**Method chosen: scale existing PASSIVE_ENVELOPE_CONTROL's exposure parameters only, to D_primary_rank1's frozen `gross_exposure_cap = 0.23`.**
