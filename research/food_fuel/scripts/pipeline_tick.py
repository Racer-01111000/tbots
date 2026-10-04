"""One pipeline tick. python scripts/pipeline_tick.py --root DIR [--mode production|rehearsal] [--now ISO8601] -- prints the tick outcome."""
import argparse, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ffmod.pipeline import Pipeline
ap = argparse.ArgumentParser(); ap.add_argument("--root", required=True); ap.add_argument("--mode", default="production"); ap.add_argument("--now"); ap.add_argument("--expires", default="2026-10-09T17:00:00")
a = ap.parse_args()
now = datetime.fromisoformat(a.now).astimezone(timezone.utc) if a.now else None
print(Pipeline(a.root, now=now, mode=a.mode, expires_et=a.expires).run())
