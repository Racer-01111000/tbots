"""Renders the dedicated Kim paper-pilot systemd units from the frozen campaign.json (four sessions) and the sealed config hash.
Output: <outdir>/*.service|*.timer + hold.sh + hardstop.sh. Pure text generation; installs nothing, enables nothing.
usage: render_units.py CAMPAIGN_JSON CONFIG_SHA256 RELEASE_DIR OUTDIR [--interpreter PATH]"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

campaign = json.loads(Path(sys.argv[1]).read_text())
seal, release, out = sys.argv[2], sys.argv[3].rstrip("/"), Path(sys.argv[4])
interp = "/home/ec2-user/validation/venv313/bin/python3.13"
if "--interpreter" in sys.argv:
    interp = sys.argv[sys.argv.index("--interpreter") + 1]
STATE = "/var/lib/tbots-kim-paper"
RUNNER = f"{release}/experiments/sam_dev_staging_20261001/kim_paper_pilot.py"
UNITS = ["preflight", "open", "monitor", "expiry", "hold"]
dates = [s["date"] for s in campaign["sessions"]]
last = campaign["sessions"][-1]
last_close = datetime.fromisoformat(last["close_utc"])
from zoneinfo import ZoneInfo
ny = last_close.astimezone(ZoneInfo("America/New_York"))
expiry_at = ny + timedelta(minutes=10)
hold_at = ny + timedelta(minutes=55)           # strictly inside the 60-minute enforcement bound


def cal(d: str, hhmm: str) -> str:
    return f"OnCalendar={d} {hhmm}:00 America/New_York"


common_hardening = """NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
ReadWritePaths=%(state)s
UMask=0077
MemoryMax=512M
TasksMax=64
KillMode=control-group
Restart=no
SuccessExitStatus=75""" % {"state": STATE}


def service(name, desc, mode, timeout, env_submit, on_failure=None, on_success=None):
    env = [f"Environment=TBOTS_KIM_PAPER_STATE_DIR={STATE}", "Environment=PYTHONDONTWRITEBYTECODE=1"]
    if env_submit:
        env += ["Environment=TBOTS_ALPACA_SUBMISSION_ENABLED=true-i-understand-the-risk", f"Environment=TBOTS_KIM_PAPER_SEAL={seal}"]
    extra = []
    if on_failure:
        extra.append(f"OnFailure={on_failure}")
    if on_success:
        extra.append(f"OnSuccess={on_success}")
    return f"""[Unit]
Description={desc}
{chr(10).join(extra)}

[Service]
Type=oneshot
User=ec2-user
ExecStart={interp} {RUNNER} {mode}
TimeoutStartSec={timeout}
{chr(10).join(env)}
{common_hardening}
""".replace("\n\n\n", "\n\n")


def timer(name, desc, lines):
    return f"""[Unit]
Description={desc}

[Timer]
{chr(10).join(lines)}
AccuracySec=1s
RandomizedDelaySec=0
Persistent=false
Unit=tbots-kim-paper-{name}.service

[Install]
WantedBy=timers.target
"""


files = {}
files["tbots-kim-paper-preflight.service"] = service("preflight", "Kim paper pilot preflight (validate-only; cannot submit)", "preflight", 180, False, "tbots-kim-paper-hardstop.service")
files["tbots-kim-paper-open.service"] = service("open", "Kim paper pilot opening window (09:30-09:35 ET, DAY orders, paper only)", "open", 420, True, "tbots-kim-paper-hardstop.service")
files["tbots-kim-paper-monitor.service"] = service("monitor", "Kim paper pilot reconcile + risk monitor (regular hours)", "monitor", 150, True, "tbots-kim-paper-hardstop.service")
files["tbots-kim-paper-expiry.service"] = service("expiry", "Kim paper pilot expiry: block strategy orders, cancel pilot orders, reconcile, HOLD", "expiry", 300, True,
                                                  "tbots-kim-paper-hold.service", "tbots-kim-paper-hold.service")
files["tbots-kim-paper-preflight.timer"] = timer("preflight", "Kim paper pilot preflight 09:15 ET on the four frozen sessions", [cal(d, "09:15") for d in dates])
files["tbots-kim-paper-open.timer"] = timer("open", "Kim paper pilot opening 09:30 ET on the four frozen sessions", [cal(d, "09:30") for d in dates])
files["tbots-kim-paper-monitor.timer"] = timer("monitor", "Kim paper pilot monitor every 5 min, 09:35-16:05 ET on the four frozen sessions",
                                               [f"OnCalendar={d} {t} America/New_York" for d in dates for t in ("09:35,40,45,50,55:00", "10..15:00/5:00", "16:00,05:00")])
files["tbots-kim-paper-expiry.timer"] = timer("expiry", "Kim paper pilot expiry after the fourth close", [cal(expiry_at.date().isoformat(), expiry_at.strftime("%H:%M"))])
files["tbots-kim-paper-hold.timer"] = timer("hold", "Kim paper pilot HOLD deadline (close + 55 min, bound is 60)", [cal(hold_at.date().isoformat(), hold_at.strftime("%H:%M"))])
files["tbots-kim-paper-hold.service"] = f"""[Unit]
Description=Kim paper pilot HOLD enforcement (root): disable every pilot timer; positions retained

[Service]
Type=oneshot
User=root
ExecStart=/usr/local/lib/tbots-kim-paper/hold.sh
"""
files["tbots-kim-paper-hardstop.service"] = f"""[Unit]
Description=Kim paper pilot HARD STOP (root): disable the opening and preflight timers; reconciliation/halt liquidation keep running

[Service]
Type=oneshot
User=root
ExecStart=/usr/local/lib/tbots-kim-paper/hardstop.sh
"""
files["hold.sh"] = f"""#!/bin/sh
# root; enforce HOLD: every pilot timer off, HOLD marker present. No trading happens here.
for u in preflight open monitor expiry hold; do systemctl disable --now tbots-kim-paper-$u.timer; done
[ -e {STATE}/HOLD ] || printf '{{"status": "HOLD_ENFORCED_BY_SYSTEMD_DEADLINE", "note": "expiry receipt missing; see receipts"}}' > {STATE}/HOLD
chown ec2-user:ec2-user {STATE}/HOLD 2>/dev/null || true
"""
files["hardstop.sh"] = """#!/bin/sh
# root; a hard stop disables new-exposure triggers only. The monitor timer stays so reconciliation and an authorized halt liquidation can continue.
systemctl disable --now tbots-kim-paper-open.timer tbots-kim-paper-preflight.timer
"""
out.mkdir(parents=True, exist_ok=True)
for n, t in files.items():
    (out / n).write_text(t)
    if n.endswith(".sh"):
        (out / n).chmod(0o755)
print("rendered", len(files), "files for", dates, "expiry", expiry_at.isoformat(), "hold", hold_at.isoformat())
