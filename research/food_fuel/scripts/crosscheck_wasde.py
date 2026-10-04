"""txt-vs-xml cross-validation of the two independent WASDE parsers on releases published in both formats."""
import json, os, sys, re
sys.path.insert(0, os.path.dirname(__file__))
from common import fetch
from wasde_parse import parse_txt, parse_xml
f = json.load(open("sources/esmis_wasde_release_formats.json"))
both = [x for x in f if "txt" in x["exts"] and "xml" in x["exts"] and "2006" <= x["released"][:4] <= "2022"]
pick = [x for x in both if x["released"] in ("2010-07-09", "2010-09-10", "2016-10-12", "2016-12-09", "2017-01-12", "2017-03-09", "2017-05-10", "2017-06-09")]
BASE = "https://esmis.nal.usda.gov"
res, mism = [], []
for x in pick:
    tx = next(l for l in x["links"] if l.endswith(".txt")); xm = next(l for l in x["links"] if l.endswith(".xml"))
    tb = fetch(BASE + tx, "xc_txt_" + x["released"] + ".txt", max_bytes=3_000_000)
    xb = fetch(BASE + xm, "xc_xml_" + x["released"] + ".xml", max_bytes=8_000_000)
    if tb is None or xb is None: res.append((x["released"], "FETCH_FAIL")); continue
    a = parse_txt(tb.decode("latin-1")); b = parse_xml(xb)
    for com in ("corn", "wheat", "soybeans"):
        for k in ("yield", "production", "use_total", "ending_stocks"):
            va, vb = a.get(com, {}).get("items", {}).get(k), b.get(com, {}).get("items", {}).get(k)
            if not va or not vb: mism.append((x["released"], com, k, "missing", bool(va), bool(vb))); continue
            for w in ("cur", "prev"):
                u, v = va[w], vb[w]
                if (u is None) != (v is None) or (u is not None and abs(u - v) > 0.051):
                    mism.append((x["released"], com, k, w, u, v))
    res.append((x["released"], "OK"))
print(res); print(len(mism)); [print(m) for m in mism[:30]]
