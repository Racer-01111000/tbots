"""Collects the latest public releases for a brief as of a date (polite cached GETs, 1 req/s) -> OUT/latest_sources.json. Read-only."""
import json, os, sys
from datetime import date, datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import common
from index_esmis import index
from ffmod.events import eia_events
from ffmod.prices import load


def main(asof: date, out):
    os.makedirs(out, exist_ok=True)
    months = sorted({(asof - timedelta(days=45)).strftime("%Y-%m"), (asof - timedelta(days=15)).strftime("%Y-%m"), asof.strftime("%Y-%m")})
    res = {"asof": asof.isoformat()}
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    wl = [r for r in index("wasde", months) if r["released_listing"][:10] <= asof.isoformat()]
    if wl:
        r = wl[-1]; name = f"wasde_{r['released_listing'][:10]}_{r['url'].rsplit('/', 1)[1]}"
        if common.fetch(r["url"], name): res["wasde"] = {**r, "cache": name, "retrieved_at_utc": now()}
    cl = [r for r in index("crop_progress", months) if r["released_listing"][:10] <= asof.isoformat()][-2:]
    res["crop_progress"] = []
    for r in cl:
        name = f"crop_progress_{r['released_listing'][:10]}_{r['url'].rsplit('/', 1)[1]}"
        if common.fetch(r["url"], name): res["crop_progress"].append({**r, "cache": name, "retrieved_at_utc": now()})
    # drought: 4 weeks of 5 corn-belt states
    import csv, io
    start = asof - timedelta(days=28); weeks = {}
    for st, fips in (("IA", 19), ("IL", 17), ("IN", 18), ("NE", 31), ("MN", 27)):
        url = f"https://usdmdataservices.unl.edu/api/StateStatistics/GetDroughtSeverityStatisticsByAreaPercent?aoi={fips}&startdate={start.month}/{start.day}/{start.year}&enddate={asof.month}/{asof.day}/{asof.year}&statisticsType=2"
        b = common.fetch(url, f"usdm_latest_{st}_{asof.isoformat()}.csv")
        if b:
            for r in csv.DictReader(io.StringIO(b.decode("utf-8-sig"))): weeks.setdefault(r["MapDate"], {})[st] = float(r["D1"])
    full = sorted(k for k, v in weeks.items() if len(v) == 5)
    if len(full) >= 2:
        res["usdm"] = {"weeks": [{"map_date": f"{k[:4]}-{k[4:6]}-{k[6:]}", "belt_d1": sum(weeks[k].values()) / 5} for k in full], "url": "https://usdmdataservices.unl.edu/api/StateStatistics (FIPS 19,17,18,31,27; cumulative)", "retrieved_at_utc": now()}
    # EIA: latest bulk-file extract (re-run scripts/extract_eia.py after refreshing cache/raw/eia_PET.zip to update)
    ex = json.load(open(os.path.join(os.path.dirname(__file__), "..", "cache", "eia_extract.json")))
    evs = [e for e in eia_events(set(load("HO").dates)) if e["release_date"] <= asof.isoformat()]
    if evs:
        rd = max(e["release_date"] for e in evs); last = {e["feature"]: e["value"] for e in evs if e["release_date"] == rd}
        we = (date.fromisoformat(rd) - timedelta(days=5)).strftime("%Y%m%d")
        lv = {k: dict(ex[k]["data"]).get(we) for k in ("distillate_stocks_kbbl", "crude_stocks_ex_spr_kbbl", "refinery_utilization_pct")}
        res["eia"] = {"release_assumed": rd, "week_ending": f"{we[:4]}-{we[4:6]}-{we[6:]}", "dist_surprise": last.get("e_dist_stock_surprise_kbbl"), "crude_surprise": last.get("e_crude_stock_surprise_kbbl"),
                      "util_chg": last.get("e_util_chg_pp"), "levels": lv, "url": "https://www.eia.gov/opendata/bulkfiles.php (PET.zip)", "retrieved_at_utc": os.path.getmtime(os.path.join(os.path.dirname(__file__), "..", "cache", "raw", "eia_PET.zip")) and datetime.fromtimestamp(os.path.getmtime(os.path.join(os.path.dirname(__file__), "..", "cache", "raw", "eia_PET.zip")), timezone.utc).isoformat(timespec="seconds"),
                      "extract_last_updated": ex["distillate_stocks_kbbl"]["last_updated"]}
    json.dump(res, open(os.path.join(out, "latest_sources.json"), "w"), indent=1)
    import shutil
    os.makedirs(os.path.join(out, "raw"), exist_ok=True)
    for k in ("wasde",):
        if k in res: shutil.copy(os.path.join(common.RAW, res[k]["cache"]), os.path.join(out, "raw", res[k]["cache"]))
    for r in res["crop_progress"]: shutil.copy(os.path.join(common.RAW, r["cache"]), os.path.join(out, "raw", r["cache"]))
    print({k: (v if not isinstance(v, (list, dict)) else "ok") for k, v in res.items()})


if __name__ == "__main__":
    main(date.fromisoformat(sys.argv[1]), sys.argv[2])
