# Fitness V2 Complete Protocol Freeze — 2026-09-26

Authority: Rick's GO "TBOTS Fitness V2 pre-result protocol completion"
(2026-09-26), and GO Addendum A — Unattended operation (2026-09-26), whose
§2 development-boundary correction and §3 post-2018/pre-2007-02-07 filter
proof are both incorporated below and into the manifest itself.

Manifest: `evolution/protocol/fitness_v2_complete_protocol_14fe15dd4525ca1314a135b4e63a6a36ab22104e789385917d9e9e8958cd518d.json`
(`complete_protocol_ready: true`, schema_version 2).

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
three historical anchors.

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
as a performance characteristic to watch during world-bank generation
(§5), not a defect — correctness was prioritized per "measure before tune."

## 5. Tests and full suite

`tests/test_fitness_v2_protocol.py`: 79 tests (65 initial + 4 manifest +
5 `DevelopmentBoundary` boundary-correction tests + 5 manifest-boundary
additions), fixtures/algebra only, never touching `data/normalized/*.csv`.
Covers every §3 rule (#0-#12, including #3b) and its reject path: sample-
stdev <2 observations, correlation/autocorrelation zero-variance, gap zero-
exceedances, world-shorter-than-one-window, desynchronized/missing assets,
§3 #12's zero-MAD abort, content-address tamper detection, determinism of
every seeded draw (stationary bootstrap, sequence segment starts, shock
event draws), Shock's exactly-three-gaps-differ and same-asset composition
invariants, Sequence's zero-join-gap and start-index-0 eligibility, and the
`DevelopmentBoundary` class above.

Full suite: **468/468 pass, 0 fail, 0 unexplained skip** (389 pre-existing +
79 new). Confirmed on a clean run after the boundary correction.

## 6. Git

Local commit `a8a1c5e` (protocol module + tests + reboot receipt +
STATUS.json, pre-boundary-fix) was pushed immediately per Addendum A §1.
This freeze commit (module/tests with the boundary correction, the real
manifest, this receipt, and an updated STATUS.json) follows and is pushed
immediately after, per the same instruction — push after every commit, not
at the end.

## 7. Scope boundary, restated

Per the GO: no world, genome, baseline, campaign, or worker was generated or
created. Tau and the reference median/MAD vector are computed from real
DEVELOPMENT data — this is calibration, explicitly authorized ahead of
production results. Candidate world generation, baseline creation, and
campaign execution remain gated on GO Addendum A's systemd-worker
requirement and its 6-hour post-freeze audit window, neither of which has
started yet as of this receipt.
