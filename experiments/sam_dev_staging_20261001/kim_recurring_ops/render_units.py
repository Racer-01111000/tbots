"""Renders the dedicated Kim RECURRING paper units (own names, own state dir, own release root). Pure text generation: installs and
enables nothing. usage: render_units.py CONFIG_SHA256 RELEASE_DIR OUTDIR [--interpreter PATH]

Schedule = fixed weekday wall-clock slots in America/New_York (systemd handles DST). They are only ever *candidates*: the runner
asks Alpaca's calendar/clock whether today is a session, when it really opened/closes (early closes), and whether this slot is
still inside its window. Persistent=false everywhere: a missed slot (instance down) is NEVER replayed after boot."""
from __future__ import annotations

import sys
from pathlib import Path

STATE = "/var/lib/tbots-kim-recurring"
BACKUPS = "/var/lib/tbots-kim-recurring-backups"
ROOT = "/opt/tbots-kim-recurring"
INTERP = "/home/ec2-user/validation/venv313/bin/python3.13"
DAYS = "Mon..Fri"
NYTZ = "America/New_York"

SCHEDULE = {
    "preflight": ["09:15:00"],
    "open": ["09:30:00", "09:32:00", "09:34:00"],          # three firings of ONE 09:30-09:35 window; done-marker makes repeats no-ops
    "monitor": ["09:35,40,45,50,55:00", "10..15:00/5:00", "16:00,05,10,15,20,25,30:00"],
    "close": ["13:10:00", "13:20:00", "16:10:00", "16:20:00"],   # early-close day uses the 13:xx slots; full day the 16:xx slots
    "snapshot": ["13:25:00", "13:40:00", "13:55:00", "16:25:00", "16:40:00", "16:55:00"],   # post-close, after the session's final reconciliation
    "offload": ["*:20:00"],                                       # hourly retry of pending uploads (a no-op when nothing is pending)
    "consolidate": ["14:00:00"],                                  # weekly (Sundays); only months older than three calendar months are touched
}
DAYS_FOR = {"offload": ("*-*-*", "UTC"), "consolidate": ("Sun *-*-*", "UTC")}
SUBMITTING = {"open", "monitor"}                              # preflight/close can never send (no submission env)
TIMEOUT = {"preflight": 240, "open": 420, "monitor": 180, "close": 240, "snapshot": 900, "offload": 900, "consolidate": 1800}
DESC = {"preflight": "validate-only preflight (cannot submit)",
        "open": "opening window 09:30-09:35 ET (DAY orders, paper only)",
        "monitor": "reconcile + risk monitor (regular hours only)",
        "close": "post-close final reconciliation + daily summary",
        "snapshot": "post-close consistent state backup, then verified off-instance upload (no broker access)",
        "offload": "upload pending snapshots to private S3, verify, delete the staged copy (no broker access)",
        "consolidate": "monthly consolidation of daily snapshots older than three months into verified archives (no broker access)"}
OFFLINE = ("snapshot", "offload", "consolidate")

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


RUN = ("runuser -u ec2-user -- env PYTHONDONTWRITEBYTECODE=1 TBOTS_KIM_RECURRING_STATE_DIR=/var/lib/tbots-kim-recurring "
       "/home/ec2-user/validation/venv313/bin/python3.13 /opt/tbots-kim-recurring/current/experiments/sam_dev_staging_20261001/kim_recurring.py")

STANDDOWN = f"""#!/bin/sh
# root. SAFE STAND-DOWN. Disabling timers alone is NOT enough: this also waits (never kills) for any running recurring service, proves the
# writer lock is free, then prints a read-only broker<->ledger reconciliation so outstanding broker orders are reviewed by a person.
# Holdings and state are never touched; nothing is sold, cancelled or rolled back.
STATE=/var/lib/tbots-kim-recurring
for u in preflight open monitor close snapshot offload consolidate; do systemctl disable --now tbots-kim-recurring-$u.timer; done
i=0
while :; do
  act=$(systemctl list-units --state=activating,active,deactivating --no-legend 'tbots-kim-recurring-*.service' | grep -v hardstop)
  [ -z "$act" ] && break
  i=$((i+1)); [ $i -gt 120 ] && {{ echo "STILL ACTIVE after 10 min (not killing a possibly-submitting run):"; echo "$act"; exit 2; }}
  sleep 5
done
flock -n $STATE/writer.lock true || {{ echo "writer lock still held by another process"; exit 3; }}
echo "QUIESCED: all five timers disabled, no recurring service active, writer lock free"
echo "--- read-only reconciliation (review outstanding broker orders below; this script does not cancel anything) ---"
{RUN} reconcile
"""

REARM = f"""#!/bin/sh
# root. usage: rearm.sh contained|full   (contained = monitor+close+snapshot+offload+consolidate; full = also preflight+open)
# Refuses while a STOP or an unreconciled restore exists, while a writer is active, or unless a fresh reconcile is clean.
STATE=/var/lib/tbots-kim-recurring
MODE="${{1:-}}"
[ "$MODE" = contained ] || [ "$MODE" = full ] || {{ echo "usage: rearm.sh contained|full"; exit 64; }}
[ -e $STATE/STOP ] && {{ echo "REFUSED: STOP present (a person must investigate and remove it)"; exit 10; }}
[ -e $STATE/RESTORED_UNRECONCILED ] && {{ echo "REFUSED: restored state not yet reconciled"; exit 11; }}
flock -n $STATE/writer.lock true || {{ echo "REFUSED: a writer is active"; exit 12; }}
OUT=$({RUN} reconcile)
echo "$OUT" | grep -q '"status": "reconciled_clean"' || {{ echo "REFUSED: reconcile is not clean:"; echo "$OUT"; exit 13; }}
UNITS="monitor close snapshot offload consolidate"; [ "$MODE" = full ] && UNITS="preflight open monitor close snapshot offload consolidate"
for u in $UNITS; do systemctl enable --now tbots-kim-recurring-$u.timer; done
systemctl list-timers 'tbots-kim-recurring-*' --no-pager
"""


def render(seal: str, release: str, interp: str = INTERP) -> dict:
    release = release.rstrip("/")
    runner = f"{release}/experiments/sam_dev_staging_20261001/kim_recurring.py"
    files = {}
    for mode in SCHEDULE:
        env = [f"Environment=TBOTS_KIM_RECURRING_STATE_DIR={STATE}", "Environment=PYTHONDONTWRITEBYTECODE=1"]
        if mode in OFFLINE:
            env.append("Environment=HOME=/tmp")
        if mode in SUBMITTING:
            env += ["Environment=TBOTS_ALPACA_SUBMISSION_ENABLED=true-i-understand-the-risk", f"Environment=TBOTS_KIM_PAPER_SEAL={seal}"]
        hard = (HARDENING.replace(f"ReadWritePaths={STATE}", f"ReadWritePaths={STATE} {BACKUPS}").replace("\nSuccessExitStatus=75", "")
                if mode in OFFLINE else HARDENING)
        onfail = "" if mode in OFFLINE else "OnFailure=tbots-kim-recurring-hardstop.service\n"          # a failed upload must be visible, but must not stop trading timers
        files[f"tbots-kim-recurring-{mode}.service"] = (
            f"[Unit]\nDescription=Kim recurring paper {DESC[mode]}\n{onfail}\n"
            f"[Service]\nType=oneshot\nUser=ec2-user\nExecStart={interp} {runner} {mode}\nTimeoutStartSec={TIMEOUT[mode]}\n"
            + "\n".join(env) + "\n" + hard + "\n")
        days_expr, tz = DAYS_FOR.get(mode, (f"{DAYS} *-*-*", NYTZ))
        cal = "\n".join(f"OnCalendar={days_expr} {t} {tz}" for t in SCHEDULE[mode])
        files[f"tbots-kim-recurring-{mode}.timer"] = (
            f"[Unit]\nDescription=Kim recurring paper {mode} slots (candidates only; the runner gates on Alpaca's calendar/clock)\n\n"
            f"[Timer]\n{cal}\nAccuracySec=1s\nRandomizedDelaySec=0\nPersistent=false\nUnit=tbots-kim-recurring-{mode}.service\n\n"
            "[Install]\nWantedBy=timers.target\n")
    files["tbots-kim-recurring-hardstop.service"] = (
        "[Unit]\nDescription=Kim recurring paper HARD STOP (root): disable the opening and preflight timers; monitor/close keep reconciling\n\n"
        f"[Service]\nType=oneshot\nUser=root\nExecStart=/usr/local/lib/tbots-kim-recurring/hardstop.sh\n")
    files["standdown.sh"] = STANDDOWN
    files["rearm.sh"] = REARM
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
