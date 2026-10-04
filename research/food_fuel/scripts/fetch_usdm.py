"""US Drought Monitor weekly cumulative drought coverage (D1+ etc.) for CONUS and corn-belt states, 2007-2022, one request per area-year.
USDM map date = Tuesday; published Thursday 08:30 ET (observable from then). Retrieved today = latest-vintage; USDM maps are seldom
re-issued but this is NOT a contemporaneous vintage -> flagged in the manifest."""
import csv, io, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HERE, fetch
STATES = {"IA": 19, "IL": 17, "IN": 18, "NE": 31, "MN": 27, "KS": 20, "SD": 46, "OH": 39, "MO": 29}
out = {}


def rows(b):
    t = b.decode("utf-8-sig").strip()
    if not t:
        return []
    return [{"map_date": f"{r['MapDate'][:4]}-{r['MapDate'][4:6]}-{r['MapDate'][6:]}", "valid_start": r["ValidStart"], "valid_end": r["ValidEnd"],
             "d0": float(r["D0"]), "d1": float(r["D1"]), "d2": float(r["D2"]), "d3": float(r["D3"]), "d4": float(r["D4"])}
            for r in csv.DictReader(io.StringIO(t)) if r.get('AreaOfInterest', 'CONUS') in ('CONUS',) or 'AreaOfInterest' not in r]

for y in range(2007, 2023):
    q = f"startdate=1/1/{y}&enddate=12/31/{y}&statisticsType=2"
    b = fetch(f"https://usdmdataservices.unl.edu/api/USStatistics/GetDroughtSeverityStatisticsByAreaPercent?aoi=us&{q}", f"usdm_US_{y}.json")
    if b: out.setdefault("CONUS", []).extend(rows(b))
    for st, fips in STATES.items():
        b = fetch(f"https://usdmdataservices.unl.edu/api/StateStatistics/GetDroughtSeverityStatisticsByAreaPercent?aoi={fips}&{q}", f"usdm_{st}_{y}.json")
        if b: out.setdefault(st, []).extend(rows(b))
(HERE / "derived").mkdir(exist_ok=True)
json.dump(out, open(HERE / "derived" / "usdm_weekly_cumulative.json", "w"))
print({k: len(v) for k, v in out.items()})
