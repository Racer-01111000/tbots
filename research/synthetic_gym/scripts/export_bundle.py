"""Builds an immutable export of the research branch for the research instance (which has no git credentials and must not get any).
Contains: the gym, the scripts the accepted decision code needs, the frozen protocol manifests, the reference genome files, the pilot runner (for the
mock-broker proofs), and ONLY development-lane rows of the normalized data (rows after 2018-12-31 are not shipped at all)."""
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parents[1]
DEV_START, DEV_END = "2007-02-07", "2018-12-31"
D = "evolution/s6b_runs/primary/D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca/development_top_eight.json"
files = []
for base in ("research/synthetic_gym", "scripts", "evolution/protocol"):
    for p in sorted((REPO / base).rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts and p.suffix not in (".pyc",) and "results" not in p.relative_to(REPO / base).parts[:1]:
            files.append(str(p.relative_to(REPO)))
files.append(D)
files.append("experiments/sam_15_month_replay_20261001/replay_harness.py")   # the accepted evaluator mirror, used by the parity tests
for p in sorted((REPO / "experiments/sam_dev_staging_20261001").rglob("*")):
    rel = p.relative_to(REPO)
    if p.is_file() and p.suffix in (".py", ".json") and "dry_run" not in rel.parts and "blind_week_20261004" not in rel.parts and "kim_shadow_pilot" not in rel.parts and "__pycache__" not in rel.parts:
        files.append(str(rel))
files = sorted(set(files))
buf = io.BytesIO()
manifest = {"git_commit": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"]).decode().strip(), "files": {}}
with tarfile.open(fileobj=buf, mode="w:xz") as tf:
    for rel in files:
        data = (REPO / rel).read_bytes()
        manifest["files"][rel] = hashlib.sha256(data).hexdigest()
        ti = tarfile.TarInfo(rel); ti.size = len(data); ti.mode = 0o644; ti.mtime = 0
        tf.addfile(ti, io.BytesIO(data))
    for s in ("SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"):
        lines = (REPO / "data/normalized" / f"{s}.csv").read_text().splitlines()
        keep = [lines[0]] + [ln for ln in lines[1:] if DEV_START <= ln.split(",")[1] <= DEV_END]
        data = ("\n".join(keep) + "\n").encode()
        rel = f"data/normalized/{s}.csv"
        manifest["files"][rel] = hashlib.sha256(data).hexdigest()
        ti = tarfile.TarInfo(rel); ti.size = len(data); ti.mode = 0o644; ti.mtime = 0
        tf.addfile(ti, io.BytesIO(data))
    mb = json.dumps(manifest, indent=1, sort_keys=True).encode()
    ti = tarfile.TarInfo("EXPORT_MANIFEST.json"); ti.size = len(mb); ti.mtime = 0; tf.addfile(ti, io.BytesIO(mb))
out = Path(sys.argv[1])
out.write_bytes(buf.getvalue())
print("bundle", out, len(buf.getvalue()), "bytes;", len(manifest["files"]), "files; sha256", hashlib.sha256(buf.getvalue()).hexdigest(), "; commit", manifest["git_commit"])
