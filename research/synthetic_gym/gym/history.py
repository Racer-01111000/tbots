"""Historical development-lane windows rendered as World objects (real raw prices, real dividends) so reference strategies can be
run on history with the SAME engine and scored by the SAME code as on synthetic worlds."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import calibration as C
from .world import CorporateAction, SYMBOLS, World


def historical_windows(data_dir: Path, n_warm: int = 400, n_eval: int = 1260, step: int = 252) -> list:
    rows = C.load_dev(data_dir)
    N = len(rows["SPY"])
    out = []
    for start in range(0, N - (n_warm + n_eval) + 1, step):
        sl = slice(start, start + n_warm + n_eval)
        dates = [date.fromisoformat(r["t"]) for r in rows["SPY"][sl]]
        o = {s: [r["o"] for r in rows[s][sl]] for s in SYMBOLS}
        h = {s: [r["h"] for r in rows[s][sl]] for s in SYMBOLS}
        l = {s: [r["l"] for r in rows[s][sl]] for s in SYMBOLS}
        c = {s: [r["c"] for r in rows[s][sl]] for s in SYMBOLS}
        v = {s: [int(r["v"]) for r in rows[s][sl]] for s in SYMBOLS}
        acts = []
        for s in SYMBOLS:
            for i, r in enumerate(rows[s][sl]):
                if r["div"] > 0 and i >= 1:
                    acts.append(CorporateAction("dividend", s, i, i + 3, r["div"]))
        w = World(f"HIST:{dates[n_warm].isoformat()}", "history", "development_lane_window", 0, dates, n_warm, o, h, l, c, v,
                  {s: [2.0] * len(dates) for s in SYMBOLS}, [0.0] * len(dates), [0.03] * len(dates), acts)
        out.append(w.finalize())
    return out
