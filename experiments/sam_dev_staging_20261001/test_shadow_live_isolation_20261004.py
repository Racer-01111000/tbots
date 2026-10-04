"""The shadow comparison must never consume a paper-execution opportunity:
checked structurally (source), by request log (no account/positions/orders/POST),
by file isolation (pilot state byte-identical), and by interleaving with the
real pilot runner in both orders."""
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
for sub in ("", "alpaca_adapter", "kim_order_simulation_tests"):
    sys.path.insert(0, str(HERE / sub))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import kim_once_per_session_v2 as k
import kim_shadow_compare as ksc
import shadow_strategies
from alpaca_paper_broker import AlpacaPaperBroker
from fake_alpaca import FakeAlpaca

PILOT_FILES = ("cadence_state.json", "halt_state.json", "peak_equity_state.json", "order_ledger.sqlite3",
               "KILL_SWITCH", "pilot_config.json")
FORBIDDEN_IN_SHADOW_SOURCE = ("cadence_state", "halt_state", "peak_equity_state", "import peak_equity", "order_ledger", "KILL_SWITCH",
                              "pilot_config", "AlpacaPaperBroker", "alpaca_paper_broker", "import alpaca_adapter", "from alpaca_adapter", "submit", "/v2/orders",
                              "/v2/account", "/v2/positions", "kim_order_logic", "method=\"POST\"", "\"POST\"")
SHADOW_SOURCES = ("shadow_compare.py", "kim_shadow_compare.py", "lineage_d.py", "shadow_strategies.py")

CFG = {
    "genome_id": "x", "max_orders_per_session": 8, "max_liquidation_orders_per_session": 8, "drawdown_halt_pct": 0.08, "max_asset_weight": 0.18,
    "gross_exposure_ceiling": 0.18, "min_cash_reserve_pct": 0.10, "time_in_force": "day",
    "kill_switch_file": "KILL_SWITCH", "expiry_date": "2026-11-01", "session_zero_date": "2026-10-05",
    "rebalance_every_n_sessions": 50,
}
GENOME = json.loads((HERE / "selected_development_bot.json").read_text())["selected_genome_definition"]


def cdeps(fake, tmp, at=(16, 45)):
    return ksc.CompareDeps(get_json=fake.get_json, headers={}, now_utc=lambda: fake.now_utc(at),
                           state_dir=tmp / "shadow_compare", strategies=shadow_strategies.both())


def pdeps(fake, tmp, gate):
    broker = AlpacaPaperBroker({"k": "v"}, http=fake.http, submission_gate=lambda: gate)
    return k.Deps(get_json=fake.get_json, headers={}, broker=broker, now_utc=lambda: fake.now_utc(),
                  submission_gate=lambda: gate, state_dir=tmp, genome=GENOME, genome_id="x", config=CFG)


def digest(tmp):
    return {n: hashlib.sha256((tmp / n).read_bytes()).hexdigest() for n in PILOT_FILES if (tmp / n).exists()}


def test_shadow_sources_do_not_reference_pilot_state_or_any_order_path():
    for name in SHADOW_SOURCES:
        text = (HERE / name).read_text()
        code = re.sub(r'""".*?"""', "", text, flags=re.S)           # ignore the docstrings that describe the isolation
        code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("#"))
        for bad in FORBIDDEN_IN_SHADOW_SOURCE:
            assert bad not in code, f"{name} references {bad!r}"


def test_first_run_is_session_zero_records_both_strategies_and_makes_only_market_data_calls(tmp_path):
    fake = FakeAlpaca()
    rec = ksc.run_compare(cdeps(fake, tmp_path))
    assert rec["status"] == "processed" and rec["processed_dates"] == ["2026-10-05"]
    assert fake.post_log == []
    assert set(fake.get_log) <= {"/v2/calendar"} | {f"/v2/stocks/{s}/bars" for s in
                                                    ("SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ")}
    latest = rec["latest"]
    assert latest["books"]["champion"]["step"] == 0 and latest["books"]["lineage_d"]["step"] == 0
    assert latest["books"]["champion"]["new_target"] and latest["books"]["lineage_d"]["new_target"]
    assert latest["comparison"]["l1_distance"] >= 0
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert files == ["shadow_compare/champion/state.json", "shadow_compare/lineage_d/state.json", "shadow_compare/sessions.jsonl"]


def test_pilot_state_is_untouched_and_pilot_still_gets_its_entry_after_shadow_runs(tmp_path):
    fake = FakeAlpaca()
    pdir = tmp_path / "pilot"
    pdir.mkdir()
    (pdir / "cadence_state.json").write_text(json.dumps({"session_zero_date": "2026-10-05", "last_rebalanced_step": None}))
    (pdir / "KILL_SWITCH_ABSENT").write_text("n/a")
    before = digest(pdir)
    ksc.run_compare(cdeps(fake, tmp_path))
    assert digest(pdir) == before
    rec = k.run_session(pdeps(fake, pdir, gate=True))      # same session, gate open, AFTER the shadow ran
    assert rec["status"] == "decided" and fake.post_log, "shadow run consumed the pilot's entry"


def test_pilot_run_does_not_disturb_the_shadow_books_either(tmp_path):
    fake = FakeAlpaca()
    k.run_session(pdeps(fake, tmp_path / "pilot", gate=True))
    s_before = None
    rec = ksc.run_compare(cdeps(fake, tmp_path))
    assert rec["status"] == "processed" and rec["latest"]["books"]["champion"]["step"] == 0


def test_shadow_catches_up_missed_sessions_in_order_and_is_idempotent(tmp_path):
    fake = FakeAlpaca()
    ksc.run_compare(cdeps(fake, tmp_path))
    fake.today = fake.sessions[4]                          # four sessions missed
    rec = ksc.run_compare(cdeps(fake, tmp_path))
    assert rec["processed_dates"] == fake.sessions[1:5]
    again = ksc.run_compare(cdeps(fake, tmp_path))
    assert again["status"] == "nothing_to_do"
    lines = [json.loads(l) for l in (tmp_path / "shadow_compare" / "sessions.jsonl").read_text().splitlines()]
    assert [l["date"] for l in lines] == fake.sessions[:5]
    assert [l["books"]["lineage_d"]["step"] for l in lines] == [0, 1, 2, 3, 4]


def test_holiday_too_early_and_calendar_failure_change_nothing(tmp_path):
    fake = FakeAlpaca()
    assert ksc.run_compare(cdeps(fake, tmp_path, at=(9, 5)))["status"] in ("nothing_to_do", "non_trading_day")
    fake.fail["calendar"] = 503
    assert ksc.run_compare(cdeps(fake, tmp_path))["status"] == "abstained_calendar_unavailable"
    del fake.fail["calendar"]
    fake.sessions = [s for s in fake.sessions if s != fake.today]
    assert ksc.run_compare(cdeps(fake, tmp_path))["status"] == "non_trading_day"
    assert not (tmp_path / "shadow_compare").exists()


def test_incomplete_bars_stop_the_books_and_escalate_instead_of_skipping_a_session(tmp_path):
    fake = FakeAlpaca()
    ksc.run_compare(cdeps(fake, tmp_path))
    fake.today = fake.sessions[2]
    fake.missing_bars = {"GLD"}
    rec = ksc.run_compare(cdeps(fake, tmp_path))
    assert rec["status"] in ("abstained_data_unavailable", "abstained_data_not_ready") and rec.get("escalate")
    lines = (tmp_path / "shadow_compare" / "sessions.jsonl").read_text().splitlines()
    assert len(lines) == 1                                  # books did not advance past the gap
