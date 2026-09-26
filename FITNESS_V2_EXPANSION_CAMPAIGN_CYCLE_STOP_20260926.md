# Fitness V2 post-expansion campaign-cycle stop

Authority: 2026-09-26 GO ("CONDITION 4 CLEARED / CONTINUE AUTONOMY CLOSURE") — close the
two identified autonomy gaps using only non-result-bearing fixtures, then hold for
activation review.

Repository: `Racer-01111000/tbots`, branch
`feat/node-resident-fitness-v2-autonomous-evolution-20260925`

## What this closes

Gap 1 (deterministic mid-campaign crash/resume) and the mechanical half of Gap 2
(world-bank expansion) are implemented in `scripts/fitness_v2_worker.py`:

- `_resume_campaign_state`/`_run_next_campaign` now resume a crashed campaign at the last
  durably checkpointed generation + 1 (loading the persisted `next_population` and
  cumulative `used_genome_ids`), never redo an already-committed generation, and reproduce
  the exact same final nominee as an uninterrupted run — proven in
  `tests/test_fitness_v2_worker.py::MidCampaignResume`.
- `_expand_or_hold` reconstructs the cross-family `admitted_reference_vectors` state from
  a persisted world bank and calls `fitness_v2_world_bank.expand_family` once per family
  (handoff §50: "add exactly: 1 Distributional, 1 Execution, 1 Sequence, 1 Shock ... Never
  remove prior worlds"), unconditionally after every completed 5-campaign batch (handoff
  §50: "Do not adapt world-bank difficulty based on candidate success/failure") — proven in
  `tests/test_fitness_v2_worker.py::WorldBankExpansion`.
- Reaching the frozen 32-synthetic-world ceiling (`ExpansionHeld`) writes
  `state: "hold", phase: "terminal_hold"` with the handoff §50 quote in `stop_reason`.
  `run_one_step` idles on any `state == "hold"` rather than re-entering
  `_finalize_or_expand` on the next timer firing.

## Mandatory stop before the next campaign batch

`AGENTS.md` requires: "If a written contract ambiguity materially changes selection, stop
that component and record the ambiguity; do not invent a rule." After every expansion, the
worker now stops in the same way (`state: "hold", phase: "expansion"`) rather than
resetting `campaigns_complete` and auto-launching the next 5-campaign batch against the
expanded bank, because the frozen text does not say:

1. **Campaign-seed reuse.** Handoff §50 only says the world bank grows by one world per
   family "after each complete 5-campaign batch" — it does not say whether the *same*
   frozen `CAMPAIGN_SEEDS` are reused for every subsequent batch against the larger bank,
   or whether a new seed set is required. §57 forbids "chang[ing] campaign seeds after
   seeing outcomes," which argues for reuse (the seeds were never changed, just reapplied),
   but that is an inference, not a stated rule.
2. **Early-champion behavior.** Handoff §50's "do not adapt world-bank difficulty based on
   candidate success/failure" argues expansion (and by extension the next batch) proceeds
   unconditionally regardless of outcome — but nothing states whether an admitted research
   champion at a smaller world-bank size ends the program, or whether expansion continues
   to the 32-world ceiling regardless of an earlier admission.
3. **Checkpoint/evidence namespacing across batches.** Reusing `CAMPAIGN_SEEDS` for a
   second batch against an expanded bank would produce genuinely different per-generation
   evaluation content under the *same* `(seed, generation)` pair the first batch already
   used — `worker_checkpoint`'s collision guard would (correctly) refuse to silently
   overwrite that evidence. `_finalize_or_expand`/`_expand_or_hold` already namespace the
   world bank and admission-decision checkpoints by synthetic-world-count; campaign and
   per-generation checkpoints do not yet have an equivalent per-batch namespace, and adding
   one is a selection-relevant design choice (what identifies "batch," where it's recorded
   in `STATUS.json`) rather than a purely mechanical one.

## Stop state and conservation

- The world bank has expanded exactly as specified (1 world per family per batch, prior
  worlds untouched) up to whatever `synthetic_world_count` `STATUS.json` currently shows.
- No campaign has run, and no genome has been evaluated, against any expanded bank.
- `campaigns_complete`, `rank1_qualifiers`, and `research_champion` reflect only the batch
  that was already complete before this expansion step.
- Continuous multi-batch evolution remains disabled; unattended/HOST-independent
  acceptance is not claimed for anything beyond one already-authorized 5-campaign batch.

## Required decision

Rick must supply or approve one content-addressed addendum resolving items 1–3 before the
worker is allowed to launch a second campaign batch on its own. Until then, `STATUS.json`
holds at `phase: "expansion"` after each expansion step; a person (or an explicit new GO)
must clear the hold to proceed.
