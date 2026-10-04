"""Bounded capture -> timestamp -> feature -> brief pipeline (stdlib only). One tick = one short process; all state in one SQLite store with a single writer
(flock). Read-only toward the world (public GETs); no broker, credential or order code. Every capture records publication time (when the source says),
retrieval time (our clock), reference period and a content hash; nothing is back-filled or invented: a missing/failed source is labelled in the brief."""
import csv, fcntl, hashlib, io, json, os, re, sqlite3, sys, time, urllib.request, urllib.error
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from . import brief as B
from .wasde import parse_txt
from .cropprog import parse_text
NY = ZoneInfo("America/New_York")
UA = "tbots-research/1.0 (read-only public-data evidence collection)"
WINDOW = ((4, 0), (16, 20))                     # collection window ET (ticks after 16:20 are ignored)
FINAL_FLUSH = (16, 15)
QUOTE_EVERY_MIN, QUOTE_PREP_WINDOWS = 15, (((9, 10), (9, 16)), ((9, 24), (9, 30)))
EDITION_RULES = {"0915": ((9, 15), (9, 29)), "0929": ((9, 29), (9, 45))}     # [earliest, latest) ET for generating an edition; later = MISSED, never backfilled
SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks(tick_id TEXT PRIMARY KEY, started_utc TEXT, ended_utc TEXT, outcome TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS polls(id INTEGER PRIMARY KEY, tick_id TEXT, source TEXT, at_utc TEXT, outcome TEXT, http_status INT, note TEXT);
CREATE TABLE IF NOT EXISTS captures(id INTEGER PRIMARY KEY, tick_id TEXT, source TEXT, key TEXT, url TEXT, retrieved_at_utc TEXT, http_status INT, bytes INT, sha256 TEXT,
  publication_time TEXT, reference_period TEXT, payload_json TEXT, raw_path TEXT, UNIQUE(source, key, sha256));
CREATE TABLE IF NOT EXISTS source_state(source TEXT PRIMARY KEY, last_attempt_utc TEXT, last_ok_utc TEXT, fail_count INT DEFAULT 0, backoff_until_utc TEXT, last_error TEXT, last_key TEXT);
CREATE TABLE IF NOT EXISTS features(id INTEGER PRIMARY KEY, feature TEXT, commodity TEXT, obs_key TEXT, value REAL, publication_time TEXT, capture_id INT, computed_at_utc TEXT, UNIQUE(feature, commodity, obs_key));
CREATE TABLE IF NOT EXISTS editions(mode TEXT, edition_key TEXT, status TEXT, target_time_utc TEXT, generated_at_utc TEXT, late_seconds INT, path TEXT, payload_sha TEXT, degraded_json TEXT, PRIMARY KEY(mode, edition_key));
"""


def utc(): return datetime.now(timezone.utc)
def iso(d): return d.astimezone(timezone.utc).isoformat(timespec="seconds")
def hm(d): return (d.hour, d.minute)


class Pipeline:
    def __init__(self, root, now=None, http_get=None, mode="production", expires_et="2026-10-09T17:00:00", sleep=time.sleep):
        self.root, self.mode, self.sleep = root, mode, sleep
        self.now = now or utc()
        self.et = self.now.astimezone(NY)
        self.expires = datetime.fromisoformat(expires_et).replace(tzinfo=NY)
        self.http_get = http_get or self._http
        os.makedirs(os.path.join(root, "state"), exist_ok=True); os.makedirs(os.path.join(root, "raw"), exist_ok=True)
        os.makedirs(os.path.join(root, "briefs"), exist_ok=True); os.makedirs(os.path.join(root, "cache"), exist_ok=True)
        self.lock_f = None
        self.db = None
        self.tick_id = f"{self.now.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"

    # ------------------------------------------------------------------ plumbing
    def _http(self, url, timeout=20):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, r.read(60_000_000)
        except urllib.error.HTTPError as e: return e.code, None
        except Exception as e: return None, str(e).encode()[:200]

    def open(self):
        self.lock_f = open(os.path.join(self.root, "state", "writer.lock"), "w")
        try: fcntl.flock(self.lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return False
        self.db = sqlite3.connect(os.path.join(self.root, "state", "pipeline.sqlite3"), timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.executescript(SCHEMA); self.db.commit()
        return True

    def get(self, source, url):
        """bounded retries (2 attempts), per-source backoff recorded in state; returns bytes or None"""
        st = self.db.execute("SELECT fail_count, backoff_until_utc FROM source_state WHERE source=?", (source,)).fetchone()
        if st and st[1] and st[1] > iso(self.now): self._poll(source, "BACKOFF_SKIPPED", None, st[1]); return None
        body, status = None, None
        for a in range(2):
            status, body = self.http_get(url)
            if status == 200 and body is not None: break
            if a == 0: self.sleep(2)
        ok = status == 200 and body is not None
        fc = 0 if ok else (st[0] if st else 0) + 1
        back = None if ok else iso(self.now + timedelta(minutes=min(30, 2 ** fc)))
        self.db.execute("INSERT INTO source_state(source,last_attempt_utc,last_ok_utc,fail_count,backoff_until_utc,last_error) VALUES(?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET "
                        "last_attempt_utc=excluded.last_attempt_utc, last_ok_utc=COALESCE(excluded.last_ok_utc, last_ok_utc), fail_count=excluded.fail_count, backoff_until_utc=excluded.backoff_until_utc, last_error=excluded.last_error",
                        (source, iso(self.now), iso(self.now) if ok else None, fc, back, None if ok else f"http {status}"))
        self._poll(source, "OK" if ok else "FAILED", status, url); self.db.commit()
        return body if ok else None

    def _poll(self, source, outcome, status, note):
        self.db.execute("INSERT INTO polls(tick_id,source,at_utc,outcome,http_status,note) VALUES(?,?,?,?,?,?)", (self.tick_id, source, iso(self.now), outcome, status, str(note)[:300]))

    def due(self, source, min_interval_min):
        r = self.db.execute("SELECT last_attempt_utc FROM source_state WHERE source=?", (source,)).fetchone()
        return not (r and r[0] and datetime.fromisoformat(r[0]) > self.now - timedelta(minutes=min_interval_min) + timedelta(seconds=20))

    def capture(self, source, key, url, body, pub, ref, payload, raw=True):
        sha = hashlib.sha256(body).hexdigest(); path = None
        if raw:
            path = os.path.join("raw", sha[:2], sha + ".bin"); full = os.path.join(self.root, path); os.makedirs(os.path.dirname(full), exist_ok=True)
            if not os.path.exists(full):
                with open(full + ".tmp", "wb") as f: f.write(body)
                os.replace(full + ".tmp", full)
        cur = self.db.execute("INSERT OR IGNORE INTO captures(tick_id,source,key,url,retrieved_at_utc,http_status,bytes,sha256,publication_time,reference_period,payload_json,raw_path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                              (self.tick_id, source, key, url, iso(utc()), 200, len(body), sha, pub, ref, json.dumps(payload) if payload is not None else None, path))
        self.db.execute("UPDATE source_state SET last_key=? WHERE source=?", (key, source)); self.db.commit()
        if cur.rowcount: return cur.lastrowid
        return None

    def add_feature(self, feature, com, obs_key, value, pub, cap_id):
        if value is None: return
        self.db.execute("INSERT OR IGNORE INTO features(feature,commodity,obs_key,value,publication_time,capture_id,computed_at_utc) VALUES(?,?,?,?,?,?,?)", (feature, com, obs_key, value, pub, cap_id, iso(self.now)))

    # ------------------------------------------------------------------ gating
    def gate(self):
        if self.et >= self.expires: return "EXPIRED_NO_COLLECTION"
        ok, why = B.is_us_trading_day(self.et.date())
        if ok is not True: return "NOT_A_TRADING_DAY" if ok is False else "CALENDAR_UNKNOWN"
        if not (WINDOW[0] <= hm(self.et) <= WINDOW[1]): return "OUTSIDE_COLLECTION_WINDOW"
        return None

    # ------------------------------------------------------------------ sources
    def poll_quotes(self):
        prep = any(a <= hm(self.et) <= b for a, b in QUOTE_PREP_WINDOWS)
        final = hm(self.et) >= FINAL_FLUSH
        syms = [(n, s, k) for rows in list(B.GLOBAL_INSTRUMENTS.values()) + [B.FOOD_FUEL_QUOTES] for n, s, k in rows]
        for n, s, k in syms:
            src = "quote:" + s
            if not (prep or final or self.due(src, QUOTE_EVERY_MIN)): continue
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{s.replace('^', '%5E')}?range=5d&interval=1d"
            body = self.get(src, url)
            if not body: continue
            try:
                meta = json.loads(body)["chart"]["result"][0]["meta"]; rt = meta.get("regularMarketTime"); px = meta.get("regularMarketPrice")
                prev = meta.get("previousClose") or meta.get("chartPreviousClose")
                self.capture(src, f"{s}@{rt}@{px}", url, body[:4000] if False else json.dumps({"price": px, "prev": prev, "rt": rt, "tz": meta.get("exchangeTimezoneName")}).encode(), None,
                             "last regular-session print", {"name": n, "symbol": s, "kind": k, "price": px, "prior_close": prev, "regularMarketTime": rt, "venue_tz": meta.get("exchangeTimezoneName")}, raw=False)
            except Exception as e:
                self._poll(src, "PARSE_ERROR", 200, repr(e)[:200])
            self.sleep(0.25)

    def poll_eia(self):
        # release-day page carries its own Release Date / Next Release Date; poll 04:05 daily, and every 5 min 10:25-11:30 ET on the release date (or until the new release is captured)
        nxt = self.db.execute("SELECT payload_json FROM captures WHERE source='eia_stocks' ORDER BY id DESC LIMIT 1").fetchone()
        next_rel = json.loads(nxt[0]).get("next_release") if nxt else None
        today = self.et.date().isoformat()
        win = (10, 25) <= hm(self.et) <= (11, 30) and next_rel == today
        daily = hm(self.et) in ((4, 0), (4, 5)) or hm(self.et) >= FINAL_FLUSH or nxt is None
        if not (win or (daily and self.due("eia_stocks", 60))): return
        if win and not self.due("eia_stocks", 5): return
        url = "https://www.eia.gov/dnav/pet/pet_stoc_wstk_dcu_nus_w.htm"; body = self.get("eia_stocks", url)
        if body:
            p = parse_eia_stocks(body.decode("utf8", "replace"))
            if p: self.capture("eia_stocks", "release=" + p["release_date"], url, body, p["release_date"] + "T10:30-04:00 (page says Release Date)", "week ending " + p["week_ending"], p)
        url2 = "https://www.eia.gov/dnav/pet/pet_pnp_wiup_dcu_nus_w.htm"
        if body and self.due("eia_util", 5):
            b2 = self.get("eia_util", url2)
            if b2:
                p2 = parse_eia_util(b2.decode("utf8", "replace"))
                if p2: self.capture("eia_util", "release=" + p2["release_date"], url2, b2, p2["release_date"] + "T10:30-04:00", "week ending " + p2["week_ending"], p2)

    def poll_esmis(self, name, base_url, monday16=False):
        h = hm(self.et)
        win = (11, 58) <= h <= (12, 30) if name == "wasde" else (16, 0) <= h <= (16, 20) and self.et.weekday() == 0
        daily = h in ((4, 0), (4, 5))
        if not ((win and self.due("list:" + name, 5)) or (daily and self.due("list:" + name, 60)) or self.db.execute("SELECT 1 FROM captures WHERE source=?", (name,)).fetchone() is None and self.due("list:" + name, 30)): return
        months = sorted({(self.et.date() - timedelta(days=20)).strftime("%Y-%m"), self.et.strftime("%Y-%m")})
        rows = []
        for ym in months:
            body = self.get("list:" + name, f"{base_url}?date={ym}")
            if body: rows += parse_esmis_listing(body.decode("utf8", "replace"))
        rows = sorted({r["released_listing"] + r["url"]: r for r in rows}.values(), key=lambda r: r["released_listing"])
        rows = [r for r in rows if r["released_listing"][:10] <= self.et.date().isoformat()]
        for r in (rows[-1:] if name == "wasde" else rows[-2:]):
            if self.db.execute("SELECT 1 FROM captures WHERE source=? AND key=?", (name, r["released_listing"][:10])).fetchone(): continue
            doc = self.get(name, r["url"])
            if doc: self.capture(name, r["released_listing"][:10], r["url"], doc, r["released_listing"] + " (listing nominal; WASDE 12:00 ET / Crop Progress 16:00 ET)", "see document", {"url": r["url"], "listing": r["released_listing"]})

    def poll_usdm(self):
        h = hm(self.et); thu = self.et.weekday() == 3
        win = thu and (8, 30) <= h <= (10, 0); daily = h in ((4, 0), (4, 5)) or h >= FINAL_FLUSH
        if not ((win and self.due("usdm", 10)) or (daily and self.due("usdm", 60)) or (self.db.execute("SELECT 1 FROM captures WHERE source='usdm'").fetchone() is None and self.due("usdm", 30))): return
        start = self.et.date() - timedelta(days=28); weeks = {}; raws = []
        for st, fips in (("IA", 19), ("IL", 17), ("IN", 18), ("NE", 31), ("MN", 27)):
            url = f"https://usdmdataservices.unl.edu/api/StateStatistics/GetDroughtSeverityStatisticsByAreaPercent?aoi={fips}&startdate={start.month}/{start.day}/{start.year}&enddate={self.et.month}/{self.et.day}/{self.et.year}&statisticsType=2"
            b = self.get("usdm", url)
            if b is None: return                                   # partial belt = no capture (labelled missing), never a partial average
            raws.append(b)
            for r in csv.DictReader(io.StringIO(b.decode("utf-8-sig"))): weeks.setdefault(r["MapDate"], {})[st] = float(r["D1"])
        full = sorted(k for k, v in weeks.items() if len(v) == 5)
        if len(full) >= 2:
            ser = [{"map_date": f"{k[:4]}-{k[4:6]}-{k[6:]}", "belt_d1": sum(weeks[k].values()) / 5} for k in full]
            self.capture("usdm", "map=" + ser[-1]["map_date"], "usdmdataservices.unl.edu StateStatistics (FIPS 19,17,18,31,27)", b"\n".join(raws), ser[-1]["map_date"] + " map (published Thursday 08:30 ET)", "map " + ser[-1]["map_date"], {"weeks": ser})

    # ------------------------------------------------------------------ features
    def compute_features(self):
        c = lambda src: self.db.execute("SELECT id, key, retrieved_at_utc, payload_json, raw_path, publication_time FROM captures WHERE source=? ORDER BY key DESC, id DESC", (src,)).fetchall()
        for cid, key, rt, pj, rp, pub in c("wasde")[:1]:
            try:
                blk = B._wasde_block(os.path.join(self.root, rp))
                for com, b in blk.items():
                    self.add_feature("w_stu_chg_pp", com, key, b["stu_chg_pp"], pub, cid); self.add_feature("w_yield_rev_pct", com, key, b["yield_rev_pct"], pub, cid)
            except Exception as e: self._poll("wasde", "FEATURE_ERROR", None, repr(e)[:200])
        cps = c("crop_progress")[:2]
        if len(cps) == 2:
            try:
                rows = [{r["crop"]: r for r in parse_text(open(os.path.join(self.root, x[4]), encoding="latin-1").read(), x[1])} for x in cps]
                for crop in ("corn", "soybeans"):
                    if crop in rows[0] and crop in rows[1]: self.add_feature("c_ge_chg_pp", crop, cps[0][1], rows[0][crop]["good_excellent"] - rows[1][crop]["good_excellent"], cps[0][5], cps[0][0])
            except Exception as e: self._poll("crop_progress", "FEATURE_ERROR", None, repr(e)[:200])
        for cid, key, rt, pj, rp, pub in c("usdm")[:1]:
            w = json.loads(pj)["weeks"]
            self.add_feature("d_belt_d1_chg_pp", "corn/soybeans", key, w[-1]["belt_d1"] - w[-2]["belt_d1"], pub, cid)
        for cid, key, rt, pj, rp, pub in c("eia_stocks")[:1]:
            try:
                f = eia_features(json.loads(pj), (self.db.execute("SELECT payload_json FROM captures WHERE source='eia_util' ORDER BY id DESC LIMIT 1").fetchone() or [None])[0], self.root)
                for feat, com, v in f: self.add_feature(feat, com, key, v, pub, cid)
            except Exception as e: self._poll("eia_stocks", "FEATURE_ERROR", None, repr(e)[:200])
        self.db.commit()

    # ------------------------------------------------------------------ brief inputs and editions
    def latest_sources(self):
        d = self.db; out = {"asof": self.et.date().isoformat()}; status = {}
        for src in ("wasde", "crop_progress", "usdm", "eia_stocks", "eia_util"):
            s = d.execute("SELECT last_ok_utc, fail_count, last_error, last_attempt_utc FROM source_state WHERE source=?", (src,)).fetchone()
            n = d.execute("SELECT COUNT(*), MAX(retrieved_at_utc) FROM captures WHERE source=?", (src,)).fetchone()
            status[src] = {"captures": n[0], "last_capture_utc": n[1], "last_ok_poll_utc": s[0] if s else None, "consecutive_failures": s[1] if s else None, "last_error": s[2] if s else None}
        out["source_status"] = status
        r = d.execute("SELECT key, url, retrieved_at_utc, raw_path, payload_json FROM captures WHERE source='wasde' ORDER BY key DESC LIMIT 1").fetchone()
        if r:
            lst = json.loads(r[4])["listing"]; out["wasde"] = {"released_listing": lst, "url": r[1], "cache": r[3], "retrieved_at_utc": r[2], "abs_path": os.path.join(self.root, r[3])}
        rs = d.execute("SELECT key, url, retrieved_at_utc, raw_path, payload_json FROM captures WHERE source='crop_progress' ORDER BY key DESC LIMIT 2").fetchall()[::-1]
        out["crop_progress"] = [{"released_listing": json.loads(x[4])["listing"], "url": x[1], "cache": x[3], "retrieved_at_utc": x[2], "abs_path": os.path.join(self.root, x[3])} for x in rs]
        r = d.execute("SELECT url, retrieved_at_utc, payload_json FROM captures WHERE source='usdm' ORDER BY key DESC, id DESC LIMIT 1").fetchone()
        if r: out["usdm"] = {"weeks": json.loads(r[2])["weeks"], "url": r[0], "retrieved_at_utc": r[1]}
        r = d.execute("SELECT retrieved_at_utc, payload_json, url FROM captures WHERE source='eia_stocks' ORDER BY id DESC LIMIT 1").fetchone()
        if r:
            u = d.execute("SELECT payload_json FROM captures WHERE source='eia_util' ORDER BY id DESC LIMIT 1").fetchone()
            f = {k: (v, c) for k, c, v in eia_features(json.loads(r[1]), u[0] if u else None, self.root)}
            p = json.loads(r[1])
            out["eia"] = {"release_assumed": p["release_date"], "release_basis": "stated on the EIA page (Release Date)", "week_ending": p["week_ending_iso"], "dist_surprise": f.get("e_dist_stock_surprise_kbbl", (None,))[0], "crude_surprise": f.get("e_crude_stock_surprise_kbbl", (None,))[0],
                          "util_chg": f.get("e_util_chg_pp", (None,))[0], "levels": {"distillate_stocks_kbbl": p["distillate"][-1], "crude_stocks_ex_spr_kbbl": p["crude"][-1]}, "url": r[2], "retrieved_at_utc": r[0], "next_release": p.get("next_release")}
        return out

    def quote_cache(self):
        qc = {}
        for sym in {s for rows in list(B.GLOBAL_INSTRUMENTS.values()) + [B.FOOD_FUEL_QUOTES] for _, s, _ in rows}:
            r = self.d_quote(sym)
            if r: qc[sym] = r
        return qc

    def d_quote(self, sym):
        r = self.db.execute("SELECT payload_json, retrieved_at_utc FROM captures WHERE source=? ORDER BY id DESC LIMIT 1", ("quote:" + sym,)).fetchone()
        if not r: return None
        p = json.loads(r[0]); tz = p.get("venue_tz") or "UTC"; ev = datetime.fromtimestamp(p["regularMarketTime"], tz=timezone.utc); loc = ev.astimezone(ZoneInfo(tz))
        age = (self.et.astimezone(ZoneInfo(tz)).date() - loc.date()).days; ret = datetime.fromisoformat(r[1]); age_min = round((self.now - ret).total_seconds() / 60, 1)
        return {"available": True, "price": p["price"], "prior_close": p["prior_close"], "pct": None if not (p["price"] and p["prior_close"]) else (p["price"] / p["prior_close"] - 1) * 100,
                "event_time_utc": ev.isoformat(), "event_local_date": loc.date().isoformat(), "session_label": "fresh_close_or_live" if age == 0 else f"stale_{age}d_unverified_cause", "venue_tz": tz,
                "retrieved_at_utc": r[1], "capture_age_min": age_min, "capture_stale": age_min > 30,
                "delay_note": "Yahoo public chart API: real-time/delay status not disclosed; indicative only, not an official or opening-auction price"}

    def editions(self):
        for ed, ((h0, m0), (h1, m1)) in EDITION_RULES.items():
            key = f"{self.et.date().isoformat()}_{ed}"
            if self.db.execute("SELECT 1 FROM editions WHERE mode=? AND edition_key=?", (self.mode, key)).fetchone(): continue
            target = self.et.replace(hour=h0, minute=m0, second=0, microsecond=0); latest = self.et.replace(hour=h1, minute=m1, second=0, microsecond=0)
            if self.et < target: continue
            jp = os.path.join(self.root, "briefs", f"{self.mode}_food_fuel_brief_{key}.json")
            if self.et >= latest and not os.path.exists(jp):
                self.db.execute("INSERT INTO editions(mode,edition_key,status,target_time_utc,generated_at_utc,late_seconds) VALUES(?,?,?,?,?,?)", (self.mode, key, "MISSED_NOT_BACKFILLED", iso(target), iso(self.now), int((self.et - target).total_seconds())))
                self.db.commit(); continue
            ls = self.latest_sources(); cache = os.path.join(self.root, "cache"); json.dump(ls, open(os.path.join(cache, "latest_sources.json"), "w"), indent=1)
            late = int((self.et - target).total_seconds())
            status, path = B.write_edition(self.et.date(), ed, os.path.join(self.root, "briefs"), cache, quote_cache=self.quote_cache(), mode=self.mode, pipeline_meta={"tick_id": self.tick_id, "late_seconds": late, "source_status": ls["source_status"], "generated_by": "pipeline"})
            p = json.load(open(path))
            self.db.execute("INSERT OR IGNORE INTO editions(mode,edition_key,status,target_time_utc,generated_at_utc,late_seconds,path,payload_sha,degraded_json) VALUES(?,?,?,?,?,?,?,?,?)",
                            (self.mode, key, "RECOVERED_EXISTING_FILE" if status == "EXISTS" else ("WRITTEN_LATE" if late > 120 else "WRITTEN"), iso(target), iso(self.now), late, path, hashlib.sha256(open(path, "rb").read()).hexdigest(), json.dumps(p.get("degraded"))))
            self.db.commit()

    # ------------------------------------------------------------------ the tick
    def run(self):
        if not self.open(): return "SKIPPED_LOCKED_OTHER_TICK_RUNNING"
        self.db.execute("UPDATE ticks SET outcome='ABANDONED_BY_CRASH', ended_utc=? WHERE outcome='RUNNING'", (iso(utc()),))     # we hold the writer lock, so any RUNNING row is an orphan
        self.db.execute("INSERT OR IGNORE INTO ticks(tick_id,started_utc,outcome) VALUES(?,?,?)", (self.tick_id, iso(self.now), "RUNNING")); self.db.commit()
        g = self.gate()
        if g:
            self.db.execute("UPDATE ticks SET ended_utc=?, outcome=? WHERE tick_id=?", (iso(utc()), g, self.tick_id)); self.db.commit(); return g
        note = []
        for fn in (self.poll_quotes, self.poll_eia, lambda: self.poll_esmis("wasde", "https://esmis.nal.usda.gov/publication/world-agricultural-supply-and-demand-estimates"),
                   lambda: self.poll_esmis("crop_progress", "https://esmis.nal.usda.gov/publication/crop-progress"), self.poll_usdm, self.compute_features, self.editions):
            try: fn()
            except Exception as e:
                note.append(f"{getattr(fn, '__name__', 'step')}: {e!r}"[:200]); self.db.rollback() if self.db.in_transaction else None
        self.db.execute("UPDATE ticks SET ended_utc=?, outcome=?, note=? WHERE tick_id=?", (iso(utc()), "OK" if not note else "OK_WITH_STEP_ERRORS", "; ".join(note), self.tick_id)); self.db.commit()
        return "OK" if not note else "OK_WITH_STEP_ERRORS: " + "; ".join(note)


# ---------------------------------------------------------------------- parsers
def parse_esmis_listing(html):
    out = []
    for tr in re.findall(r"<tr.*?</tr>", html, re.S):
        t = re.search(r'datetime="([^"]+)"', tr); l = re.findall(r'href="([^"]+\.(?:txt|xml))"', tr)
        if t and l: out.append({"released_listing": t.group(1), "url": "https://esmis.nal.usda.gov" + l[0] if l[0].startswith("/") else l[0]})
    return out


def _wk(s):  # 09/25/26 -> 2026-09-25
    m, d, y = s.split("/"); return f"20{y}-{m}-{d}"


def parse_eia_stocks(html):
    rel = re.search(r"Release Date:\s*(\d+)/(\d+)/(\d{4})", html); nxt = re.search(r"Next Release Date:\s*(\d+)/(\d+)/(\d{4})", html)
    wk = re.findall(r'class="Series5">(\d\d/\d\d/\d\d)', html)
    if not (rel and wk): return None
    def row(label):
        i = html.find(f'class="DataStub1">{label}<')
        if i < 0: return None
        vals = re.findall(r'class="(?:DataB|Current2)">([\d,\.\-]+)<', html[i:i + 2500])
        return [float(v.replace(",", "")) for v in vals[:len(wk)]]
    dist, crude = row("Distillate Fuel Oil"), row("Commercial Crude Oil (Excl. Lease Stock)")
    if not dist or not crude: return None
    rd = f"{rel.group(3)}-{int(rel.group(1)):02d}-{int(rel.group(2)):02d}"
    nr = None if not nxt else f"{nxt.group(3)}-{int(nxt.group(1)):02d}-{int(nxt.group(2)):02d}"
    return {"release_date": rd, "next_release": nr, "weeks_iso": [_wk(w) for w in wk], "week_ending": wk[-1], "week_ending_iso": _wk(wk[-1]), "distillate": dist, "crude": crude}


def parse_eia_util(html):
    rel = re.search(r"Release Date:\s*(\d+)/(\d+)/(\d{4})", html); wk = re.findall(r'class="Series5">(\d\d/\d\d/\d\d)', html)
    i = html.find('class="DataStub1">Percent Operable Utilization<')
    if not (rel and wk) or i < 0: return None
    vals = [float(v) for v in re.findall(r'class="(?:DataB|Current2)">([\d\.]+)<', html[i:i + 3000])][:len(wk)]
    if len(vals) < 2: return None
    return {"release_date": f"{rel.group(3)}-{int(rel.group(1)):02d}-{int(rel.group(2)):02d}", "week_ending": wk[-1], "week_ending_iso": _wk(wk[-1]), "util": vals}


def eia_features(stocks, util_json, root):
    """'surprise' = this week's change in stocks minus the mean change in the same week of the previous 5 years (seasonal-norm deviation); it is NOT a
    forecast error against analyst consensus (no free consensus source). History from the bulk-file extract; latest weeks from the page."""
    ex = json.load(open(os.path.join(os.path.dirname(__file__), "..", "cache", "eia_extract.json"))) if os.path.exists(os.path.join(os.path.dirname(__file__), "..", "cache", "eia_extract.json")) else json.load(open(os.path.join(root, "eia_extract.json")))
    out = []
    def surprise(name, page_vals):
        s = {a: b for a, b in ex[name]["data"]}
        for w, v in zip(stocks["weeks_iso"], page_vals): s[w.replace("-", "")] = v
        ds = sorted(s); we = stocks["week_ending_iso"].replace("-", ""); i = ds.index(we)
        chg = s[we] - s[ds[i - 1]]; ref = []
        for y in range(1, 6):
            t = (date.fromisoformat(stocks["week_ending_iso"]) - timedelta(days=364 * y)).strftime("%Y%m%d")
            if t in s and ds.index(t) >= 1: ref.append(s[t] - s[ds[ds.index(t) - 1]])
        return chg - sum(ref) / len(ref) if len(ref) >= 3 else None
    out.append(("e_dist_stock_surprise_kbbl", "distillate", surprise("distillate_stocks_kbbl", stocks["distillate"])))
    out.append(("e_crude_stock_surprise_kbbl", "crude", surprise("crude_stocks_ex_spr_kbbl", stocks["crude"])))
    if util_json:
        u = json.loads(util_json)["util"]; out.append(("e_util_chg_pp", "distillate", u[-1] - u[-2]))
    return out
