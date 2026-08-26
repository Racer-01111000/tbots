"""The one canonical, frozen S4 control genome. No optimization, no
mutation, no manual tuning after this point -- if it performs badly,
that result is recorded, not repaired."""

CONTROL_GENOME = {
    "strategy_family": "trend",
    "universe": ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"],
    "momentum_lookbacks": [63, 126, 252],
    "trend_filter_window": 200,
    "volatility_window": 63,
    "max_positions": 3,
    "target_max_exposure": 0.80,
    "max_asset_weight": 0.35,
    "direction": "long_only",
    "leverage": "none",
    "shorting": "none",
    "rebalance_every_n_sessions": 21,
    "drawdown_halt_pct": 0.12,
}
