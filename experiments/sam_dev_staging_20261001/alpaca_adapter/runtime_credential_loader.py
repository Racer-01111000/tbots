"""Private runtime credential loader -- the ONLY place that reads
/home/ec2-user/.config/tbots/alpaca-paper.json. Read-only, GET-only caller
of the Alpaca paper-trading host. Never imports/uses route_order() or any
order-submission path, never sets TBOTS_ALPACA_SUBMISSION_ENABLED, never
constructs a POST request anywhere. Never prints or logs the key/secret.

Fail-closed on anything but an exact match to the installer's own written
posture: regular file, mode 0600, owned by the running user, not reached
through a symlink anywhere in its path.
"""
from __future__ import annotations

import json
import os
import stat
import urllib.request
import urllib.error

CRED_PATH = "/home/ec2-user/.config/tbots/alpaca-paper.json"


class CredentialTrustError(RuntimeError):
    pass


def _verify_path_safety(path: str) -> None:
    parts = path.split("/")
    cur = ""
    for part in parts[1:-1]:
        cur += "/" + part
        if cur and os.path.islink(cur):
            raise CredentialTrustError(f"refusing: {cur} is a symlink")
    if os.path.islink(path):
        raise CredentialTrustError("refusing: credential file itself is a symlink")


def load_credential_headers() -> dict:
    _verify_path_safety(CRED_PATH)
    st = os.stat(CRED_PATH, follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode):
        raise CredentialTrustError("refusing: not a regular file")
    mode = stat.S_IMODE(st.st_mode)
    if mode != 0o600:
        raise CredentialTrustError(f"refusing: mode {oct(mode)} != 0600")
    if st.st_uid != os.getuid():
        raise CredentialTrustError("refusing: not owned by the running user")

    with open(CRED_PATH) as f:
        creds = json.load(f)
    key_id = creds.get("APCA_API_KEY_ID") or creds.get("key_id") or creds.get("api_key_id")
    secret = creds.get("APCA_API_SECRET_KEY") or creds.get("secret_key") or creds.get("api_secret_key")
    if not key_id or not secret:
        raise CredentialTrustError(f"unexpected credential file shape: keys={sorted(creds.keys())}")
    return {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}


_READ_ONLY_ALLOWED_HOSTS = (
    "https://paper-api.alpaca.markets",
    "https://data.alpaca.markets",
)


def get_json(url: str, headers: dict) -> tuple[int, dict | None]:
    """GET only. Refuses any URL not on the read-only host allowlist, and
    refuses any method but GET by construction (urllib.request.Request
    defaults to GET when no `data`/`method` is given, and neither is ever
    passed here)."""
    if not any(url.startswith(h) for h in _READ_ONLY_ALLOWED_HOSTS):
        raise CredentialTrustError(f"refusing non-allowlisted host: {url}")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return e.code, (json.loads(body) if body else None)

