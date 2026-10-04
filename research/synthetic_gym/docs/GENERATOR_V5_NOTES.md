# Experiment V5 (WORK IN PROGRESS, NOT FROZEN, NOT DEPLOYED, NO BATCH RUN)

Generator admission PASSED once on never-generated development worlds (tag ENG-V5FROZEN): `configs/ADMISSION_V5.json`, written by `scripts/validate_generator_v5.py` from `docs/generator_v5_validation_ENG-V5FROZEN.json` (84/84 length-matched statistics, 11/11 tail bands with SPY 5y max-DD median -0.306, structure 0 violations, family exercise 95-100%, stress effective). Do NOT rerun that tag to "improve" the result; it is the single confirmation.

## Disclosure
gen-v4 parameters (recession anchor min severity 0.9; SPY/VNQ event scales 0.70/0.75; VNQ variance 0.35; GLD asymmetric jump loading 0.80 down / 0.20 up) were SELECTED by probe grids against the gate statistics (ENG-V4B, ENG-V5PROBE-GRID) after V4 failed four gates. Thresholds, tolerances, targets and bands were not changed and the confirmation used never-generated worlds, but this is tuning-to-gates followed by held-out confirmation, not independent evidence. Event-to-asset scales are declared assumptions. Design compliance only.

## Review findings fixed in batch_v5 (tests in tests/test_batch_v5.py, 15 pass)
frozen manifest mandatory (absence refuses); resume cannot extend the frozen generation target; validation only on the 2026-10-09 slot; validation exposure recorded with nominee+checkpoint identity and a no-reselection recovery rule; terminal publication/status reconcilable without re-evaluation; world construction persists in-progress seed and honours SIGTERM; status totals from durable receipts.

## Remaining before any batch (resume checklist)
1. Run the full synthetic_gym suite (baseline was 115 passed on the V4 code) plus food_fuel tests under the service interpreter.
2. Write `scripts/freeze_hashes_v5.py` (records code_hashes() incl. ADMISSION_V5 + report; refuses if admission did not pass), run it, commit the freeze BEFORE any score.
3. Push to the research branch, verify remote SHA, deploy the pinned SHA to the instance (SSM relay + deploy_release.sh), update units: research service -> `gym.batch_v5 --state /var/lib/tbots-foodfuel-research-v5`, verify-research root -> same dir, create the dir (tbotsgym), keep research timer DISABLED until the immediate batch is done.
4. Immediate batch: `--slot 2026-10-05 --gens 5` (consumes Monday's slot; the scheduled trigger then skips), under the service sandbox; then re-arm the research timer only for slots 06-09 (Friday final = validation, once).
5. Report with the distinct statuses from the handoff GO. Never describe results as a qualified strategy.
