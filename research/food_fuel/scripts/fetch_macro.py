"""NOAA CPC ONI (ENSO) and World Bank Pink Sheet monthly prices (fertilizers, grains, energy), pure-Python xlsx reader.
Pink Sheet is a LATEST-vintage file (history is restated; first-release vintages not available) -> not usable for predictive claims, context only."""
import json, re, sys, zipfile, io
import xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HERE, fetch
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

oni = fetch("https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt", "noaa_oni.txt").decode().split("\n")[1:]
oni_rows = [{"season": l.split()[0], "year": int(l.split()[1]), "anom": float(l.split()[3])} for l in oni if l.strip()]

b = fetch("https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx", "worldbank_pinksheet_monthly.xlsx")
z = zipfile.ZipFile(io.BytesIO(b))
ss = [("".join(t.itertext())) for t in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS)]
wb = ET.fromstring(z.read("xl/workbook.xml")); names = [s.get("name") for s in wb.find("m:sheets", NS)]
print("sheets", names)
sheet = "xl/worksheets/sheet%d.xml" % (names.index("Monthly Prices") + 1)
cells = {}
for c in ET.fromstring(z.read(sheet)).iter("{%s}c" % NS["m"]):
    v = c.find("m:v", NS)
    if v is None: continue
    val = ss[int(v.text)] if c.get("t") == "s" else v.text
    cells[c.get("r")] = val
def col(r): return re.match(r"[A-Z]+", r).group(0)
def row(r): return int(re.search(r"\d+", r).group(0))
hdr_row = next(r for r in range(1, 12) if any(cells.get(f"{c}{r}", "").startswith("Crude oil, average") for c in ["B", "C", "D", "E"]))
heads = {col(k): v for k, v in cells.items() if row(k) in (hdr_row,)}
want = [c for c, h in heads.items() if re.search(r"Crude oil, (Brent|average)|Natural gas, (US|Europe)|Maize|Wheat, US HRW|Soybeans$|Rice, Thai 5%|Urea|DAP|Potassium chloride|TSP|Phosphate rock|Fertilizers index|Grains index|Food index", h)]
series = {heads[c]: {} for c in want}
for k, v in cells.items():
    r = row(k)
    if r > hdr_row + 1 and col(k) == "A" and re.match(r"\d{4}M\d{2}", v):
        for c in want:
            x = cells.get(f"{c}{r}")
            try: series[heads[c]][v] = float(x)
            except (TypeError, ValueError): pass
out = {"oni": oni_rows, "pink_sheet": series, "source": "World Bank Commodity Markets (Pink Sheet) monthly; latest vintage retrieved 2026-10-04"}
json.dump(out, open(HERE / "derived" / "macro_oni_pinksheet.json", "w"))
print(len(oni_rows), {k: len(v) for k, v in series.items()})
