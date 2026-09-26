# Fitness V2 warm-up erratum and resolution status

Authority: Rick, 2026-09-26. This visible status receipt supplements, and does not
rewrite or remove, the pre-result receipts at `9ff6582c9798e9fad70d5e3cf666238c04cf2e78`
and `008b39ad9197b02cdf629e5818a1c0a2b94a7a06`.

**Execution: resolved by Rick.**

> Execution worlds use the same available real historical warm-up as their matching historical anchor under frozen S5A insufficient-history eligibility semantics, even when fewer than 378 synchronized bars exist. The exact 378 generated-bar requirement remains unchanged for Distributional, Sequence, and Shock worlds. No missing Execution-family warm-up bars may be fabricated, interpolated, backfilled, or borrowed from protected/post-2018 data; assets lacking sufficient history remain ineligible until the frozen S5A lookback requirements are naturally satisfied.

The prior H1 stop was correct under the then-unspecified Execution rule and is
now resolved for Execution. The existing Execution-only distance exemption also
remains valid. Neither resolution weakens other structural checks.

**Shock: unresolved, stop before results.** See
`FITNESS_V2_SHOCK_WARMUP_CONFLICT_20260926.md` for the exact conflict and minimal
decision. Distributional, Sequence, and Shock retain the exact 378 generated-bar
requirement. No new executable protocol has been frozen.

At Rick's explicit request, `scripts/fitness_v2_admission.py` is restored to its
committed form. Its old module-level H1-blocker description, and readiness STOP
strings in the preserved `008b39a` evidence, describe the historical checkpoint;
they do not override Rick's accepted Execution resolution. This receipt is the
current resolution status. Overall readiness remains stopped on Shock, not
Execution.

Conservation validation for this receipt commit: focused admission tests and
static comparisons only; no production generation, full training run, worker
launch, or acceptance attempt. Results:

- `python3 -m unittest discover -s tests -p 'test_fitness_v2_admission.py' -v`:
  7/7 passed (0.209 seconds), including existing-source coverage/isolation.
- `git diff --check`: passed.
- Byte comparison against HEAD: admission module exactly restored.
- Draft-path absence and ancestor checks for both preserved commits: passed.
- No tracked-file differences before adding these two new receipts; historical
  code, protocols, and evidence unchanged.
