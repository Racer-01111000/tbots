# Fitness V2 pre-result Shock warm-up conflict

Authority: Rick's 2026-09-26 stop and conservation instruction.
Checkpoint: `008b39ad9197b02cdf629e5818a1c0a2b94a7a06` on
`feat/node-resident-fitness-v2-autonomous-evolution-20260925`.

Rick's Execution warm-up resolution is accepted: Execution uses its matching
historical anchor's available real history under frozen S5A insufficient-history
eligibility, even below 378 bars. This resolves the Execution issue recorded at
`008b39a`; that receipt was correct under the then-unspecified rule and remains
historical evidence. See `FITNESS_V2_WARMUP_RESOLUTION_STATUS_20260926.md`.

Shock still requires exactly 378 same-stream generated warm-up bars. The H1
real path has only 252 synchronized prior bars, with DBC limiting, as proven by
the preserved content-addressed coverage audit at `008b39a`. The authorized
Shock transformation relocates exactly three DEVELOPMENT donor gaps into three
distinct destination asset/session locations, preserving each destination's
existing intraday OHLC geometry. It supplies no base-path or warm-up generator.
Unlike Execution, Shock has no available-real-history exception.

Supplying the missing bars by choosing another DEVELOPMENT segment, bootstrap,
recombination, interpolation, backfill, or replacing the whole base path changes
price inputs and can change early eligibility, orders, and selection. Such a
choice is not a delegated serialization convention. No completion rule is adopted.

**Minimal Rick decision:** specify the Shock base path and exact deterministic
source/construction for its 378 H1 warm-up bars, or explicitly give Shock the
same available-real-history exception as Execution.

The untracked executable-protocol draft was deleted with `apply_patch` before
commit or results. Its seeded contiguous Shock base stream, same-asset donor/
destination restriction, and disjoint fixed-grid Sequence pool were unauthorized
and are not preserved as protocol. Sequence segments are identified by distinct
start indices as Rick states; no disjointness restriction is adopted here.
`scripts/fitness_v2_admission.py` was restored byte-for-byte to its committed form
with `apply_patch`. No partial executable-protocol draft remains.

No world, genome, campaign, or performance result was generated in this work.
No training or unattended acceptance is claimed. Prior commits, parameter freeze,
Fitness V1/S5D lineage, and S6B evidence remain unchanged. Work stops before
results pending the Shock decision.
