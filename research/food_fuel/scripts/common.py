"""Polite, bounded, cached HTTP for public sources. 1 request per second, at most 2 attempts, 40 s timeout, honest User-Agent,
every response cached under cache/raw with a sha256 + retrieval-time record in cache/MANIFEST.jsonl. Bot-protection pages are
recorded as inaccessible and never retried or evaded."""
import hashlib, json, re, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
RAW = HERE / "cache" / "raw"
MANIFEST = HERE / "cache" / "MANIFEST.jsonl"
UA = "tbots-research/1.0 (read-only public-data evidence collection; no scraping of protected content)"
CHALLENGE = re.compile(r"just a moment|access denied|cf-chl|captcha|attention required|enable javascript", re.I)
_last = [0.0]


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch(url: str, cache_name: str, max_bytes: int = 400_000_000, refresh: bool = False) -> bytes | None:
    """returns bytes (from cache if present) or None when inaccessible; logs the retrieval"""
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / cache_name
    if path.exists() and not refresh:
        return path.read_bytes()
    for attempt in range(2):
        wait = 1.0 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        rec = {"url": url, "cache": cache_name, "retrieved_at_utc": now(), "attempt": attempt + 1}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read(max_bytes + 1)
                if len(body) > max_bytes:
                    rec.update(status=r.status, outcome="TOO_LARGE_ABORTED", bytes=len(body)); _log(rec); return None
                rec.update(status=r.status, content_type=r.headers.get("content-type"), bytes=len(body), last_modified=r.headers.get("last-modified"),
                           sha256=hashlib.sha256(body).hexdigest())
                if CHALLENGE.search(body[:6000].decode("utf-8", "replace")) and r.status in (200, 403):
                    rec["outcome"] = "BOT_PROTECTION_NOT_CIRCUMVENTED"; _log(rec); return None
                rec["outcome"] = "OK"; _log(rec)
                path.write_bytes(body)
                return body
        except urllib.error.HTTPError as e:
            rec.update(status=e.code, outcome=f"HTTP_{e.code}"); _log(rec)
            if e.code in (401, 403, 404, 410):
                return None
        except Exception as e:
            rec.update(status=None, outcome="ERROR", error=repr(e)[:120]); _log(rec)
        time.sleep(2.0)
    return None


def _log(rec):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST, "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
