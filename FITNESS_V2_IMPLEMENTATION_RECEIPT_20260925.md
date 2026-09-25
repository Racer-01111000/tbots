# TBOTS Fitness V2 implementation checkpoint

Authority: Rick GO — NODE-RESIDENT SABLE + FITNESS V2 + AUTONOMOUS EVOLUTION  
Recorded: 2026-09-25T05:25:28Z  
HOST source worktree: `/home/rick/tbots-worktrees/node-resident-fitness-v2-20260925`  
Branch: `feat/node-resident-fitness-v2-autonomous-evolution-20260925`  
Pre-implementation base: `f38da911ba98f57697ac35b1e52ab430946001ac`
Tested Fitness V2 code revision: `4ac2de7350d75edb4100d136adec691fd18d426a`

## Evidence labels and results

- **OBSERVED LIVE — HOST:** the original NODE history is reachable from
  `origin/preserve/node-original-22-commit-history-20260924` at
  `74acb3666b046dd363c2b4b502ae0d0a2a1806c1`; `rev-list --count` returned
  exactly 22 and `git fsck --full` returned no errors.
- **OBSERVED LIVE — HOST:** frozen S5D champion file SHA-256 is
  `82a0e1b1ad860d5fbfc75740cd6d84e4b56188e952e17a30f6c8d330ccdaad21`
  in both the fetched original NODE tree and the current TBOTS tree.
- **OBSERVED LIVE — HOST:** all 130 entries in
  `evolution/s6b_runs/PRESERVATION_MANIFEST.json` passed `sha256sum -c`.
- **OBSERVED LIVE — Dell NODE (`NODE`, Dell Latitude E7440):** the former
  `/opt/evolutionary-markets` repository is absent; the directory exists but
  is empty. Reconstruction therefore depends on the verified Git preservation
  ref plus the verified current TBOTS evidence tree, not the live directory.
- **OBSERVED LIVE — Dell NODE:** official Codex CLI `0.157.0` is installed in
  `/home/rick/.npm-global`; `codex login status` reports `Not logged in`.
- **IMPLEMENTED:** `scripts/fitness_v2.py` contains the exact per-world,
  per-family, cross-family, inactivity, passive-comparator, OC1/OC2,
  rank-1-only, and strict-majority admission algebra. It rejects protected
  calibration periods and refuses non-content-addressed or range-only world
  manifests.
- **OBSERVED LIVE — tests:** focused Fitness V2 suite: 20/20 passed.
- **OBSERVED LIVE — tests:** complete relevant TBOTS suite: 362/362 passed in
  43.832 seconds, with zero skips or unexplained failures.
- **IMPLEMENTED / VERIFIED:** the dividend-accounting repair commit was applied
  before Fitness V2 (`f38da91`); the full suite includes its deterministic,
  ex-date cash-credit, no-double-counting, and signal-invariance tests.

## Material ambiguity stop

The written World League design is not an executable campaign freeze. It
leaves all of the following selection-relevant choices unresolved:

1. exact initial synthetic bank size (`12–16`, not one number);
2. exact transformation techniques and parameter values per family;
3. exact world seeds and generation-specific subset/rotation schedule;
4. exact diversity-distance metric and threshold;
5. exact independently seeded campaign count `N` and seed list.

The final errata freezes the fitness/admission mathematics but does not resolve
those choices. Picking them here would silently reinterpret the contract and
materially change selection. Therefore this checkpoint intentionally did not:

- create a Fitness V2 baseline population;
- evaluate a real candidate or generate a new world;
- run an evolutionary generation;
- promote or mutate the immutable S5D champion;
- enable an autonomous evolution service.

## Conservation and rollback

- The dirty preserved checkout at `/home/rick/tbots` was not changed.
- The S5D champion and historical S6B trees were read and hashed only.
- No brokerage, paper, live-feed, or real-capital surface was used.
- Rollback for this checkpoint is deletion of the isolated feature branch and
  worktree; no historical artifact needs restoration.

## Required decision

Rick must freeze one exact world-bank/campaign manifest covering items 1–5
above. After that manifest exists, the baseline/evolution/throughput/autonomy
phases can continue without inventing scientific rules.
