[Unit]
Requires=tbots-v5-guard-preflight@{T}.service
After=tbots-v5-guard-preflight@{T}.service
[Service]
ExecStartPre=+/opt/python-3.13.5-isolated/bin/python3.13 /usr/local/lib/tbots-v5-guard/guard.py require-token --target {T}
