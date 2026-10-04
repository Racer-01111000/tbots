"""Release index for a USDA ESMIS publication, built from the public month-filtered listing. Output rows: release time as listed,
and the URL of the .txt version (the document as originally published)."""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HERE, fetch

PUBS = {"wasde": "https://esmis.nal.usda.gov/publication/world-agricultural-supply-and-demand-estimates",
        "crop_progress": "https://esmis.nal.usda.gov/publication/crop-progress"}


def rows_in(html: str):
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", html, flags=re.S):
        t = re.search(r'<time datetime="([^"]+)">', tr)
        links = re.findall(r'href="(/sites/default/release-files/[^"]+)"', tr)
        txt = [l for l in links if l.lower().endswith(".txt")]
        if t and txt:
            out.append({"released_listing": t.group(1), "url": "https://esmis.nal.usda.gov" + txt[0]})
    return out


def index(name: str, months: list, maxpages: int = 4):
    rows = []
    for ym in months:
        for p in range(maxpages):
            q = f"?date={ym}" + (f"&page={p}" if p else "")
            b = fetch(PUBS[name] + q, f"esmis_{name}_{ym}_p{p}.html")
            if not b:
                break
            got = rows_in(b.decode("utf8", "replace"))
            fresh = [r for r in got if r["url"] not in {x["url"] for x in rows}]
            rows += fresh
            if len(got) < 10:
                break
    rows.sort(key=lambda r: r["released_listing"])
    return rows


if __name__ == "__main__":
    name, y0, y1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    mon = (range(1, 13) if name == "wasde" else range(4, 12))
    months = [f"{y}-{m:02d}" for y in range(y0, y1 + 1) for m in mon]
    rows = index(name, months)
    Path(HERE / "sources" / f"esmis_{name}_index.json").write_text(json.dumps(rows, indent=1))
    print(name, "releases indexed:", len(rows), "| earliest", rows[0]["released_listing"], "| latest", rows[-1]["released_listing"])
