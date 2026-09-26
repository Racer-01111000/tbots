"""Composes and sends the TBOTS Fitness V2 status report (GO Addendum C
section 2). Runs as its own systemd timer+service, entirely separate from
the evolution worker: it only ever reads STATUS.json and its own small
local state file, and invokes the system mail transport. It never takes
.tbots.lock, never writes to the repo, and never touches the worker --
this module imports no worker/checkpoint code, and every function that
does I/O is confined to: reading STATUS.json, reading/writing the local
report-state file, read-only git introspection (rev-parse/fetch, no
working-tree change), and invoking an external mail transport binary. A
reporting failure must never affect the experiment, and vice versa.
"""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    SAIGON_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:
    SAIGON_TZ = timezone(timedelta(hours=7))

ROOT = Path(__file__).resolve().parents[1]
REPORT_STATE_SCHEMA = "tbots-report-state-v1"
DEFAULT_RECIPIENT = "wright.richard@echocorelabs.com"
DEFAULT_SENDER = "claude@echocorelabs.com"
CAMPAIGN_BATCH_SIZE = 5
DONE_MAX_REPORTS = 2


class ReportError(Exception):
    pass


# ---------------------------------------------------------------------------
# Local report-state file (outside the repo; cadence bookkeeping only)
# ---------------------------------------------------------------------------

def default_report_state() -> dict:
    return {
        "schema": REPORT_STATE_SCHEMA,
        "last_state": None,
        "last_stop_code": None,
        "red_report_count": 0,
        "done_report_count": 0,
        "last_sent_utc": None,
    }


def read_report_state(path) -> dict:
    path = Path(path)
    if not path.exists():
        return default_report_state()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"report state file unreadable: {path}") from exc
    if data.get("schema") != REPORT_STATE_SCHEMA:
        raise ReportError(f"unexpected report state schema: {data.get('schema')!r}")
    return data


def write_report_state(path, state: dict) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Classification + cadence decision (pure -- no I/O)
# ---------------------------------------------------------------------------

def classify(status: dict) -> str:
    if status.get("state") == "stopped":
        return "red"
    if status.get("state") == "terminal_hold":
        return "done"
    return "green"


def decide_report(status: dict, prior_state: dict) -> dict:
    """Decide whether to send, the category, and the report number (for
    RED's "(report N)" suffix). Returns
    {"send": bool, "category": str, "report_number": int|None, "next_state": dict}.
    Never sends anything -- the caller must only persist `next_state` after
    a successful send, so a mail-transport failure never advances cadence
    counters (the next attempt correctly re-tries the same report number)."""
    category = classify(status)
    stop_code = status.get("stop_code")

    if category == "green":
        next_state = {
            **prior_state, "last_state": status.get("state"),
            "last_stop_code": None, "red_report_count": 0, "done_report_count": 0,
        }
        return {"send": True, "category": "green", "report_number": None, "next_state": next_state}

    if category == "red":
        is_new = (
            prior_state.get("last_state") != "stopped"
            or prior_state.get("last_stop_code") != stop_code
        )
        report_number = 1 if is_new else prior_state.get("red_report_count", 0) + 1
        next_state = {
            **prior_state, "last_state": "stopped", "last_stop_code": stop_code,
            "red_report_count": report_number, "done_report_count": 0,
        }
        return {"send": True, "category": "red", "report_number": report_number, "next_state": next_state}

    already_sent = (
        prior_state.get("done_report_count", 0) if prior_state.get("last_state") == "terminal_hold" else 0
    )
    report_number = already_sent + 1
    send = report_number <= DONE_MAX_REPORTS
    next_state = {
        **prior_state, "last_state": "terminal_hold", "last_stop_code": None,
        "red_report_count": 0, "done_report_count": min(report_number, DONE_MAX_REPORTS),
    }
    return {"send": send, "category": "done", "report_number": report_number, "next_state": next_state}


# ---------------------------------------------------------------------------
# Subject / body composition (pure -- no I/O)
# ---------------------------------------------------------------------------

def _truncate(text, limit=80):
    text = text or ""
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def compose_subject(status: dict, category: str, report_number: int | None) -> str:
    if category == "green":
        phase = status.get("phase") or "-"
        c_tag = f"C{status['campaign']}" if status.get("campaign") is not None else "C-"
        g_tag = f"G{status['generation']}" if status.get("generation") is not None else "G-"
        return (
            f"[TBOTS GREEN] {phase} {c_tag}/{g_tag} — "
            f"{status.get('synthetic_world_count')} worlds, "
            f"{status.get('campaigns_complete')}/{CAMPAIGN_BATCH_SIZE} done"
        )
    if category == "red":
        suffix = "" if report_number == 1 else f" (report {report_number})"
        return (
            f"[TBOTS RED] STOPPED {status.get('stop_code')} — "
            f"{_truncate(status.get('stop_reason'))}{suffix}"
        )
    champion = status.get("research_champion")
    label = champion if champion else "negative result"
    return f"[TBOTS DONE] {label} — awaiting new GO"


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fmt_ago(now_utc: datetime, then_iso: str | None) -> str:
    if not then_iso:
        return "unknown"
    delta = now_utc - _parse_utc(then_iso)
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "in the future (clock skew?)"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h{minutes:02d}m ago" if hours else f"{minutes}m ago"


def compose_body(status: dict, git_info: dict, now_utc: datetime) -> str:
    updated = status.get("updated_utc")
    saigon = (
        _parse_utc(updated).astimezone(SAIGON_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        if updated else "unknown"
    )

    lines = []
    if status.get("state") == "stopped":
        lines.append(f"stop_code: {status.get('stop_code')}")
        lines.append(f"stop_reason: {status.get('stop_reason')}")

    disk_free = status.get("disk_free_bytes")
    lines += [
        f"updated_utc: {updated or 'unknown'} (Asia/Saigon: {saigon})",
        f"state: {status.get('state')}",
        f"phase: {status.get('phase')}",
        f"protocol_manifest: {status.get('protocol_manifest')}",
        f"world_bank_id: {status.get('world_bank_id')}",
        f"synthetic_world_count: {status.get('synthetic_world_count')}",
        f"campaign: {status.get('campaign')}  campaign_seed: {status.get('campaign_seed')}  "
        f"generation: {status.get('generation')}",
        f"campaigns_complete: {status.get('campaigns_complete')}/{CAMPAIGN_BATCH_SIZE}",
        f"rank1_qualifiers: {status.get('rank1_qualifiers')}",
        f"research_champion: {status.get('research_champion')}",
        f"negative_result: {status.get('negative_result')}",
        f"recoveries: {status.get('recoveries')}",
        f"last_generation_seconds: {status.get('last_generation_seconds')}",
        f"last_progress_utc: {status.get('last_progress_utc')} "
        f"({_fmt_ago(now_utc, status.get('last_progress_utc'))})",
        f"disk_free_gb: {round(disk_free / 1024 ** 3, 1) if disk_free is not None else 'unknown'}",
        f"local_head: {git_info.get('local_head')}",
        f"local_equals_remote: {git_info.get('local_equals_remote')}",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# I/O: git introspection (read-only) and mail transport
# ---------------------------------------------------------------------------

def _run_git(repo_root, *args):
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise ReportError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def collect_git_info(repo_root, branch: str) -> dict:
    """Never mutates the working tree. `fetch` updates only the remote-
    tracking ref; if it fails (network down, remote unreachable) this
    degrades to unknown-remote rather than raising -- a reporting problem
    must never look like an experiment problem."""
    local_head = _run_git(repo_root, "rev-parse", "HEAD")
    try:
        _run_git(repo_root, "fetch", "origin", branch)
        remote_head = _run_git(repo_root, "rev-parse", f"origin/{branch}")
        local_equals_remote = local_head == remote_head
    except ReportError:
        remote_head, local_equals_remote = None, None
    return {"local_head": local_head, "remote_head": remote_head, "local_equals_remote": local_equals_remote}


def send_report(*, subject: str, body: str, to_addr: str = DEFAULT_RECIPIENT,
                from_addr: str = DEFAULT_SENDER, sendmail_cmd=("msmtp", "-t")) -> None:
    message = (
        f"To: {to_addr}\r\n"
        f"From: {from_addr}\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n{body}"
    )
    result = subprocess.run(
        list(sendmail_cmd), input=message.encode("utf-8"), capture_output=True, timeout=30,
    )
    if result.returncode != 0:
        raise ReportError(
            f"mail transport failed (exit {result.returncode}): "
            f"{result.stderr.decode(errors='replace')}"
        )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def run_once(*, status_path, state_path, branch, subject_prefix=None,
            repo_root=None, sendmail_cmd=None) -> dict:
    status = json.loads(Path(status_path).read_text())
    prior_state = read_report_state(state_path)
    decision = decide_report(status, prior_state)
    if not decision["send"]:
        return {"sent": False, "category": decision["category"]}

    now_utc = datetime.now(timezone.utc)
    git_info = collect_git_info(repo_root if repo_root is not None else ROOT, branch)
    subject = compose_subject(status, decision["category"], decision["report_number"])
    if subject_prefix:
        subject = f"{subject_prefix} {subject}"
    body = compose_body(status, git_info, now_utc)
    send_kwargs = {"subject": subject, "body": body}
    if sendmail_cmd is not None:
        send_kwargs["sendmail_cmd"] = sendmail_cmd
    send_report(**send_kwargs)

    next_state = dict(decision["next_state"])
    next_state["last_sent_utc"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    write_report_state(state_path, next_state)
    return {"sent": True, "category": decision["category"], "subject": subject}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-path", default=str(ROOT / "STATUS.json"))
    parser.add_argument("--state-path", default=str(Path.home() / ".tbots_report_state.json"))
    parser.add_argument(
        "--branch", default="feat/node-resident-fitness-v2-autonomous-evolution-20260925",
    )
    parser.add_argument(
        "--subject-prefix", default=None,
        help="prepend a marker such as [TEST] -- used only for delivery tests",
    )
    args = parser.parse_args()
    result = run_once(
        status_path=args.status_path, state_path=args.state_path,
        branch=args.branch, subject_prefix=args.subject_prefix,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
