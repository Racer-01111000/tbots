import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import worker_disk
from worker_disk import MIN_FREE_BYTES, disk_gate, disk_snapshot


class _Usage:
    def __init__(self, total, used, free):
        self.total, self.used, self.free = total, used, free


class DiskSnapshot(unittest.TestCase):
    def test_reports_used_and_free_bytes(self):
        with patch.object(worker_disk.shutil, "disk_usage",
                          return_value=_Usage(100, 60, 40)):
            snapshot = disk_snapshot("/anything")
        self.assertEqual(snapshot, {"disk_used_bytes": 60, "disk_free_bytes": 40})


class DiskGate(unittest.TestCase):
    def test_ok_when_free_space_is_comfortably_above_the_minimum(self):
        with patch.object(worker_disk.shutil, "disk_usage",
                          return_value=_Usage(1, 1, MIN_FREE_BYTES * 10)):
            result = disk_gate("/anything")
        self.assertEqual(result["outcome"], "ok")
        self.assertIsNone(result["stop_code"])

    def test_stops_with_code_r_when_below_the_minimum(self):
        with patch.object(worker_disk.shutil, "disk_usage",
                          return_value=_Usage(1, 1, MIN_FREE_BYTES - 1)):
            result = disk_gate("/anything")
        self.assertEqual(result["outcome"], "stopped")
        self.assertEqual(result["stop_code"], "R")

    def test_exactly_at_the_minimum_is_still_ok(self):
        with patch.object(worker_disk.shutil, "disk_usage",
                          return_value=_Usage(1, 1, MIN_FREE_BYTES)):
            result = disk_gate("/anything")
        self.assertEqual(result["outcome"], "ok")

    def test_gate_never_calls_anything_that_deletes(self):
        # disk_gate must only ever read usage -- assert the module exposes no
        # delete/remove helpers a future edit could wire in by accident.
        self.assertFalse(hasattr(worker_disk, "free_up_space"))
        self.assertFalse(hasattr(worker_disk, "delete_oldest"))


if __name__ == "__main__":
    unittest.main()
