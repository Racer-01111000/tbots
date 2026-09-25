# Dell Node bootstrap and Fitness V2 status

Recorded UTC: `2026-09-25T06:07:40+00:00`

Status: **NOT ACCEPTED FOR CONTINUOUS EVOLUTION**

## Reconstructability and historical evidence

- Dell host identity observed as `NODE`.
- Repository path: `/opt/evolutionary-markets`.
- Active branch: `feat/node-resident-fitness-v2-autonomous-evolution-20260925`.
- Node policy/configuration checkpoint before this receipt: `aa78676b3c12e1e2da3e81587fc5d71df763fcff`.
- Preserved original Node history is reachable at
  `preserve/node-original-22-commit-history-20260924` with exactly 22 commits and
  tip `74acb3666b046dd363c2b4b502ae0d0a2a1806c1`.
- Frozen S5D champion SHA-256 remains
  `82a0e1b1ad860d5fbfc75740cd6d84e4b56188e952e17a30f6c8d330ccdaad21`.
- All 130 entries in `evolution/s6b_runs/PRESERVATION_MANIFEST.json` were
  rechecked on the Node for existence, byte size, and SHA-256: `bad=0`.
- `git fsck --full --no-progress` found no corrupt object. It reported one
  reachable-by-bundle but unreferenced historical commit as dangling; this is
  not corruption.

## Fitness V2 implementation and validation

- Dividend repair commit is present through `f38da911`.
- Exact tested Fitness V2 implementation revision:
  `4ac2de7350d75edb4100d136adec691fd18d426a`.
- Focused Node suite: 20 tests, all passed.
- Full Node suite: 362 tests, all passed in 41.524 seconds.
- The implementation fails closed unless supplied an exact, content-addressed,
  DEVELOPMENT-only world manifest. It implements corrected return/drawdown
  treatment, family aggregation, passive comparison/overrides, inactivity
  rejection, rank-1-only nomination, anti-sibling-shopping, and deterministic
  majority admission.

## Resident Codex/Sable bootstrap

- Official `@openai/codex@0.157.0` is installed for user `rick` at
  `/home/rick/.npm-global/bin/codex`.
- `codex-cli 0.157.0` parses the `tbots_unattended` profile successfully.
- `/home/rick/.codex/config.toml` is owned by `rick:rick`, mode `0600`.
- The explicit `tbots_unattended` profile uses `approval_policy = "never"` and
  `sandbox_mode = "danger-full-access"`; ordinary Codex sessions are not changed
  to that permission profile automatically.
- `/opt/evolutionary-markets` is marked trusted for Codex project configuration.
- Codex authentication status is `Not logged in`; initial human authentication
  remains required.
- GitHub CLI `2.46.0` is installed. Initial human GitHub authentication remains
  required before Node-originated push preservation can be accepted.
- Git author identity is configured as `Rick <rickinvn@outlook.com>`.
- A convenience attempt to create `/usr/local/bin/codex` generated an
  interactive sudo password prompt and was terminated. No symlink was created.
  The explicit user-scoped Codex path is used instead. This occurred during
  bootstrap, not during an acceptance generation.

## Scientific stop condition

No new population or evolution was started because the preserved written
contract does not uniquely freeze values that materially affect selection:

1. exact initial synthetic bank size within the specified 12--16 range;
2. exact techniques, numerical parameters, and deterministic seeds per family;
3. exact per-generation subset/rotation, withheld-world allocation, exposure
   cap, and expansion schedule;
4. exact diversity distance, descriptor normalization, and admission threshold;
5. exact independently seeded campaign count `N` and campaign seeds.

An exact manifest decision from Rick is required. These values were not invented
from examples or recommendations in the design documents.

## Unattended acceptance gate

The required complete hands-off generation has **not** been attempted. Therefore:

- no baseline population exists;
- no qualification or research-champion decision was made;
- no throughput/capacity claim is accepted;
- no autonomous evolution service is enabled; and
- the laboratory is not accepted as HOST-independent or unattended.

After the manifest is frozen and both initial authentications are complete, the
entire acceptance cycle must run without any human approval prompt: tests,
artifact writes, evaluation, selection, breeding, next-generation persistence,
qualification, research-champion decision, Git commit/preservation, and
deterministic continuation. Any prompt invalidates that attempt and requires a
full repeat after configuration repair.

No broker, paper-trading, live-order, or real-capital action occurred.
