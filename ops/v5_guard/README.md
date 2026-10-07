# V5 guard (invocation-scoped) — source snapshot

Durable guard for the TBOTS V5 synthetic-gym research slots (frozen release `0988a26b30ae5f2005e31fa1173f8fc49cb0cc07`). Stdlib-only Python 3 (tested on 3.13.5).
This directory is a **sanitized snapshot**. It contains no credentials, account or host identifiers, market data or run state; it is not a deployment record and installing it is a separate, authorized operation.

`src/guard.py` — SHA-256 `c04f1b233c3bf3ba022492c21e6b2bc2fbaf4cab96a2d66ba04f7d17d3d07caf` (byte-identical to the version installed during the 2026-10-07 repair; see `PROVENANCE.json`).

## What changed vs the 2026-10-05 guard (`2ee5c16f…`)
The post-slot rule "no broker/Alpaca/order lines" used to grep the journal of every `tbots-*` unit since slot start, so an unrelated unit's receipts (e.g. a paper-trading unit printing `broker_order_id`) stopped V5 (slot 06 false positive).
Now `require-token` (ExecStartPre, inside the research unit) records `$INVOCATION_ID` once per slot to `/var/lib/tbots-v5-guard/invocations/research_<slot>.json`, and the check scans only the journal of exactly that invocation, with the same keywords and no exemptions. It fails closed (hard stop: STOP sentinel + research timer disabled) if the record is missing/malformed/mismatched, ExecStopPost's own `$INVOCATION_ID` differs, the journal is empty/unreadable, entries come from another unit, or more than one research invocation falls in the slot window. All other checks are unchanged.

## Layout
- `src/guard.py` — the guard (`preflight`, `require-token`, `check postslot|postslot_timer|watch|final`, `seal-local`).
- `tests/test_guard.py` — 89-check regression suite against a synthetic fake root; no real batch, systemd, journal, network or production path is touched.
- `templates/` — systemd unit/drop-in templates (research drop-ins, guard preflight/check units, timers, selftest units) and `expected.UNSEALED_EXAMPLE.json`.

## Running the tests
From the repo root checked out at the frozen release (the suite needs the release's `research/` tree as its fixture):

    python3 -I ops/v5_guard/tests/test_guard.py        # last line: TOTAL FAILS: 0

To use a different fixture, set `GUARD_TEST_FIXTURE` to a directory containing `research/` (e.g. `git archive 0988a26b research | tar -x -C DIR`). Exit status is non-zero on any failure. Each run leaves ~90 synthetic roots (`gt_*`, ~7 MB each) under `$TMPDIR`; point `TMPDIR` at a disk-backed directory (not a small tmpfs) and delete them afterwards.

## Packaging / installation (target host, root)
1. `guard.py` -> `/usr/local/lib/tbots-v5-guard/guard.py` (mode 0755, root:root; install to a temp name then `mv` for an atomic swap). Interpreter used by the units: `/opt/python-3.13.5-isolated/bin/python3.13`.
2. Units/drop-ins from `templates/` -> `/etc/systemd/system/` (drop-ins under `tbots-foodfuel-research.service.d/`). The timers carry the fixed 2026-10-06..09 calendar of that campaign.
3. Expected state -> `/etc/tbots-v5-guard/expected.json` (0644).

## Configuration sealing
`templates/expected.UNSEALED_EXAMPLE.json` is an **UNSEALED EXAMPLE, not installed production configuration**. Its frozen-release fields (tree manifest, freeze-file and key-config hashes, admission report hash, expiry/calendar values) were verified against release `0988a26b…`. `guard_sha256`, `interpreter.sha256` and `unit_sha` are placeholders; on the target run `guard.py seal-local`, which fills them from the installed guard, interpreter and unit files. Re-run `seal-local` after any guard or unit change. A production file is host-local state and does not belong in git.

## Rollback
Keep the previous `guard.py` and `expected.json` before installing. To roll back: reinstall the previous `guard.py` (atomic `mv`), restore the previous `expected.json`, confirm the guard identity check passes. Rolling back to the 2026-10-05 guard restores the unit-wide journal rule and its false positives. To stop research at any time: disable only `tbots-foodfuel-research.timer`.

## Notes
- `guard.py` contains literal `/home/ec2-user/...` paths: they are the credential locations its probe checks that the research user cannot read. They are behavior, not configuration of this repo.
- Network-boundary, credential, import, hash and validation-policy checks run at preflight (hard stop + timer disable); result/ledger/validation/evidence checks run post-slot.
