# SABLE — end-to-end TBOTS audit (read-only)

Reviewer: SABLE. Bounded, read-only. Nothing in this audit executed an organism,
advanced a generation, modified code or data, merged PR #2, or touched a broker/
live/paper surface. Everything marked "verified" below was checked directly by
me this session (hash comparisons, live SSH queries, an actual test run); everything
marked "per receipt" is a claim from an existing document that I did not
independently re-derive.

---

## 1. Revision and artifact map

### HOST (this machine) — `/home/rick/tbots`

| Item | Value |
|---|---|
| Remote | `https://github.com/Racer-01111000/tbots.git` |
| Current branch | `repair/self-contained-canonical-20260901` |
| HEAD | `9e57332` — Merge PR #1 (`feat/s6b-deterministic-resume-executor-20260904`) |
| `main` | `e00af4a` — "Import sanitized NODE laboratory snapshot" (root import, no history) |
| `feat/s6b-deterministic-resume-executor-20260904` | `f7ac06a` (merged into current branch via PR #1) |
| `feat/historical-evolution-v1-20260901` (remote only) | `51a01ea` — unrelated root, README only |
| Working tree | **dirty** — see §4 |

Working-tree state (verified via `git status`):
- Modified, unstaged: `scripts/s6b_resume_executor.py` (12 insertions / 5 deletions — adds a `generation == 0` / `build_gen0` branch and a `predecessor_generation` default of `-1`).
- Untracked: `scripts/s6b_primary_development.py`, `tests/test_s6b_primary_development.py` (new Gen0-bootstrap capability for a *new* primary lineage; hard-gated behind `population_execution_authorized: bool = False`, import-only, no CLI).
- Untracked: `experiments/` — 14 dated research directories, none committed (list in §2/§4).

### NODE (physical gym) — `/opt/evolutionary-markets`, host `NODE` (100.68.14.50, Tailscale, `ssh node`)

Verified live via SSH this session:

| Item | Value |
|---|---|
| Branch | `master` |
| HEAD | `74acb36` (full: `74acb3666b046dd363c2b4b502ae0d0a2a1806c1`) — "Complete S6B execution surface" |
| Commit count | 22 (matches `IMPORT_PROVENANCE.md`'s "22-commit history") |
| Remote | none configured — standalone repo, not the origin of `tbots` on GitHub |
| Working tree | clean except one untracked dir: `evolution/s6b_runs/` |
| Running processes | no `evolutionary-markets` process; only unrelated `kestrel_brain`/`node_validate_thermal_watchdog` processes and two unrelated 12h backup cron jobs (NODE and kestrel-memory repos) |
| Pending transactions | none found (no `.s6b_resume_transaction.json`, no `*.lock`) |

**This is the single most important fact in this audit**: NODE's `evolution/s6b_runs/` tree is still at the **pre-resume, interrupted** state — C primary Gen0–11 only (no `completion.json`, no Gen12), D reproduction Gen0–6 only (no `completion.json`, no Gen7+). The Gen12/Gen7-12 resume documented in the `tbots` HEAD was performed **only against the preserved copy inside the `tbots` git repository**, never against NODE itself. See §5.

### GitHub PRs

| PR | Branch | Base | State | Head |
|---|---|---|---|---|
| #1 | `feat/s6b-deterministic-resume-executor-20260904` | — | **MERGED** 2026-09-04, into `repair/self-contained-canonical-20260901` | `f7ac06a` |
| #2 | `claude/loving-davinci-wbfut7` | `main` | **DRAFT**, opened 2026-09-24T02:46:42Z, mergeable, no CI configured on this base | adds one file, `SNAPSHOT_RECONCILIATION.md` |

Per instructions, PR #2 was treated as separate history and not merged. Its content is a reconciliation, produced by a separate cloud session, comparing the untouched `main` import snapshot against the current `repair/...` branch. Its own numbers (188 files / +1,275,138 lines difference, 26/26 dataset-bundle hashes re-verified matching) line up with what I independently found. One thing in it is imprecise and worth flagging to Rick directly: PR #2's body cites `/home/rick/tbots/experiments/…` and "uncommitted Gen0-bootstrap work" as examples of "work that exists only [on NODE]" — that path is on **HOST**, not NODE (NODE's repo root is `/opt/evolutionary-markets`, confirmed above), and I read those exact files directly from HOST this session. The cloud session that wrote PR #2 had no HOST filesystem access, so it could not have distinguished "not in GitHub" from "on NODE" — this looks like a scope-of-visibility artifact of that session, not a claim about NODE that I could confirm or refute from NODE's side. NODE has no `experiments/` directory at all (verified: NODE's top-level listing has no such path).

### Dataset / champion identity chain (verified byte-for-byte, HOST vs. NODE, this session)

Spot-checked three artifacts by SHA-256 directly on both hosts — all matched exactly:
- `data/raw/SPY.json` → `834636...f6fe`
- `data/normalized/SPY.csv` → `c28535...5734`
- `reports/s5c_championship_08325c0_result.json` → `974b91...46c0`
- `evolution/s6b_runs/primary/C_.../generations/gen_11.json` (last shared pre-resume generation) → `312db1...ae6d`

This corroborates (does not merely repeat) the `HISTORICAL_PRESERVATION_MIGRATION.md` and `S6B_HISTORICAL_PRESERVATION.md` receipts' claims of 26/26 and 130/130 exact matches — I re-hashed a sample myself rather than trusting the documents.

Frozen identities (per receipts, cross-referenced against locked protocol files, not independently recomputed by me):
- Dataset revision: `ds_7e16896c873671fe86ac416b24a0ce74502249a8a0fc33603e0f1935e5fab131`
- S5A DEVELOPMENT bundle: `s5adev_98e2f764f466b90ee2bbc2532b75188bfc4fd20b4a13523f94bce65e6a1f193a`
- S5C run / champion chain: `s5c_c4f43cb6...`, digest `ecada3c0...`, frozen champion genome `gen_0307d23c...`, S5D champion identity `s5d_champion_00ac1019...`

---

## 2. Stage-by-stage status

Pipeline as laid out in `scripts/`: **S5A (evolution/DEVELOPMENT) → S5B (qualification) → S5C (championship) → S5D (final reserve / frozen champion) → S6A (executable runtime preparation) → S6B (primary/reproduction runtime, lineages B–G)**.

| Stage | Inputs | Outputs / artifacts | Gate state (verified against `evolution/protocol/*_preparation_lock.json`) | Status |
|---|---|---|---|---|
| **Datasets** | `data/raw/*.json` (8 symbols, Yahoo-sourced per provenance note) | `data/normalized/*.csv` + manifest | dataset_revision pinned in every downstream lock | Verified present, byte-identical HOST/NODE (see §1) |
| **S5A — evolution/DEVELOPMENT** | normalized dataset, mutation/population protocol (`evolution/protocol/mutation_bounds_*`, `population_rules_*`, `fitness_formula_*`) | `data/development_bundles/s5adev_98e2f764.../` (9 files) | `qualification_organisms_executed_during_preparation: 0` in S5B lock confirms prep didn't leak execution | Bundle present and hash-verified via receipt; **not independently re-hashed by me this session** (only spot-checked SPY raw/normalized, not the DEV bundle itself) |
| **S5B — qualification** | DEV bundle, frozen top-10 snapshot | `data/qualification_bundles/s5bqual_f1fdd7c4.../`, `frozen_top10_s5b_top10_2f013550...json` | lock present, `qualification_organisms_executed_during_preparation: 0` | Present; not re-hashed this session |
| **S5C — championship** | qualification bundle | `data/championship_bundles/s5cchamp_ccc9d10a.../`, `reports/s5c_championship_08325c0_result.json` | lock present, `alpaca_access_configuration_authentication: 0`, `broker_connections: 0`, `breeding_or_retraining: 0` | Verified result-file hash matches receipt; champion selection is `championship_rank_1` (in-sample by construction — see the prior Sable review, §"circularity call") |
| **S5D — final reserve / frozen champion** | championship result | `evolution/protocol/frozen_champion_s5d_champion_00ac1019...json`, final-reserve bundle | lock present, `broker_connections: 0`, `evolutionary_feedback: 0`, evaluator window 2024-10-28→2026-08-25 | Frozen champion identity is load-bearing across every later document; not independently re-executed (correctly — re-executing it is out of scope and prohibited) |
| **S6A — executable runtime prep** | frozen protocols B–G | `evolution/protocol/s6a_*` locks/schemas | `population_execution_authorized: False`, `alpaca_access: 0`, `broker_connections: 0`, `live_feeds: 0`, `paper_orders: 0` | Locked, fail-closed by construction |
| **S6B — primary/reproduction runtime** | S6A primitives | `evolution/s6b_runs/{primary,reproduction}/{B,C,D,E}...` | see §1/§5 | **B primary/reproduction, D primary: COMPLETE (Gen0–12) on both HOST and — per the original preservation snapshot — NODE. C primary and D reproduction: COMPLETE (Gen0–12) on HOST/GitHub only; still INTERRUPTED (C@Gen11, D-repro@Gen6) on NODE. E/F/G: NOT_STARTED everywhere (no directories on either host).** |

### Post-pipeline research (uncommitted, HOST-only, `experiments/`)

Fourteen untracked, dated experiment directories exist only in the HOST working tree (never committed, not present on NODE). In rough chronological order:
1. `controls_comparison_20260921T064013Z/` — baseline controls (NO_TRADE, BUY_AND_HOLD_SPY, RANDOM_SIGNAL) vs. 41 evolved agents across 12 DEV episodes.
2. `controls_interpretation_holdout_20260921T140000Z/` — first interpretation pass.
3. `championship_holdout_20260921T160000Z/` — champion + D-lineage top-3 vs. controls on CHAMPIONSHIP/FINAL_RESERVE windows.
4. `sable_review_20260921T104614Z/` — my own prior independent review of 1–3; found the champion is in-sample by construction, and that a naive, non-adaptive `PASSIVE_ENVELOPE` control **beats every evolved agent and the champion on Sharpe** in the one genuinely blind window (FINAL_RESERVE), and beats them on absolute return in both non-DEV windows. Flagged this as unaddressed in the written record.
5–10. `passive_envelope_dev_gap_...`, `passive_envelope_lineage_decomposition_...`, `d_exposure_isolation_...`, `d1_qualification_generalization_...`, `qualification_harness_equivalence_...` — follow-up empirical work (2026-09-23 morning) responding directly to finding 4.
11–14. `cross_regime_fitness_design_...` → `fitness_contract_adversarial_review_...` → `world_league_fitness_correction_...` → `fitness_v2_prefreeze_errata_20260923T084553Z/FINAL_CONTRACT.md` (latest, 2026-09-23T08:45:53Z) — a from-scratch fitness-formula redesign ("Fitness V2 / World League"), explicitly **design-only**, that ends with: *"FITNESS V2 / WORLD LEAGUE CONTRACT — READY TO FREEZE"*. No evaluator code, no genome, and none of the three S6B execution scripts were touched while producing it (stated explicitly in its own hard-boundary compliance section, and consistent with it being pure markdown/JSON with synthetic-only numeric examples).

This entire thread (5–14) is the live frontier of the project: a response to a real, independently-verified methodological gap (naive envelope beats evolved agents in the only real holdout), currently at "contract designed, not yet implemented in code."

---

## 3. Verified test evidence

I ran the suite myself this session (not relying on any commit message's claim):

```
python3 -m unittest discover -s tests -v
Ran 334 tests in 58.520s
OK
```

This includes the two newest, currently-untracked test files (`test_s6b_primary_development.py`, 4 tests, all passing) layered on top of the 330 that the `f7ac06a` merge commit claims. 330 + 4 = 334 — consistent, and independently confirms the uncommitted `s6b_primary_development.py`/`s6b_resume_executor.py` diff does not break anything in the existing suite.

Cross-referenced against what each side's receipts separately claim:
- `f7ac06a` commit message (D reproduction Gen7-12): "Focused (51/51) and full suite (330 passed, 14 subtests) green after the run" — **not independently re-verified by me at that historical commit**, but consistent with what I measured at current HEAD plus the 4 new tests.
- PR #2: "`main` (bundle absent): 259 tests, 7 errors ... Repair branch `9e57332`: 330 tests, all pass" — internally consistent with the above; I did not re-run tests against `main` myself.
- `HISTORICAL_PRESERVATION_MIGRATION.md` (2026-09-01, earliest): "279 tests... OK" — an earlier point in the codebase's growth; not re-verified, stated here only for the historical trend.

No pytest is installed (`ModuleNotFoundError: No module named pytest`); the suite runs via `unittest discover`, which is what all historical receipts also appear to have used.

---

## 4. Discrepancies and missing evidence

1. **Gym (NODE) and Git artifacts (HOST/GitHub) do not agree**, by design but not clearly re-documented: NODE remains at the pre-resume interrupted state for C primary (Gen11) and D reproduction (Gen6); HOST/GitHub show both COMPLETE through Gen12. The resume executor was deliberately run only against the *preserved copy* inside `tbots`, never against NODE (consistent with every "NODE remained unchanged" claim in the preservation docs) — but there is no single current document that states this divergence as the **present** state; you have to reconstruct it from `CHECKPOINT_S6B_20260901.md` (superseded, pre-resume) + two commit messages + a live NODE check. **Recommend**: a short, current checkpoint note stating explicitly "NODE = source of truth, still INTERRUPTED at C@11/D-repro@6; tbots copy = resumed to COMPLETE@12 for both; the two are intentionally divergent pending a future decision on whether/how to reconcile."
2. **`CHECKPOINT_S6B_20260901.md` is stale.** Its "HOLD/STOP" list ("Do not resume C or D yet") predates the two GOs that did resume them; nothing in the repo formally supersedes it with an updated checkpoint reflecting current accepted state. The acceptance record for the Gen12 work lives only in two commit messages, not a document analogous to the original checkpoint.
3. **Working tree on HOST is dirty and not on any named branch/PR**: `scripts/s6b_resume_executor.py` (modified) + `scripts/s6b_primary_development.py` + `tests/test_s6b_primary_development.py` (untracked) implement a Gen0-bootstrap path for a *new* primary lineage. It's fail-closed (`population_execution_authorized` must be explicitly `True`) and passes tests, but it exists only as uncommitted local state — not reviewed, not on a branch, not backed up, not on NODE.
4. **14 experiment directories are entirely untracked** (confirmed via `git status`), including the most consequential finding in the whole audit trail (PASSIVE_ENVELOPE beating the champion/evolved population) and the newest fitness-formula redesign. My prior review already flagged the provenance limitation this creates: untracked files could in principle be edited post-hoc with no git trail; mtime-ordering is the best available check, and I did not find anything inconsistent with normal sequential creation, but this is not the same guarantee git would provide.
5. **PR #2 misattributes HOST-local paths to NODE** (see §1) — minor, but worth correcting before anyone acts on that PR's "what needs NODE access" framing.
6. **S5A/S5B DEVELOPMENT and qualification bundle hashes were not independently re-verified by me this session** — I relied on the existing receipts for those (I did re-hash the raw/normalized dataset and the S5C result directly). If Rick wants full parity with the S5C-level verification, that's a bounded, read-only follow-up (hash the DEV/qualification bundle files against `HISTORICAL_PRESERVATION_MIGRATION.md`'s table).
7. **No CI runs against PR #2's branch** (`gh pr checks 2` reports none configured) — not a defect, just means the PR's own claims (26/26 hashes, test counts) are self-reported in the PR body, not machine-checked by GitHub Actions on that branch.

Nothing found suggests any broker, live-feed, paper-trading, or credential surface exists anywhere in the current pipeline — every S5C/S5D/S6A preparation lock I read has `broker_connections: 0`, `alpaca_access...: 0`, `live_feeds: 0`, `paper_orders: 0` explicitly recorded, and the prior Sable review separately grepped the experiment directories for the same terms with the same (clean) result.

---

## 5. Smallest next decision for Rick

**Merge or continue holding PR #2.** It is the lowest-risk, most fully-specified open item in the entire audit: a single new markdown file, zero code/data changes, base `main` (which nothing else currently branches from), already self-reconciled (188-file diff enumerated, 26/26 dataset hashes re-verified, test-count discrepancy on `main` explained as caused entirely by the missing DEV bundle). It does not touch S6B, HOLD, or any generation state. Merging it only makes `main`'s documentation of the situation accurate; **not** merging it costs nothing except leaving `main` stale. This is a pure paperwork decision, unlike every other open thread (the uncommitted Gen0-bootstrap code, the Fitness V2 contract, and the NODE/tbots C-primary+D-reproduction divergence), each of which requires a substantive judgment call and is properly a separate, later GO.
