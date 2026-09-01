"""S4.10: independent verifier. Reimplements the same FROZEN
definitions from indicators.py/control_agent.py using genuinely
different code -- not a copy with renamed variables -- so it catches
implementation bugs, not just definition disagreements:
  - momentum via log-return accumulation + expm1, not direct division
  - moving average via a manual running-sum loop, not sum()/len()
  - variance via the sum-of-squares identity E[X^2]-E[X]^2 (times the
    ddof=1 Bessel correction), not sum-of-squared-deviations
  - ranking/sizing re-derived from scratch against this module's own
    metrics, not by calling into control_agent.py

Tolerance, declared before running the full episode: 1e-6 relative
(generous against the ~1e-10 to 1e-9 discrepancy expected from
IEEE754 double arithmetic taking a different path to the same sum).
Any disagreement beyond that blocks S4 acceptance.
"""
import math

TOLERANCE = 1e-6


def _series(view, symbol, bars_needed):
    rows = view.history(symbol, bars_needed)
    if len(rows) < bars_needed:
        return None
    out = []
    for r in rows:
        out.append(float(r["adjusted_close"]))
    return out


def momentum_v(view, symbol, lookback):
    series = _series(view, symbol, lookback + 1)
    if series is None:
        return None
    log_ratio = math.log(series[-1]) - math.log(series[0])
    return math.expm1(log_ratio)


def moving_average_v(view, symbol, window):
    series = _series(view, symbol, window)
    if series is None:
        return None
    acc = 0.0
    count = 0
    for price in series:
        acc += price
        count += 1
    return acc / count


def volatility_v(view, symbol, window):
    series = _series(view, symbol, window + 1)
    if series is None:
        return None
    rets = []
    i = 1
    while i < len(series):
        rets.append(series[i] / series[i - 1] - 1.0)
        i += 1
    # Welford's online algorithm: a single-pass, numerically stable
    # variance -- genuinely different code from indicators.py's two-pass
    # sum-of-squared-deviations, but without the catastrophic-
    # cancellation failure mode of the sum-of-squares identity (E[X^2] -
    # E[X]^2) this originally used, which could spuriously go negative
    # on a near-constant-return series purely from floating-point noise.
    mean = 0.0
    m2 = 0.0
    count = 0
    for r in rets:
        count += 1
        delta = r - mean
        mean += delta / count
        m2 += delta * (r - mean)
    var = m2 / (count - 1)
    return math.sqrt(var) if var > 0 else 0.0


def evaluate_symbol_v(view, symbol, genome):
    obs = view.observe()
    out = {"eligible": False, "reason": None, "m63": None, "m126": None, "m252": None,
           "ma200": None, "trend_pass": None, "ensemble_momentum": None, "volatility": None}
    if not obs["assets"][symbol]["available"]:
        out["reason"] = "not_available"
        return out

    lb63, lb126, lb252 = genome["momentum_lookbacks"]
    m63 = momentum_v(view, symbol, lb63)
    m126 = momentum_v(view, symbol, lb126)
    m252 = momentum_v(view, symbol, lb252)
    out["m63"], out["m126"], out["m252"] = m63, m126, m252
    if None in (m63, m126, m252):
        out["reason"] = "insufficient_history_momentum"
        return out

    ma = moving_average_v(view, symbol, genome["trend_filter_window"])
    out["ma200"] = ma
    if ma is None:
        out["reason"] = "insufficient_history_trend"
        return out
    current = float(obs["assets"][symbol]["adjusted_close"])
    out["trend_pass"] = current > ma

    vol = volatility_v(view, symbol, genome["volatility_window"])
    out["volatility"] = vol
    if vol is None or vol <= 0.0:
        out["reason"] = "insufficient_or_degenerate_volatility"
        return out

    ens = (m63 + m126 + m252) / 3.0
    out["ensemble_momentum"] = ens
    if m252 > 0 and ens > 0 and out["trend_pass"]:
        out["eligible"], out["reason"] = True, "eligible"
    else:
        out["reason"] = "filtered_by_momentum_or_trend"
    return out


def evaluate_universe_v(view, genome):
    return {s: evaluate_symbol_v(view, s, genome) for s in genome["universe"]}


def rank_and_select_v(evaluations, max_positions):
    pool = []
    for sym, ev in evaluations.items():
        if ev["eligible"]:
            pool.append((sym, ev["ensemble_momentum"]))
    # deterministic total order: descending momentum, ascending symbol
    pool_sorted = sorted(pool, key=lambda t: (-t[1], t[0]))
    return [s for s, _ in pool_sorted[:max_positions]]


def size_positions_v(selected, evaluations, genome):
    if len(selected) == 0:
        return {}
    inv_vols = {}
    total = 0.0
    for s in selected:
        iv = 1.0 / evaluations[s]["volatility"]
        inv_vols[s] = iv
        total += iv
    weights = {}
    for s in selected:
        share = inv_vols[s] / total
        w = share * genome["target_max_exposure"]
        weights[s] = w if w < genome["max_asset_weight"] else genome["max_asset_weight"]
    return weights


def _rel_close(a, b, tol=TOLERANCE):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=tol, abs_tol=tol)
    return a == b


def compare_decision(control_decision: dict, verifier_evaluations: dict, verifier_selected: list,
                      verifier_weights: dict) -> list[str]:
    """Returns a list of disagreement descriptions; empty means full
    agreement within TOLERANCE."""
    disagreements = []
    for sym, cev in control_decision["evaluations"].items():
        vev = verifier_evaluations[sym]
        for field in ("m63", "m126", "m252", "ma200", "trend_pass", "ensemble_momentum",
                      "volatility", "eligible"):
            if not _rel_close(cev[field], vev[field]):
                disagreements.append(f"{sym}.{field}: control={cev[field]!r} verifier={vev[field]!r}")

    if control_decision["selected"] != verifier_selected:
        disagreements.append(
            f"selection: control={control_decision['selected']!r} verifier={verifier_selected!r}"
        )
    for sym in set(control_decision["weights"]) | set(verifier_weights):
        cw = control_decision["weights"].get(sym)
        vw = verifier_weights.get(sym)
        if not _rel_close(cw, vw):
            disagreements.append(f"weight[{sym}]: control={cw!r} verifier={vw!r}")
    return disagreements
