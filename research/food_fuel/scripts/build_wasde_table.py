"""Parse every cached WASDE vintage -> derived/wasde_vintage.json (one row per release x commodity), release time 12:00 America/New_York."""
import glob, json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from wasde_parse import parse_txt, parse_xml
rows, skipped = [], []
for f in sorted(glob.glob("cache/raw/wasde_*")):
    base = os.path.basename(f)
    date = re.match(r"wasde_(\d{4}-\d\d-\d\d)_", base).group(1)
    r = parse_xml(open(f, "rb").read()) if f.endswith(".xml") else parse_txt(open(f, encoding="latin-1").read())
    if not r:
        skipped.append({"file": base, "reason": "no supply/use tables (announcement, replacement notice or non-WASDE text)"}); continue
    for com, v in r.items():
        it = v["items"]
        if "ending_stocks" not in it or "use_total" not in it:
            skipped.append({"file": base, "commodity": com, "reason": "partial/abbreviated report"}); continue
        def g(k, w): return it[k][w] if k in it else None
        cur_use, cur_end = g("use_total", "cur"), g("ending_stocks", "cur")
        pu, pe = g("use_total", "prev"), g("ending_stocks", "prev")
        rows.append({"release_date": date, "release_time_et": date + "T12:00:00-04:00/05:00 (nominal; WASDE 12:00 ET)", "file": base,
                     "commodity": com, "marketing_year": v["newest_year_label"], "units": "US: mil bu (corn,wheat,soy), yield bu/acre, price $/bu",
                     "yield": g("yield", "cur"), "yield_prev_month": g("yield", "prev"),
                     "production": g("production", "cur"), "production_prev_month": g("production", "prev"),
                     "ending_stocks": cur_end, "ending_stocks_prev_month": pe, "use_total": cur_use, "use_total_prev_month": pu,
                     "exports": g("exports", "cur"),
                     "stocks_to_use": cur_end / cur_use if cur_use else None,
                     "stocks_to_use_prev_month": pe / pu if pe is not None and pu else None})
json.dump({"rows": rows, "skipped": skipped}, open("derived/wasde_vintage.json", "w"), indent=1)
print(len(rows), "rows;", len(skipped), "skipped")
