# Controls comparison — interpretation (Phase 1)

Source: `experiments/controls_comparison_20260921T064013Z/results.json` (preserved, read-only, unmodified). This file reframes that data's aggregate claims; it does not recompute or rerun anything.

## 1. The "beats all 3 controls on all 4 metrics" flag was FALSE for all 41/41 real agents — and that is a structural artifact, not a finding

Every real/champion agent is fully invested in all 12 DEVELOPMENT episodes (100% of episodes), and NO_TRADE_CONTROL's max-drawdown is exactly 0.00% in every episode by construction (cash never loses value). An invested strategy cannot have a less-negative drawdown than a strategy that never invests. So the `max_drawdown` column of the `vs NO_TRADE` comparison is **mathematically forced to 0% for every agent**, regardless of strategy quality — it is not measuring skill, it is measuring "did you take any risk at all."

Once that one forced comparison is excluded, the picture (drawn directly from the comparison matrix already reported):

- **vs NO_TRADE** (return / Sharpe / Sortino only): every lineage wins the large majority of episodes — roughly 67–83% depending on agent, champion included (67%).
- **vs BUY_AND_HOLD_SPY**: real agents win **100% of episodes on drawdown** (worst single-episode drawdowns of −2.2% to −5.5% vs. SPY's −14.4%), split roughly evenly to favorably on return (50–58%) and on Sharpe/Sortino (42–67%, agent-dependent — some lineages are a coin flip here, none are worse than roughly even).
- **vs RANDOM_SIGNAL**: real agents win on drawdown (92–100%) and generally win or split on return/Sharpe/Sortino (50–75%).

Correct summary: no real agent dominates every control on every axis, but that's an artifact of one forced comparison, not evidence the agents are unskilled. Excluding it, every evolved lineage and the champion clears the bar against all three controls on return and drawdown, and is competitive-to-favorable on risk-adjusted metrics.

## 2. NO_TRADE is a reference baseline, not a target to beat on every axis

NO_TRADE_CONTROL is the zero-return/zero-risk anchor: 0.00% return, 0.00% drawdown, 0 Sharpe/Sortino, in every episode, by definition. Its only real function in this comparison is the "beats no-trade after costs" flag — the actual minimum bar (does this agent do better than doing nothing, net of its own trading costs). **All 41/41 real agents including the champion pass this bar in >50% of episodes.** It should not appear in any "beat every metric" framing since its drawdown column is not a genuine comparison.

## 3. Literal 100%-SPY vs. policy-envelope-matched controls are different things, not two points on one scale

- **BUY_AND_HOLD_SPY_CONTROL** (both comparisons): single-asset, no exposure cap, no per-asset cap (`max_asset_weight=1.0, max_total_exposure=1.0`) — a maximally concentrated, maximally simple benchmark. Its DEVELOPMENT-lane median return was actually negative (−1.24%) with a −14.4% worst-case drawdown; on the HOLDOUT window (see below) it returned +11.5% — the difference is entirely which historical window SPY itself had, not a change in the control's design.
- **PASSIVE_ENVELOPE_CONTROL** (new, holdout only — see Phase 3): equal-weight across all 8 universe assets at 0.10 each, capped the same way the evolved agents are capped (`max_asset_weight=0.35`, `target_max_exposure=0.80`), bought once, never rebalanced. This is the fairer "did evolution add anything over a dumb-but-envelope-matched allocation" comparison; RANDOM_SIGNAL_CONTROL (already existing) is the fairer "did evolution add anything over an envelope-matched but uninformed *and actively re-signaling* strategy" comparison.

Treating these three (100%-SPY, RANDOM_SIGNAL, PASSIVE_ENVELOPE) as interchangeable "the control" would blur three different questions. Keep them separate in any future read of this data.

## 4. Champion and strongest D candidates — descriptive only, not a ranking recommendation

DEVELOPMENT-lane (12 episodes, 2007–2018) medians, for reference:

| agent | median return | worst drawdown | Sharpe (median) | Sortino (median) |
|---|---|---|---|---|
| S5D_CHAMPION (`gen_0307d23c...`) | 2.00% | −5.45% | 0.610 | 0.857 |
| D_primary_rank1 (`gen_8e7a622b...`) | 3.05% | −3.39% | 0.782 | 1.099 |
| D_primary_rank2 (`gen_e9fb7fe3...`) | 2.67% | −3.77% | 0.898 | 1.329 |
| D_primary_rank3 (`gen_d890eb9f...`) | 2.52% | −3.09% | 0.773 | 1.079 |

D_primary_rank1/2/3 numerically outperform the champion on every one of these four DEVELOPMENT-lane metrics. **This is reported descriptively only.** Per the GO: no promotion, no replacement, no production ranking is authorized or implied by 12 episodes of development-lane evidence — see Phase 3 for what the same four agents do on genuinely unseen data, which is the actual point of this exercise.
