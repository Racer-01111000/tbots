# Regression coverage for end_to_end_dry_run.py's scenarios -- reuses the
# exact same functions the standalone dry-run script runs, so this is not a
# parallel reimplementation.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import end_to_end_dry_run as e2e


def test_scenario_normal_full_cycle(tmp_path):
    result = e2e.scenario_normal_full_cycle(tmp_path)
    assert result["submitted"] == 3
    assert result["blocked"] == 0


def test_scenario_crash_mid_send_and_restart(tmp_path):
    result = e2e.scenario_crash_mid_send_and_restart(tmp_path)
    assert result["recovered_from_broker_record"] == "reconciled_from_prior_crash"
    assert result["submitted_fresh_on_restart"] == "filled"
    assert result["no_duplicate_order_created"] is True


def test_scenario_first_submission_session_delta(tmp_path):
    result = e2e.scenario_first_submission_session_delta(tmp_path)
    assert result["seeded_from_broker_state"] == 1
    assert result["computed_delta"] == {"DBC": 2, "SPY": 10}
    assert result["delta_orders_submitted"] == 2


def test_run_full_dry_run_produces_all_three_scenarios(tmp_path):
    results = e2e.run_full_dry_run(work_dir=tmp_path)
    names = {s["scenario"] for s in results["scenarios"]}
    assert names == {"normal_full_cycle", "crash_mid_send_and_restart", "first_submission_session_delta"}
    assert all(s.get("blocked", 0) == 0 for s in results["scenarios"] if "blocked" in s)
