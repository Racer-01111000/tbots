# Fitness V2 Complete Protocol Freeze — 2026-09-26

Authority: Rick's GO "TBOTS Fitness V2 pre-result protocol completion"
(2026-09-26), GO Addendum A — Unattended operation (2026-09-26, §2
development-boundary correction and §3 post-2018/pre-2007-02-07 filter
proof), and Rick's post-freeze-audit Sequence warm-up settlement
(2026-09-26, §0 below) — all incorporated into the manifest itself.

**Current manifest** (supersedes the one below):
`evolution/protocol/fitness_v2_complete_protocol_49c71da11d46f56838a560481350cb3436af3177bab6cb13254f9c84860c6e89.json`
(`complete_protocol_ready: true`, schema_version 3).

Superseded, retained as provenance/audit trail (never used by production
code): `evolution/protocol/fitness_v2_complete_protocol_14fe15dd4525ca1314a135b4e63a6a36ab22104e789385917d9e9e8958cd518d.json`
(schema_version 2) — pushed at `e5d1923`/`bb7cbf9`, superseded within the
same audit window before any world generation, per §0 below. Superseding it
changed only the manifest's added fields (`sequence_warmup_rule`,
`sequence_warmup_bars`, `schema_version`, `authority`) — tau, the reference
median/MAD vector, the H-shape anchors, and every other §3 rule are
identical, since the Sequence warm-up decision does not touch H1/H2/H3
calibration at all (real historical anchors have no warm-up/segment
structure). Rebuilt from the same cached calibration result with no
recomputation.

## 0. Post-freeze-audit Sequence warm-up settlement

Rick's freeze audit (independently reverified 465/465, manifest rehash,
sample n-1, strict `>` tau, Sequence start-0, no zero-MAD, filter proof,
boundary arithmetic — all confirmed, no hold file) found one remaining gap
before generation: `join_sequence_segments` emits the anchor as the
Sequence world's own session 0, but rule #9 only defines the anchor as
Distributional's "generated warm-up bar 0" explicitly; rule #10 only says
the anchor "stands in as segment 0" for Sequence's join mechanics, without
saying whether that row counts inside the 378 warm-up bars or before them.
Left unsettled, that becomes a post-freeze convention choice made silently
by whichever world-builder gets written later — the exact failure mode the
freeze exists to prevent.

**Settled, inside the 6-hour audit window, before any world generation**:
the anchor row is warm-up bar 0 for Sequence exactly as for Distributional.
Anchor (bar 0) + 377 bars from the Sequence transformation stream = 378
warm-up bars (joined-sequence indices 0..377); the scored horizon begins at
index 378. The warm-up/scored split is a pure index into the already-joined
sequence — segments (63 intact rows each, per §10) are never split or
realigned at this boundary; a single segment may straddle indices 377/378,
which is expected, not an error (verified directly:
`SequenceJoins::test_warmup_length_is_exactly_378_counting_the_anchor`
builds an 8-segment, 505-row joined world and confirms the cut falls inside
the sixth segment, at row 316–379, not on a 63-row boundary).

Frozen as `SEQUENCE_WARMUP_RULE` / `SEQUENCE_WARMUP_BARS = 378` in
`scripts/fitness_v2_protocol.py`, exposed as `sequence_warmup_and_scored()`,
embedded verbatim in the manifest's `sequence_warmup_rule` /
`sequence_warmup_bars` fields, and exercised by two new tests
(`test_warmup_length_is_exactly_378_counting_the_anchor`,
`test_rejects_sequence_world_shorter_than_warmup`) plus a manifest test
(`test_records_sequence_warmup_rule`). 79 tests in
`tests/test_fitness_v2_protocol.py` (was 76); full suite re-verified.

The manifest hash changed as a direct, expected consequence
(`14fe15dd…` → `49c71da1…`) — content-addressed identity means any real
content change must produce a new hash; this is correct, not a defect. Per
Rick's instruction, the 6-hour post-freeze audit window restarts from this
new push (§6 below).

## 1. Reboot recovery

Recorded separately in `experiments/reboot_recovery_20260926/RECEIPT.md`.
Summary: all §1 decision-rule checks passed independently (HEAD == origin ==
`b6f1b010a3932709fedfd1424f5579b6ef3212c6`, clean worktree, no lock files, no
untracked files, no active tbots/fitness/evolution worker or systemd unit, no
dmesg filesystem errors). One discrepancy noted and non-blocking: live
`uptime` showed NODE up continuously for ~4d22h at the time of the check,
meaning no reboot occurred that morning — the GO's "just had an unexpected
reboot" premise was not corroborated, but every actual safety check passed
regardless, so recovery proceeded per the GO's own decision rule.

## 2. Development-boundary correction (Rick's blocking review, Addendum A §2)

The first implementation windowed rule #0 over 252 consecutive **sessions**,
taking 251 returns per window. Rick identified this as outcome-material and
blocking: a return/gap at session t needs session t-1 as its base, so a
252-session window and a 252-return window are not the same window set, and
conflating them shifts every window's boundary by one session throughout the
world. The running calibration at the time used the incorrect boundary and
was killed and re-run from scratch (pre-result; no contamination, only
wasted wall-clock, per Rick's note).

**Corrected and now frozen, in `scripts/fitness_v2_protocol.py`
(`DEVELOPMENT_BOUNDARY_RULE`, embedded verbatim in the manifest's
`development_boundary_rule` field) and exercised by
`tests/test_fitness_v2_protocol.py`'s `DevelopmentBoundary` test class:**

- No row before a world's own first DEVELOPMENT session is read, for any
  purpose, ever, in calibration or descriptor computation. The Execution/
  Shock real-warm-up amendment governs world construction only and grants no
  exception here.
- The return series for asset i begins at the world's second session (first
  return's base = the world's own first-session close). An N-session world
  yields exactly N-1 returns.
- A 252-window means 252 consecutive **returns** (253 consecutive prices),
  never 252 consecutive sessions. An N-session world yields exactly
  N-1-252+1 = N-252 complete 252-return windows.
- Gaps need close[t-1], so the first gap is also at the world's second
  session; gap windows are sliced from the same return-aligned index as the
  returns themselves.
- Trend and turnover need raw prices (not returns); each 252-return window
  uses the 253 consecutive prices spanning it.
- M63 turnover's first defined value is at the 64th session within a
  window; leader-change denominators count only consecutive pairs where both
  leaders are defined (§3 #6, unchanged).

**Verified against Rick's own worked example**: H1 = 1008 sessions -> 1007
returns -> 1007-252+1 = **756** windows. The corrected implementation
reproduces this exactly (`tests/test_fitness_v2_protocol.py::
DevelopmentBoundary::test_window_count_formula_matches_h1_worked_example`),
and the real calibration run reports the identical count (§4 below).

**Structural no-leakage proof**: `DevelopmentBoundary::
test_no_component_ever_reads_a_negative_index` wraps a world's own arrays in
a list subclass that raises on any negative index or negative-bounded slice
— the only way a "read before the world's own start" could occur in Python,
since there is no data before index 0 to read and a negative index would
otherwise silently wrap to the array's own tail rather than raising. The full
descriptor computation completes with zero such reads.

## 3. Post-2018 / pre-2007-02-07 filter: positive proof, not inventory

`run_calibration.py` now asserts, for every one of the eight raw per-symbol
CSV streams (which run from as early as 1993-01-29 through 2026-08-25, i.e.
they do carry protected and pre-DEVELOPMENT rows), that after filtering:
`min(timestamp) >= 2007-02-07` and `max(timestamp) <= 2018-12-31`, and
records the post-filter row count. All eight assertions passed:

| Symbol | Raw rows | Raw range | Post-filter rows | Post-filter range |
|---|---|---|---|---|
| DBC | 5170 | 2006-02-06..2026-08-25 | 2996 | 2007-02-07..2018-12-31 |
| EEM | 5879 | 2003-04-14..2026-08-25 | 2996 | 2007-02-07..2018-12-31 |
| EFA | 6285 | 2001-08-27..2026-08-25 | 2996 | 2007-02-07..2018-12-31 |
| GLD | 5475 | 2004-11-18..2026-08-25 | 2996 | 2007-02-07..2018-12-31 |
| IEF | 6057 | 2002-07-30..2026-08-25 | 2996 | 2007-02-07..2018-12-31 |
| SPY | 8450 | 1993-01-29..2026-08-25 | 2996 | 2007-02-07..2018-12-31 |
| TLT | 6057 | 2002-07-30..2026-08-25 | 2996 | 2007-02-07..2018-12-31 |
| VNQ | 5511 | 2004-09-29..2026-08-25 | 2996 | 2007-02-07..2018-12-31 |

All eight assets have complete, synchronized daily coverage within the
DEVELOPMENT window itself (2996 post-filter rows each, identical). The
pooled 2996-date common index and `validate_development_source` both also
passed. The full assertion trail is recorded verbatim in
`post_development_filter_proof` inside the manifest itself.

## 4. H1/H2/H3 real calibration results

H-shape starts (2007-02-07, 2011-02-07, 2015-02-07) come from the original
GO §3 #9. "Use the first trading session on or after" (also §3 #9) resolved
two of the three against real data: 2007-02-07 and 2011-02-07 are themselves
trading days; 2015-02-07 is a Saturday, so H3's real anchor is 2015-02-09,
the next trading Monday.

| Shape | Sessions | Returns | 252-return windows | First | Last |
|---|---|---|---|---|---|
| H1 | 1008 | 1007 | 756 | 2007-02-07 | 2011-02-04 |
| H2 | 1007 | 1006 | 755 | 2011-02-07 | 2015-02-06 |
| H3 | 981 | 980 | 729 | 2015-02-09 | 2018-12-31 |

Pooled reference calibration used 756+755+729 = 2240 windows across the
three historical anchors, rather than one continuous 2007-02-07..2018-12-31
252-return stream (~2744 windows, including roughly 504 that straddle the
H1/H2/H3 anniversary cuts).

**Rationale for pooled-per-shape over one continuous stream** (Rick's
review flagged that the choice is documented in the counts but not
justified anywhere; recorded here per his instruction — receipt-only, the
manifest hash is unaffected by this section): rule #0 requires the
DEVELOPMENT reference and every candidate/anchor to be computed by the
identical procedure, and every candidate the reference will ever be
compared against is itself a single-shape world — Distributional and
Sequence worlds are generated to match one H-shape's own length (§3 #9),
Shock's base is one matching historical anchor, and Execution's base path
is one unchanged historical anchor. A reference pooled from a continuous
stream would include ~504 windows that straddle an H1/H2/H3 boundary —
observations no single-shape candidate could ever produce, since no real
candidate world spans two H-shapes end to end. Pooling those in would
compare the candidate against a reference partly built from a structurally
different kind of object, which is exactly what rule #0's "identical
procedure" requirement is written to prevent. Pooled-per-shape keeps the
reference and every candidate drawn from the same population of window
shapes; it is not changed.

Gap threshold (§3 #8), pooled across all eight assets and all three anchors,
linear interpolation (numpy default / Hyndman-Fan type 7), from 23510 pooled
gaps: **tau = 0.017099908776601856**. Pooled gap extrema:
`(-0.12307697618791791, 0.1332944020849367)`.

DEVELOPMENT reference median/MAD vector (§3 #0-#7): all 32 components
computed; **no zero-MAD abort (§3 #12) occurred** — the minimum MAD across
all 32 real components was `0.000790919124370758`, safely nonzero. Anchor
descriptor vectors for H1/H2/H3 were also computed successfully via the same
code path. Full values are recorded in the manifest's `reference_median`,
`reference_mad`, and `h_shapes.anchor_rows` fields.

Wall-clock: reference calibration 1862.6s (~31 min); anchor vectors +
total 3720.4s (~62 min). Pure-Python nested 252/63-window computation; noted
as a performance characteristic to watch during world-bank generation (the
GO's §5), not a defect — correctness was prioritized per "measure before
tune."

**Perf note (Rick, not blocking, deferred to the worker-build phase)**: the
anchor-vector pass (1857.8s) recomputes `_component_window_values` over the
same H1/H2/H3 data the pooled reference pass (1862.6s) already computed —
calling it once per world and reusing those per-window lists for both the
pooled reference and the per-world median would roughly halve total
calibration time, and the same duplication will recur at scale during
world-bank generation (~10 min/descriptor vector × 16 initial candidates
plus rejected seeds is 3h+; expansion to 32 synthetic worlds is another
~3h). Permitted post-freeze as implementation, not convention, but any such
change (caching, numpy, incremental window statistics) must be proven
bit-identical against this receipt's frozen `reference_median`/
`reference_mad` before being relied on. Not applied in this freeze; picked
up when the world-bank generator is built.

## 5. Tests and full suite

`tests/test_fitness_v2_protocol.py`: 79 tests (65 initial + 4 manifest +
6 `DevelopmentBoundary` boundary-correction tests + 2 Sequence-warmup tests
+ 2 manifest-boundary/warmup additions), fixtures/algebra only, never
touching `data/normalized/*.csv`. Covers every §3 rule (#0-#12, including
#3b) and its reject path: sample-stdev <2 observations, correlation/
autocorrelation zero-variance, gap zero-exceedances, world-shorter-than-
one-window, desynchronized/missing assets, §3 #12's zero-MAD abort,
content-address tamper detection, determinism of every seeded draw
(stationary bootstrap, sequence segment starts, shock event draws),
Shock's exactly-three-gaps-differ and same-asset composition invariants,
Sequence's zero-join-gap, start-index-0 eligibility and 378-bar warm-up
accounting, and the `DevelopmentBoundary` class above.

Full suite: **468/468 pass, 0 fail, 0 unexplained skip** (389 pre-existing +
79 new). Confirmed on a clean run after both the boundary correction and
the Sequence warm-up settlement.

## 6. Git

- `a8a1c5e` — protocol module + tests + reboot receipt + STATUS.json
  (pre-boundary-fix). Pushed immediately per Addendum A §1.
- `e5d1923` — protocol freeze: development-boundary fix + real calibration
  (schema_version 2 manifest, now superseded per §0). Pushed immediately.
- `bb7cbf9` — STATUS.json: freeze pushed, began the first 6-hour audit
  window. Pushed immediately.
- This commit — Sequence warm-up settlement (schema_version 3 manifest,
  current), this receipt's §0/§4 updates, and a STATUS.json update
  restarting the 6-hour audit window from this new push, per Rick's
  instruction. Pushed immediately, not batched with anything after it.

Every commit above was pushed before the next one started, per Addendum A
§1's "push after every commit, not at the end" — verified local == remote
after each push.

## 7. Scope boundary, restated

Per the GO: no world, genome, baseline, campaign, or worker was generated or
created. Tau and the reference median/MAD vector are computed from real
DEVELOPMENT data — this is calibration, explicitly authorized ahead of
production results. Candidate world generation, baseline creation, and
campaign execution remain gated on GO Addendum A's systemd-worker
requirement and its 6-hour post-freeze audit window, neither of which has
started yet as of this receipt.
