"""WASDE parsers -> one normalised record per (release, commodity): the newest marketing year's CURRENT-month and PREVIOUS-month
projections for yield, production, total use, ending stocks, exports, season-average farm price, plus the two earlier years for context.
TXT parser: 2006-2010 and 2017+ layouts. XML parser: 2010-2023 (flattens the nested report by its attribute names).
Everything is read from the document AS ORIGINALLY PUBLISHED (a contemporaneous vintage); nothing is taken from later revisions."""
import re
import xml.etree.ElementTree as ET

NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
ITEMS = {  # normalised key -> label regexes (first match wins), applied to the commodity's segment
    "yield": [r"yield per harvested\s*(acre)?", r"^\s*acre\s*:"],
    "production": [r"^\s*production"],
    "supply_total": [r"^\s*supply,\s*total"],
    "use_total": [r"^\s*use,\s*total"],
    "exports": [r"^\s*exports"],
    "ending_stocks": [r"^\s*ending stocks(?!.*(?:ccc|free|loans))"],
    "farm_price": [r"avg\.?\s*farm\s*price"],
}
TABLES = {"corn": r"U\.S\. (?:Feed Grains? and )?Corn Supply and Use|U\.S\. Feed Grains? and Corn Supply and Use",
          "wheat": r"U\.S\. Wheat Supply and Use", "soybeans": r"U\.S\. Soybeans and Products Supply and Use"}
SEG_START = {"corn": r"^\s*CORN\b", "wheat": None, "soybeans": r"^\s*SOYBEANS\b"}


def _floats(s):
    s = s.replace(",", "")
    rng = re.search(r"(-?\d+\.\d+)\s*-\s*(-?\d+\.\d+)", s)             # price range "4.10- 4.60" -> midpoint
    if rng:
        a, b = float(rng.group(1)), float(rng.group(2))
        tail = s[rng.end():]
        return [(a + b) / 2] + [float(x) for x in NUM.findall(tail)]
    return [float(x) for x in NUM.findall(s)]


def parse_txt(text: str) -> dict:
    """returns {commodity: {item: {"years": [...], "cur": x, "prev": y|None, "n": k}}, "_meta": {...}}"""
    out = {}
    lines = text.splitlines()
    for com, title in TABLES.items():
        start = next((i for i, l in enumerate(lines) if re.search(title, l)), None)
        if start is None:
            continue
        # block runs until the next table's closing rule after the header
        end = len(lines)
        seen_rules = 0
        for j in range(start + 1, len(lines)):
            if lines[j].startswith("====") or lines[j].strip().startswith("===="):
                seen_rules += 1
                if seen_rules >= 3:
                    end = j; break
        block = lines[start:end]
        hdr = "\n".join(block[:7])
        years = re.findall(r"\d{4}/\d{2}", hdr)
        seg = block
        if SEG_START[com]:
            k = next((i for i, l in enumerate(block) if re.match(SEG_START[com], l)), None)
            if k is None:
                continue
            seg = block[k:]
        rec = {}
        for key, pats in ITEMS.items():
            for l in seg:
                low = l.lower()
                if any(re.search(p, low) for p in pats):
                    nums = _floats(l.split(":", 1)[1] if ":" in l else re.sub(r"^[^\d\-]*", "", l))
                    if len(nums) >= 3:
                        rec[key] = {"values": nums, "cur": nums[-1], "prev": nums[-2] if len(nums) >= 4 else None, "n": len(nums)}
                        break
        if rec:
            out[com] = {"items": rec, "newest_year_label": max(years) if years else None, "header_years": years}
    return out


# ------------------------------------------------------------------------------------------ XML
def _local(tag):
    return tag.split("}")[-1]


def flatten_xml(path_or_bytes):
    """yield (sub_report_title, commodity_header, attribute, market_year, forecast_month, value) for every numeric Cell"""
    root = ET.fromstring(path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb").read())
    rows = []

    def walk(el, ctx):
        c = dict(ctx)
        for k, v in el.attrib.items():
            kl = k.lower()
            if kl.startswith("sub_report_title"):
                c["title"] = v
            elif kl.startswith("commodity"):
                c["commodity"] = v
            elif re.match(r"(attribute|attribute_group)\d*$", kl) or kl.startswith("attribute"):
                if not kl.startswith("attribute_group"):
                    c["attribute"] = re.sub(r"\s+", " ", v).strip()
            elif kl.startswith("market_year"):
                c["year"] = re.sub(r"\s+", " ", v).strip()
            elif kl.startswith("forecast_month"):
                c["month"] = v.strip()
            elif kl.startswith("cell_value"):
                try:
                    rows.append((c.get("title"), c.get("commodity"), c.get("attribute"), c.get("year"), c.get("month"), float(v.replace(",", "").replace("*", "").strip())))
                except ValueError:
                    pass
        for ch in el:
            walk(ch, c)
    walk(root, {})
    return rows


XML_TITLES = {"corn": r"Feed Grain and Corn", "wheat": r"U\.S\. Wheat (?:by Class: )?Supply and Use", "soybeans": r"Soybeans and Products Supply and Use"}
XML_ATTR = {"yield": r"yield", "production": r"^production", "use_total": r"^use,? total", "supply_total": r"^supply,? total", "exports": r"^exports",
            "ending_stocks": r"^ending stocks", "farm_price": r"farm\s*price"}


def parse_xml(data) -> dict:
    rows = flatten_xml(data)
    out = {}
    for com, tpat in XML_TITLES.items():
        sub = [r for r in rows if r[0] and re.search(tpat, r[0], re.I)]
        if not sub:
            continue
        rec = {}
        for key, ap in XML_ATTR.items():
            cand = [r for r in sub if r[2] and re.search(ap, r[2], re.I)]
            # the same attribute repeats once per commodity inside a sub-report (feed grains aggregate, CORN, sorghum, ...;
            # soybeans, oil, meal). Split into runs of consecutive rows and pick the commodity's run by position.
            runs, last = [], object()
            for r in sub:
                if r[2] and re.search(ap, r[2], re.I):
                    akey = r[2]
                    if not runs or runs[-1][0] != akey or last is not runs[-1]:
                        runs.append([akey, []])
                    runs[-1][1].append(r); last = runs[-1]
                else:
                    last = None
            pick = {"corn": 1, "wheat": 0, "soybeans": 0}[com]
            runs = [x[1] for x in runs]
            if len(runs) > pick:
                cand = runs[pick]
            if not cand:
                continue
            years = []
            for r in cand:
                if r[3] and r[3] not in years:
                    years.append(r[3])
            newest = years[-1]
            vals = [(r[4], r[5]) for r in cand if r[3] == newest]
            months = [m for m, _ in vals]
            cur = vals[-1][1]
            prev = vals[-2][1] if len(vals) >= 2 else None
            rec[key] = {"values": [r[5] for r in cand], "cur": cur, "prev": prev, "n": len(cand), "months": months}
            rec["_year_" + key] = newest
        if rec:
            ny = next((v for k, v in rec.items() if k.startswith("_year_")), None)
            out[com] = {"items": {k: v for k, v in rec.items() if not k.startswith("_")}, "newest_year_label": ny}
    return out
