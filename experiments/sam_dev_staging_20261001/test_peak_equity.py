"""Regression tests for peak_equity.py -- pure logic, no network, no broker."""
import pytest

from peak_equity import load_state, record_and_get_drawdown


def test_first_recording_has_zero_drawdown(tmp_path):
    path = tmp_path / "peak_equity_state.json"
    peak, drawdown = record_and_get_drawdown(path, "2026-10-01", 100_000.0)
    assert peak == 100_000.0
    assert drawdown == 0.0


def test_new_peak_resets_drawdown_to_zero(tmp_path):
    path = tmp_path / "peak_equity_state.json"
    record_and_get_drawdown(path, "2026-10-01", 100_000.0)
    peak, drawdown = record_and_get_drawdown(path, "2026-10-02", 110_000.0)
    assert peak == 110_000.0
    assert drawdown == 0.0


def test_drawdown_computed_when_equity_drops_below_peak(tmp_path):
    path = tmp_path / "peak_equity_state.json"
    record_and_get_drawdown(path, "2026-10-01", 100_000.0)
    peak, drawdown = record_and_get_drawdown(path, "2026-10-02", 90_000.0)
    assert peak == 100_000.0
    assert drawdown == pytest.approx(0.10)


def test_peak_persists_across_a_later_lower_session(tmp_path):
    path = tmp_path / "peak_equity_state.json"
    record_and_get_drawdown(path, "2026-10-01", 100_000.0)
    record_and_get_drawdown(path, "2026-10-02", 90_000.0)
    peak, drawdown = record_and_get_drawdown(path, "2026-10-03", 95_000.0)
    assert peak == 100_000.0  # still the historical high, not today's 95,000
    assert drawdown == pytest.approx(0.05)


def test_idempotent_restart_same_session_date_same_equity(tmp_path):
    path = tmp_path / "peak_equity_state.json"
    record_and_get_drawdown(path, "2026-10-01", 100_000.0)
    first_peak, first_drawdown = record_and_get_drawdown(path, "2026-10-02", 90_000.0)
    # Simulate a restart mid-session: the job re-runs for the SAME session
    # date with the SAME equity figure. Must be a true no-op, not a second
    # history entry or a shifted peak.
    second_peak, second_drawdown = record_and_get_drawdown(path, "2026-10-02", 90_000.0)
    assert second_peak == first_peak
    assert second_drawdown == first_drawdown

    state = load_state(path)
    assert len(state["history"]) == 2  # 2026-10-01 and 2026-10-02 only -- no duplicate entry


def test_restart_same_session_date_different_equity_overwrites_not_doubles(tmp_path):
    # A restart mid-session where a fresh broker read gives a different
    # equity figure for the SAME session date (e.g. a price tick moved
    # equity between two reads). Must overwrite that date's entry, not
    # create a second one -- corruption/double-counting would mean the
    # peak/drawdown math silently uses stale data forever after.
    path = tmp_path / "peak_equity_state.json"
    record_and_get_drawdown(path, "2026-10-01", 100_000.0)
    record_and_get_drawdown(path, "2026-10-01", 101_000.0)

    state = load_state(path)
    assert len(state["history"]) == 1
    assert state["history"]["2026-10-01"] == 101_000.0
    assert state["peak_equity"] == 101_000.0
