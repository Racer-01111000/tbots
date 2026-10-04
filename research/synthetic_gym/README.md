# TBOTS synthetic market gym (research branch `research/synthetic-market-gym-20261004`)

Hypothetical five-year market worlds, a bounded evolutionary search over price-driven genomes, and frozen scoring. **Research only**: it has no broker
code, no order endpoint, and its outputs cannot be imported by the paper runner. A winning genome here is at most a `RESEARCH_CANDIDATE`; it does not
replace Kim, change admission, or become a paper/live trader. Forward Alpaca shadow evidence remains necessary.

* `configs/experiment_v1.json` frozen experiment (objective, eligibility, splits, budgets); `configs/generator_v1.json` generator assumptions;
  `configs/FROZEN_HASHES.json` code hashes recorded before the run; `sources/SOURCE_MANIFEST.json` economic sources (all three unverified).
* `gym/` generator, world model, engine, scoring, evolution, checkpoints, news interface (`events.py`), campaign driver.
* `docs/GENERATOR_VALIDATION.md` what is calibrated vs assumed and every realism gap; `docs/pilot_semantics_receipts.json` mock-broker evidence for the
  repaired execution semantics; `results/` campaign outputs (small; bulky regenerated price data is NOT committed).
* Run (offline, one worker): `python -m gym.campaign --out DIR [--smoke] [--resume]`; tests: `python -m pytest tests`.
* `run_isolated_ec2.sh` shows the sandbox used on the research instance (dedicated user, no network, 1 vCPU, memory cap, no credential access).
