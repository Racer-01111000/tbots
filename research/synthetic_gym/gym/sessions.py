"""Synthetic exchange calendar (NYSE-style rules, applied to any year). Used only to label sessions,
place month boundaries and dividend dates; it is not a claim about any real year."""
from __future__ import annotations

from datetime import date, timedelta


def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(y, month, day + 1)


def _nth_weekday(y, m, wd, n):
    d = date(y, m, 1)
    d += timedelta(days=(wd - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y, m, wd):
    d = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - wd) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def holidays(y: int) -> set[date]:
    h = {
        _observed(date(y, 1, 1)), _nth_weekday(y, 1, 0, 3), _nth_weekday(y, 2, 0, 3),
        _easter(y) - timedelta(days=2), _last_weekday(y, 5, 0), _observed(date(y, 6, 19)),
        _observed(date(y, 7, 4)), _nth_weekday(y, 9, 0, 1), _nth_weekday(y, 11, 3, 4), _observed(date(y, 12, 25)),
    }
    return {d for d in h if d.year == y}   # an observed Jan-1 falling on Dec 31 belongs to no year here


def is_session(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def sessions_from(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def sessions_before(end_exclusive: date, n: int) -> list[date]:
    out, d = [], end_exclusive - timedelta(days=1)
    while len(out) < n:
        if is_session(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def sessions_in_years(start: date, years: int) -> list[date]:
    """all sessions from `start` up to (not including) start + `years` calendar years"""
    end = date(start.year + years, start.month, start.day)
    out, d = [], start
    while d < end:
        if is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out
