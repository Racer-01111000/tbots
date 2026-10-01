"""TBOTS read-only shadow pilot -- ONE session (one invocation = one
observation session; run manually once per trading day, 5 sessions total
per the GO). Fetches real paper-account/live-market state via the private
runtime loader (GET-only, allowlisted hosts) and computes what the staged
development bot (the accepted champion genome) would decide RIGHT NOW,
through the real, unmodified control_agent.decide() pathway. Logs the
observation. Never imports/calls route_order(), never sets
TBOTS_ALPACA_SUBMISSION_ENABLED, never constructs a POST request anywhere
in this file -- there is no code path to order submission here at all.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path("/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots")
DEV_STAGING = REPO / "experiments/sam_dev_staging_20261001"
sys.path.insert(0, str(DEV_STAGING / "alpaca_adapter"))
sys.path.insert(0, str(REPO / "scripts"))

from runtime_credential_loader import load_credential_headers, get_json  # noqa: E402
from alpaca_adapter import (  # noqa: E402
    assert_trading_host_allowed, submission_enabled, MarketClock, require_fresh_clock,
    PAPER_TRADING_HOST,
)
import control_agent  # noqa: E402

SESSIONS_DIR = DEV_STAGING / "shadow_pilot_sessions"
UNIVERSE = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
LOOKBACK_CALENDAR_DAYS = 600  # buffer well past the champion's longest lookback (295 trading days)


class LiveView:
    """Minimal AgentView-compatible object (observe()/history()) backed by
    real fetched IEX daily bars. Only the two fields control_agent/indicators
    actually read (adjusted_close, per source grep) are populated."""

    def __init__(self, bars_by_symbol: dict):
        self._bars = bars_by_symbol  # {symbol: [{"timestamp":..., "adjusted_close": float}, ...]} ascending

    def observe(self):
        assets = {s: {"adjusted_close": self._bars[s][-1]["adjusted_close"], "available": True} for s in self._bars}
        ts = self._bars[UNIVERSE[0]][-1]["timestamp"]
        return {"assets": assets, "timestamp": ts}

    def history(self, symbol, bars):
        rows = self._bars[symbol]
        return rows[-bars:] if bars > 0 else []


def fetch_live_bars(headers: dict) -> dict:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    out = {}
    for symbol in UNIVERSE:
        url = (
            f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
            f"?timeframe=1Day&start={start.isoformat()}&end={end.isoformat()}"
            f"&adjustment=all&feed=iex&limit=10000"
        )
        status, body = get_json(url, headers)
        if status != 200:
            raise RuntimeError(f"bars fetch failed for {symbol}: HTTP {status} {body}")
        bars = body.get("bars", [])
        out[symbol] = [{"timestamp": b["t"][:10], "adjusted_close": float(b["c"])} for b in bars]
    return out


def main():
    t0 = time.time()
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    assert submission_enabled() is False, "REFUSING: submission_enabled() must be False"
    assert_trading_host_allowed(PAPER_TRADING_HOST)

    headers = load_credential_headers()

    acct_status, acct = get_json("https://paper-api.alpaca.markets/v2/account", headers)
    clock_status, clock_raw = get_json("https://paper-api.alpaca.markets/v2/clock", headers)
    clock = MarketClock.from_api(clock_raw)
    require_fresh_clock(clock)

    bars = fetch_live_bars(headers)
    view = LiveView(bars)

    sel = json.loads((DEV_STAGING / "selected_development_bot.json").read_text())
    genome = sel["selected_genome_definition"]

    decision = control_agent.decide(view, genome)

    existing = sorted(SESSIONS_DIR.glob("session_*.json"))
    session_index = len(existing) + 1

    record = {
        "go": "TBOTS_SAM_15_MONTH_REPLAY_AND_LIVE_SHADOW_20261001 -- five-session read-only shadow pilot",
        "session_index": session_index,
        "session_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "orders_disabled_confirmed": submission_enabled() is False,
        "order_submission_attempted": False,
        "host_used": PAPER_TRADING_HOST,
        "account_http_status": acct_status,
        "account_status_field": acct.get("status") if acct else None,
        "account_trading_blocked": acct.get("trading_blocked") if acct else None,
        "market_clock_is_open": clock.is_open,
        "market_clock_timestamp": clock.timestamp.isoformat(),
        "market_clock_next_open": clock.next_open.isoformat(),
        "market_clock_next_close": clock.next_close.isoformat(),
        "feed_used": "iex",
        "universe": UNIVERSE,
        "bars_per_symbol": {s: len(v) for s, v in bars.items()},
        "selected_genome_id_check": sel.get("ranking_result_summary", {}).get("winner_genome_id"),
        "decision": decision,
        "wall_time_s": round(time.time() - t0, 2),
    }
    out_path = SESSIONS_DIR / f"session_{session_index:02d}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    out_path.write_text(json.dumps(record, indent=2, default=str))
    print(f"Session {session_index} written to {out_path} ({out_path.stat().st_size} bytes)")
    print(f"Account: {record['account_status_field']}, trading_blocked={record['account_trading_blocked']}")
    print(f"Market open: {record['market_clock_is_open']}")
    print(f"Decision: selected={decision['selected']} weights={decision['weights']}")
    print(f"Orders disabled confirmed: {record['orders_disabled_confirmed']}, submission attempted: {record['order_submission_attempted']}")


if __name__ == "__main__":
    main()

