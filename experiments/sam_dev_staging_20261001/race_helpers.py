"""Minimal, import-light helpers for multiprocessing race tests. Used with the 'spawn' start method so a
worker can never inherit the parent's open SQLite connections (forking with an open connection loses
POSIX file locks and can silently break mutual exclusion; that is a test artifact, not production)."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
for sub in ("kim_order_simulation_tests",):
    sys.path.insert(0, str(HERE / sub))

import order_limits as ol
from kim_order_logic import IntendedOrderSpec, PersistentIntentLedger


def record_one(args):
    db, i, cap, order_class, session = args
    ledger = PersistentIntentLedger(db)
    try:
        suffix = "-liq" if order_class == "liquidation" else ""
        ledger.record_intent(IntendedOrderSpec(f"kim-{session}-S{i}{suffix}", f"S{i}", 1,
                                               "sell" if order_class == "liquidation" else "buy"),
                             session, cap, order_class)
        return True
    except ol.DailyOrderLimitReached:
        return False
    finally:
        ledger.close()
