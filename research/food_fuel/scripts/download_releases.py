"""Downloads the original published .txt of each indexed ESMIS release, one request at a time with the shared 1 req/s limiter."""
import json, sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HERE, fetch

name, y0, y1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
idx = json.loads((HERE / "sources" / f"esmis_{name}_index.json").read_text())
ok = fail = 0
for r in idx:
    y = int(r["released_listing"][:4])
    if not (y0 <= y <= y1):
        continue
    cache = f"{name}_{r['released_listing'][:10]}_{r['url'].rsplit('/', 1)[1]}"
    b = fetch(r["url"], cache)
    if b:
        ok += 1
    else:
        fail += 1; print("FAILED", r["released_listing"], r["url"])
print(name, "downloaded/cached:", ok, "failed:", fail)
