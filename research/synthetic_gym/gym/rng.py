"""Deterministic RNG built ONLY on random.Random(seed).random(), so draws are bit-stable across Python
versions/machines (random.gauss/choice/randrange internals are not guaranteed stable). Every world,
mutation and tournament derives its seed from sha256 of its identity; nothing uses global state or the clock."""
from __future__ import annotations

import hashlib
import math
import random


def derive_seed(*parts) -> int:
    blob = "|".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big") & ((1 << 63) - 1)


class Rng:
    def __init__(self, seed: int):
        self._r = random.Random(seed)
        self._spare: float | None = None

    def u(self) -> float:
        """uniform in (0,1), never exactly 0 or 1"""
        x = self._r.random()
        return x if x > 0.0 else 5e-324

    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * self.u()

    def normal(self, mu: float = 0.0, sd: float = 1.0) -> float:
        if self._spare is not None:
            z, self._spare = self._spare, None
            return mu + sd * z
        u1, u2 = self.u(), self.u()
        rad = math.sqrt(-2.0 * math.log(u1))
        z0, z1 = rad * math.cos(2 * math.pi * u2), rad * math.sin(2 * math.pi * u2)
        self._spare = z1
        return mu + sd * z0

    def int_below(self, n: int) -> int:
        if n <= 0:
            raise ValueError("n must be positive")
        return min(n - 1, int(self.u() * n))

    def choice(self, seq):
        return seq[self.int_below(len(seq))]

    def weighted_index(self, weights) -> int:
        tot = sum(weights)
        x = self.u() * tot
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if x <= acc:
                return i
        return len(weights) - 1

    def expo(self, mean: float) -> float:
        return -mean * math.log(self.u())

    def geometric(self, mean: float, cap: int) -> int:
        """stationary-bootstrap block length: geometric with the given mean, truncated to [1, cap]"""
        p = 1.0 / max(1.0, mean)
        k = 1 + int(math.log(self.u()) / math.log(1 - p)) if p < 1 else 1
        return max(1, min(cap, k))

    def student_t(self, df: float) -> float:
        """t(df) via normal / sqrt(chi2/df) with chi2 as a sum of squares (integer df) or gamma approx"""
        z = self.normal()
        k = max(1, int(round(df)))
        chi2 = sum(self.normal() ** 2 for _ in range(k))
        return z / math.sqrt(chi2 / k)

    def poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.u()
            if p <= L:
                return k
            k += 1

    def sample_without_replacement(self, seq, k):
        pool = list(seq)
        out = []
        for _ in range(min(k, len(pool))):
            out.append(pool.pop(self.int_below(len(pool))))
        return out

    def state(self):
        return self._r.getstate()

    def set_state(self, st):
        self._r.setstate(st)
