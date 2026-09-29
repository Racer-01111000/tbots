# Fitness V2 Shock market-distance exemption

Authority: Rick, 2026-09-29 (live session instruction to Claude Code on NODE).

## What changed

`scripts/fitness_v2_admission.py`'s `market_distance_passes()` now exempts the
**Shock** family from the `>=0.75` normalized-descriptor distance threshold,
in addition to the pre-existing Execution exemption. Unlike Execution, Shock
is exempt from the threshold **only**: it still runs the full structural
validation (non-empty candidate/references, matching vector length, no
`bool`/non-numeric/non-finite values) before the exemption is applied. This
mirrors the schema already declared in `fitness_v2_warmup.py`'s
`WARMUP_AMENDMENT`, where Execution and Shock are documented as distinct
cases (`market_distance_exempt: True` vs `False` respectively), except this
change flips Shock's live behavior without editing that declared dict — see
"Declarative/live divergence" below.

Distributional and Sequence are unchanged: both still require full structural
validation and the exact `>=0.75` threshold against every supplied reference.
No seed, admission ordering, ranking, or fitness formula was touched.

Shock's generator/base path (`fitness_v2_world_bank.py`'s Shock construction:
exactly three authorized DEVELOPMENT donor-gap relocations into three distinct
eligible destinations, preserving destination intraday geometry) is
unmodified.

## Declarative/live divergence (deliberate, flagged for Rick)

`fitness_v2_warmup.py`'s `WARMUP_AMENDMENT["shock"]["market_distance_exempt"]`
still reads `False`. That dict is content-hashed
(`AMENDMENT_ID = content_identity(...)`), a persisted envelope exists at
`evolution/protocol/fitness_v2_real_warmup_amendment_<AMENDMENT_ID>.json`, and
`AMENDMENT_ID` is itself embedded in `fitness_v2_protocol.py`'s
`build_complete_protocol()` → `complete_protocol_manifest()`, which is in turn
validated against the frozen `evolution/protocol/fitness_v2_complete_protocol_
49c71da1....json` (matching `STATUS.json`'s `protocol_manifest`). Editing the
dict would change `AMENDMENT_ID`, invalidate that chain, and could fail-closed
the worker at startup (`validate_amendment`/`validate_complete_protocol`
raise on any mismatch). The dict was therefore left untouched; the exemption
lives only in `fitness_v2_admission.py`'s `market_distance_passes()`, which is
not part of any content-hashed envelope. `tests/test_fitness_v2_warmup.py`'s
`test_content_address_and_no_readiness_claim` still asserts that flipping the
dict's `shock.market_distance_exempt` to `True` breaks validation — that
assertion remains true and intentional. If Rick wants the declarative record
to match the live predicate, that requires a separate, explicit re-freeze of
the warm-up amendment (new `AMENDMENT_ID`, new persisted envelope, and
re-verification of everything downstream that depends on it) — out of scope
here.

## This does NOT resolve the Shock warm-up bar conflict

`FITNESS_V2_SHOCK_WARMUP_CONFLICT_20260926.md` remains open and unresolved.
That conflict is about Shock's **378 generated warm-up bars** requirement
(H1 real history only provides 252 synchronized prior bars, and Shock — unlike
Execution — has no available-real-history exception, no base-path/generator
for the missing warm-up, per that receipt). This change addresses a
completely separate admission gate (post-warm-up market-distance diversity
check) and has no bearing on warm-up bar sourcing/feasibility. A Shock
candidate that clears this distance exemption can still be structurally
infeasible or blocked by the unresolved warm-up conflict. Rick's minimal
decision from that conflict doc — specify Shock's 378-bar base
path/construction, or explicitly extend it the same available-real-history
exception as Execution — is still outstanding.

## Authorization note

Unlike the Execution resolution (`FITNESS_V2_WARMUP_RESOLUTION_STATUS_20260926.md`,
`FITNESS_V2_SHOCK_WARMUP_CONFLICT_20260926.md`), this repo has no prior commit
or receipt recording a decision to exempt Shock from the market-distance
threshold specifically. This change is made on Rick's direct first-person
instruction in the live 2026-09-29 session (not a pasted/forwarded directive),
after independently confirming: HEAD/local==remote==clean at `12b6ddb`, burn-in
PID 1064391 alive, `tbots-fitness-v2.service`/`.timer` both disabled, and
`STATUS.json`/`evolution/state` showing no completed world-bank, campaign, or
generation (still pre-campaign) — i.e. no in-flight or completed production
result exists that this change could retroactively affect.

## Verification

- Focused: `python3 -m unittest discover -s tests -p 'test_fitness_v2_admission.py' -v` — 8/8 passed.
- Focused: `python3 -m unittest discover -s tests -p 'test_fitness_v2_warmup.py' -v` — 8/8 passed.
- Focused: `python3 -m unittest discover -s tests -p 'test_fitness_v2_world_bank.py' -v` — 17/17 passed.
- Focused: `python3 -m unittest discover -s tests -p 'test_fitness_v2_protocol.py' -v` — 79/79 passed.
- Full suite: `python3 -m unittest discover -s tests` — see commit for result count (run before commit/push).
- New discriminating tests added: `test_fitness_v2_admission.py::test_shock_distance_threshold_exemption`
  (shock always passes distance regardless of threshold, still requires valid
  descriptors per the existing `test_invalid_descriptors_fail_closed_for_market_families`);
  `test_fitness_v2_admission.py::test_other_families_keep_exact_threshold` and
  `test_all_references_remain_binding` narrowed to `(distributional, sequence)`
  only, so a future regression that leaks the exemption into those families
  would be caught; `test_fitness_v2_warmup.py::
  test_shock_warmup_exception_now_also_carries_a_distance_exemption` updated
  (was `..._does_not_exempt_distance`, asserted the opposite pre-2026-09-29).

No world, genome, campaign, or performance result was generated by this work.
