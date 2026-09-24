"""Three synthetic control agents, per Rick's GO. Each is a decide_fn(view,
step) -> {symbol: weight} | None, plugged into harness._simulate_generic_episode
alongside its own risk envelope.

Interpretation choices made explicit (GO phrasing was not fully prescriptive
on these two points):

  - BUY_AND_HOLD_SPY: the GO says "100% SPY", not "80% per the frozen S6A
    policy" -- so its risk envelope here is max_asset_weight=1.0,
    max_total_exposure=1.0 (the only way to let a literal 100% SPY order
    pass risk.validate), NOT genome_control.CONTROL_GENOME's 0.35/0.80.
    Every other execution assumption (T+1 open fill, same commission/
    slippage, same drawdown halt) is identical to every other agent.

  - RANDOM_SIGNAL: "constrained to the same max-positions/max-asset-weight/
    max-exposure envelope as the frozen S6A policy" is read as
    genome_control.CONTROL_GENOME's values (max_positions=3,
    max_asset_weight=0.35, target_max_exposure=0.80) -- the one canonical,
    frozen, universe-spanning risk envelope on record. Each rebalance
    (every 21 sessions, matching CONTROL_GENOME's rebalance_every_n_sessions)
    it draws exactly 3 of the 8 universe symbols uninformed by any market
    data (a clean structural null, not a data-driven-but-unskilled
    strategy) and weights them equally at 0.80/3 each (< the 0.35 cap, so
    the cap never binds and doesn't distort turnover). No market data is
    read at all, so "no lookahead" is satisfied trivially. Determinism
    comes from a fresh, episode-scoped RNG seeded from the published base
    seed plus the episode index, so a rerun reproduces identical draws.
"""
import random

UNIVERSE = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
RANDOM_CONTROL_SEED = 20260921  # published per the GO's requirement

# Matches genome_control.CONTROL_GENOME
CONTROL_ENVELOPE = {"max_positions": 3, "max_asset_weight": 0.35, "target_max_exposure": 0.80,
                    "rebalance_every_n_sessions": 21}


def no_trade_decide_fn(view, step):
    return None


NO_TRADE_RISK = {"max_asset_weight": CONTROL_ENVELOPE["max_asset_weight"],
                 "max_total_exposure": CONTROL_ENVELOPE["target_max_exposure"]}


def buy_and_hold_spy_decide_fn(view, step):
    if step == 0:
        return {"SPY": 1.0}
    return None


BUY_AND_HOLD_SPY_RISK = {"max_asset_weight": 1.0, "max_total_exposure": 1.0}


def make_random_signal_decide_fn(episode_index: int):
    rng = random.Random(f"{RANDOM_CONTROL_SEED}-episode-{episode_index}")
    k = CONTROL_ENVELOPE["max_positions"]
    per_symbol_weight = CONTROL_ENVELOPE["target_max_exposure"] / k

    def decide_fn(view, step):
        if step % CONTROL_ENVELOPE["rebalance_every_n_sessions"] != 0:
            return None
        selected = rng.sample(UNIVERSE, k)
        return {s: per_symbol_weight for s in selected}

    return decide_fn


RANDOM_SIGNAL_RISK = {"max_asset_weight": CONTROL_ENVELOPE["max_asset_weight"],
                      "max_total_exposure": CONTROL_ENVELOPE["target_max_exposure"]}
