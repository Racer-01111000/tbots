import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from worker_lock import LockHeldElsewhere, worker_lock


class WorkerLock(unittest.TestCase):
    def test_creates_the_lock_file_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".tbots.lock"
            self.assertFalse(path.exists())
            with worker_lock(path):
                self.assertTrue(path.exists())

    def test_writes_own_pid_into_the_lock_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".tbots.lock"
            with worker_lock(path):
                self.assertEqual(path.read_text().strip(), str(os.getpid()))

    def test_second_acquire_while_held_raises_lock_held_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".tbots.lock"
            with worker_lock(path):
                with self.assertRaises(LockHeldElsewhere):
                    with worker_lock(path):
                        pass  # pragma: no cover

    def test_lock_is_released_after_context_exits_and_can_be_reacquired(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".tbots.lock"
            with worker_lock(path):
                pass
            with worker_lock(path):
                self.assertEqual(path.read_text().strip(), str(os.getpid()))

    def test_lock_is_released_even_if_the_body_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".tbots.lock"
            with self.assertRaises(RuntimeError):
                with worker_lock(path):
                    raise RuntimeError("simulated failure inside the held lock")
            with worker_lock(path):
                pass  # must not raise LockHeldElsewhere -- proves release happened


if __name__ == "__main__":
    unittest.main()
