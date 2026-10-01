"""Kim bounded read-only shadow capture -- one tick = one invocation, run
every minute by tbots-kim-shadow-20261001.timer within today's authorized
window only (2026-10-01 04:00-16:15 America/New_York).

Read-only. GET-only. Host-allowlisted. Never imports/calls route_order(),
never sets TBOTS_ALPACA_SUBMISSION_ENABLED, never constructs a POST
anywhere in this file. Fails closed on any identity/clock/resource/switch
check. Does not recompute or duplicate the decision already recorded for
today (session 1, the manual smoke test) -- today's decision credit is
already consumed.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path("/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots")
DEV_STAGING = REPO / "experiments/sam_dev_staging_20261001"
sys.path.insert(0, str(DEV_STAGING / "alpaca_adapter"))
sys.path.insert(0, str(REPO / "scripts"))

from runtime_credential_loader import load_credential_headers  # noqa: E402
from alpaca_adapter import submission_enabled, assert_trading_host_allowed, PAPER_TRADING_HOST  # noqa: E402

PILOT_DIR = DEV_STAGING / "kim_shadow_pilot"
DB_PATH = PILOT_DIR / "state.sqlite3"
LOCK_PATH = PILOT_DIR / "tick.lock"

SESSION_DATE = "2026-10-01"
WINDOW_START_UTC = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)   # 04:00 America/New_York (EDT)
EXPIRY_UTC = datetime(2026, 10, 1, 20, 15, 0, tzinfo=timezone.utc)       # 16:15 America/New_York (EDT)
IEX_EXPECTED_OPEN_UTC = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)  # 08:00 America/New_York

UNIVERSE = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]

ALPACA_HOSTS = ("https://paper-api.alpaca.markets", "https://data.alpaca.markets")
NEWS_FEEDS = (
    "https://www.eia.gov/rss/todayinenergy.xml",
    "https://www.eia.gov/petroleum/weekly/includes/week_in_petroleum_rss.xml",
    "https://www.nass.usda.gov/rss/reports.xml",
    "https://www.nass.usda.gov/rss/news.xml",
)
NEWS_POLL_INTERVAL_S = 300
DISK_RESERVE_BYTES = 1 * 1024 ** 3
OUTPUT_CAP_BYTES = 5 * 1024 ** 3
PILOT_ID = "KIM_BOUNDED_SHADOW_20261001"
GENOME_ID = "gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155"


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def bounded_get(url: str, headers: dict | None, allowed_hosts: tuple) -> tuple[int | None, bytes | None, str | None]:
    """GET only, <=2 attempts, <=5s each. Never sends headers to a host not
    in allowed_hosts for THIS call. Returns (status, body_bytes, error)."""
    if not any(url.startswith(h) for h in allowed_hosts):
        return None, None, f"refused: host not allowlisted for this call: {url}"
    last_err = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read(), None
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, body, None  # explicit HTTP error status, not an exception path
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(1)
    return None, None, last_err


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS session_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tick_ts TEXT NOT NULL, status TEXT NOT NULL, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS observations (
            symbol TEXT NOT NULL, requested_feed TEXT NOT NULL, actual_feed TEXT,
            event_ts TEXT, receive_ts TEXT NOT NULL, age_seconds REAL,
            phase TEXT NOT NULL, source TEXT NOT NULL, http_status INTEGER, raw_json TEXT,
            PRIMARY KEY (symbol, receive_ts, source)
        );
        CREATE TABLE IF NOT EXISTS sip_checks (
            check_name TEXT PRIMARY KEY, checked_at TEXT NOT NULL,
            http_status INTEGER, sanitized_error TEXT, data_returned INTEGER
        );
        CREATE TABLE IF NOT EXISTS news_items (
            source_url TEXT NOT NULL, provider_id TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT, item_url TEXT, pub_ts TEXT, first_seen_ts TEXT NOT NULL,
            PRIMARY KEY (source_url, provider_id, content_hash)
        );
        """
    )
    conn.commit()


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM session_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn, key, value):
    conn.execute("INSERT INTO session_meta(key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()


def record_tick(conn, status, notes):
    conn.execute("INSERT INTO ticks(tick_ts, status, notes) VALUES (?,?,?)",
                 (datetime.now(timezone.utc).isoformat(), status, notes))
    conn.commit()
    log(f"{status}: {notes}")


def clock_synchronized() -> bool:
    try:
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return out == "yes"
    except Exception:
        return False


def dir_size_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def ensure_pilot_dir():
    PILOT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    PILOT_DIR.chmod(0o700)


def do_iex_snapshot(conn, headers, now):
    symbols = ",".join(UNIVERSE)
    url = f"https://data.alpaca.markets/v2/stocks/snapshots?symbols={symbols}&feed=iex"
    status, body, err = bounded_get(url, headers, ALPACA_HOSTS)
    receive_ts = now.isoformat()
    phase = "pre_market_iex_unavailable_expected" if now < IEX_EXPECTED_OPEN_UTC else "regular_session"
    if err:
        record_tick(conn, "IEX_FETCH_ERROR", f"{err} (phase={phase})")
        return
    if status != 200 or not body:
        conn.execute(
            "INSERT OR IGNORE INTO observations "
            "(symbol, requested_feed, actual_feed, event_ts, receive_ts, age_seconds, phase, source, http_status, raw_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("ALL", "iex", None, None, receive_ts, None, phase, "alpaca_snapshots", status,
             (body or b"")[:500].decode(errors="replace")),
        )
        conn.commit()
        return
    data = json.loads(body)
    for sym in UNIVERSE:
        snap = data.get(sym) or {}
        trade = snap.get("latestTrade") or {}
        event_ts = trade.get("t")
        age = None
        if event_ts:
            try:
                et = datetime.fromisoformat(event_ts.replace("Z", "+00:00"))
                age = (now - et).total_seconds()
            except Exception:
                age = None
        conn.execute(
            "INSERT OR IGNORE INTO observations "
            "(symbol, requested_feed, actual_feed, event_ts, receive_ts, age_seconds, phase, source, http_status, raw_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sym, "iex", "iex" if trade else None, event_ts, receive_ts, age, phase,
             "alpaca_snapshots", status, json.dumps(snap)[:2000]),
        )
    conn.commit()
    record_tick(conn, "IEX_SNAPSHOT_OK", f"{len(UNIVERSE)} symbols, phase={phase}")


def do_sip_checks_once(conn, headers, now):
    if get_meta(conn, "sip_recent_checked"):
        pass
    else:
        status, body, err = bounded_get(
            "https://data.alpaca.markets/v2/stocks/SPY/bars/latest?feed=sip", headers, ALPACA_HOSTS
        )
        conn.execute(
            "INSERT OR REPLACE INTO sip_checks VALUES (?,?,?,?,?)",
            ("recent_sip_denied_check", now.isoformat(), status,
             (err or (body or b"")[:300].decode(errors="replace")), 1 if status == 200 else 0),
        )
        conn.commit()
        set_meta(conn, "sip_recent_checked", "1")
        record_tick(conn, "SIP_RECENT_CHECK", f"status={status}")

    if get_meta(conn, "sip_delayed_checked"):
        return
    end = (now.replace(microsecond=0) - timedelta(minutes=20))
    url = (
        f"https://data.alpaca.markets/v2/stocks/SPY/bars"
        f"?timeframe=1Day&feed=sip&end={end.strftime('%Y-%m-%dT%H:%M:%SZ')}&limit=5"
    )
    status, body, err = bounded_get(url, headers, ALPACA_HOSTS)
    data_returned = 0
    if status == 200 and body:
        try:
            data_returned = 1 if json.loads(body).get("bars") else 0
        except Exception:
            data_returned = 0
    conn.execute(
        "INSERT OR REPLACE INTO sip_checks VALUES (?,?,?,?,?)",
        ("delayed_sip_16min_cutoff_check", now.isoformat(), status,
         (err or (body or b"")[:300].decode(errors="replace")), data_returned),
    )
    conn.commit()
    set_meta(conn, "sip_delayed_checked", "1")
    record_tick(conn, "SIP_DELAYED_CHECK", f"status={status} data_returned={bool(data_returned)}")


def parse_rss_items(xml_bytes: bytes, source_url: str):
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return items
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link or title).strip()
        pub = (item.findtext("pubDate") or "").strip()
        items.append({"title": title, "link": link, "guid": guid, "pub": pub})
    return items


def do_news_poll(conn, now):
    last = get_meta(conn, "last_news_poll_ts")
    if last:
        last_dt = datetime.fromisoformat(last)
        if (now - last_dt).total_seconds() < NEWS_POLL_INTERVAL_S:
            return
    total_new = 0
    for feed_url in NEWS_FEEDS:
        status, body, err = bounded_get(feed_url, None, ("https://www.eia.gov", "https://www.nass.usda.gov"))
        if err or status != 200 or not body:
            record_tick(conn, "NEWS_FETCH_ERROR", f"{feed_url}: status={status} err={err}")
            continue
        for it in parse_rss_items(body, feed_url):
            content_hash = hashlib.sha256((it["title"] + it["link"] + it["pub"]).encode()).hexdigest()
            cur = conn.execute(
                "INSERT OR IGNORE INTO news_items (source_url, provider_id, content_hash, title, item_url, pub_ts, first_seen_ts) "
                "VALUES (?,?,?,?,?,?,?)",
                (feed_url, it["guid"], content_hash, it["title"], it["link"],
                 it["pub"] or None, now.isoformat()),
            )
            total_new += cur.rowcount
    conn.commit()
    set_meta(conn, "last_news_poll_ts", now.isoformat())
    record_tick(conn, "NEWS_POLL_OK", f"{total_new} new items across {len(NEWS_FEEDS)} feeds")




# ---------------------------------------------------------------------
# Global pre-open brief -- cached across ticks, two idempotent editions
# per day (09:15 and 09:29 America/New_York), built from already-free/
# already-entitled sources only. Added per Rick's direct authorization
# 2026-10-01. Never touches order submission.
# ---------------------------------------------------------------------
GLOBAL_INSTRUMENTS = {
    "asia": [
        ("Nikkei 225", "^N225", "index"), ("Hang Seng", "^HSI", "index"),
        ("CSI 300 (mainland, Shanghai)", "000300.SS", "index"),
        ("KOSPI", "^KS11", "index"), ("S&P/ASX 200", "^AXJO", "index"),
    ],
    "europe": [
        ("FTSE 100", "^FTSE", "index"), ("DAX Performance Index", "^GDAXI", "index"),
        ("Euro Stoxx 50", "^STOXX50E", "index"),
    ],
    "us_futures": [
        ("S&P 500 futures (ES)", "ES=F", "future"), ("Nasdaq futures (NQ)", "NQ=F", "future"),
    ],
    "commodities_fx": [
        ("WTI Crude (CL)", "CL=F", "future"), ("Brent Crude (BZ)", "BZ=F", "future"),
        ("Gold (GC)", "GC=F", "future"),
        ("ICE US Dollar Index, spot/cash (DXY)", "DX-Y.NYB", "index"),
    ],
}
ALL_SYMBOLS = [(name, sym, kind, sec) for sec, items in GLOBAL_INSTRUMENTS.items() for name, sym, kind in items]
EDITION_LOCAL_TIMES = [(9, 15, "0915"), (9, 29, "0929")]  # America/New_York, hour, minute
REFRESH_LEAD_MINUTES = 25
YAHOO_HOST = "https://query1.finance.yahoo.com"


def init_global_brief_tables(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS global_obs_cache (
            symbol TEXT PRIMARY KEY, name TEXT, kind TEXT, section TEXT,
            fetched_at TEXT, http_status INTEGER, payload_json TEXT
        );
        CREATE TABLE IF NOT EXISTS brief_editions (
            edition_key TEXT PRIMARY KEY, generated_at TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        """
    )
    conn.commit()


def yahoo_meta_bounded(symbol):
    url = f"{YAHOO_HOST}/v8/finance/chart/{symbol}?range=5d&interval=1d"
    status, body, err = bounded_get(url, {"User-Agent": "Mozilla/5.0"}, (YAHOO_HOST,))
    if err or status != 200 or not body:
        return status, None, err
    try:
        meta = json.loads(body)["chart"]["result"][0]["meta"]
        return status, meta, None
    except Exception as e:
        return status, None, f"parse_error: {e}"


def label_session(meta, now):
    tzname = meta.get("exchangeTimezoneName")
    rt = meta.get("regularMarketTime")
    if not tzname or not rt:
        return "unavailable", None, None
    event_dt_utc = datetime.fromtimestamp(rt, tz=timezone.utc)
    local_dt = event_dt_utc.astimezone(ZoneInfo(tzname))
    today_local = now.astimezone(ZoneInfo(tzname)).date()
    age_days = (today_local - local_dt.date()).days
    if age_days == 0:
        return "fresh_close_or_live", event_dt_utc.isoformat(), local_dt.date().isoformat()
    # Softened per correction: do not assert "holiday" without an independently
    # verified exchange calendar -- this session has no verified calendar
    # source wired up. Report the fact (stale, N days old) without inventing a cause.
    return f"stale_{age_days}d_unverified_cause", event_dt_utc.isoformat(), local_dt.date().isoformat()


def refresh_one_stale_instrument(conn, now):
    """Round-robin: refresh whichever symbol has the oldest (or no) cache entry.
    At most one fetch per tick -- this is how the 45s budget stays safe even
    though there are 14 instruments to cover before each edition deadline."""
    existing = dict(conn.execute("SELECT symbol, fetched_at FROM global_obs_cache").fetchall())
    candidates = sorted(ALL_SYMBOLS, key=lambda t: existing.get(t[1], ""))
    name, sym, kind, section = candidates[0]
    status, meta, err = yahoo_meta_bounded(sym)
    if err or meta is None:
        payload = {"available": False, "error": err, "http_status": status}
    else:
        label, event_ts, event_local_date = label_session(meta, now)
        payload = {
            "available": True, "name": name, "symbol": sym, "kind": kind,
            "venue_timezone": meta.get("exchangeTimezoneName"), "currency": meta.get("currency"),
            "price": meta.get("regularMarketPrice"),
            "reference_prior_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "event_time_utc": event_ts, "event_local_date": event_local_date,
            "session_label": label,
            "known_delay": "Yahoo public chart API -- delay/real-time status not disclosed by the source; treat as indicative, not entitled real-time",
        }
    conn.execute(
        "INSERT INTO global_obs_cache (symbol, name, kind, section, fetched_at, http_status, payload_json) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
        "fetched_at=excluded.fetched_at, http_status=excluded.http_status, payload_json=excluded.payload_json",
        (sym, name, kind, section, now.isoformat(), status, json.dumps(payload)),
    )
    conn.commit()
    record_tick(conn, "GLOBAL_OBS_REFRESH", f"{sym} ({name})")


def next_edition_targets(now):
    ny_now = now.astimezone(ZoneInfo("America/New_York"))
    out = []
    for h, m, key in EDITION_LOCAL_TIMES:
        target_ny = ny_now.replace(hour=h, minute=m, second=0, microsecond=0)
        target_utc = target_ny.astimezone(timezone.utc)
        edition_key = f"{SESSION_DATE}_{key}"
        out.append((edition_key, target_utc))
    return out


def assemble_and_write_edition(conn, edition_key, now, headers):
    rows = conn.execute("SELECT symbol, payload_json FROM global_obs_cache").fetchall()
    by_symbol = {sym: json.loads(pj) for sym, pj in rows}
    sections = {}
    for sec, items in GLOBAL_INSTRUMENTS.items():
        sections[sec] = [by_symbol.get(sym, {"available": False, "name": name, "symbol": sym,
                                              "error": "not_yet_cached_this_edition"})
                          for name, sym, kind in items]

    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sip_status, sip_body, sip_err = bounded_get(
        f"https://data.alpaca.markets/v2/stocks/SPY/bars?timeframe=1Day&feed=sip&start=2026-09-24&end={end.strftime('%Y-%m-%dT%H:%M:%SZ')}&limit=10",
        headers, ALPACA_HOSTS,
    )
    sip_bars = []
    if sip_status == 200 and sip_body:
        try:
            sip_bars = json.loads(sip_body).get("bars", [])
        except Exception:
            pass

    news_rows = conn.execute(
        "SELECT title, source_url, item_url, pub_ts, first_seen_ts FROM news_items ORDER BY first_seen_ts DESC LIMIT 10"
    ).fetchall()
    news = [{"title": r[0], "source": r[1], "url": r[2], "pub_ts": r[3], "first_seen": r[4]} for r in news_rows]

    payload = {
        "edition_key": edition_key,
        "generated_at_utc": now.isoformat(),
        "sections": sections,
        "delayed_sip_completed_session": {
            "status": sip_status, "bars_returned": len(sip_bars),
            "sample": sip_bars[-1] if sip_bars else None,
        },
        "recent_news": news,
        "note": "Collection-time snapshot. Cache entries may be up to ~{} minutes old at edition time; each row carries its own event_time_utc/fetched_at for honest staleness.".format(REFRESH_LEAD_MINUTES),
    }

    if edition_key.endswith("_0929"):
        prev_key = edition_key.replace("_0929", "_0915")
        prev = conn.execute("SELECT payload_json FROM brief_editions WHERE edition_key=?", (prev_key,)).fetchone()
        if prev:
            prev_payload = json.loads(prev[0])
            prev_news_ids = {(n["title"], n["source"]) for n in prev_payload.get("recent_news", [])}
            payload["new_since_0915"] = [n for n in news if (n["title"], n["source"]) not in prev_news_ids]
            payload["note"] += " new_since_0915 lists headlines first seen after the 09:15 edition only."
        else:
            payload["new_since_0915"] = None
            payload["note"] += " 09:15 edition not found -- cannot diff."

    cur = conn.execute(
        "INSERT OR IGNORE INTO brief_editions (edition_key, generated_at, payload_json) VALUES (?,?,?)",
        (edition_key, now.isoformat(), json.dumps(payload, default=str)),
    )
    conn.commit()
    if cur.rowcount == 0:
        record_tick(conn, "EDITION_ALREADY_EXISTS", edition_key)
        return False

    out_json = PILOT_DIR / f"global_brief_{edition_key}.json"
    out_json.write_text(json.dumps(payload, indent=2, default=str))
    record_tick(conn, "EDITION_WRITTEN", f"{edition_key} -> {out_json.name}")
    return True


def do_global_brief_step(conn, headers, now):
    init_global_brief_tables(conn)
    targets = next_edition_targets(now)
    for edition_key, target_utc in targets:
        already = conn.execute("SELECT 1 FROM brief_editions WHERE edition_key=?", (edition_key,)).fetchone()
        if already:
            continue
        lead_start = target_utc - timedelta(minutes=REFRESH_LEAD_MINUTES)
        if lead_start <= now < target_utc:
            refresh_one_stale_instrument(conn, now)
            return  # one refresh per tick, budget-safe
        if now >= target_utc:
            assemble_and_write_edition(conn, edition_key, now, headers)
            return


def main():
    ensure_pilot_dir()
    lock_fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("SKIPPED: another tick holds the lock")
        return 0

    conn = sqlite3.connect(DB_PATH)
    DB_PATH.chmod(0o600)
    init_db(conn)

    now = datetime.now(timezone.utc)

    if not get_meta(conn, "pilot_id"):
        set_meta(conn, "pilot_id", PILOT_ID)
        set_meta(conn, "genome_id", GENOME_ID)
        set_meta(conn, "session_date", SESSION_DATE)
        set_meta(conn, "expiry_utc", EXPIRY_UTC.isoformat())
        set_meta(conn, "decision_recorded_2026-10-01", "session_01_manual_smoke_test")
        set_meta(conn, "status", "ACTIVE")
        record_tick(conn, "INIT", "pilot state initialized")

    stored_genome = get_meta(conn, "genome_id")
    if stored_genome != GENOME_ID:
        record_tick(conn, "HOLD_IDENTITY_MISMATCH", f"stored={stored_genome} expected={GENOME_ID}")
        set_meta(conn, "status", "HOLD")
        return 1

    assert submission_enabled() is False, "CRITICAL: submission_enabled() must be False"
    assert_trading_host_allowed(PAPER_TRADING_HOST)

    if now >= EXPIRY_UTC:
        if get_meta(conn, "status") != "EXPIRED":
            set_meta(conn, "status", "EXPIRED")
            record_tick(conn, "EXPIRED", "final flush only, no network requests")
        return 0

    ny_now = now.astimezone(ZoneInfo("America/New_York"))
    if ny_now.hour == 16 and ny_now.minute == 15:
        record_tick(conn, "FINAL_TICK_FLUSH", "16:15 NY reached -- flush only, no new network requests this tick")
        set_meta(conn, "status", "SESSION_COMPLETE_PENDING_CLEANUP")
        return 0

    if now < WINDOW_START_UTC:
        record_tick(conn, "HOLD_BEFORE_WINDOW", f"now={now.isoformat()} before window start")
        return 0

    if not clock_synchronized():
        record_tick(conn, "HOLD_CLOCK_NOT_SYNCED", "decision/collection held")
        return 0

    free = shutil.disk_usage("/").free
    if free < DISK_RESERVE_BYTES:
        record_tick(conn, "HOLD_DISK_RESERVE", f"free={free} < reserve={DISK_RESERVE_BYTES}")
        return 0

    used = dir_size_bytes(PILOT_DIR)
    if used >= OUTPUT_CAP_BYTES:
        record_tick(conn, "HOLD_OUTPUT_CAP", f"used={used} >= cap={OUTPUT_CAP_BYTES}")
        return 0

    headers = load_credential_headers()
    do_iex_snapshot(conn, headers, now)
    do_sip_checks_once(conn, headers, now)
    do_news_poll(conn, now)
    do_global_brief_step(conn, headers, now)

    conn.close()
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    return 0


if __name__ == "__main__":
    sys.exit(main())

