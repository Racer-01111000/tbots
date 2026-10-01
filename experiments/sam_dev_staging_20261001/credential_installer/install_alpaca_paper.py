#!/opt/python-3.13.5-isolated/bin/python3.13
"""Private Alpaca PAPER credential installer -- written from the spec in
Rick GO -- TBOTS_DATA_REPAIR_BEST_BOT_AND_PAPER_KEYS_20261001, section 6.

Run this yourself, interactively, as ec2-user, in your own AWS SSM Session
Manager terminal. It is never executed by the automated session that wrote
it -- there is no stdin channel for a secret to reach this script through
that session's tool calls.

What it does, exactly:
  1. Prompts for the key id and secret with hidden input (getpass). Refuses
     to run at all if the terminal cannot suppress echo -- it will not fall
     back to visible input.
  2. Makes exactly one HTTPS GET to https://paper-api.alpaca.markets/v2/account
     to validate the credentials. Follows no redirects (so a compromised or
     misconfigured DNS/proxy can't silently point this at a different host).
     Does not print the response body or any account identifier -- only
     ACTIVE / NOT ACTIVE / FAILED.
  3. Refuses to store anything unless the account's `status` field is
     exactly "ACTIVE".
  4. Writes /home/ec2-user/.config/tbots/alpaca-paper.json atomically
     (write to a temp file in the same directory, fsync, rename), with the
     parent directory at mode 0700 and the file at mode 0600, owned by the
     invoking user. Refuses to overwrite an existing file (if one is
     already there, it stops and tells you to remove it yourself first --
     it never silently replaces a credential file). Refuses to write
     through a symlink at any point in the path.
  5. Never writes the secret to argv, environment, a log file, or stdout/
     stderr at any point. The only values printed are static status words.

This script does not grant trading permission by itself. The adapter
(alpaca_adapter.py) enforces paper-host-only + both-switches-disabled
independently of anything in the stored file -- a flag in the credential
file claiming "read-only" is not trusted or even read for that purpose.
"""
from __future__ import annotations

import getpass
import json
import os
import stat
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_DIR = Path("/home/ec2-user/.config/tbots")
CONFIG_FILE = CONFIG_DIR / "alpaca-paper.json"
VALIDATE_URL = "https://paper-api.alpaca.markets/v2/account"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # refuse every redirect, 3xx is treated as failure


def require_hidden_input(prompt: str) -> str:
    if not sys.stdin.isatty():
        print("FAILED: no interactive TTY on stdin -- refusing to read a secret "
              "from a non-interactive stream.", file=sys.stderr)
        sys.exit(1)
    try:
        value = getpass.getpass(prompt)
    except Exception as exc:  # getpass itself falls back to echo on failure
        print(f"FAILED: terminal cannot suppress echo ({exc!r}) -- refusing to "
              "prompt visibly for a secret.", file=sys.stderr)
        sys.exit(1)
    if not value:
        print("FAILED: empty input.", file=sys.stderr)
        sys.exit(1)
    return value


def validate_account(key_id: str, secret: str) -> bool:
    req = urllib.request.Request(
        VALIDATE_URL,
        headers={
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret,
            "User-Agent": "tbots-paper-installer/1",
        },
    )
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=15) as resp:
            if resp.status != 200:
                print(f"FAILED: unexpected HTTP status {resp.status}")
                return False
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"FAILED: HTTP {exc.code} -- credentials rejected or endpoint error")
        return False
    except urllib.error.URLError as exc:
        print(f"FAILED: connection error ({exc.reason})")
        return False

    status = body.get("status")
    if status != "ACTIVE":
        print(f"FAILED: account status is not ACTIVE (status reported as a "
              f"non-identifying category only, account not stored)")
        return False
    print("ACTIVE: paper account validated. No identifiers printed.")
    return True


def store_credentials(key_id: str, secret: str) -> None:
    if CONFIG_FILE.exists() or CONFIG_FILE.is_symlink():
        print(f"FAILED: {CONFIG_FILE} already exists -- refusing to overwrite. "
              "Remove it yourself first if you intend to replace it.", file=sys.stderr)
        sys.exit(1)

    for part in (CONFIG_DIR.parent, CONFIG_DIR):
        if part.exists() and part.is_symlink():
            print(f"FAILED: {part} is a symlink -- refusing to write through it.",
                  file=sys.stderr)
            sys.exit(1)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(CONFIG_DIR, 0o700)

    payload = {
        "key_id": key_id,
        "secret": secret,
        "trading_host": "https://paper-api.alpaca.markets",
        "data_host": "https://data.alpaca.markets",
        "note": "Enforcement of read-only / paper-only behavior lives in the "
                "adapter code, independently of this file -- nothing here is "
                "trusted as a permissions claim.",
    }

    fd, tmp_path = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".alpaca-paper-", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(CONFIG_FILE):  # race check immediately before the atomic rename
            print(f"FAILED: {CONFIG_FILE} appeared during write -- aborting, not overwriting.",
                  file=sys.stderr)
            os.unlink(tmp_path)
            sys.exit(1)
        os.rename(tmp_path, CONFIG_FILE)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    final_mode = stat.S_IMODE(os.stat(CONFIG_FILE).st_mode)
    assert final_mode == 0o600, f"unexpected file mode {oct(final_mode)}"
    print(f"Stored: {CONFIG_FILE} (mode 0600, dir 0700). Secret values not printed.")


def main() -> None:
    print("Alpaca PAPER credential installer -- hidden input, one validation "
          "GET, atomic no-overwrite write. Ctrl+C to abort at any prompt.")
    key_id = require_hidden_input("Alpaca PAPER key id: ")
    secret = require_hidden_input("Alpaca PAPER secret key: ")

    if not validate_account(key_id, secret):
        sys.exit(1)
    store_credentials(key_id, secret)


if __name__ == "__main__":
    main()
