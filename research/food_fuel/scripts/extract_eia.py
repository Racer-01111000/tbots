"""Streams the EIA bulk PET file once and keeps only the series this module uses (weekly petroleum status + daily spot prices)."""
import io, json, zipfile
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
WANT = {
 "PET.WDISTUS1.W": "distillate_stocks_kbbl", "PET.WCESTUS1.W": "crude_stocks_ex_spr_kbbl", "PET.WCRSTUS1.W": "crude_stocks_incl_spr_kbbl",
 "PET.WPULEUS3.W": "refinery_utilization_pct", "PET.WCRFPUS2.W": "crude_production_kbd", "PET.WCREXUS2.W": "crude_exports_kbd",
 "PET.WCRIMUS2.W": "crude_imports_kbd", "PET.WDIRPUS2.W": "distillate_production_kbd", "PET.WGFSTUS1.W": "gasoline_stocks_kbbl",
 "PET.RWTC.D": "wti_spot_usd_bbl", "PET.EER_EPD2DXL0_PF4_RGC_DPG.D": "ulsd_gulf_spot_usd_gal", "PET.EER_EPD2F_PF4_Y35NY_DPG.D": "heating_oil_ny_spot_usd_gal",
}
z = zipfile.ZipFile(HERE / "cache/raw/eia_PET.zip")
out = {}
with z.open("PET.txt") as f:
    for line in io.TextIOWrapper(f, encoding="utf8"):
        if '"series_id"' not in line:
            continue
        i = line.find('"series_id":"')
        sid = line[i + 13:line.find('"', i + 13)]
        if sid in WANT:
            d = json.loads(line)
            out[WANT[sid]] = {"series_id": sid, "name": d["name"], "units": d["units"], "frequency": d["f"], "last_updated": d.get("last_updated"),
                              "data": sorted([r for r in d["data"] if r[0] >= "20050101"])}
(HERE / "cache" / "eia_extract.json").write_text(json.dumps(out))
print({k: (len(v["data"]), v["data"][0][0], v["data"][-1][0]) for k, v in out.items()})
