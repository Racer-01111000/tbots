"""Drives the READ-ONLY remote fetch over SSM and stores the result locally with SHA-256 verification at each hop.
Prints only counts / date ranges / hashes (never prices). The remote script is piped to stdin of ec2-user's
python; nothing is written on the instance and no order endpoint is ever contacted."""
import base64, hashlib, json, sys, time, zlib
from pathlib import Path
import boto3

HERE = Path(__file__).resolve().parent
REGION, INSTANCE = "ap-southeast-1", "i-035547b52ca11d1c3"
PY = "/home/ec2-user/validation/venv313/bin/python3.13"
START, END = "2026-03-01", "2026-10-02"
GROUPS = [["SPY", "EFA", "EEM"], ["IEF", "TLT", "GLD"], ["DBC", "VNQ"]]
CAL = ("import sys,json;sys.path.insert(0,'/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots/experiments/"
       "sam_dev_staging_20261001/alpaca_adapter');from runtime_credential_loader import load_credential_headers,get_json;"
       "h=load_credential_headers();s,b=get_json('https://paper-api.alpaca.markets/v2/calendar?start=2026-09-21&end=2026-10-09',h);"
       "print('CAL:'+json.dumps({'status':s,'days':b}))")

ssm = boto3.client("ssm", region_name=REGION)


def run(cmd: str) -> str:
    r = ssm.send_command(InstanceIds=[INSTANCE], DocumentName="AWS-RunShellScript",
                         Parameters={"commands": [cmd]}, TimeoutSeconds=120, Comment="read-only Alpaca bars fetch (blind-week replay)")
    cid = r["Command"]["CommandId"]
    for _ in range(60):
        time.sleep(2)
        try:
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=INSTANCE)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if inv["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
            if inv["Status"] != "Success":
                raise RuntimeError(f"SSM {inv['Status']}: stderr={inv['StandardErrorContent'][:300]!r}")
            return inv["StandardOutputContent"]
    raise RuntimeError("SSM timeout")


script = (HERE / "fetch_bars_remote.py").read_text()
merged = {"params": None, "symbols": {}}
hashes = {}
for grp in GROUPS:
    cmd = (f"OUT=$(sudo -u ec2-user {PY} - {START} {END} {','.join(grp)} <<'PYEOF'\n{script}\nPYEOF\n); "
           f"echo \"$OUT\"; echo \"SHA256:$(printf %s \"$OUT\" | sha256sum | cut -d' ' -f1)\"")
    out = run(cmd)
    lines = out.strip().splitlines()
    blob_line = next(l for l in lines if l.startswith("BLOB:"))
    remote_sha = next(l for l in lines if l.startswith("SHA256:")).split(":", 1)[1]
    local_sha = hashlib.sha256(blob_line.encode()).hexdigest()
    if local_sha != remote_sha:
        raise RuntimeError(f"hash mismatch for {grp}: truncated or corrupted transfer")
    hashes["+".join(grp)] = local_sha
    payload = json.loads(zlib.decompress(base64.b64decode(blob_line[5:])))
    merged["params"] = payload["params"]
    merged["symbols"].update(payload["symbols"])

cal_out = run(f"sudo -u ec2-user {PY} -c \"{CAL}\"")
cal = json.loads(next(l for l in cal_out.splitlines() if l.startswith("CAL:"))[4:])

merged["retrieval"] = {"via": f"SSM RunShellScript on {INSTANCE} (read-only GET, existing runtime_credential_loader)",
                       "transfer_sha256_by_group": hashes, "calendar_http_status": cal["status"]}
(HERE / "data").mkdir(exist_ok=True)
blob = json.dumps({"bars": merged, "calendar": cal["days"]}, sort_keys=True, separators=(",", ":")).encode()
(HERE / "data" / "alpaca_bars_20260301_20261002.json").write_bytes(blob)
print("saved sha256", hashlib.sha256(blob).hexdigest(), len(blob), "bytes")
for s, e in merged["symbols"].items():
    a, r_ = e["all"], e["raw"]
    print(s, "all:", a["http_status"], a["n"], a["bars"][0]["t"][:10], a["bars"][-1]["t"][:10], "token:", a["next_page_token"],
          "| raw:", r_["http_status"], r_["n"], r_["bars"][0]["t"][:10], r_["bars"][-1]["t"][:10], "token:", r_["next_page_token"])
print("calendar:", cal["status"], [d["date"] for d in cal["days"]])
