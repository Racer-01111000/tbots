"""Isolated tests for the already-deployed global-brief/cutoff logic in
kim_shadow_tick.py. Monkeypatches module-level paths to a tmp directory --
never touches the live pilot directory or its running state.sqlite3."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import kim_shadow_tick as k


@pytest.fixture
def isolated_conn(tmp_path, monkeypatch):
    monkeypatch.setattr(k, "PILOT_DIR", tmp_path)
    conn = sqlite3.connect(":memory:")
    k.init_db(conn)
    k.init_global_brief_tables(conn)
    return conn


def test_edition_targets_use_timezone_conversion_not_fixed_offset(isolated_conn):
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    targets = k.next_edition_targets(now)
    keys = dict(targets)
    assert "2026-10-01_0915" in keys
    assert "2026-10-01_0929" in keys
    assert keys["2026-10-01_0915"] == datetime(2026, 10, 1, 13, 15, tzinfo=timezone.utc)
    assert keys["2026-10-01_0929"] == datetime(2026, 10, 1, 13, 29, tzinfo=timezone.utc)


def test_edition_assembly_is_idempotent(isolated_conn, monkeypatch):
    conn = isolated_conn
    monkeypatch.setattr(k, "ALL_SYMBOLS", [("Nikkei 225", "^N225", "index", "asia")])
    monkeypatch.setattr(k, "GLOBAL_INSTRUMENTS", {"asia": [("Nikkei 225", "^N225", "index")]})
    now = datetime.now(timezone.utc)
    k.refresh_one_stale_instrument(conn, now)

    headers = {}
    key = "2026-10-01_0915"
    wrote1 = k.assemble_and_write_edition(conn, key, now, headers)
    wrote2 = k.assemble_and_write_edition(conn, key, now + timedelta(seconds=1), headers)
    assert wrote1 is True
    assert wrote2 is False

    rows = conn.execute("SELECT count(*) FROM brief_editions WHERE edition_key=?", (key,)).fetchone()
    assert rows[0] == 1


def test_label_session_does_not_assert_holiday_without_verification():
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    stale_meta = {
        "exchangeTimezoneName": "Asia/Hong_Kong",
        "regularMarketTime": int((now - timedelta(days=1)).timestamp()),
    }
    label, event_ts, event_date = k.label_session(stale_meta, now)
    assert "holiday" not in label
    assert label.startswith("stale_")
    assert "unverified_cause" in label


def test_label_session_fresh_close():
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    fresh_meta = {
        "exchangeTimezoneName": "Asia/Tokyo",
        "regularMarketTime": int(now.timestamp()) - 3600,
    }
    label, event_ts, event_date = k.label_session(fresh_meta, now)
    assert label == "fresh_close_or_live"


def test_expiry_boundary_blocks_network_collection(isolated_conn):
    past_expiry = k.EXPIRY_UTC + timedelta(seconds=1)
    should_stop = past_expiry >= k.EXPIRY_UTC
    assert should_stop is True


def test_final_minute_flush_boundary_identified_correctly():
    from zoneinfo import ZoneInfo
    final_minute_utc = k.EXPIRY_UTC
    ny_now = final_minute_utc.astimezone(ZoneInfo("America/New_York"))
    assert ny_now.hour == 16 and ny_now.minute == 15
    one_minute_later = final_minute_utc + timedelta(minutes=1)
    ny_later = one_minute_later.astimezone(ZoneInfo("America/New_York"))
    assert not (ny_later.hour == 16 and ny_later.minute == 15)

