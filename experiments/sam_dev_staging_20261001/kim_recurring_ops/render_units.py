"""Renders the dedicated Kim RECURRING paper units (own names, own state dir, own release root). Pure text generation: installs and
enables nothing. usage: render_units.py CONFIG_SHA256 RELEASE_DIR OUTDIR [--interpreter PATH]

Schedule = fixed weekday wall-clock slots in America/New_York (systemd handles DST). They are only ever *candidates*: the runner
asks Alpaca's calendar/clock whether today is a session, when it really opened/closes (early closes), and whether this slot is
still inside its window. Persistent=false everywhere: a missed slot (instance down) is NEVER replayed after boot."""
from __future__ import annotations

import sys
from pathlib import Path

STATE = "/var/lib/tbots-kim-recurring"
ROOT = "/opt/tbots-kim-recurring"
INTERP = "/home/ec2-user/validation/venv313/bin/python3.13"
DAYS = "Mon..Fri"
NYTZ = "America/New_York"

SCHEDULE = {
    "preflight": ["09:15:00"],
    "open": ["09:30:00", "09:32:00", "09:34:00"],          # three firings of ONE 09:30-09:35 window; done-marker makes repeats no-ops
    "monitor": ["09:35,40,45,50,55:00", "10..15:00/5:00", "16:00,05,10,15,20,25,30:00"],
    "close": ["13:10:00", "13:20:00", "16:10:00", "16:20:00"],   # early-close day uses the 13:xx slots; full day the 16:xx slots
}
SUBMITTING = {"open", "monitor"}                              # preflight/close can never send (no submission env)
TIMEOUT = {"preflight": 240, "open": 420, "monitor": 180, "close": 240}
DESC = {"preflight": "validate-only preflight (cannot submit)",
        "open": "opening window 09:30-09:35 ET (DAY orders, paper only)",
        "monitor": "reconcile + risk monitor (regular hours only)",
        "close": "post-close final reconciliation + daily summary"}

HARDENING = f"""NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
ReadWritePaths={STATE}
UMask=0077
MemoryMax=512M
TasksMax=64
KillMode=control-group
Restart=no
SuccessExitStatus=75"""


def render(seal: str, release: str, interp: str = INTERP) -> dict:
    release = release.rstrip("/")
    runner = f"{release}/experiments/sam_dev_staging_20261001/kim_recurring.py"
    files = {}
    for mode in SCHEDULE:
        env = [f"Environment=TBOTS_KIM_RECURRING_STATE_DIR={STATE}", "Environment=PYTHONDONTWRITEBYTECODE=1"]
        if mode in SUBMITTING:
            env += ["Environment=TBOTS_ALPACA_SUBMISSION_ENABLED=true-i-understand-the-risk", f"Environment=TBOTS_KIM_PAPER_SEAL={seal}"]
        files[f"tbots-kim-recurring-{mode}.service"] = (
            f"[Unit]\nDescription=Kim recurring paper {DESC[mode]}\nOnFailure=tbots-kim-recurring-hardstop.service\n\n"
            f"[Service]\nType=oneshot\nUser=ec2-user\nExecStart={interp} {runner} {mode}\nTimeoutStartSec={TIMEOUT[mode]}\n"
            + "\n".join(env) + "\n" + HARDENING + "\n")
        cal = "\n".join(f"OnCalendar={DAYS} *-*-* {t} {NYTZ}" for t in SCHEDULE[mode])
        files[f"tbots-kim-recurring-{mode}.timer"] = (
            f"[Unit]\nDescription=Kim recurring paper {mode} slots (candidates only; the runner gates on Alpaca's calendar/clock)\n\n"
            f"[Timer]\n{cal}\nAccuracySec=1s\nRandomizedDelaySec=0\nPersistent=false\nUnit=tbots-kim-recurring-{mode}.service\n\n"
            "[Install]\nWantedBy=timers.target\n")
    files["tbots-kim-recurring-hardstop.service"] = (
        "[Unit]\nDescription=Kim recurring paper HARD STOP (root): disable the opening and preflight timers; monitor/close keep reconciling\n\n"
        f"[Service]\nType=oneshot\nUser=root\nExecStart=/usr/local/lib/tbots-kim-recurring/hardstop.sh\n")
    files["hardstop.sh"] = ("#!/bin/sh\n# root. A latched failure removes the new-exposure triggers only; reconciliation, the 8% halt liquidation of\n"
                            "# pilot-owned shares and the daily close summary keep running. Nothing here clears STOP or touches state.\n"
                            "systemctl disable --now tbots-kim-recurring-open.timer tbots-kim-recurring-preflight.timer\n")
    return files


if __name__ == "__main__":
    seal, release, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    interp = sys.argv[sys.argv.index("--interpreter") + 1] if "--interpreter" in sys.argv else INTERP
    out.mkdir(parents=True, exist_ok=True)
    for n, t in render(seal, release, interp).items():
        (out / n).write_text(t)
        if n.endswith(".sh"):
            (out / n).chmod(0o755)
    print("rendered", len(render(seal, release, interp)), "files")
