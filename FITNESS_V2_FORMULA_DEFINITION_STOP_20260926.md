# Fitness V2 pre-result formula-definition stop

Authority: Rick's continuing GO and explicit recovery/conservation instruction.
Recovered checkpoint: `69fa48db8e791ac8be8862f16dab6060f70a4acf` on
`feat/node-resident-fitness-v2-autonomous-evolution-20260925`.

Execution and Shock warm-ups are now resolved by Rick: each uses its matching
immutable historical anchor's available real history under frozen S5A
insufficient-history eligibility. Neither fabricates missing bars. Shock's only
price transformation remains exactly three authorized gap relocations;
Execution's only stress remains skipped fills. Distributional and Sequence
retain exactly 378 generated same-stream warm-up bars. Previous stop receipts
remain correct historical evidence under the rules then specified, not current
warm-up blockers. The new content-addressed warm-up amendment preserves these
resolutions without declaring the complete executable protocol ready.

Two outcome-material formula omissions still block the full protocol:

1. **Trend normalization.** The rule says “median absolute normalized 63-session
   linear-trend strength per asset” but supplies no executable normalization
   formula. Absolute standardized regression slope (absolute correlation) and
   absolute slope t-statistic are different normalizations. A descriptor-only
   counterexample uses reference correlations `[.1,.2,.3,.4,.5]`, an anchor at
   `.3`, and a candidate at `.449`. For each of eight identical trend components,
   compute reference median/MAD after applying either `f(r)=r` or
   `f(r)=r*sqrt(61/(1-r*r))`; hold the other 24 components equal. The prescribed
   32-component RMS z-distance is respectively **0.745** and
   **0.770866618310756**. The same example therefore fails or passes the frozen
   **0.75** admission threshold solely because of the normalization choice.
   This is algebra on descriptor scalars, not a generated world or market result.

2. **OC1 zero-MAD family aggregation.** The rule defines the ordinary numeric
   `passive_robust_z=(Sharpe-DEV_median)/(1.4826*DEV_MAD)`, then says that at
   zero MAD the per-world anomalous boolean is true iff Sharpe differs from the
   median. Family admission, however, requires `median(passive_robust_z)>3`.
   The boolean does not supply a numeric z/sentinel or a family aggregation rule.
   For reference Sharpes all zero and four world Sharpes all `-1`, every supplied
   per-world fallback boolean is true while the numeric formula divides by zero.
   Illustrative unsigned anomaly coding (`true -> +4`) would flag the family;
   signed deviation coding (`negative deviation -> -4`) would not. Neither coding
   is authorized or adopted. The example shows why substituting a sentinel or
   silently choosing a boolean aggregation can change admission.

**Required Rick definitions:** specify the exact trend regression inputs and
normalization formula, including degenerate-window behavior; specify the OC1
family rule for zero DEVELOPMENT MAD, either numeric z/sentinels with their
aggregation or an explicit boolean-to-family fallback. These are selection
rules, not serialization or loop-order conventions. No alternative is chosen.

The scoped warm-up validation API compares proposed history with a trusted,
hash-verified real anchor; it is not a complete world-admission API. The two
algebra proofs live only in tests. No full executable-protocol draft is present,
no world/genome or production performance result was generated, and no worker,
training, research-champion, or unattended acceptance is claimed. Historical
Fitness V1/S5D/S6B evidence and earlier commits/receipts are preserved.

See `FITNESS_V2_WARMUP_RESOLVED_FORMULAS_PENDING_20260926.md` for current status
and the associated validation evidence. Work stops before results.
