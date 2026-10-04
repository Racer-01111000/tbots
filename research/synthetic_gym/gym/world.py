"""World container, invariants, as-of adjusted series, deterministic hashing.

Raw prices are as-traded (they drop on ex-dividend dates and change level at splits). Valuation and fills use raw
prices. Signal series are built by `Adjuster`, which uses ONLY corporate actions whose effective session is on or
before the decision session, so a decision can never see a future dividend/split adjustment."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date

SYMBOLS = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]


@dataclass(frozen=True)
class CorporateAction:
    kind: str                 # "dividend" | "split"
    symbol: str
    ex_idx: int               # session index on which the action takes effect (ex-date / split date)
    pay_idx: int | None = None
    amount: float = 0.0       # dividend per share, raw price units
    num: int = 1              # split: new shares per `den` old shares
    den: int = 1


@dataclass
class World:
    world_id: str
    split: str
    family: str
    seed: int
    dates: list                                  # list[date], ascending
    n_warmup: int
    open: dict; high: dict; low: dict; close: dict; volume: dict; half_spread_bps: dict
    policy_rate: list                            # annualized, decimal
    yield10: list
    actions: list                                # list[CorporateAction]
    hidden: object = None                        # HiddenState: never handed to agents
    meta: dict = field(default_factory=dict)

    @property
    def n(self):
        return len(self.dates)

    @property
    def n_eval(self):
        return self.n - self.n_warmup

    def actions_at(self, idx):
        return self._by_idx.get(idx, ())

    def finalize(self):
        by = {}
        for a in self.actions:
            by.setdefault(a.ex_idx, []).append(a)
        self._by_idx = by
        pays = {}
        for a in self.actions:
            if a.kind == "dividend" and a.pay_idx is not None:
                pays.setdefault(a.pay_idx, []).append(a)
        self._pay_by_idx = pays
        self._adjuster = Adjuster(self)
        return self

    def payments_at(self, idx):
        return self._pay_by_idx.get(idx, ())

    @property
    def adjuster(self):
        return self._adjuster

    def month_start_indices(self, lo=None):
        out, prev = [], None
        for i, d in enumerate(self.dates):
            key = (d.year, d.month)
            if key != prev and (lo is None or i >= lo):
                out.append(i)
            prev = key
        return out

    def content_hash(self) -> str:
        """sha256 over fixed-decimal renderings of everything an agent or the accounting could ever read"""
        h = hashlib.sha256()
        h.update(json.dumps([self.world_id, self.split, self.family, self.seed, self.n_warmup, [d.isoformat() for d in self.dates]]).encode())
        for s in SYMBOLS:
            for name, arr, dec in (("o", self.open[s], 4), ("h", self.high[s], 4), ("l", self.low[s], 4), ("c", self.close[s], 4), ("sp", self.half_spread_bps[s], 3)):
                h.update(f"{s}{name}".encode()); h.update(",".join(f"{x:.{dec}f}" for x in arr).encode())
            h.update(f"{s}v".encode()); h.update(",".join(str(int(x)) for x in self.volume[s]).encode())
        h.update(",".join(f"{x:.6f}" for x in self.policy_rate).encode()); h.update(",".join(f"{x:.6f}" for x in self.yield10).encode())
        for a in sorted(self.actions, key=lambda a: (a.ex_idx, a.symbol, a.kind)):
            h.update(f"{a.kind}|{a.symbol}|{a.ex_idx}|{a.pay_idx}|{a.amount:.6f}|{a.num}|{a.den}".encode())
        return h.hexdigest()


class Adjuster:
    """as-of adjusted close history. adjusted_t(j) = raw_close_j * exp(S_t - S_j) where S is the running sum of log
    adjustment factors of actions effective at or before each index. A split of num:den scales earlier prices by
    den/num; a dividend D on ex-date d scales earlier prices by (1 - D / raw_close_{d-1})."""

    def __init__(self, w: World):
        self.w = w
        self.S = {}
        for s in SYMBOLS:
            logf = [0.0] * w.n
            for a in w.actions:
                if a.symbol != s:
                    continue
                if a.kind == "split":
                    logf[a.ex_idx] += math.log(a.den / a.num)
                elif a.kind == "dividend" and a.ex_idx >= 1:
                    logf[a.ex_idx] += math.log(1.0 - a.amount / w.close[s][a.ex_idx - 1])
            run, cum = 0.0, []
            for x in logf:
                run += x; cum.append(run)
            self.S[s] = cum

    def history(self, sym: str, t: int, length: int) -> list:
        """adjusted closes for sessions t-length+1 .. t as known at the close of session t"""
        lo = max(0, t - length + 1)
        S, c = self.S[sym], self.w.close[sym]
        st = S[t]
        return [c[j] * math.exp(st - S[j]) for j in range(lo, t + 1)]


# ------------------------------------------------------------------ invariants
def check_invariants(w: World) -> list[str]:
    """returns a list of violation strings (empty == coherent)"""
    bad = []
    n = w.n
    if any(w.dates[i] >= w.dates[i + 1] for i in range(n - 1)):
        bad.append("timestamps not strictly ascending")
    for s in SYMBOLS:
        for name in ("open", "high", "low", "close", "volume", "half_spread_bps"):
            arr = getattr(w, name)[s]
            if len(arr) != n:
                bad.append(f"{s}.{name} length {len(arr)} != {n}")
        for i in range(n):
            o, h, l, c, v = w.open[s][i], w.high[s][i], w.low[s][i], w.close[s][i], w.volume[s][i]
            if not all(math.isfinite(x) for x in (o, h, l, c, v)):
                bad.append(f"{s}[{i}] non-finite"); break
            if min(o, h, l, c) <= 0:
                bad.append(f"{s}[{i}] non-positive price"); break
            if l > min(o, c) + 1e-9 or h < max(o, c) - 1e-9:
                bad.append(f"{s}[{i}] OHLC incoherent o={o} h={h} l={l} c={c}"); break
            if v < 0:
                bad.append(f"{s}[{i}] negative volume"); break
    for a in w.actions:
        if a.kind == "dividend":
            if a.amount <= 0 or a.pay_idx is None or a.pay_idx < a.ex_idx:
                bad.append(f"bad dividend {a}")
            elif a.amount >= w.close[a.symbol][a.ex_idx - 1]:
                bad.append(f"dividend >= price {a}")
        elif a.kind == "split":
            if a.num <= 0 or a.den <= 0 or a.num == a.den:
                bad.append(f"bad split {a}")
        else:
            bad.append(f"unknown action {a.kind}")
    if any((not math.isfinite(x)) or x < 0 for x in w.policy_rate):
        bad.append("policy rate invalid")
    return bad
