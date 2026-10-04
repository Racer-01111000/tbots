"""Pre-market food/fuel evidence brief. READ-ONLY and order-free: GET requests to public sources only, writes only its own cache and output files.
Idempotent per (US trading date, edition 0915|0929): an existing edition file is never rewritten. The 0929 edition lists what changed since 0915.
Reuses the contract of the existing global pre-open brief (instrument sections, session-staleness labelling, edition keys); the source list
below is verified equal to kim_shadow_tick.GLOBAL_INSTRUMENTS by tests, but this module imports nothing from the paper runner."""
import json, os, re, sys, time, urllib.request, urllib.error
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from ffmod.cropprog import parse_text
from ffmod.wasde import parse_txt
NY = ZoneInfo("America/New_York")
EDITIONS = {"0915": (9, 15), "0929": (9, 29)}
GLOBAL_INSTRUMENTS = {   # same contract as kim_shadow_tick.GLOBAL_INSTRUMENTS
    "asia": [("Nikkei 225", "^N225", "index"), ("Hang Seng", "^HSI", "index"), ("CSI 300 (mainland, Shanghai)", "000300.SS", "index"), ("KOSPI", "^KS11", "index"), ("S&P/ASX 200", "^AXJO", "index")],
    "europe": [("FTSE 100", "^FTSE", "index"), ("DAX Performance Index", "^GDAXI", "index"), ("Euro Stoxx 50", "^STOXX50E", "index")],
    "us_futures": [("S&P 500 futures (ES)", "ES=F", "future"), ("Nasdaq futures (NQ)", "NQ=F", "future")],
    "commodities_fx": [("WTI Crude (CL)", "CL=F", "future"), ("Brent Crude (BZ)", "BZ=F", "future"), ("Gold (GC)", "GC=F", "future"), ("ICE US Dollar Index, spot/cash (DXY)", "DX-Y.NYB", "index")]}
FOOD_FUEL_QUOTES = [("Corn futures (ZC, front-month continuous)", "ZC=F", "future"), ("Soybean futures (ZS)", "ZS=F", "future"), ("Chicago wheat futures (ZW)", "ZW=F", "future"),
                    ("Heating oil / ULSD futures (HO)", "HO=F", "future"), ("Natural gas futures (NG)", "NG=F", "future"),
                    ("Corn ETF proxy (CORN)", "CORN", "etf_proxy"), ("Agriculture ETF proxy (DBA)", "DBA", "etf_proxy"), ("Oil ETF proxy (USO)", "USO", "etf_proxy"),
                    ("CF Industries (fertilizer equity)", "CF", "equity"), ("Mosaic (fertilizer equity)", "MOS", "equity")]
HOLIDAYS = {"2026": ["01-01", "01-19", "02-16", "04-03", "05-25", "06-19", "07-03", "09-07", "11-26", "12-25"], "2027": ["01-01", "01-18", "02-15", "03-26", "05-31", "06-18", "07-05", "09-06", "11-25", "12-24"]}


def is_us_trading_day(d: date):
    ys = HOLIDAYS.get(str(d.year))
    if ys is None: return None, "no holiday table for this year"
    if d.weekday() >= 5 or d.strftime("%m-%d") in ys: return False, "weekend or NYSE holiday (rule-based table, not exchange-verified)"
    return True, "rule-based NYSE calendar, not exchange-verified"


def _get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "tbots-research/1.0 (read-only public-data evidence collection)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, None
    except Exception as e: return None, str(e).encode()


def quote(sym, now, offline_cache):
    if offline_cache is not None and sym in offline_cache: return offline_cache[sym]
    st, body = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym.replace('^', '%5E')}?range=5d&interval=1d")
    time.sleep(0.3)
    if st != 200: return {"available": False, "http_status": st}
    try:
        meta = json.loads(body)["chart"]["result"][0]["meta"]; tz, rt = meta.get("exchangeTimezoneName"), meta.get("regularMarketTime")
        ev = datetime.fromtimestamp(rt, tz=timezone.utc); loc = ev.astimezone(ZoneInfo(tz)); age = (now.astimezone(ZoneInfo(tz)).date() - loc.date()).days
        px, prev = meta.get("regularMarketPrice"), meta.get("previousClose") or meta.get("chartPreviousClose")
        return {"available": True, "price": px, "prior_close": prev, "pct": None if not (px and prev) else (px / prev - 1) * 100, "event_time_utc": ev.isoformat(),
                "event_local_date": loc.date().isoformat(), "session_label": "fresh_close_or_live" if age == 0 else f"stale_{age}d_unverified_cause", "venue_tz": tz,
                "delay_note": "Yahoo public chart API: real-time/delay status not disclosed; indicative only, not an official or opening-auction price"}
    except Exception as e:
        return {"available": False, "error": f"parse_error: {e}"}


def _wasde_block(path):
    txt = open(path, encoding="latin-1").read(); r = parse_txt(txt); out = {}
    for com, v in r.items():
        it = v["items"]
        if not {"ending_stocks", "use_total"} <= set(it): continue
        e, u = it["ending_stocks"], it["use_total"]; stu = e["cur"] / u["cur"]; stu_prev = None if e["prev"] is None or u["prev"] is None else e["prev"] / u["prev"]
        y = it.get("yield", {})
        out[com] = {"marketing_year": v["newest_year_label"], "stocks_to_use": stu, "stocks_to_use_prev_month": stu_prev,
                    "stu_chg_pp": None if stu_prev is None else (stu - stu_prev) * 100, "ending_stocks": e["cur"], "yield": y.get("cur"), "yield_prev_month": y.get("prev"),
                    "yield_rev_pct": None if not y.get("prev") else (y["cur"] / y["prev"] - 1) * 100}
    return out


def build_payload(asof: date, edition: str, cache_dir, offline=False, quote_cache=None):
    """cache_dir/raw must hold the latest WASDE + Crop Progress texts (fetched by fetch_latest) and the EIA extract / USDM json."""
    now = datetime.combine(asof, datetime.min.time(), NY).replace(hour=EDITIONS[edition][0], minute=EDITIONS[edition][1]).astimezone(timezone.utc)
    ok, why = is_us_trading_day(asof)
    p = {"edition_key": f"{asof.isoformat()}_{edition}", "edition_target_time_utc": now.isoformat(), "actual_run_time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "asof_session_date": asof.isoformat(), "trading_day_check": {"is_trading_day": ok, "basis": why},
         "scope_notes": ["Research brief only; it cannot issue orders and does not alter any bot.", "ETF/equity/futures quotes are proxies, NOT spot commodity prices and NOT opening-auction data.",
                         "Evidence labels come from FEATURE_SPEC_v1 tests: indicators are priced on release; no post-release drift was supported."]}
    if ok is not True:
        p["status"] = "NO_BRIEF_NOT_A_US_TRADING_DAY" if ok is False else "NO_BRIEF_CALENDAR_UNKNOWN"; return p
    ev = {e["feature"] + "|" + e["commodity"]: e for e in json.load(open(os.path.join(ROOT, "features/EVIDENCE_MAP.json")))}
    sources, items = [], []
    lat = json.load(open(os.path.join(cache_dir, "latest_sources.json")))
    # --- WASDE
    w = lat.get("wasde")
    if w:
        blk = _wasde_block(os.path.join(cache_dir, "raw", w["cache"]))
        sources.append({"source": "USDA WASDE", "publication_time": w["released_listing"], "retrieval_time_utc": w["retrieved_at_utc"], "reference_period": "marketing year " + (blk.get("corn", {}).get("marketing_year") or "?"), "url": w["url"], "units": "mil bu, bu/acre; stocks-to-use ratio", "kind": "forecast (USDA projection); previous-month projection from the same document"})
        for com, b in blk.items():
            for feat, val in (("w_stu_chg_pp", b["stu_chg_pp"]), ("w_yield_rev_pct", b["yield_rev_pct"])):
                if val is None: continue
                e = ev.get(f"{feat}|{com}", {})
                items.append({"area": "food", "commodity": com, "feature": feat, "value": round(val, 3), "observed_level": {"stocks_to_use": round(b["stocks_to_use"], 4), "yield": b["yield"]}, "source": "WASDE " + w["released_listing"][:10],
                              "class": "forecast revision (interpretation of NASS/FAS inputs)", "evidence_support": e.get("support_by_target"), "mechanism": e.get("mechanism"), "competing_explanations": e.get("competing"),
                              "transmission_horizon": e.get("horizon"), "invalidation": e.get("invalid")})
    # --- crop progress
    cp = lat.get("crop_progress") or []
    if len(cp) >= 2:
        a, b = cp[-2], cp[-1]
        ra = {r["crop"]: r for r in parse_text(open(os.path.join(cache_dir, "raw", a["cache"]), encoding="latin-1").read(), a["released_listing"][:10])}
        rb = {r["crop"]: r for r in parse_text(open(os.path.join(cache_dir, "raw", b["cache"]), encoding="latin-1").read(), b["released_listing"][:10])}
        sources.append({"source": "USDA NASS Crop Progress", "publication_time": b["released_listing"], "retrieval_time_utc": b["retrieved_at_utc"], "reference_period": "week ending " + (date.fromisoformat(b["released_listing"][:10]) - timedelta(days=1)).isoformat(), "url": b["url"], "units": "percent of crop", "kind": "observation (NASS survey)"})
        for crop in ("corn", "soybeans"):
            if crop in ra and crop in rb:
                e = ev.get(f"c_ge_chg_pp|{crop}", {})
                items.append({"area": "food", "commodity": crop, "feature": "c_ge_chg_pp", "value": rb[crop]["good_excellent"] - ra[crop]["good_excellent"], "observed_level": {"good_excellent_pct": rb[crop]["good_excellent"]},
                              "source": "Crop Progress " + b["released_listing"][:10], "class": "observation", "evidence_support": e.get("support_by_target"), "mechanism": e.get("mechanism"), "competing_explanations": e.get("competing"),
                              "transmission_horizon": e.get("horizon"), "invalidation": e.get("invalid")})
    # --- drought
    d = lat.get("usdm")
    if d and len(d["weeks"]) >= 2:
        sources.append({"source": "US Drought Monitor", "publication_time": d["weeks"][-1]["map_date"] + " (Tuesday map; published Thursday 08:30 ET)", "retrieval_time_utc": d["retrieved_at_utc"], "reference_period": "map " + d["weeks"][-1]["map_date"], "url": d["url"], "units": "percent area D1+, mean of IA/IL/IN/NE/MN", "kind": "observation (expert-assessed map)"})
        e = ev.get("d_belt_d1_chg_pp|corn", {})
        items.append({"area": "food", "commodity": "corn/soybeans", "feature": "d_belt_d1_chg_pp", "value": round(d["weeks"][-1]["belt_d1"] - d["weeks"][-2]["belt_d1"], 2), "observed_level": {"belt_d1_pct": round(d["weeks"][-1]["belt_d1"], 2)},
                      "source": "USDM " + d["weeks"][-1]["map_date"], "class": "observation", "evidence_support": e.get("support_by_target"), "mechanism": e.get("mechanism"), "competing_explanations": e.get("competing"),
                      "transmission_horizon": e.get("horizon"), "invalidation": e.get("invalid"), "season_note": "feature defined for Apr-Sep only; outside the window it is context, not a tested signal" if not 4 <= asof.month <= 9 else None})
    # --- EIA
    eia = lat.get("eia")
    if eia:
        sources.append({"source": "EIA Weekly Petroleum Status (bulk PET)", "publication_time": eia["release_assumed"] + "T10:30 ET (assumed Wednesday schedule)", "retrieval_time_utc": eia["retrieved_at_utc"], "reference_period": "week ending " + eia["week_ending"], "url": eia["url"], "units": "thousand bbl; percent", "kind": "observation (weekly survey)"})
        for feat, com, key in (("e_dist_stock_surprise_kbbl", "distillate", "dist_surprise"), ("e_crude_stock_surprise_kbbl", "crude", "crude_surprise"), ("e_util_chg_pp", "distillate", "util_chg")):
            if eia.get(key) is None: continue
            e = ev.get(f"{feat}|{com}", {})
            items.append({"area": "fuel", "commodity": com, "feature": feat, "value": round(eia[key], 2), "observed_level": eia.get("levels"), "source": "EIA week ending " + eia["week_ending"], "class": "observation",
                          "evidence_support": e.get("support_by_target"), "mechanism": e.get("mechanism"), "competing_explanations": e.get("competing"), "transmission_horizon": e.get("horizon"), "invalidation": e.get("invalid")})
    p["sources"], p["items"] = sources, items
    # --- markets
    qs = {}
    for sec, rows in GLOBAL_INSTRUMENTS.items():
        qs[sec] = [{"name": n, "symbol": s, "kind": k, **quote(s, now, quote_cache)} for n, s, k in rows]
    qs["food_fuel_proxies"] = [{"name": n, "symbol": s, "kind": k, **quote(s, now, quote_cache)} for n, s, k in FOOD_FUEL_QUOTES]
    p["markets"] = qs
    flat = [q for rows in qs.values() for q in rows]
    p["coverage"] = {"quotes_total": len(flat), "unavailable": [q["symbol"] for q in flat if not q.get("available")], "stale": [q["symbol"] for q in flat if str(q.get("session_label", "")).startswith("stale")],
                     "not_covered_by_this_module": ["FAO Food Price Index (page blocked: bot protection, not circumvented)", "IEA Oil Market Report (blocked)", "USDA FAS export sales (no accessible machine-readable page found)", "freight/shipping indices", "fertilizer spot prices (World Bank monthly, latest-vintage only)"]}
    p["status"] = "OK"
    return p


def render_md(p):
    if p.get("status") != "OK": return f"# Food/fuel brief {p['edition_key']}\n\n{p['status']}: {p['trading_day_check']}\n"
    L = [f"# Food & fuel pre-market evidence brief - {p['edition_key']}", f"Edition target {p['edition_target_time_utc']}; actually run {p['actual_run_time_utc']} (if the run time precedes the target, quotes are from the last completed sessions and are labelled stale). Trading-day check: {p['trading_day_check']['basis']}.", ""]
    L += ["> " + n for n in p["scope_notes"]] + [""]
    for area in ("food", "fuel"):
        L += [f"## {area.title()} - what changed (latest releases)", ""]
        for it in p["items"]:
            if it["area"] != area: continue
            sup = it["evidence_support"] if it["evidence_support"] else "none"
            L += [f"- **{it['commodity']} / {it['feature']}**: {it['value']:+} ({it['class']}; {it['source']}); level {it['observed_level']}",
                  f"  - tested support: {sup}", f"  - mechanism: {it['mechanism']}", f"  - competing explanations: {'; '.join(it['competing_explanations'] or [])}",
                  f"  - transmission horizon: {it['transmission_horizon']}; invalidation: {it['invalidation']}"]
            if it.get("season_note"): L.append(f"  - note: {it['season_note']}")
        L.append("")
    L += ["## Sources (publication time / reference period / retrieved / units)", ""]
    for s in p["sources"]: L.append(f"- {s['source']}: published {s['publication_time']}; period {s['reference_period']}; retrieved {s['retrieval_time_utc']}; {s['units']}; {s['kind']}; {s['url']}")
    L += ["", "## Overnight / intraday context (indicative quotes)", ""]
    for sec, rows in p["markets"].items():
        L.append(f"**{sec}**")
        for q in rows:
            L.append(f"- {q['name']}: " + (f"{q['price']:.2f} ({q['pct']:+.2f}%) [{q['session_label']}, {q['event_local_date']}]" if q.get("available") and q.get("pct") is not None else "UNAVAILABLE"))
        L.append("")
    c = p["coverage"]
    L += ["## Coverage and data quality", "", f"- quotes available {c['quotes_total']-len(c['unavailable'])}/{c['quotes_total']}; unavailable: {c['unavailable'] or 'none'}; stale: {c['stale'] or 'none'}"]
    L += [f"- not covered: {x}" for x in c["not_covered_by_this_module"]]
    if p.get("new_since_0915") is not None: L += ["", "## Changed since 09:15 edition", "", json.dumps(p["new_since_0915"], indent=1)]
    return "\n".join(L) + "\n"


def write_edition(asof: date, edition: str, out_dir, cache_dir, offline=False, quote_cache=None):
    """idempotent: returns ('EXISTS'|'WRITTEN', path). The edition file is created with O_EXCL so concurrent runs cannot both write."""
    os.makedirs(out_dir, exist_ok=True); key = f"{asof.isoformat()}_{edition}"; jp = os.path.join(out_dir, f"food_fuel_brief_{key}.json")
    if os.path.exists(jp): return "EXISTS", jp
    p = build_payload(asof, edition, cache_dir, offline, quote_cache)
    if edition == "0929":
        prev = os.path.join(out_dir, f"food_fuel_brief_{asof.isoformat()}_0915.json")
        if os.path.exists(prev) and p.get("status") == "OK":
            old = json.load(open(prev)); oi = {(i["feature"], i["commodity"]): i["value"] for i in old.get("items", [])}
            p["new_since_0915"] = [{"feature": i["feature"], "commodity": i["commodity"], "was": oi.get((i["feature"], i["commodity"])), "now": i["value"]} for i in p["items"] if oi.get((i["feature"], i["commodity"])) != i["value"]]
            oq = {q["symbol"]: q for rows in old["markets"].values() for q in rows}
            p["moved_since_0915"] = [{"symbol": q["symbol"], "pct_0915": oq.get(q["symbol"], {}).get("pct"), "pct_0929": q.get("pct")} for rows in p["markets"].values() for q in rows if q.get("available") and oq.get(q["symbol"], {}).get("pct") is not None and abs((q.get("pct") or 0) - oq[q["symbol"]]["pct"]) >= 0.2]
        elif p.get("status") == "OK": p["new_since_0915"] = None; p["note_0915"] = "09:15 edition not found - cannot diff"
    fd = os.open(jp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as f: json.dump(p, f, indent=1, default=str)
    open(os.path.join(out_dir, f"food_fuel_brief_{key}.md"), "w").write(render_md(p))
    return "WRITTEN", jp
