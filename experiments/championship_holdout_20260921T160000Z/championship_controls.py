"""Controls for the CHAMPIONSHIP-window evaluation. Reuses NO_TRADE /
BUY_AND_HOLD_SPY / RANDOM_SIGNAL / PASSIVE_ENVELOPE verbatim from the
preserved experiments/controls_comparison_20260921T064013Z/synthetic_controls.py
and experiments/controls_interpretation_holdout_20260921T140000Z/holdout_controls.py
(read-only imports; neither prior file/dir is modified). No new control
logic -- this file only re-exports for the new window's runner, per this
GO's "same 4 synthetic controls" constraint.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIOR_COMPARISON = HERE.parents[0] / "controls_comparison_20260921T064013Z"
sys.path.insert(0, str(PRIOR_COMPARISON))

import synthetic_controls as sc  # noqa: E402  (preserved dir, read-only import)

UNIVERSE = sc.UNIVERSE
NO_TRADE_RISK = sc.NO_TRADE_RISK
BUY_AND_HOLD_SPY_RISK = sc.BUY_AND_HOLD_SPY_RISK
RANDOM_SIGNAL_RISK = sc.RANDOM_SIGNAL_RISK
RANDOM_CONTROL_SEED = sc.RANDOM_CONTROL_SEED
no_trade_decide_fn = sc.no_trade_decide_fn
buy_and_hold_spy_decide_fn = sc.buy_and_hold_spy_decide_fn
make_random_signal_decide_fn = sc.make_random_signal_decide_fn

# PASSIVE_ENVELOPE_CONTROL: identical design to the 2026 holdout's (equal-weight
# across all 8 universe assets at 0.10 each = 0.80 target exposure / 8, single
# order batch at step 0, no rebalancing thereafter). Re-derived here rather than
# imported cross-directory from the other preserved dir, to keep this new
# directory self-contained; logic is byte-identical.
PASSIVE_ENVELOPE_TARGET_EXPOSURE = 0.80  # CONTROL_GENOME.target_max_exposure
PASSIVE_ENVELOPE_PER_ASSET = PASSIVE_ENVELOPE_TARGET_EXPOSURE / len(UNIVERSE)  # 0.10, < 0.35 cap
PASSIVE_ENVELOPE_RISK = {"max_asset_weight": 0.35, "max_total_exposure": PASSIVE_ENVELOPE_TARGET_EXPOSURE}


def passive_envelope_decide_fn(view, step):
    if step == 0:
        return {s: PASSIVE_ENVELOPE_PER_ASSET for s in UNIVERSE}
    return None
