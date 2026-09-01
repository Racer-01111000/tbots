"""Frozen S4.2 indicator definitions. These access the market ONLY
through AgentView.history() -- the same sealed interface any agent
uses -- so insufficient lookback is a structural fact (history() simply
returns fewer rows), never a value to backfill or interpolate around.

Convention, fixed before any performance was inspected:
  M_L(S, T)  = adjusted_close[T] / adjusted_close[T-L] - 1
  MA200      = mean(adjusted_close) over the latest 200 available bars
  trend_pass = adjusted_close[T] > MA200
  ensemble_momentum = (M63 + M126 + M252) / 3
  volatility = sample stdev (ddof=1) of the most recent `window` daily
               simple returns of adjusted_close (needs window+1 prices)
  eligible   = M252 > 0 AND ensemble_momentum > 0 AND trend_pass
               AND all of the above were computable (no substituting a
               shorter window when history is short)
"""


def _adj_close_series(view, symbol, bars_needed):
    rows = view.history(symbol, bars_needed)
    if len(rows) < bars_needed:
        return None
    return [float(r["adjusted_close"]) for r in rows]


def momentum(view, symbol, lookback):
    series = _adj_close_series(view, symbol, lookback + 1)
    if series is None:
        return None
    return series[-1] / series[0] - 1.0


def moving_average(view, symbol, window):
    series = _adj_close_series(view, symbol, window)
    if series is None:
        return None
    return sum(series) / len(series)


def volatility(view, symbol, window):
    series = _adj_close_series(view, symbol, window + 1)
    if series is None:
        return None
    returns = [series[i] / series[i - 1] - 1.0 for i in range(1, len(series))]
    n = len(returns)
    mean_r = sum(returns) / n
    var = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    return var ** 0.5


def ensemble_momentum(m63, m126, m252):
    return (m63 + m126 + m252) / 3.0


def evaluate_symbol(view, symbol, genome):
    """Returns a dict: {eligible, reason, m63, m126, m252, ma200,
    trend_pass, ensemble_momentum, volatility} -- metrics present
    whenever computable, None otherwise, so a rejection is always
    explainable rather than silent."""
    obs = view.observe()
    result = {
        "eligible": False, "reason": None,
        "m63": None, "m126": None, "m252": None,
        "ma200": None, "trend_pass": None,
        "ensemble_momentum": None, "volatility": None,
    }
    if not obs["assets"][symbol]["available"]:
        result["reason"] = "not_available"
        return result

    lb63, lb126, lb252 = genome["momentum_lookbacks"]
    m63 = momentum(view, symbol, lb63)
    m126 = momentum(view, symbol, lb126)
    m252 = momentum(view, symbol, lb252)
    result["m63"], result["m126"], result["m252"] = m63, m126, m252
    if m63 is None or m126 is None or m252 is None:
        result["reason"] = "insufficient_history_momentum"
        return result

    ma = moving_average(view, symbol, genome["trend_filter_window"])
    result["ma200"] = ma
    if ma is None:
        result["reason"] = "insufficient_history_trend"
        return result
    current_adj_close = float(obs["assets"][symbol]["adjusted_close"])
    result["trend_pass"] = current_adj_close > ma

    vol = volatility(view, symbol, genome["volatility_window"])
    result["volatility"] = vol
    if vol is None or vol <= 0.0:
        result["reason"] = "insufficient_or_degenerate_volatility"
        return result

    ens = ensemble_momentum(m63, m126, m252)
    result["ensemble_momentum"] = ens

    if m252 > 0 and ens > 0 and result["trend_pass"]:
        result["eligible"] = True
        result["reason"] = "eligible"
    else:
        result["reason"] = "filtered_by_momentum_or_trend"
    return result


def evaluate_universe(view, genome):
    return {symbol: evaluate_symbol(view, symbol, genome) for symbol in genome["universe"]}


def rank_and_select(evaluations: dict, max_positions: int) -> list[str]:
    """S4.3: eligible only, ranked by ensemble_momentum descending, tie
    broken by symbol ascending -- a fully deterministic total order."""
    eligible = [(sym, ev["ensemble_momentum"]) for sym, ev in evaluations.items() if ev["eligible"]]
    eligible.sort(key=lambda pair: (-pair[1], pair[0]))
    return [sym for sym, _ in eligible[:max_positions]]
