"""Bounded probe of candidate primary sources. Honest User-Agent, 1 req/s, <=2 attempts per URL, 25 s timeout.
A bot-protection / challenge page is recorded as INACCESSIBLE; it is never circumvented."""
import hashlib, json, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

UA = "tbots-research/1.0 (read-only public-data evidence collection; no scraping of protected content)"
CHALLENGE = re.compile(r"just a moment|access denied|cf-chl|captcha|attention required|enable javascript|request blocked|akamai", re.I)
CANDIDATES = [
 # --- USDA / NASS / FAS / ERS
 ("USDA_WASDE_index", "https://www.usda.gov/oce/commodity/wasde"),
 ("CORNELL_ESMIS_WASDE", "https://usda.library.cornell.edu/concern/publications/3t945q76s"),
 ("CORNELL_API_WASDE", "https://usda.library.cornell.edu/api/v1/publication/findPublication/wasde"),
 ("NASS_cropprogress_page", "https://www.nass.usda.gov/Publications/National_Crop_Progress/"),
 ("FAS_export_sales", "https://apps.fas.usda.gov/export-sales/esrd1.html"),
 ("FAS_PSD", "https://apps.fas.usda.gov/psdonline/app/index.html"),
 ("ERS_fertilizer_use_prices", "https://www.ers.usda.gov/data-products/fertilizer-use-and-price/"),
 # --- NOAA
 ("NOAA_CPC_ONI", "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"),
 ("NOAA_NCEI_CAG_json", "https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/statewide/time-series/13/pdsi/1/0/2012-2012/data.json"),
 ("USDM_state_stats", "https://usdmdataservices.unl.edu/api/StateStatistics/GetDroughtSeverityStatisticsByAreaPercent?aoi=IA&startdate=1/1/2012&enddate=1/31/2012&statisticsType=1"),
 # --- EIA
 ("EIA_WPSR_page", "https://www.eia.gov/petroleum/supply/weekly/"),
 ("EIA_crude_stocks_xls", "https://www.eia.gov/dnav/pet/hist_xls/WCRSTUS1w.xls"),
 ("EIA_distillate_stocks_xls", "https://www.eia.gov/dnav/pet/hist_xls/WDISTUS1w.xls"),
 ("EIA_refinery_util_xls", "https://www.eia.gov/dnav/pet/hist_xls/WPULEUS3w.xls"),
 ("EIA_wti_spot_xls", "https://www.eia.gov/dnav/pet/hist_xls/RWTCd.xls"),
 ("EIA_ulsd_ny_spot_xls", "https://www.eia.gov/dnav/pet/hist_xls/EER_EPD2DXL0_PF4_Y35NYd.xls"),
 ("EIA_release_schedule", "https://www.eia.gov/petroleum/supply/weekly/schedule.php"),
 # --- FAO / IEA / World Bank / IMF
 ("FAO_FFPI_page", "https://www.fao.org/worldfoodsituation/foodpricesindex/en/"),
 ("FAO_AMIS_market_monitor", "https://www.amis-outlook.org/amis-monitoring"),
 ("IEA_OMR_page", "https://www.iea.org/reports/oil-market-report-september-2026"),
 ("IEA_data", "https://www.iea.org/data-and-statistics"),
 ("WORLDBANK_pinksheet_page", "https://www.worldbank.org/en/research/commodity-markets"),
 # --- market data (project's existing free source for ETF history)
 ("YAHOO_corn_future", "https://query1.finance.yahoo.com/v8/finance/chart/ZC=F?period1=1167609600&period2=1170288000&interval=1d"),
 ("YAHOO_dba_etf", "https://query1.finance.yahoo.com/v8/finance/chart/DBA?period1=1167609600&period2=1170288000&interval=1d"),
 ("YAHOO_nikkei", "https://query1.finance.yahoo.com/v8/finance/chart/%5EN225?period1=1167609600&period2=1170288000&interval=1d"),
]


def probe(name, url):
    out = {"name": name, "url": url, "attempts": []}
    for attempt in range(2):
        t0 = time.time()
        rec = {"at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read(2_000_000)
                rec.update(status=r.status, content_type=r.headers.get("content-type"), bytes=len(body), final_url=r.geturl(),
                           last_modified=r.headers.get("last-modified"), sha256_head=hashlib.sha256(body[:200000]).hexdigest(),
                           head=body[:160].decode("utf-8", "replace").replace("\n", " "), challenge=bool(CHALLENGE.search(body[:6000].decode("utf-8", "replace"))))
        except urllib.error.HTTPError as e:
            b = e.read(6000).decode("utf-8", "replace")
            rec.update(status=e.code, content_type=e.headers.get("content-type"), bytes=len(b), challenge=bool(CHALLENGE.search(b)), head=b[:120].replace("\n", " "))
        except Exception as e:
            rec.update(status=None, error=repr(e)[:160])
        rec["seconds"] = round(time.time() - t0, 2)
        out["attempts"].append(rec)
        if rec.get("status") == 200 or rec.get("challenge") or (rec.get("status") and rec["status"] in (401, 403, 404)):
            break                                  # do not hammer a refusal
        time.sleep(2)
    last = out["attempts"][-1]
    out["verdict"] = ("ACCESSIBLE" if last.get("status") == 200 and not last.get("challenge") else
                      "BLOCKED_BY_BOT_PROTECTION" if last.get("challenge") or last.get("status") == 403 else
                      "NOT_FOUND" if last.get("status") == 404 else "ERROR")
    return out


if __name__ == "__main__":
    results = []
    for name, url in CANDIDATES:
        results.append(probe(name, url)); time.sleep(1.0)
        r = results[-1]; a = r["attempts"][-1]
        print(f"{r['verdict']:26s} {name:28s} HTTP {a.get('status')} {a.get('content_type') or ''} {a.get('bytes') or ''}")
    Path(sys.argv[1]).write_text(json.dumps({"user_agent": UA, "results": results}, indent=1))
