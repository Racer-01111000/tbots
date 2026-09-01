"""The control organism: a pure function of (AgentView, genome) ->
proposed target weights. Owns no portfolio/halt/schedule state -- that
lives in the orchestration loop, which decides whether decide() is even
called on a given step. Sees the market exclusively through AgentView.
"""
import indicators


def size_positions(selected: list[str], evaluations: dict, genome: dict) -> dict[str, float]:
    """S4.5: inverse-volatility weighting, normalized, scaled to the
    80% exposure target, then capped per-asset at 35%. The cap is never
    redistributed to hit 80% -- residual goes to cash, not forced."""
    if not selected:
        return {}
    inv_vol = {sym: 1.0 / evaluations[sym]["volatility"] for sym in selected}
    total_inv_vol = sum(inv_vol.values())
    target_exposure = genome["target_max_exposure"]
    max_weight = genome["max_asset_weight"]
    weights = {}
    for sym in selected:
        normalized = inv_vol[sym] / total_inv_vol
        scaled = normalized * target_exposure
        weights[sym] = min(scaled, max_weight)
    return weights


def decide(view, genome) -> dict:
    evaluations = indicators.evaluate_universe(view, genome)
    selected = indicators.rank_and_select(evaluations, genome["max_positions"])
    weights = size_positions(selected, evaluations, genome)
    return {"selected": selected, "weights": weights, "evaluations": evaluations}
