"""Hidden economic factors, event kinds, scenario-family templates and the asset-response table.

EVERYTHING IN THIS FILE IS A DECLARED ASSUMPTION, not a calibration. The development-lane history (2007-2018) has
no sustained inflationary regime and mostly NEGATIVE stock/bond correlation, so stagflation, positive stock/bond
correlation, fertilizer and freight dynamics cannot be estimated from it; they are plausible-range hypotheses.
No asset response has a fixed sign or lag: each pair carries a sign-flip probability and a random lag
(negative = the market anticipates), and every event may be priced-in, muted or reversed."""
from __future__ import annotations

import math

FACTORS = ["oil", "diesel", "natgas", "fertilizer", "freight", "crop", "food", "inflation", "yield", "credit",
           "usd", "tech", "liquidity", "growth"]
# units: all factors are log-level deviations EXCEPT "yield", which is a decimal change in the 10y yield (0.01 = 100bp)

# event kind -> {factor: amplitude}, plus how strongly it stresses markets (drives regime transitions)
EVENT_KINDS = {
    "supply_disruption":   ({"oil": .45, "diesel": .35, "freight": .25, "inflation": .08, "growth": -.04, "liquidity": .05}, .5),
    "refinery_squeeze":    ({"diesel": .50, "oil": .10, "inflation": .05, "growth": -.02}, .3),
    "reopening":           ({"oil": -.30, "diesel": -.25, "freight": -.20, "inflation": -.04, "growth": .03}, .0),
    "reserve_release":     ({"oil": -.15, "diesel": -.10}, .0),
    "demand_destruction":  ({"oil": -.25, "growth": -.06, "credit": .15}, .4),
    "input_cost_shock":    ({"natgas": .50, "fertilizer": .40, "inflation": .03}, .2),
    "planting_stress":     ({"fertilizer": .10, "crop": -.10, "food": .05}, .1),
    "harvest_shortfall":   ({"crop": -.25, "food": .20, "inflation": .04}, .2),
    "good_harvest":        ({"crop": .20, "food": -.15, "fertilizer": -.10, "inflation": -.02}, .0),
    "inflation_surprise":  ({"inflation": .12, "yield": .010, "growth": -.03}, .4),
    "disinflation":        ({"inflation": -.08, "yield": -.006}, .0),
    "rate_hike_cycle":     ({"yield": .012, "usd": .05, "growth": -.04, "credit": .08}, .5),
    "recession_onset":     ({"growth": -.08, "credit": .35, "liquidity": .40, "yield": -.008, "tech": -.04}, .9),
    "policy_response":     ({"growth": .04, "credit": -.20, "liquidity": -.30, "yield": -.003}, .0),
    "recovery":            ({"growth": .05, "credit": -.25, "liquidity": -.15}, .0),
    "tech_boom":           ({"tech": .30, "growth": .03}, .0),
    "tech_reversal":       ({"tech": -.30, "growth": -.01, "liquidity": .10}, .6),
    "false_breakout_up":   ({"growth": .035}, .0),
    "false_breakout_down": ({"growth": -.035}, .1),
}
SHORT_EVENTS = {"false_breakout_up", "false_breakout_down"}   # quick rise and full reversal

# family -> template.  menu: (event kind, weight);  composite: (trigger kind, follow-on kinds, lag range in sessions)
FAMILIES = {
    "energy_shipping_squeeze": {
        "n_events": (2, 5),
        "menu": [("supply_disruption", 3.0), ("refinery_squeeze", 2.0), ("reopening", 2.2), ("reserve_release", 1.0), ("demand_destruction", 1.5), ("inflation_surprise", .5)],
        "regime_stress_bias": 1.0, "bond_flip_prob": .20},
    "fertilizer_harvest_stress": {
        "n_events": (2, 5),
        "menu": [("input_cost_shock", 3.0), ("good_harvest", 2.0), ("harvest_shortfall", 1.5), ("inflation_surprise", .6), ("disinflation", .5)],
        "composite": [("input_cost_shock", ["harvest_shortfall", "good_harvest"], (90, 220))],
        "regime_stress_bias": .9, "bond_flip_prob": .15},
    "stagflation_rates": {
        "n_events": (2, 5),
        "menu": [("inflation_surprise", 3.0), ("rate_hike_cycle", 2.5), ("disinflation", 2.0), ("demand_destruction", 1.0), ("supply_disruption", .8)],
        "regime_stress_bias": 1.1, "bond_flip_prob": .65},
    "recession_credit_shock": {
        "n_events": (2, 4),
        "menu": [("recession_onset", 3.0), ("policy_response", 2.5), ("recovery", 2.5), ("rate_hike_cycle", .6)],
        "composite": [("recession_onset", ["policy_response", "recovery"], (25, 140))],
        "regime_stress_bias": 1.5, "bond_flip_prob": .10},
    "technology_boom_reversal": {
        "n_events": (2, 5),
        "menu": [("tech_boom", 3.0), ("tech_reversal", 2.5), ("rate_hike_cycle", .8), ("inflation_surprise", .5), ("recovery", .5)],
        "regime_stress_bias": .9, "bond_flip_prob": .25},
    "normalization_whipsaw": {
        "n_events": (1, 4),
        "menu": [("false_breakout_up", 3.0), ("false_breakout_down", 3.0), ("inflation_surprise", .5), ("disinflation", .5), ("tech_boom", .6)],
        "regime_stress_bias": .6, "bond_flip_prob": .45, "calm_bias": True},
}

# asset response: symbol -> {factor: (mean beta per unit factor, probability the sign is flipped in a given world)}
BETAS = {
    "SPY": {"oil": (-.10, .30), "diesel": (-.08, .30), "natgas": (-.03, .40), "freight": (-.05, .40), "food": (-.05, .40), "inflation": (-.30, .25),
            "yield": (-4.0, .30), "credit": (-.30, .10), "usd": (-.15, .30), "tech": (.55, .15), "liquidity": (-.10, .10), "growth": (1.8, .08)},
    "EFA": {"oil": (-.12, .30), "diesel": (-.10, .30), "natgas": (-.05, .40), "freight": (-.06, .40), "food": (-.05, .40), "inflation": (-.30, .25),
            "yield": (-3.5, .30), "credit": (-.30, .10), "usd": (-.25, .25), "tech": (.40, .20), "liquidity": (-.10, .10), "growth": (1.9, .08)},
    "EEM": {"oil": (.02, .50), "diesel": (-.05, .45), "natgas": (-.02, .50), "freight": (-.08, .40), "food": (-.08, .45), "inflation": (-.25, .30),
            "yield": (-4.0, .30), "credit": (-.35, .10), "usd": (-.45, .20), "tech": (.40, .20), "liquidity": (-.12, .10), "growth": (2.2, .08)},
    "IEF": {"inflation": (-.15, .20), "yield": (-7.5, .04), "credit": (.10, .30), "liquidity": (.05, .40), "growth": (-.30, .30), "usd": (.00, .50)},
    "TLT": {"inflation": (-.25, .20), "yield": (-17.0, .04), "credit": (.15, .30), "liquidity": (.08, .40), "growth": (-.45, .30), "usd": (.00, .50)},
    "GLD": {"oil": (.10, .35), "inflation": (.45, .30), "yield": (-3.0, .35), "usd": (-.50, .25), "liquidity": (.15, .40), "growth": (-.10, .45), "credit": (.10, .45)},
    "DBC": {"oil": (.45, .08), "diesel": (.10, .25), "natgas": (.06, .30), "food": (.15, .25), "crop": (-.10, .35), "inflation": (.10, .35),
            "usd": (-.40, .20), "growth": (.45, .15), "liquidity": (-.10, .30), "credit": (-.15, .30), "yield": (.00, .50)},
    "VNQ": {"inflation": (-.20, .40), "yield": (-8.0, .20), "credit": (-.45, .10), "liquidity": (-.15, .15), "growth": (.80, .12), "tech": (.10, .40), "usd": (-.05, .50)},
}

REGIME_VOL_MULT = [0.75, 1.00, 1.70, 2.80]
# how strongly each asset's volatility responds to the market regime (ASSUMPTION; equities 1.0, bonds/gold/commodities muted)
REGIME_EXPONENT = {"SPY": 1.0, "EFA": 1.0, "EEM": 1.0, "VNQ": 1.0, "GLD": .6, "DBC": .7, "IEF": .5, "TLT": .5}
REGIME_BASE_P = [
    [.985, .012, .003, .000],
    [.006, .984, .009, .001],
    [.004, .020, .972, .004],
    [.000, .012, .035, .953],
]
JUMP_RATE_PER_YEAR = [1.5, 3.0, 6.0, 12.0]
JUMP_LOADING = {"SPY": 1.0, "EFA": 1.1, "EEM": 1.4, "VNQ": 1.3, "DBC": .5, "GLD": -.15, "IEF": -.12, "TLT": -.25}
BASE_HALF_SPREAD_BPS = {"SPY": .5, "EFA": 1.5, "EEM": 3.0, "IEF": 1.5, "TLT": 1.5, "GLD": 1.5, "DBC": 4.0, "VNQ": 3.0}   # ASSUMPTION: no quote data in the dataset
ANNUAL_DRIFT_PRIOR = {  # (mean, sd) of total-return drift per world, excess of cash where noted
    "SPY": (.060, .030, "total"), "EFA": (.055, .035, "total"), "EEM": (.060, .040, "total"), "VNQ": (.050, .035, "total"),
    "IEF": (.004, .008, "excess"), "TLT": (.008, .015, "excess"), "GLD": (.020, .040, "total"), "DBC": (.000, .040, "total"),
}
DBC_EXPENSE_RATIO = .0085
DURATION = {"IEF": 7.5, "TLT": 17.0}


def envelope(t: int, t0: int, rise: int, hold: int, decay: int, resid: float) -> float:
    """0 before t0; smoothstep rise; plateau; exponential decay toward `resid` of the peak"""
    if t < t0:
        return 0.0
    x = t - t0
    if x < rise:
        u = x / max(1, rise)
        return u * u * (3 - 2 * u)
    x -= rise
    if x < hold:
        return 1.0
    x -= hold
    tau = max(1.0, decay / 3.0)
    return resid + (1.0 - resid) * math.exp(-x / tau)
