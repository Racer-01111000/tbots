"""Controls for the holdout evaluation. Reuses NO_TRADE / BUY_AND_HOLD_SPY /
RANDOM_SIGNAL verbatim from the preserved
experiments/controls_comparison_20260921T064013Z/synthetic_controls.py
(read-only import, that file is never modified), plus one new control per
this GO's addendum: a passive, envelope-matched, buy-once allocation.

PASSIVE_ENVELOPE_CONTROL design choice (GO said "if that can be implemented
without changing the frozen evaluator" -- it can, and is): equal-weight
across all 8 universe assets at 0.10 each (0.80 target exposure / 8), a
single order batch at step 0, no rebalancing thereafter, no data read, no
RNG. This deliberately avoids picking any subset of the universe (which
RANDOM_SIGNAL already covers as the "uninformed subset" case) -- it is the
cleanest possible non-adaptive, non-selective passive allocation inside
CONTROL_GENOME's envelope (max_asset_weight=0.35 not binding at 0.10/asset;
target_max_exposure=0.80 hit exactly). Distinct from RANDOM_SIGNAL (redraws
3-of-8 every 21 sessions) and from BUY_AND_HOLD_SPY (single asset, no cap).
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIOR = HERE.parents[0] / "controls_comparison_20260921T064013Z"
sys.path.insert(0, str(PRIOR))

import synthetic_controls as sc  # noqa: E402  (preserved dir, read-only import)

UNIVERSE = sc.UNIVERSE
NO_TRADE_RISK = sc.NO_TRADE_RISK
BUY_AND_HOLD_SPY_RISK = sc.BUY_AND_HOLD_SPY_RISK
RANDOM_SIGNAL_RISK = sc.RANDOM_SIGNAL_RISK
RANDOM_CONTROL_SEED = sc.RANDOM_CONTROL_SEED
no_trade_decide_fn = sc.no_trade_decide_fn
buy_and_hold_spy_decide_fn = sc.buy_and_hold_spy_decide_fn
make_random_signal_decide_fn = sc.make_random_signal_decide_fn

PASSIVE_ENVELOPE_TARGET_EXPOSURE = 0.80  # CONTROL_GENOME.target_max_exposure
PASSIVE_ENVELOPE_PER_ASSET = PASSIVE_ENVELOPE_TARGET_EXPOSURE / len(UNIVERSE)  # 0.10, < 0.35 cap
PASSIVE_ENVELOPE_RISK = {"max_asset_weight": 0.35, "max_total_exposure": PASSIVE_ENVELOPE_TARGET_EXPOSURE}


def passive_envelope_decide_fn(view, step):
    if step == 0:
        return {s: PASSIVE_ENVELOPE_PER_ASSET for s in UNIVERSE}
    return None
