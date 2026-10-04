import math
from datetime import date

from gym.rng import Rng, derive_seed
from gym import sessions as S


def test_rng_is_deterministic_and_seed_sensitive():
    a, b, c = Rng(1), Rng(1), Rng(2)
    xa = [a.normal() for _ in range(50)]
    assert xa == [b.normal() for _ in range(50)] and xa != [c.normal() for _ in range(50)]


def test_derive_seed_is_stable_and_disjoint_by_identity():
    assert derive_seed("X", "training", "fam", 0) == derive_seed("X", "training", "fam", 0)
    assert len({derive_seed("X", s, f, i) for s in ("training", "validation", "sealed") for f in "abcdef" for i in range(2)}) == 36


def test_moments_are_sane():
    r = Rng(7)
    xs = [r.normal() for _ in range(40000)]
    m = sum(xs) / len(xs); v = sum((x - m) ** 2 for x in xs) / len(xs)
    assert abs(m) < 0.03 and abs(v - 1) < 0.04
    ts = [r.student_t(4) for _ in range(40000)]
    kurt = sum(t ** 4 for t in ts) / len(ts) / (sum(t * t for t in ts) / len(ts)) ** 2
    assert kurt > 4.0                                       # fat tails (normal is 3)
    assert abs(sum(r.poisson(3.0) for _ in range(20000)) / 20000 - 3.0) < 0.1


def test_geometric_block_length_bounds_and_mean():
    r = Rng(3)
    xs = [r.geometric(10, 40) for _ in range(20000)]
    assert min(xs) >= 1 and max(xs) <= 40 and 7 < sum(xs) / len(xs) < 11


def test_u_never_hits_the_endpoints():
    r = Rng(0)
    assert all(0.0 < r.u() < 1.0 for _ in range(10000))


def test_calendar_has_about_252_sessions_a_year_and_no_weekends_or_known_holidays():
    for y in (2100, 2101, 2104):
        n = len(S.sessions_in_years(date(y, 1, 1), 1))
        assert 249 <= n <= 253, (y, n)
    d = S.sessions_from(date(2100, 1, 4), 30)
    assert all(x.weekday() < 5 for x in d) and d == sorted(d) and len(set(d)) == 30
    assert S._easter(2024) == date(2024, 3, 31) and S._easter(2025) == date(2025, 4, 20)
    assert date(2025, 4, 18) in S.holidays(2025) and date(2025, 11, 27) in S.holidays(2025) and date(2025, 12, 25) in S.holidays(2025)


def test_five_year_window_is_about_1260_sessions():
    n = len(S.sessions_in_years(date(2100, 1, 4), 5))
    assert 1250 <= n <= 1262
