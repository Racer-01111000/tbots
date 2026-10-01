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

    conn.close()
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    return 0


if __name__ == "__main__":
    sys.exit(main())

