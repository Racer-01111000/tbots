"""NASS Crop Progress (original weekly text) -> derived/crop_condition.json (release 16:00 ET on the listed date)."""
import glob, json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ffmod.cropprog import parse_text
ROWS = []
for f in sorted(glob.glob("cache/raw/crop_progress_20*.txt")):
    d = re.match(r".*crop_progress_(\d{4}-\d\d-\d\d)_", f).group(1)
    if d > "2022-12-31": continue
    ROWS += parse_text(open(f, encoding="latin-1").read(), d)
json.dump(ROWS, open("derived/crop_condition.json", "w"))
print(len(ROWS), "condition rows;", len({r["release_date"] for r in ROWS}), "releases")
