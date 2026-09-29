# TBOTS Cloud V1 migration inventory

Authority: Rick, 2026-09-29 ("GO — TBOTS CLOUD V1 MIGRATION INVENTORY / NODE HOLD").
Inventory only -- no deployment, no new evolution started. NODE preserved as
burn-in source and fallback laboratory, HOLD at commit
`6042841d45612ca0b484e78a6ec0fda014d32a22`.

## 0. Stray-file investigation (requested again this GO)

Already investigated and removed in the immediately preceding turn (read-only
proof first, then removal). Re-verified clean this GO: file absent, no
recurrence, `git status` shows only the expected local-only `STATUS.json`.

- **Filename:** `ystemctl is-enabled tbots-fitness-v2.timer` (repo root,
  `/opt/evolutionary-markets/`)
- **Size:** 1301 bytes
- **mtime/birth:** 2026-09-29 14:19:45 +07 (= 2026-09-29T07:19:45Z)
- **Type:** regular file, ASCII text with ANSI escape sequences, mode 0644,
  owner `rick:rick`, 1 hardlink -- not executable, not a symlink, nothing
  unusual about the inode.
- **Contents:** a colorized `git diff STATUS.json` snapshot, captured at the
  moment `STATUS.json` first showed `world_bank_id: null -> world_bank_v1`
  (i.e., right around world-bank completion). Non-sensitive (a JSON
  heartbeat diff; no credentials).
- **Hash:** not captured. It was deleted in the prior turn (after read-only
  proof, per that turn's own explicit instruction) before a hash was asked
  for; recomputing one now is not possible since the file no longer exists.
  Flagging this gap rather than fabricating a value.
- **References:** none found anywhere in the tree (`grep -rn` for the
  filename returned nothing); not git-tracked, not git-ignored, no sibling
  debris of the same kind.
- **Likely origin:** the filename is `systemctl is-enabled tbots-fitness-v2.timer`
  missing its leading `s`, and the timing (07:19:45Z) falls in the ~10-minute
  window before the "FIRST REAL CAMPAIGN BURN-IN" GO arrived -- consistent
  with a shell-redirect mishap in a separate, parallel NODE session (not
  anything any of my own commands could have produced; none of them used
  output redirection).
- **Action:** removed (`rm`) after proof, in the prior turn. Nothing else in
  the worktree touched.

## 1. Runtime dependencies

**Zero third-party Python packages.** Every `scripts/*.py` import is either
the Python 3 standard library (`argparse, bisect, contextlib, copy, csv,
dataclasses, datetime, fcntl, hashlib, io, json, math, os, pathlib, random,
re, shutil, sqlite3, statistics, subprocess, sys, time, typing, urllib`) or
an internal, same-repo module. No `requirements.txt`, `pyproject.toml`,
`Pipfile`, or `setup.py` exists anywhere in the repo -- there was never a
declared dependency set because there are no external ones. `fcntl` (used by
`worker_lock.py`'s advisory `flock`) requires a POSIX platform.

The host's system `pip3 list` (Debian's system Python) includes `anthropic`,
`requests`, `pydantic`, etc. -- these belong to *other* tools on NODE (Claude
Code itself, `apt`/`reportbug` machinery), not to TBOTS. TBOTS does not
`import` any of them.

## 2. Python / package / runtime versions

- **Python:** 3.13.5 (`/usr/bin/python3`, Debian's system interpreter --
  no venv).
- **OS:** Debian GNU/Linux 13 "trixie", kernel `6.12.74+deb13+1-amd64`.
- **Git:** whatever `apt` ships on trixie (not separately pinned by the
  project); `worker_git.py`/`worker_gate.py` shell out to the `git` binary
  via `subprocess.run(["git", ...])`, so any reasonably current `git` works
  -- no version-specific flags observed beyond `merge --ff-only`,
  `rev-parse`, `fetch`, `push`, `show`, `add`, `commit`, `status`.
- **No compiled/native extensions, no Docker/container tooling used on
  NODE today** -- the worker runs as a bare `python3` process under
  `systemd` (currently disabled) or a bare `setsid nohup` background job
  (how every burn-in this session was actually run).

## 3. CPU / RAM / storage measurements from burn-in (this session, measured live)

**Host (NODE):** Intel Core i5-4310U (2 cores / 4 threads, 2.0-3.0GHz), 7.7GiB
RAM, 225GB disk (199GB free at session start). A modest 2019-class ULV laptop
CPU -- everything below ran comfortably on it.

**World-bank generation** (`_generate_world_bank`, one-time per world-bank
id): single-threaded, ~92-99% of one core the entire time (never blocks;
`voluntary_ctxt_switches` stayed near-flat across samples), RSS 53-69MB
observed across the run (grew slowly, no runaway). **Wall time this run:
~54 minutes** (launch ~03:33Z, `STATUS.json` world-bank-complete write at
04:27:23Z) for 3 historical + 16 synthetic worlds -- this is *after* the
2026-09-28 `statistics.mean`->`math.fsum` perf fix and *after* today's Shock
market-distance exemption (which removes Shock's expensive
draw-reject-retry loop entirely). The systemd unit's own comment says an
earlier, unfixed pass measured "~30-60 min per pass" for calibration alone,
before this session's fixes -- today's full 19-world generation landing in
~54 min end-to-end is consistent with, not contradicting, that note, and is
dramatically faster than the *original* (pre-fix, pre-exemption) run, which
burned ~7h54m and ~18h in two separate incidents without ever completing.

**Campaign generation evaluation** (`_run_next_campaign`, population 50
against 19 worlds): the first measurement ever taken of this phase. Generation
0 completed (including process startup and reloading the 39MB world-bank
JSON) in well under 90 seconds; generation 1 in roughly 20-25 seconds;
generation 2 similarly fast after a fresh process restart. CPU 82-97% of one
core, RSS 67-69MB. **This is the single most important sizing datum in this
inventory: a full 11-generation campaign (generations 0-10) is very
plausibly a low-single-digit-minutes operation on this class of hardware**,
not hours -- radically cheaper than world-bank generation itself. (Caveat:
only 3 of 11 generations were actually observed; if evaluation cost grows
with population/fitness complexity in later generations this could shift,
but nothing in the code suggests per-generation cost is generation-number-
dependent.)

**Storage:**
- `world_bank_v1.json`: 39,834,487 bytes (~38MB), 19 worlds (3 historical +
  16 synthetic), written once, never modified in place.
- Generation checkpoints: 72,746 / 78,322 / 82,245 bytes for generations
  0/1/2 respectively -- growing ~5-10KB/generation (population + ranking
  data), plateauing is expected but not yet observed past generation 2.
- Repo total: 148MB (`.git`: 33MB, `evolution/`: 79MB [world bank + 3
  generation checkpoints + all frozen protocol/amendment manifests],
  `experiments/`: 5.7MB, DEVELOPMENT data bundle: 2.9MB).
- `MIN_FREE_BYTES` disk gate: 10GB hard floor (`worker_disk.py`) -- the
  worker refuses to proceed (stop code `R`, deletes nothing) below this,
  regardless of how much smaller the repo actually is.

## 4. World-bank size

19 worlds total (3 historical H1/H2/H3 + 16 synthetic, 4 per family:
distributional/execution/sequence/shock) = 39.8MB as one JSON file,
`evolution/state/world_bank_v1.json`. Ceiling: `MAX_TOTAL_SYNTHETIC_WORLDS =
32` (8 per family, `MAX_WORLDS_PER_FAMILY = 8`), reached via +1-world-per-
family expansions after every completed 5-campaign batch (handoff §50).
**Each expansion writes an entirely new, immutable world-bank file (`v2`,
`v3`, ...) -- never a delta, never overwritten.** Projected sizes at
roughly linear per-world growth (~2.1MB/world): v2 (23 worlds) ~48MB, v3 (27
worlds) ~56MB, v4 (31 worlds) ~64MB, v5 (35 worlds, ceiling reached) ~70MB.
All coexist permanently in the repo and in git history.

## 5. Checkpoint size and cadence

Per `worker_git.py`'s contract ("commit+push once per generation plus per
state change -- never per artifact"), every durable unit of work is its own
commit, immediately pushed and verified (`local HEAD == origin/<branch>`)
before the process returns:

- 1x world-bank checkpoint per world-bank id (initial + each expansion) --
  the large one (tens of MB).
- 1x checkpoint per generation per campaign (11 generations x 5 campaigns =
  55 commits per batch), ~73-85KB each based on measured gen 0-2.
- 1x nominee-evaluation checkpoint per campaign (5 per batch), size not yet
  observed (no campaign has reached nomination in this burn-in) -- expect
  small, a single evaluated genome record.
- 1x admission-decision checkpoint per completed 5-campaign batch, size not
  yet observed -- expect small (5 nominee summaries + a ratio/verdict).
- **`STATUS.json` is never committed by the worker, at any phase, by
  design** -- confirmed by reading every `write_status()` call site; none
  passes `STATUS_PATH` into `commit_and_push`. It is a local-only heartbeat.
  Verified separately: a handful of *manual*, human-driven STATUS.json
  commits exist in history (`bb7cbf9`, `8371805`, `f806501`, `58ab3b8`), all
  at pre-campaign protocol-freeze milestones, never as routine per-
  generation bookkeeping.

Rough total per fully-completed batch (55 generation + 5 nominee + 1
admission commits = 61 commits): dominated by the 55 generation checkpoints,
~55 x 80KB ~ 4.4MB, plus whatever the nominee/admission records add (small).

## 6. Campaign/generation restart semantics

- **One process invocation = one unit of work**, and for the campaign phase
  specifically, "one unit of work" means *the entire campaign* -- all
  generations 0 through `FINAL_GENERATION` (=10), each individually
  checkpointed+pushed as the loop proceeds, in a single `python3
  scripts/fitness_v2_worker.py` run. Interrupting mid-campaign is the only
  way to stop between generations; there is no built-in per-generation
  pause.
- **Crash safety is two-layered:**
  1. `worker_checkpoint.py`: stage-then-hardlink-commit. Every artifact is
     written with an exclusive create + fsync, a transaction manifest
     (per-artifact sha256) is fsync'd *last*, and `recover_pending_
     transaction()` (called at the top of every `run_one_step()`) either
     finishes a half-committed transaction via hash-verified hardlink or
     discards only inert pre-manifest staging debris. **Hardlinking requires
     the stage and final paths to be on the same filesystem/device** --
     `os.link` raises `EXDEV` across mount boundaries. This is a real
     constraint if `evolution/state/` were ever placed on a separate volume
     from the repo root in the cloud.
  2. `worker_lock.py`: non-blocking `flock` on `.tbots.lock` -- a second
     concurrent invocation refuses to start rather than queuing
     (`LockHeldElsewhere`, clean exit code 0). Requires a real POSIX
     filesystem with working `flock` semantics (local disk/EBS fine; many
     network filesystems, including some NFS configurations, are not
     reliable for this).
- **Resume point comes from local `STATUS.json`, not git.** `_advance()`
  branches on `status.world_bank_id`/`status.campaigns_complete`/
  `status.generation`, all read from the local file. `_resume_campaign_state`
  specifically: if `status.campaign_seed == seed and status.generation is
  not None`, it reloads `next_population`/`used_genome_ids` from that exact
  generation's checkpoint file and resumes at `generation + 1` -- proven live
  this session (restart correctly resumed at `resume_from: 2`, genome IDs
  accumulated strictly monotonically across the restart with zero overlap:
  90 -> 130 -> 170 used ids, each generation's set a proper subset of the
  next).
- **Important asymmetry, confirmed by reading the code, not yet exercised
  live:** if `STATUS.json` itself is lost (not just the process killed --
  the *file* gone, e.g. instance replacement without carrying local state),
  `read_status` returns `None` and `_advance(None)` unconditionally calls
  `_generate_world_bank` again -- there is no fallback that reconstructs
  `world_bank_id`/`campaigns_complete`/`generation` from the durable,
  git-committed checkpoint files that are still sitting right there in
  `evolution/state/`. Worst case this is wasteful (~54 min re-derivation)
  rather than unsafe (the checkpoint-commit hardlink logic refuses to
  silently overwrite a differing artifact), but it is a real gap for cloud
  instance-replacement scenarios. See §14.
- **Audit gate runs `git fetch origin <branch>` on every single invocation**
  (`worker_gate.check_audit_gate`), fast-forwards local HEAD if the remote
  is a strict ancestor-compatible descendant, and refuses to proceed
  (`stop_code C`) on any non-fast-forward divergence, or `stop_code H` if
  `FREEZE_AUDIT_HOLD.md` exists on the remote branch tip. This makes GitHub
  the de facto coordination point for any two workers (NODE and a future
  cloud instance) that might ever point at the same branch -- see §16.

## 7. Hard-coded NODE paths / assumptions

The Python code itself is **not** hardcoded to `/opt/evolutionary-markets`:
`REPO_ROOT = Path(__file__).resolve().parents[1]` and every checkpoint/
protocol/parameter-freeze path is built relative to that. The code would run
correctly from any directory the repo is cloned into.

What *is* hardcoded to `/opt/evolutionary-markets` (and to NODE specifics)
lives entirely in the **deployment surface**, tracked in-repo at
`ops/node/`:

- `ops/node/tbots-fitness-v2.service`: `WorkingDirectory=/opt/evolutionary-markets`,
  `ExecStart=/usr/bin/python3 /opt/evolutionary-markets/scripts/fitness_v2_worker.py`,
  `Documentation=file:///opt/evolutionary-markets/AGENTS.md`, `User=rick`.
- `ops/node/tbots-fitness-v2.timer`: no path references itself, but its
  `Documentation=` line also points at `file:///opt/evolutionary-markets/AGENTS.md`.
- NODE's root-helper sudoers grant (`/usr/bin/install -d -o rick -g rick
  /opt/evolutionary-markets`) provisions this exact path.
- Ad hoc burn-in logs this session were written to `~/tbots_burnin_run*.log`,
  `~/tbots_campaign_run1*.log` -- these are throwaway, not part of the
  application, and not present on any fresh checkout.

Additionally, a **non-filesystem hardcoded assumption**:
`BRANCH = "feat/node-resident-fitness-v2-autonomous-evolution-20260925"` is
a literal constant in `fitness_v2_worker.py` -- every commit/push/fetch/
audit-gate check targets this exact branch name. A cloud instance running
the same code must either target this same branch (recommended for parity)
or the constant must change identically everywhere it's read.

## 8. systemd assumptions

- `Type=oneshot`, `User=rick`, no `Restart=` (a stop or crash both simply
  end the run; only the *next* timer firing or manual invocation decides
  whether to retry -- systemd never retry-loops a real STOP).
- `TimeoutStartSec=86400` (24h hang backstop; explicitly "generous, not
  tight" per the unit's own comment, since one invocation can run a full
  11-generation campaign).
- Timer: `OnBootSec=2min` then `OnUnitActiveSec=5min` (measured from the
  *end* of the last run, so a still-running generation is never joined by
  an overlapping firing), `Persistent=true` (a missed firing while the
  machine was off runs once on next boot instead of being silently
  skipped), `AccuracySec=30sec`.
- **Currently disabled on NODE for both service and timer** -- every run
  this session (world-bank + all 3 generations) was launched manually via
  `setsid nohup ... &`, not through systemd at all. The unit files are
  tracked in-repo and ready, but have never yet been the actual execution
  path for a real burn-in.
- `openclaw-root-helper` on NODE does *not* have `tbots-fitness-v2.service`/
  `.timer` on its per-service allowlist -- enabling/starting them requires a
  direct `sudo systemctl` (password-gated) rather than the passwordless
  helper. Not a cloud-relevant fact per se, but relevant if NODE's own
  systemd path is ever exercised for comparison.

## 9. Git persistence assumptions

This is the core of the durability model: **git (specifically, this one
GitHub repo/branch) *is* the durable store**, not local disk. Every
checkpoint artifact is committed and pushed, with a hard verification
(`local HEAD == origin/<branch>`, via `git fetch` + `rev-parse` comparison)
before the process considers the unit of work done -- `worker_git.py`'s
`commit_and_push` raises `GitCadenceError` if the push didn't land, rather
than silently continuing. There is no code path that commits without also
verifying the push landed.

Consequences for migration:
- The cloud instance needs push access to `https://github.com/Racer-01111000/tbots.git`
  on this exact branch -- not read-only, not a fork.
- The audit gate's `git fetch` + `ff-only merge` on every invocation means
  GitHub, not any node-to-node channel, is the coordination point if NODE
  and a cloud instance were ever run against the same branch concurrently
  (the flock lock only prevents concurrency *within one machine*; across
  machines, safety currently rests entirely on `stop_code C`'s non-fast-
  forward detection plus each side's own `flock`, which is why running two
  autonomous workers against the same branch/seed set at once is not
  something this architecture is designed to make safe -- avoid it, or give
  a cloud instance a different branch/world-bank id until parity is proven).
- `commit_checkpoint`'s hardlink-commit pattern is local-filesystem-only;
  the git commit/push happens *after* that local step succeeds, as a
  separate operation (`_checkpoint_and_push` calls `commit_checkpoint` then
  `commit_and_push`). A crash between those two steps leaves a locally-
  complete, durable-on-disk checkpoint that simply hasn't been pushed yet --
  `recover_pending_transaction()` only handles the local half; nothing
  currently re-attempts a dropped push on the next invocation (each fresh
  `run_one_step()` starts its own new unit of work, it doesn't check "did my
  last local checkpoint actually get pushed"). Worth flagging, not
  necessarily worth fixing before cloud migration -- `worker_gate`'s
  fetch-and-compare on the *next* invocation would still catch a real
  divergence.

## 10. Local-disk assumptions

- `worker_checkpoint.py`'s stage-then-hardlink-commit pattern (§6) requires
  stage and destination on the same filesystem.
- `worker_lock.py`'s `flock` requires a filesystem with working advisory
  locking.
- `.tbots.lock` and `.worker_checkpoint_transaction.json` /
  `.worker_checkpoint_stage_*` live at the repo root and are git-ignored
  (confirmed in `.gitignore`) -- correctly excluded from the durable
  git-backed store; they are purely local, ephemeral coordination state,
  reconstructed or cleaned up on every invocation's `recover_pending_
  transaction()`/`cleanup_orphan_stages()`.
- No use of any local database engine at runtime (`sqlite3` appears in the
  import list only from a stdlib import elsewhere, not from Fitness V2's own
  code path -- not confirmed further, low priority).

## 11. Secrets / config requirements

- **Git push authentication:** NOT an SSH deploy key. `git remote -v` shows
  HTTPS (`https://github.com/Racer-01111000/tbots.git`); the actual auth is
  via GitHub CLI (`gh`) configured as git's credential helper
  (`credential.https://github.com.helper = !/usr/bin/gh auth git-credential`
  in `~/.gitconfig`), backed by an OAuth token in `~/.config/gh/hosts.yml`
  (mode 0600) with scopes `gist, read:org, repo, workflow`, logged in as
  account `Racer-01111000`. **Per `AGENTS.md`'s explicit instruction ("Never
  read, print, copy, commit, or report authentication tokens or
  credentials"), the token value itself was never read or printed here --
  only its existence, mechanism, and scopes, via `gh auth status`'s own
  redacted output.** A cloud instance needs an equivalent, independently
  issued credential (a fresh `gh auth login` or a scoped fine-grained PAT
  with `contents:write` on this one repo is the minimal footprint) --
  copying NODE's token is not recommended even if it were technically
  possible without violating the "never copy" instruction.
- **No other secrets found anywhere in the runtime path.** No API keys, no
  cloud credentials, no brokerage/exchange credentials -- consistent with
  `AGENTS.md`'s scope statement that real-money execution is never
  authorized by this policy; everything evaluated is simulated against
  pre-existing, git-tracked historical/synthetic worlds.
- **Frozen protocol config is entirely in-repo, content-hashed, and
  version-controlled** (parameter freeze, formula definitions, warmup
  amendment, execution-diversity amendment, shock-distance amendment,
  complete-protocol manifest) -- no external config service, no environment
  variables read by the worker for any of this (grep for `os.environ`
  across `scripts/*.py` found nothing in the worker's own path).

## 12. Network requirements

- **Outbound HTTPS to `github.com` (and `api.github.com` for `gh` CLI
  auth-token refresh)** -- required on *every* invocation (the audit gate
  fetches even before deciding whether to do any work), and required again
  at the end of every checkpoint (`commit_and_push`).
- **No other outbound network call exists in the worker's execution path.**
  `urllib` is used only in `scripts/fetch_raw.py`, a separate, manual,
  offline data-ingestion tool (not imported by `fitness_v2_worker.py` or
  anything it calls) -- not part of the autonomous loop.
- The DEVELOPMENT market-data bundle (§13) is loaded entirely from local,
  git-tracked files -- no network fetch of market data at runtime.
- Inbound network: none required. The worker is a batch process with no
  listening socket.

## 13. Data that must be durable across instance replacement

- **`evolution/state/*.json`** (world bank + generation/nominee/admission
  checkpoints) -- already durable via git commit+push; a fresh clone of the
  branch on any new instance recovers all of it byte-for-byte.
- **`evolution/protocol/*.json`** (parameter freeze, all amendments,
  complete-protocol manifest, `SUPERSEDED.json` registry) -- same, already
  git-durable.
- **`data/development_bundles/s5adev_98e2f764.../*.csv` + manifest`** (the
  authorized DEVELOPMENT market-data bundle, 2.9MB) -- **confirmed
  git-tracked**, via a deliberate `.gitignore` allowlist (`data/
  development_bundles/*` is ignored by default, then the one authorized
  revision's exact files are explicitly un-ignored). A fresh clone brings
  this automatically -- no separate S3/data-transfer step needed for this
  data specifically.
- **`STATUS.json`** -- **not** durable today (local-only by design, §6/§9).
  This is the one piece of "current progress" state that instance
  replacement would lose outright, with no reconstruction path from the
  otherwise-durable checkpoint files. Treat this as the single most
  important gap to close (or explicitly accept) before cloud becomes
  authoritative -- see §14/§17.
- **`.tbots.lock` / `.worker_checkpoint_transaction.json` / stage dirs** --
  correctly NOT durable (ephemeral coordination state, git-ignored,
  reconstructed every invocation). Losing these on instance replacement is
  fine and expected.
- **GitHub itself** is therefore the actual durability backbone for
  everything except the live-progress pointer.

## 14. Known gap (flagged, not fixed): STATUS.json loss forces full world-bank re-derivation

Confirmed by reading the code (§6), not yet exercised live: if `STATUS.json`
does not survive an instance replacement, the next invocation has no way to
learn that `world_bank_v1.json` (or a later campaign's progress) already
exists and is git-durable -- it will call `_generate_world_bank` again from
scratch. Not unsafe (the hardlink-commit logic refuses to silently overwrite
a differing artifact if the regenerated content ever disagreed with what's
already committed -- and given fully deterministic seeds/inputs it
shouldn't differ), but wasteful: ~54 minutes of recomputation measured this
session, and proportionally more once further expansions/campaigns have run.
Recommend this be resolved (STATUS.json reconstruction from durable
checkpoints, or making STATUS.json itself durable via the same git
mechanism) before cloud replaces NODE as authoritative -- not before this
inventory, per this GO's scope.

## 15. Proposed split

The single `fitness_v2_worker.py` monolith already has natural seams along
its own phase boundaries (`_generate_world_bank` / `_run_next_campaign` /
`_finalize_or_expand`) and its own docstring's phrase "the only code path
authorized to touch real DEVELOPMENT data." A three-way split maps cleanly
onto that, and onto AGENTS.md's own explicit boundary ("real-money
brokerage execution... never authorized"):

- **TBOTS Research** -- world-bank generation + reference calibration
  (`fitness_v2_world_bank.py`, `fitness_v2_protocol.py`'s descriptor/
  calibration functions, `s5a_development_bundle.py`). The expensive,
  CPU-bound, DEVELOPMENT-data-touching phase (~54 min measured). Natural
  home for the heaviest/burstiest compute; runs rarely (once per world-bank
  id, i.e. once per expansion event, not every generation).
- **TBOTS Decision** -- the evolutionary loop itself: population breeding,
  per-generation evaluation, campaign nomination, 5-campaign-batch admission
  decision (`fitness_v2_campaign.py`, `_run_next_campaign`/
  `_finalize_or_expand`/`_expand_and_continue` in `fitness_v2_worker.py`).
  This is the frequent, cheap-per-invocation (seconds, measured) workload --
  the part that would actually run on a timer/schedule most often.
  **"Decision" here means the rank-1/admission research decision (which
  simulated genome becomes research champion) -- not a live trading
  decision; AGENTS.md's "never authorized" boundary still applies.**
- **TBOTS Execution** -- this name is worth flagging explicitly: in the
  *current* codebase, "execution" only ever means the Execution *synthetic
  world family* (one of the four world types genomes are evaluated against)
  and `fitness_v2_simulator.py`'s in-process backtest simulation
  (`simulate_passive_comparator`, order/fill mechanics *inside* a
  simulated world) -- there is no real order-routing or brokerage
  connectivity anywhere in this repo, and per AGENTS.md that stays
  categorically out of scope. If "TBOTS Execution" is meant as a future,
  separately-authorized service, it should be built and gated
  independently, never as a natural extension of this split; if it's meant
  as this repo's existing simulate-only evaluation engine
  (`fitness_v2_simulator.py`), that already lives inside Decision's
  per-generation evaluation loop and doesn't need to be its own service
  for V1.

All three would still share: the frozen `evolution/protocol/` manifests
(read-only inputs), the git-persistence contract (`worker_git.py`,
`worker_checkpoint.py`, `worker_lock.py`, `worker_gate.py`, `worker_
status.py` -- none of these are family-specific, all are safe to share
as-is), and the same branch/repo as the coordination point.

## 16. Minimum AWS instance sizing, based on measured NODE behavior

Measured peak RSS across every phase this session: under 70MB. Measured CPU:
single-threaded, one full core saturated, never more (the code has no
threading/multiprocessing anywhere observed). NODE itself is only a 4-vCPU,
7.7GB laptop-class machine and was never remotely stressed.

- **Research (world-bank generation):** 1 vCPU is fully used (single-
  threaded); more vCPUs buy nothing without code changes. 1-2GB RAM is
  generous headroom over the measured <70MB. This is the only phase worth
  giving a slightly larger instance to, purely to reduce wall-clock (a
  faster single-core clock speed helps directly; NODE's 2.0-3.0GHz ULV core
  is not a demanding bar to beat) -- e.g. a `c7g.medium`/`c6i.large`-class
  single-vCPU-workload instance (burstable `t4g.small`/`t3.small` would
  also comfortably cover the measured RAM, though a non-burstable compute-
  optimized type avoids any CPU-credit throttling risk during the ~1-hour
  compute-bound run).
- **Decision (per-generation campaign evaluation):** measured 15-90 seconds
  per generation, RAM identical order of magnitude. Even a `t4g.micro`/
  `t3.micro` (1-2 vCPU burstable) would be oversized for what was actually
  observed; the real constraint here is invocation *frequency* (systemd
  timer every 5 min) and reliable network access to GitHub, not compute.
- **Storage:** repo currently 148MB, growing by ~2-8MB per completed batch
  (§5) plus ~8-30MB per world-bank expansion event (§4) -- an 8-20GB root
  volume comfortably clears the `worker_disk.py` 10GB floor for a very long
  time; revisit sizing once multiple batches/expansions have actually run
  and the growth rate is confirmed rather than projected.
- **Bottom line: this workload is not resource-constrained at all on the
  hardware already proven to run it.** The limiting factors for cloud
  sizing are reliability (don't get CPU-throttled mid-generation on a
  burstable instance if avoidable) and network access to GitHub, not raw
  CPU/RAM/disk headroom.

## 17. Recommended durable storage/state design

- **Keep git/GitHub as the durability backbone for checkpoints** -- it's
  already proven, already fail-closed (push-verified), and already the
  cross-machine coordination point via the audit gate. Don't introduce a
  second, competing durable store for the same data (e.g. also writing
  checkpoints to S3) without also making the two consistent; that's new
  complexity the current design doesn't need.
- **Do add durability for `STATUS.json` specifically** (§14) before cloud
  becomes authoritative -- either (a) reconstruct it from the latest
  git-committed checkpoint on startup if the local file is missing
  (cheapest, no protocol change, no new infra), or (b) start committing it
  like every other artifact (simplest to reason about, but a behavior
  change to a currently-intentional "never committed" design, and would
  need `worker_git`'s "once per generation... never per artifact" cadence
  respected rather than bypassed). Reconstruction (a) is the lower-risk
  choice and doesn't touch the existing, tested commit cadence at all.
- **Use a real EBS (or equivalent block-storage) root volume, not an
  instance-store/ephemeral-only volume, and not a network filesystem for
  `evolution/state/`'s stage-then-hardlink path** (§6/§10) -- the hardlink-
  commit and `flock` mechanisms both need genuine local POSIX semantics.
- **Size storage for the *repo*, not for any single artifact** -- the
  world-bank files are the only large individual files (tens of MB each,
  accumulating, never deleted), everything else is small; an 8-20GB volume
  is generous for a long time based on measured growth rates (§16), cheap
  to over-provision relative to compute cost, and should be revisited with
  real multi-batch data once available rather than projected further now.

## 18. Migration sequence that proves NODE/cloud deterministic parity before AWS becomes authoritative

Proposed, staged, each step gated on the previous one -- no step here has
been executed; this is a sequence proposal only, per this GO's "do not
deploy yet."

1. **Read-only environment parity check on the target instance:** Python
   3.13.x present, no other dependency needed (§1/§2) -- trivial to verify,
   no repo access required yet.
2. **Clone the exact branch** (`feat/node-resident-fitness-v2-autonomous-
   evolution-20260925`) onto the cloud instance with an independently
   issued, scoped push credential (§11) -- confirm `git rev-parse HEAD`
   matches NODE's and matches `origin` exactly (the same three-way check
   this session performed repeatedly), and confirm the DEVELOPMENT data
   bundle's content-hash validation (`s5a_development_bundle.py`'s
   fail-closed loader) passes on the clone with zero changes.
3. **Run the full test suite on the cloud instance** (694 tests as of this
   HEAD) and diff the result against NODE's last full-suite run
   (`experiments/fitness_v2_shock_distance_amendment_20260929/
   full_suite.log`, 694/694) -- byte-identical pass/fail outcome is the
   parity bar, not just "green."
4. **Deterministic replay, not new work:** on the cloud instance, with the
   real worker *not yet* pointed at a live campaign, recompute
   `world_descriptor_vector`/`development_reference_calibration` (or an
   equivalent read-only harness) against the *same already-committed*
   `world_bank_v1.json` and compare byte-for-byte against NODE's own
   values already embedded in that checkpoint -- proves the pure-Python
   math (no numpy/BLAS version sensitivity, since there is none, §1) is
   bit-identical across the two environments before any new,
   irreversible campaign work is attempted from the cloud side.
5. **One supervised, bounded cloud-side generation, exactly as this
   session's NODE proof did:** launch the worker once from the cloud
   instance against a *scratch* branch (not the real one, to avoid a
   two-writer race against NODE per §9), let it complete generation 0 of a
   throwaway campaign, interrupt it, restart it, and confirm the same
   resume/no-duplication/no-seed-reuse properties measured on NODE this
   session (§6) -- this is the direct cloud-side repeat of today's NODE
   acceptance proof, on the same code, same branch topology pattern, just
   a disposable branch.
6. **Only after 1-5 all pass identically:** authorize (separately, a future
   GO) pointing a cloud instance at the *real* branch for genuine campaign
   work, with NODE explicitly stepped back to fallback/laboratory status
   for that branch (never running the worker against the same branch
   concurrently from both places, per §9's two-writer caution) rather than
   decommissioned.

## Current HOLD state (end of this inventory)

- HEAD: `6042841d45612ca0b484e78a6ec0fda014d32a22`, NODE == remote, verified.
- No worker process running on NODE (confirmed via `pgrep`).
- `evolution/state/`: exactly `world_bank_v1.json` + generation 0/1/2
  checkpoints -- unchanged since burn-in acceptance.
- `tbots-fitness-v2.service`/`.timer`: both `disabled`.
- Working tree: `STATUS.json` modified (local-only, by design, §6) --
  nothing else. Stray debris file removed, proof recorded in §0.
- No new evolution started while preparing this inventory.
