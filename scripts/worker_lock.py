"""Single-writer advisory lock (GO Addendum A: 'single-writer flock
/opt/evolutionary-markets/.tbots.lock')."""
import contextlib
import fcntl
import os


class LockHeldElsewhere(Exception):
    pass


@contextlib.contextmanager
def worker_lock(path):
    """Non-blocking exclusive flock. Raises LockHeldElsewhere immediately
    rather than waiting -- a second worker instance must never queue up
    behind the first, only refuse to start."""
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockHeldElsewhere(f"another process already holds {path}")
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        os.fsync(fd)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
