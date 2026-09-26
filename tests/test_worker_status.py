import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from worker_status import StatusValidationError, build_status, read_status, with_updates, write_status


def _base(**overrides):
    fields = dict(
        schema="tbots-status-v1", updated_utc="2026-09-26T06:29:05Z",
        head_commit_parent="bb7cbf9e1e8969426a6f78bdc1022ada4eabea12",
        state="audit_window", phase="freeze_audit_window",
        protocol_manifest="fitness_v2_complete_protocol_49c71da1",
        freeze_commit="1c3b6b013c4a47dbaf74a8a9ef5cda09f43afcd2",
        audit_window_ends_utc="2026-09-26T12:29:05Z",
        world_bank_id=None, synthetic_world_count=0, campaign=None,
        campaign_seed=None, generation=None, campaigns_complete=0,
        rank1_qualifiers=0, research_champion=None, negative_result=False,
        last_progress_utc="2026-09-26T06:29:05Z", stop_code=None, stop_reason="",
        recoveries=0, last_generation_seconds=None,
        disk_used_bytes=15712251904, disk_free_bytes=213484064768,
    )
    fields.update(overrides)
    return fields


class BuildStatus(unittest.TestCase):
    def test_accepts_the_full_valid_field_set(self):
        status = build_status(**_base())
        self.assertEqual(status["schema"], "tbots-status-v1")

    def test_rejects_missing_field(self):
        fields = _base()
        del fields["disk_free_bytes"]
        with self.assertRaises(StatusValidationError):
            build_status(**fields)

    def test_rejects_unknown_field(self):
        with self.assertRaises(StatusValidationError):
            build_status(**_base(), bogus_field=1)

    def test_rejects_wrong_schema(self):
        with self.assertRaises(StatusValidationError):
            build_status(**_base(schema="tbots-status-v2"))

    def test_rejects_unknown_state(self):
        with self.assertRaises(StatusValidationError):
            build_status(**_base(state="mid_generation_lunch_break"))

    def test_rejects_unknown_stop_code(self):
        with self.assertRaises(StatusValidationError):
            build_status(**_base(state="stopped", stop_code="Z"))

    def test_stopped_state_requires_stop_code(self):
        with self.assertRaises(StatusValidationError):
            build_status(**_base(state="stopped", stop_code=None))

    def test_stop_code_requires_stopped_state(self):
        with self.assertRaises(StatusValidationError):
            build_status(**_base(state="audit_window", stop_code="H"))

    def test_accepts_every_documented_stop_code(self):
        for code in ("A", "B", "C", "D", "E", "F", "G", "H", "R"):
            build_status(**_base(state="stopped", stop_code=code))

    def test_accepts_every_documented_state(self):
        # Addendum A section 4's exact enum: "running | audit_window |
        # stopped | hold | complete". "stopped" needs a stop_code; the
        # rest must not have one.
        for state in ("running", "audit_window", "hold", "complete"):
            build_status(**_base(state=state, stop_code=None))
        build_status(**_base(state="stopped", stop_code="A"))

    def test_accepts_every_documented_phase(self):
        for phase in (
            "protocol_freeze", "freeze_audit_window", "world_bank", "baseline",
            "campaign", "admission", "champion_decision", "expansion", "terminal_hold",
        ):
            build_status(**_base(phase=phase))

    def test_rejects_unknown_phase(self):
        with self.assertRaises(StatusValidationError):
            build_status(**_base(phase="lunch_break"))

    def test_state_and_phase_are_separate_axes_not_interchangeable(self):
        # An earlier version of worker_status.py conflated the two --
        # phase-only values (e.g. "world_bank") must never validate as a
        # state, and state-only values (e.g. "hold") must never validate
        # as a phase. Fixed 2026-09-26 during the Addendum A reconciliation.
        phase_only_values = (
            "world_bank", "baseline", "campaign", "admission",
            "champion_decision", "expansion", "protocol_freeze",
        )
        for bad_state in phase_only_values:
            with self.assertRaises(StatusValidationError):
                build_status(**_base(state=bad_state, stop_code=None))
        state_only_values = ("running", "hold", "complete")
        for bad_phase in state_only_values:
            with self.assertRaises(StatusValidationError):
                build_status(**_base(phase=bad_phase))

    def test_hold_state_does_not_require_a_stop_code(self):
        status = build_status(**_base(state="hold", phase="terminal_hold", stop_code=None))
        self.assertEqual(status["state"], "hold")
        self.assertIsNone(status["stop_code"])


class ReadWriteStatus(unittest.TestCase):
    def test_round_trips_through_disk(self, tmp_path=None):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "STATUS.json"
            write_status(path, _base())
            reloaded = read_status(path)
            self.assertEqual(reloaded, build_status(**_base()))

    def test_write_is_atomic_no_tmp_file_left_behind(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "STATUS.json"
            write_status(path, _base())
            self.assertFalse((path.with_name(path.name + ".tmp")).exists())

    def test_read_rejects_malformed_json(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "STATUS.json"
            path.write_text("{not json")
            with self.assertRaises(StatusValidationError):
                read_status(path)

    def test_read_rejects_a_json_array(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "STATUS.json"
            path.write_text("[]")
            with self.assertRaises(StatusValidationError):
                read_status(path)


class WithUpdates(unittest.TestCase):
    def test_applies_a_partial_update_and_revalidates(self):
        status = build_status(**_base())
        updated = with_updates(status, generation=3, campaign=1, campaign_seed=42)
        self.assertEqual(updated["generation"], 3)
        self.assertEqual(status["generation"], None)

    def test_rejects_an_update_that_breaks_a_cross_field_rule(self):
        status = build_status(**_base())
        with self.assertRaises(StatusValidationError):
            with_updates(status, stop_code="H")


if __name__ == "__main__":
    unittest.main()
