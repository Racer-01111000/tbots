"""python scripts/run_brief.py ASOF_DATE EDITION(0915|0929) OUT_DIR [CACHE_DIR] [--no-fetch]  -- read-only, idempotent, order-free."""
import os, sys
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from ffmod.brief import write_edition, is_us_trading_day
asof, ed, out = date.fromisoformat(sys.argv[1]), sys.argv[2], sys.argv[3]
cache = next((a for a in sys.argv[4:] if not a.startswith("--")), os.path.join(out, "_cache"))
if is_us_trading_day(asof)[0] is True and "--no-fetch" not in sys.argv and not os.path.exists(os.path.join(out, f"food_fuel_brief_{asof.isoformat()}_{ed}.json")):
    import fetch_latest; fetch_latest.main(asof, cache)
status, path = write_edition(asof, ed, out, cache)
print(status, path)
