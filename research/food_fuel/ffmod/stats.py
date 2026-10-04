"""Rank correlation, within-stratum permutation p-values and Benjamini-Hochberg, in pure Python (deterministic seeds)."""
import random


def ranks(x):
    order = sorted(range(len(x)), key=lambda i: x[i]); r = [0.0] * len(x); i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def pearson(a, b):
    n = len(a); ma, mb = sum(a) / n, sum(b) / n
    sa = sum((x - ma) ** 2 for x in a) ** .5; sb = sum((y - mb) ** 2 for y in b) ** .5
    return 0.0 if sa == 0 or sb == 0 else sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def spearman(x, y):
    return pearson(ranks(x), ranks(y))


def perm_p(x, y, strata, n_perm=2000, seed=0):
    """two-sided p for Spearman rho; x is shuffled only WITHIN strata (calendar month) so seasonality cannot manufacture a result"""
    obs = spearman(x, y); rng = random.Random(seed); groups = {}
    for i, s in enumerate(strata):
        groups.setdefault(s, []).append(i)
    ry = ranks(y); hits = 0
    for _ in range(n_perm):
        xs = list(x)
        for idx in groups.values():
            v = [x[i] for i in idx]; rng.shuffle(v)
            for i, val in zip(idx, v): xs[i] = val
        if abs(pearson(ranks(xs), ry)) >= abs(obs) - 1e-12:
            hits += 1
    return obs, (hits + 1) / (n_perm + 1)


def bh(pvals, q=0.10):
    """Benjamini-Hochberg: returns list of booleans (rejected) in input order and adjusted q-values"""
    m = len(pvals); order = sorted(range(m), key=lambda i: pvals[i]); adj = [0.0] * m; prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]; prev = min(prev, pvals[i] * m / rank); adj[i] = prev
    return [a <= q for a in adj], adj
