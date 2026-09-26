# GO Addendum A reconciliation, 2026-09-26

Authority: Rick, instruction delivered 2026-09-26 with the full text of
`GO ADDENDUM A -- Unattended operation (TBOTS Fitness V2)` re-attached:
"Reconcile its campaign rules and stop conditions against the current
branch at 93e0853; its older commit references are historical, so do not
reset the branch."

Reconciled against the branch at commit `93e08539b42b63e63f93c2854e7dd5df5e74980c`
(the seed-casing decision commit -- current HEAD at the start of this pass).

## Material conflict found and fixed

**`scripts/worker_status.py`'s `state` enum did not match Addendum A
section 4's schema.**

Addendum A's exact schema comment:
```
"state": "running | audit_window | stopped | hold | complete"
"phase": "protocol_freeze | freeze_audit_window | world_bank | baseline |
          campaign | admission | champion_decision | expansion | terminal_hold"
```

The already-shipped module instead had:
```python
VALID_STATES = {
    "audit_window", "idle", "world_bank", "baseline", "campaign",
    "admission", "terminal_hold", "stopped",
}
```

This conflated the two axes: `state` (coarse -- overall operational
status) and `phase` (fine -- what the worker is doing within that state)
are two different fields with two different enums in Addendum A, and this
module had accidentally put several PHASE-only values (`world_bank`,
`baseline`, `campaign`, `admission`, `terminal_hold`) into the STATE enum,
used `terminal_hold` where Addendum A section 3 explicitly says
`state: "hold"` on reaching the terminal goal, and never allowed
`"running"` or `"complete"` at all -- both explicitly documented state
values that the module would have rejected.

**No PHASE validation existed at all** -- `phase` was accepted as any
string, unchecked against Addendum A's 9-value enum.

**Why this matters**: the currently-persisted `STATUS.json` on Node
happens to use `state: "audit_window"` and `phase: "freeze_audit_window"`,
both of which were coincidentally valid under the old (wrong) enum too,
so no bad file was ever written. But the module itself would have
accepted or rejected the wrong values once the (not-yet-built) campaign
runner started writing `state: "running"`/`"hold"`/`"complete"`, or a
correctly-named phase -- catching this now, before that code exists,
rather than after.

**Fix applied**: `VALID_STATES` is now exactly Addendum A's 5 values
(`running`, `audit_window`, `stopped`, `hold`, `complete`); a new
`VALID_PHASES` set holds exactly its 9 phase values and `build_status`
now validates `phase` against it. The existing cross-field rule (a
`stop_code` is required if and only if `state == "stopped"`) is unchanged
and remains correct: `"hold"` (the successful terminal-goal outcome, per
section 3) carries no `stop_code`, distinct from `"stopped"` (an
interrupted/blocked run, GO section 6 letters A-G plus H/R), and from
`"stopped"` + `stop_code: "H"` (the audit-hold case, section 2.3 --
`FREEZE_AUDIT_HOLD.md` present -- a different thing from the terminal
`"hold"` state despite the similar English word).

5 new tests added (`test_accepts_every_documented_state`,
`test_accepts_every_documented_phase`, `test_rejects_unknown_phase`,
`test_state_and_phase_are_separate_axes_not_interchangeable`,
`test_hold_state_does_not_require_a_stop_code`). Full suite: see commit.

## Everything else checked and found consistent -- no conflict

- **Campaign structure** (section 3: campaigns 1-5, Gen0-Gen10 each,
  withheld rank-1 evaluation, 19-world admission, 3-of-5 rule, expansion
  +1/family per completed batch until 32 synthetic worlds) matches
  `fitness_v2.py`'s `decide_admission` (`predeclared_n=5`,
  `required = predeclared_n // 2 + 1 = 3`) and the parameter-freeze
  manifest's `campaign_batch` (`campaign_count: 5, admission_required: 3`)
  and `expansion` (`after_each_complete_campaign_batch: 4`,
  `family_allocation`: 1 each, `synthetic_world_hold_count: 32`) exactly.
- **Stop condition C** (section 2.4: "the worker pulls with `--ff-only`.
  A non-fast-forward is stop condition C") matches `worker_gate.py`'s
  `check_audit_gate`, which already returns `stop_code: "C"` on exactly
  this condition.
- **STATUS.json field set** (section 4) matches `worker_status.py`'s
  `FIELDS` tuple name-for-name and in the same order (the enum bug above
  was a validation-logic bug, not a field-set mismatch).
- **Single-writer lock** (`.tbots.lock`, section 5) matches
  `worker_lock.py` exactly.
- **Disk floor** (10 GB free -> `stop_code: "R"`, delete nothing, section
  5) matches `worker_disk.py`'s `MIN_FREE_BYTES` exactly.
- **Acceptance-proof requirements** (section 6: one full generation with
  zero prompts, `kill -9` resume, real reboot resume, STATUS.json advancing
  and pushed at each step, lock held throughout,
  `experiments/node_autonomy_acceptance_20260926/RECEIPT.md`) -- not yet
  built (the campaign runner and `tbots-fitness-v2.service` do not exist
  yet), so nothing to reconcile against; recorded here as still pending,
  not as a conflict.

## Not yet implemented (pending, not a conflict)

Two operational rules from section 5 have no code yet because the
campaign runner they belong to has not been built:
- git push retried with backoff on failure (current `worker_git.py` raises
  immediately; local durable persistence via `worker_checkpoint.py` is
  already independent of push success, so this does not block anything
  today, but the retry wrapper itself is not written).
- the >5 MB-per-generation `evidence/` hash-not-blob rule, and the
  3x-median stale-progress warning.

Neither is a conflict with anything already committed -- both are simply
work not yet reached.

## Hold status (unchanged)

Per Rick's explicit instruction, world generation remains on hold. At
`audit_window_ends_utc` (2026-09-26T12:29:05Z), proceeding under the
existing research-only GO additionally requires, checked at that moment:
(1) `git fetch` shows no `FREEZE_AUDIT_HOLD.md` on the branch, and (2) the
complete original Sable handoff has been received and reconciled -- the
window's expiry alone never clears the hold. This reconciliation pass
covered Addendum A specifically; it does not by itself constitute
receiving or reconciling "the complete original Sable handoff" as a
whole, since Addendum A was already known content before this message
arrived. No live capital, no live orders, no world generated.
