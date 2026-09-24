# DIVIDEND_ACCOUNTING_REPAIRED — validation receipt (2026-09-24)

Authority: Rick, `GO — TBOTS RESEARCH EVIDENCE PRESERVATION + DIVIDEND
ACCOUNTING REPAIR / REVALIDATION`, Phase C (append-only, second batch onto
this evidence branch).

## Designation

**DIVIDEND_ACCOUNTING_REPAIRED.** Every result in this batch's
`post_repair_results.json` was produced by code carrying this repair;
`pre_repair_results.json` was produced by the unrepaired code at the same
starting revision, for comparison only — neither supersedes nor is treated
as more authoritative than any pre-existing preserved result elsewhere in
this repository. Per `HISTORICAL_PRESERVATION_MIGRATION.md`'s and
`S6B_HISTORICAL_PRESERVATION.md`'s own convention: **no historical result
artifact anywhere in the repository was overwritten, regenerated, or
reinterpreted by this repair or this validation.**

## Provenance

- Repair branch: `repair/dividend-accounting-20260924`
- Repair commit (new simulator/code revision): `6b4336f875fd85de9e1474c0b1c7f7a313bb7b8d`
- Branched from authoritative revision: `4ee5b098f73d0db3b8b97485b3fcefa3200582f3`
  (`repair/self-contained-canonical-20260901`) — **not merged into it under
  this GO.**
- Repaired-file SHA-256 at the repair commit:
  - `scripts/execution.py`: `c99ec955ce41570f57f883e53c723cf746c8a51688fcbeb6430ee4919786f810`
  - `scripts/run_control_episode.py`: `c0ab51961c6d391dbfb8762caaeb7c669f649e2ad0dde70df2adb74ecfd64418`
  - `scripts/s6b_evaluator.py`: `145ea2ab18b7223faa7f8a70b386eaf28bdbf2ae20da27f7058aaab78a0fd1b3`
  - `tests/test_dividend_accounting.py`: `e670c9b3810b55cbaa951d5d7f209a4ac231344543eb0980d2fc932bfdd8ddba`
- Source dataset identity: `ds_7e16896c873671fe86ac416b24a0ce74502249a8a0fc33603e0f1935e5fab131`
  (unchanged by this repair — same dataset, only the accounting of an
  already-present `corporate_action.dividend_amount` field changed).
- This evidence branch's own base: `evidence/research-log-20260924`
  (Phase A commit `927df75c9b8fd958b17e7958a04ceb8ba5019b6a`).

## Root cause (established by reading, before any code was changed)

`execution.Portfolio` marked equity on each session's RAW `close` — correct;
it never reads `adjusted_close` (which already embeds reinvested
dividends) for cash/equity/fill/sizing math anywhere in the codebase, so
raw-close marking was never itself the defect and repairing it would have
double-counted. The defect was that nothing — not `Portfolio`, not
`run_control_episode.py`'s loop, not `s6b_evaluator.py`'s loop — ever read
the `corporate_action` field each normalized row already carries
(populated by `lib.normalize` whenever the source has a dividend event
that date) or credited `cash_cents` for it. A held position's ex-date
dividend was silently discarded.

## Repair

`execution.py` adds `dividend_amount_dollars()` (parses `corporate_action`,
ignores a concurrent split — out of scope) and `Portfolio.credit_dividend()`
(credits `shares_of(symbol) x per-share amount`, rounded to the cent,
no-op if unheld, tracked in a new `total_dividend_cents` counter). Both
orchestration loops call it once per step for that day's held-into-today
positions, before that day's fill can change what's held — symmetric with
the codebase's existing T+1 fill convention (a same-day buy was never a
holder of record for that date; a same-day sell still was). No change to
strategy signals, indicators, sizing, fill timing, transaction costs, the
drawdown formula, or the 12% halt threshold.

## Test evidence

- Focused: `tests/test_dividend_accounting.py`, 12/12 passed (all 10 GO-
  required properties, plus 2 supporting checks), run from the clean repair
  worktree.
- Complete tracked suite, same worktree: `python3 -m unittest discover -s
  tests` → **342 passed, 0 failures/errors/unexpected skips** (330
  pre-existing + 12 new; matches exactly, no regression).

## Bounded DEVELOPMENT-lane revalidation

Scope, per the GO: no new genomes, no candidate search, no protected lane
(QUALIFICATION/CHAMPIONSHIP/FINAL_RESERVE untouched), all 12 frozen DEV
episodes. Five subjects — chosen because they are each individually and
exactly reproducible without needing anything outside this repository's own
tracked/preserved evidence, unlike the original 41-agent controls
comparison (in `experiments/controls_comparison_20260921T064013Z/`, itself
preserved in this evidence branch's first batch), which this validation
does not attempt to fully re-run:

- `S5D_CHAMPION` — the actual frozen champion genome (`evolution/protocol/frozen_champion_s5d_champion_00ac1019...json`), run through `control_agent.decide`.
- `D_primary_rank1` — the actual accepted rank-1 genome from D primary Gen12 (`evolution/s6b_runs/primary/D_.../development_top_eight.json`, `genome_id=gen_8e7a622b48276bc68b7b70773bb9e2a5250dbb82584c53f6c67416bd6ffdf0cf`), run through the real, unmodified-apart-from-the-repair `s6b_evaluator.evaluate_genome`.
- `NO_TRADE_CONTROL`, `BUY_AND_HOLD_SPY_CONTROL`, `RANDOM_SIGNAL_CONTROL` — reproduced exactly per the specification in `experiments/controls_comparison_20260921T064013Z/synthetic_controls.py` (same published seed, `20260921`, for `RANDOM_SIGNAL_CONTROL`), run through a same-primitives loop (`execution.Portfolio`, `risk.validate`, `execution.compute_orders`, identical T+1-fill/raw-close-mark/12%-halt sequence) matching `experiments/controls_comparison_20260921T064013Z/harness.py`'s `_simulate_generic_episode`.

Each subject evaluated twice — once against the pre-repair code (a detached
checkout at `4ee5b098...`, before this branch's commit) and once against
the repaired code (`6b4336f...`) — using the identical script,
`dev_revalidation.py` (included in this batch), so the only variable
between the two runs is the repair itself.

### Results

| Subject | Episodes changed (of 12) | Dividend credited (cents, 12-episode total) | Median `total_return` delta | Max `total_return` delta |
|---|---:|---:|---:|---:|
| `S5D_CHAMPION` | 12 | 5,006,089 | +0.4196pp | +0.5992pp |
| `D_primary_rank1` | 12 | 5,705,823 | +0.4401pp | +0.7003pp |
| `NO_TRADE_CONTROL` | 0 | 0 | +0.0000pp | +0.0000pp |
| `BUY_AND_HOLD_SPY_CONTROL` | 11 | 18,812,272 | +1.1740pp | +2.4479pp |
| `RANDOM_SIGNAL_CONTROL` | 12 | 16,894,709 | +1.2723pp | +2.5860pp |

**47 of 60 agent-episode pairs changed.** `NO_TRADE_CONTROL` — which never
holds a position and therefore has zero dividend entitlement in every
episode — is unaffected in all 12, exactly as the repair's own logic
predicts; this is the strongest available sanity check that the repair
does nothing to agents with nothing to be entitled to.

**Ranking**: `D_primary_rank1` still ranks above `S5D_CHAMPION` on median
DEV-lane `total_return`, both pre- and post-repair — unchanged, so nothing
in the prior D-vs-champion generalization discussion (see this evidence
branch's `sable_review_20260921T104614Z/review.md`) is directly overturned
by this repair alone. The three controls' *relative* order to each other
does change: pre-repair, `RANDOM_SIGNAL_CONTROL` ranked last (median
−1.58%); post-repair it ranks ahead of both `NO_TRADE_CONTROL` (0%, by
construction) and `BUY_AND_HOLD_SPY_CONTROL` (still negative, −0.33%,
though much less so than pre-repair's −1.24%).

**Halt state**: one change. `BUY_AND_HOLD_SPY_CONTROL`'s 12%-drawdown halt
fires in 6 of 12 DEV episodes post-repair, versus 7 of 12 pre-repair — the
dividend cash cushions one episode's drawdown just enough to stay under the
threshold. **This is a specific, material correction to a number the prior
Sable review cited directly** (`experiments/sable_review_20260921T104614Z/review.md`,
section 2(v): "BUY_AND_HOLD_SPY_CONTROL (7/12 DEV episodes...)") — that
figure was accurate against the pre-repair simulator at the time it was
written, and is now 6/12 under the repaired accounting. No other subject's
halt pattern changed in any episode.

### What this does and does not settle

This revalidation confirms the repair behaves as intended (dividend-blind
strategies are no longer silently understated, non-holding strategies are
unaffected, no double-counting, deterministic) and surfaces one concrete
correction to a previously-cited figure. It does **not** re-run the
original 41-agent, 12-episode `controls_comparison` in full, does not
include `PASSIVE_ENVELOPE_CONTROL` (added in a later experiment batch), and
does not touch QUALIFICATION/CHAMPIONSHIP/FINAL_RESERVE at all — so it
cannot say whether the prior review's headline finding ("a naive
PASSIVE_ENVELOPE control beats every evolved agent and the champion on
Sharpe in the one real out-of-sample window") is itself affected by this
repair. That remains open for a future, separately-scoped GO if Rick wants
it settled.

### Determinism

The post-repair run was executed twice from the identical repair-branch
worktree; the two output files are byte-identical (`diff` reports no
difference).

## Files in this batch

- `dev_revalidation.py` — the revalidation script (works unmodified against
  both the pre- and post-repair code; detects the repair via
  `hasattr(execution, "dividend_amount_dollars")`).
- `pre_repair_results.json` — all 5 subjects x 12 episodes, run against
  `4ee5b098...` (unrepaired).
- `post_repair_results.json` — the same, run against `6b4336f...` (repaired).
- `PRESERVATION_MANIFEST_20260924B.json` — this batch's own append-only
  manifest (does not modify `PRESERVATION_MANIFEST_20260924.json` from the
  first preservation batch on this branch).

## Boundary compliance

No source code was modified by this preservation step (Phase C is
copy-only, mirroring Phase A). The dividend-accounting source code itself
lives only on `repair/dividend-accounting-20260924` and is **not** copied
into this evidence branch. `scripts/s6b_primary_development.py`,
`tests/test_s6b_primary_development.py`, and the unrelated dirty edit to
`scripts/s6b_resume_executor.py` were not read or touched by Phase B or
Phase C. NODE was not contacted by either phase. No QUALIFICATION,
CHAMPIONSHIP, or FINAL_RESERVE execution occurred. No mutation,
reproduction, or evolutionary run occurred. No broker/live/paper activity
occurred.
