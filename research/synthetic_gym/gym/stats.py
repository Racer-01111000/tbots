"""Descriptive statistics shared by calibration and validation, so historical and synthetic series are measured
by exactly the same code."""
from __future__ import annotations

import math


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def var(xs):
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / max(1, len(xs) - 1)


def sd(xs):
    return math.sqrt(var(xs))


def skew(xs):
    m, s = mean(xs), sd(xs)
    return mean([((x - m) / s) ** 3 for x in xs]) if s > 0 else 0.0


def ex_kurt(xs):
    m, s = mean(xs), sd(xs)
    return mean([((x - m) / s) ** 4 for x in xs]) - 3.0 if s > 0 else 0.0


def autocorr(xs, lag=1):
    m = mean(xs)
    den = sum((x - m) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return sum((xs[i] - m) * (xs[i - lag] - m) for i in range(lag, len(xs))) / den


def corr(a, b):
    ma, mb = mean(a), mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den > 0 else 0.0


def quantile(xs, q):
    """linear interpolation between order statistics at position q*(n-1) (numpy 'linear' / R type 7)"""
    if not xs:
        raise ValueError("empty")
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def max_drawdown_from_levels(levels):
    peak, mdd = -1e300, 0.0
    for v in levels:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return mdd


def recovery_sessions(levels):
    """longest run of sessions spent below a previous peak (peak-to-recovery duration), censored at the end"""
    peak, start, longest = -1e300, None, 0
    for i, v in enumerate(levels):
        if v >= peak:
            peak = v
            if start is not None:
                longest = max(longest, i - start)
                start = None
        elif start is None:
            start = i
    if start is not None:
        longest = max(longest, len(levels) - start)
    return longest


def expected_shortfall(returns, q=0.05):
    s = sorted(returns)
    k = max(1, int(math.floor(q * len(s))))
    return mean(s[:k])


def ann_vol(logrets, per_year=252):
    return sd(logrets) * math.sqrt(per_year)
