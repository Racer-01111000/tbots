# Fitness V2 multi-batch expansion/campaign cycle freeze

Supersedes: `FITNESS_V2_EXPANSION_CAMPAIGN_CYCLE_STOP_20260926.md` (not deleted —
retained as the investigation record and provenance for this decision).

Authority: Rick, 2026-09-26 GO ("RESOLVE FINAL PRE-RESULT FITNESS V2 STOPS / AUTHORIZE
NODE ACCEPTANCE"), items 2–3.

## Ruling

Each expansion cycle is one complete five-campaign batch. For every batch:

1. Reuse exactly the five frozen `CAMPAIGN_SEEDS` (`2066557696, 604610261, 3608585586,
   3251376561, 1894202052`) — never generate replacement seeds for later batches.
2. Complete all five campaigns; apply rank-1 admission; apply the frozen 3-of-5 rule.
3. Preserve the batch's own result (champion or negative) as an immutable, batch-specific
   artifact. A later batch's result never discards or overwrites an earlier one. No
   cross-batch "best champion" rule exists — that remains a later scientific-interpretation
   question, out of scope here.
4. A qualifying champion in an earlier batch does not end the program. Add exactly one
   accepted world to each synthetic family (handoff §50) and proceed automatically to the
   next batch — never a HOLD.
5. Continue until the frozen terminal condition (32 synthetic worlds), then terminal HOLD.

Durable state is namespaced `protocol_id -> world_bank_id -> expansion_batch_id ->
campaign_seed/campaign_index -> generation`, so a checkpoint from one world-bank/batch
combination can never satisfy or overwrite one from another. Exact filesystem naming is an
implementation detail; the identity is deterministic, persisted, and tested.

## What changed (`scripts/fitness_v2_worker.py`)

- `_expansion_batch_id(synthetic_world_count)`: batch 0 → 16 worlds, batch 1 → 20, ...,
  batch 4 → 32. Derived from `synthetic_world_count` (already durably persisted in
  `STATUS.json`) rather than kept as a separate field, so there is exactly one source of
  truth for "which batch is this."
- `_generation_checkpoint_path`, `_nominee_checkpoint_path`, `_admission_decision_path` all
  now take `batch_id` and write to `evolution/state/batch{N}_...json` — the world-bank
  identity (`world_bank_id`) is the previous link in the namespace chain, since each
  expansion produces its own uniquely-named bank file.
- `_run_next_campaign`: computes `batch_id` from `status["synthetic_world_count"]` once and
  threads it through every checkpoint path for that invocation. `CAMPAIGN_SEEDS` selection
  (`CAMPAIGN_SEEDS[status["campaigns_complete"]]`) is unchanged and does not depend on
  `batch_id` — only the evidence path does.
- `_finalize_or_expand`: reads this batch's own nominee files (batch-namespaced) and writes
  this batch's own admission decision (batch-namespaced) — an earlier batch's decision file
  is never touched.
- `_expand_and_continue` (renamed from `_expand_or_hold`, whose STOP-doc-era behavior was to
  hold after every expansion): after a successful expansion, resets
  `campaigns_complete=0, campaign=None, campaign_seed=None, generation=None,
  rank1_qualifiers=0` and sets `state="running", phase="campaign"` — the worker's very next
  invocation dispatches straight into the new batch's first campaign. Reaching the 32-world
  ceiling (`ExpansionHeld`) is the only path that still writes `state="hold",
  phase="terminal_hold"`.

## Tests

- `tests/test_fitness_v2_worker.py::WorldBankExpansion` — expansion now asserts
  `state="running"` and a reset campaign block, not a hold; a stubbed
  `_run_next_campaign` proves the next `run_one_step()` dispatches into the new batch
  without a human/Claude prompt.
- `::test_a_qualifying_champion_does_not_stop_expansion` — an admitted champion still
  results in `state="running"`, proving no early stop.
- `::CheckpointNamespaceAcrossBatches` — `_expansion_batch_id`'s boundary arithmetic; the
  same `(seed, generation)` pair in two different batches writes two different files with
  different content and no `ArtifactCollision`; nominee/admission paths are likewise
  batch-distinct; `_run_next_campaign`'s seed-selection line is inspected directly to
  confirm it never references `batch_id`.
- The ceiling test (`test_expansion_holds_at_the_frozen_32_world_ceiling`) still proves
  batch 4 runs against the 32-world bank before the ceiling holds on the *next* attempt.

Full suite green before this freeze was crossed (see commit for exact count).
