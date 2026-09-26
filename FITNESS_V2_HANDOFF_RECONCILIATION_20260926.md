# Sable-to-Claude handoff reconciliation, 2026-09-26

Authority: Rick, GO Addendum "AUTHORITATIVE HANDOFF RECOVERED" and follow-up
git-retrieval instruction, 2026-09-26.

## Provenance (independently checked, not taken on the document's own word)

The first attempt to deliver this document as a chat paste rendered only a
placeholder (`Pasted markdown(20260926-023842).md`) with no body -- consistent
with the known Codex/terminal paste-swallowing regression already in prior
session memory. What can actually be attested:

- Content matching this document was already present in HOST's
  `~/.claude/paste-cache/7b7050ebb03755da.txt` with mtime
  `2026-09-26T02:37:30Z` -- hours before this reconciliation began, and before
  any pushback on the earlier failed-paste message.
- That local file's SHA-256 is
  `7b7050ebb03755da6799ded647e28bd0408d0a0e67257e0efc5a4b8bc7606214`.
- The same hash was independently reproduced by fetching
  `origin/handoff/sable-to-claude-20260926` (commit `7648f63`, isolated branch,
  confirmed NOT an ancestor of this feature branch) and running
  `git show 7648f63:SABLE_HANDOFF_20260926.md | sha256sum` on Node.
- That commit is authored `Claude <claude@anthropic.local>` at
  `2026-09-26T09:04:52Z` -- i.e. a Claude session, not Rick directly, made the
  isolated-branch commit. Line count (1127) and ending (`75. FINAL AUTHORITY
  ... -- Rick`) match Rick's stated expectation exactly.

Net: the document is byte-identical across three independent access paths
(local cache, git object store, working copy) and predates today's
reconciliation exchange. It is not something assembled on the fly to answer
this session's pushback.

## Method

Every claim below was checked against live repository state on Node
(`/opt/evolutionary-markets`, branch
`feat/node-resident-fitness-v2-autonomous-evolution-20260925`), not accepted
from the handoff's own text.

## Seed constants -- verified against an independent, earlier source

Handoff §40 (5 campaign seeds) and §49 (16 family seeds) match
`FITNESS_V2_PARAMETER_FREEZE_20260925.md` digit-for-digit. That file was
committed 2026-09-25, before this handoff existed in any cache -- this is a
genuine independent cross-check, not circular.

## Apparent contradictions -- all resolved by chronology, not by content conflict

Three STOP files initially read as currently-open blockers
(`FITNESS_V2_SHOCK_WARMUP_CONFLICT_20260926.md`,
`FITNESS_V2_EXECUTION_DIVERSITY_CONFLICT_20260926.md`,
`FITNESS_V2_FORMULA_DEFINITION_STOP_20260926.md`). Reading each in full (not
just the tail) shows each is explicitly checkpointed to an earlier commit
(`008b39a`, `69fa48d`) and is superseded, without being rewritten, by a later
receipt:

- Shock warm-up: `FITNESS_V2_WARMUP_RESOLVED_FORMULAS_PENDING_20260926.md`
  explicitly supersedes the earlier `WARMUP_RESOLUTION_STATUS`'s
  "Shock: unresolved" line -- "Execution and Shock warm-up: resolved... They
  no longer block warm-up readiness." Matches handoff §21 exactly, and matches
  the live `scripts/fitness_v2_warmup.py` (`"shock": {"warmup": "Same
  available real historical warm-up as matching immutable historical
  anchor"...}`, `resolution_status.shock_h1_stop: "resolved by Rick"`).
- Execution diversity exemption: matches handoff §17 exactly and matches the
  live admission code's distance-only exemption.
- Trend/OC1 formulas: `scripts/fitness_v2_formula_definitions.py` implements
  handoff §25 (trend) and §26 (OC1 zero-MAD) verbatim -- same window, same
  Pearson definition, same ±4.0/0.0 zero-MAD sentinels, same >3.0 threshold.

No genuine rule contradiction (handoff §71.A) was found anywhere.

## Handoff §32 remaining-convention list -- checked against `scripts/fitness_v2_protocol.py`

All nine items the handoff itself flagged as still needing a frozen,
documented, tested convention are implemented there, embedded verbatim (via
`RULE_SUMMARY`) in the content-addressed manifest -- confirmed by reading the
live manifest
`evolution/protocol/fitness_v2_complete_protocol_49c71da1...json` directly
(`complete_protocol_ready: true`, all 13 rule keys `0`-`12`/`3b` present,
`gap_threshold_tau` and `sequence_warmup_bars` matching the freeze receipt):

| §32 item | Resolution found |
|---|---|
| Returns semantics | §3#2: adjusted-close simple returns |
| Equal-weight portfolio | §3#3: daily-rebalanced, E0=1.0 (not buy-once) |
| Cross-asset correlation | §3#4: 63-session windows, nested median of 28 pairwise Pearson, reject zero-variance |
| Drawdown structure | §3#5: depth/duration on the §3 equity curve, peak includes E0, mid-window drawdown counts through window end |
| Turnover-opportunity | §3#6: M63 leader momentum, lexicographic tie-break, defined-pairs denominator |
| Gap descriptor | §3#8: pooled 95th-percentile tau (linear interpolation), reject zero exceedances -- matches the live tau=0.0171 in the freeze receipt |
| Distributional reconstruction | §3#9: bar 0 = the real historical anchor price (not an arbitrary normalization) -- avoids exactly the pitfall the handoff warned about |
| Sequence boundaries | §3#10 + the freeze's own §0 addendum: anchor as segment 0, zero join gap, start-0 eligible, anchor counted as warm-up bar 0 |
| Shock transformation | §3#11: exactly 3 donor-gap relocations, multiplicative from destination through world end, no same-asset restriction |
| Zero-MAD edge case (§33) | §3#12: any zero-MAD reference component aborts world-bank construction; no sentinel invented -- and the real calibration run confirmed this never triggered (min MAD 0.00079) |

The variance convention (handoff §31, sample stdev n-1) is `scripts/
fitness_v2_protocol.py`'s §3#1, exactly as specified, and is exercised by a
dedicated reject-below-2-observations test per the freeze receipt's test list.

## Conclusion

Condition 4 ("complete original Sable handoff received and reconciled,
including its campaign rules and stop conditions") is satisfied. No open
scientific ambiguity, no unresolved contradiction, and no invented rule was
found. This reconciliation pass did not generate any world, genome, baseline,
or campaign, and did not touch the frozen S5D champion or S6B evidence.

## Remaining before world generation

Only condition 1 (audit window, ends `2026-09-26T12:29:05Z`) and re-checking
`FREEZE_AUDIT_HOLD.md` against the remote branch at expiry. The campaign
runner / `tbots-fitness-v2.service` (handoff §67, GO step 4) remains unbuilt,
per `FITNESS_V2_COMPLETE_PROTOCOL_FREEZE_20260926.md` §7's own scope
statement -- that is the next work, not yet started.
