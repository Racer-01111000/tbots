"""NASS Crop Progress weekly text -> condition rows (national 18-state G+EX etc.)."""
import re
NORM = {"Previous year": "Prev Yr", "Previous week": "Prev Wk", "Iowa": "IA", "Illinois": "IL", "Nebraska": "NE", "Indiana": "IN", "Minnesota": "MN"}
PATS = (("corn", r"^\s*Corn(?::\s+Crop)? Condition( by Percent| - Selected States)"), ("soybeans", r"^\s*Soybeans?(?::\s+Crop)? Condition( by Percent| - Selected States)"))


def table(lines, title_pat):
    for i, l in enumerate(lines):
        if re.search(title_pat, l):
            out = {}
            for l2 in lines[i + 1:i + 60]:
                m = re.match(r"\s*([A-Za-z0-9 ]{2,20}?)\s*\.*\s*:\s*([\d\s]+)$", l2)
                if m:
                    nums = [int(x) for x in m.group(2).split()]
                    if len(nums) == 5: out[NORM.get(m.group(1).strip(), m.group(1).strip())] = nums
                if l2.strip().startswith(("Prev Yr", "Previous year")) or (out and re.match(r"\s*-{5,}\s*$", l2) and len(out) > 5 and "Prev Yr" in out):
                    break
                if out and not l2.strip() and ("Prev Wk" in out):
                    break
            return out
    return {}


def parse_text(text, release_date):
    lines = text.splitlines(); rows = []
    for crop, pat in PATS:
        t = table(lines, pat)
        if not t: continue
        nat = next((v for k, v in t.items() if re.match(r"\d+ (Sts|States)", k)), None)
        if not nat: continue
        rec = {"release_date": release_date, "crop": crop, "national_vp_p_f_g_ex": nat, "good_excellent": nat[3] + nat[4], "poor_verypoor": nat[0] + nat[1],
               "prev_year_ge": None if t.get("Prev Yr") is None else t["Prev Yr"][3] + t["Prev Yr"][4]}
        for st in ("IA", "IL", "NE", "IN", "MN"):
            if st in t: rec[f"{st}_ge"] = t[st][3] + t[st][4]
        rows.append(rec)
    return rows
