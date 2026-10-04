#!/bin/bash
# Launches the gym campaign inside a sandbox on the research instance. Run as root (via SSM). Nothing here touches the paper runner, its
# configuration, timers, cadence, positions or ledgers; the simulation user cannot read the broker credential and has no network.
#   $1 = output dir (under /var/lib/tbots-gym)   $2.. = extra args for gym.campaign
set -euo pipefail
OUT="$1"; shift
PYBIN=/opt/python-3.13.5-isolated/bin/python3.13      # the interpreter venv313 is built on (venv313 itself lives under the 0700 /home/ec2-user)
SRC=/opt/tbots-gym/src
id tbotsgym >/dev/null 2>&1 || useradd --system --no-create-home --shell /sbin/nologin tbotsgym
install -d -o tbotsgym -g tbotsgym -m 0750 /var/lib/tbots-gym
systemd-run --unit="tbots-gym-$(basename "$OUT")" --collect --wait \
  --uid=tbotsgym --gid=tbotsgym \
  --property=PrivateNetwork=yes --property=CPUQuota=100% --property=MemoryMax=800M --property=MemorySwapMax=0 \
  --property=RuntimeMaxSec=7200 --property=Restart=no --property=NoNewPrivileges=yes \
  --property=ProtectHome=yes --property=ProtectSystem=strict --property=PrivateTmp=yes \
  --property=ReadWritePaths=/var/lib/tbots-gym --property=TasksMax=64 \
  --setenv=PYTHONPATH="$SRC/research/synthetic_gym:$SRC/scripts:$SRC/scripts/lib:$SRC/experiments/sam_dev_staging_20261001" \
  --setenv=PYTHONHASHSEED=0 \
  --working-directory="$SRC/research/synthetic_gym" \
  "$PYBIN" -m gym.campaign --out "$OUT" "$@"
