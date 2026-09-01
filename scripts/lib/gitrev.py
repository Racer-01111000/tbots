"""Capture the exact code revision an experiment ran under."""
import subprocess


def get_code_revision(repo_dir: str) -> tuple[str, bool]:
    """Returns (sha, dirty). dirty=True means the working tree had
    uncommitted changes, which breaks the "same code revision reproduces
    the same result" guarantee even if the sha matches."""
    sha = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", repo_dir, "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    return sha, bool(status.strip())
