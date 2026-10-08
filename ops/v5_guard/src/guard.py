#!/opt/python-3.13.5-isolated/bin/python3.13
"""TBOTS V5 durable guard (stdlib only). Independent of any Claude session.

Modes:  preflight --target research|selftest   (dependency of the research unit; must PASS or the slot cannot start)
        require-token                          (ExecStartPre of the research unit; single-use token written by a passing preflight)
        check postslot|watch|final             (ExecStopPost + timers: durable per-slot result / HOLD after expiry)
        seal-local                             (fills instance-only hashes into expected.json)
Fail-closed: any failed preflight on target=research writes a PREFLIGHT_FAIL receipt, drops the STOP sentinel and disables ONLY the V5 research timer.
A hard stop after a slot does the same with a STOP receipt. Never touches capture/verify/expiry timers, the freeze, or research logic.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
PLANNED = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
RESEARCH_TIMER = "tbots-foodfuel-research.timer"
RESEARCH_SERVICE = "tbots-foodfuel-research.service"
GUARD_TIMERS = ["tbots-v5-guard-watch.timer", "tbots-v5-guard-postslot.timer", "tbots-v5-guard-final.timer"]
OTHER_TIMERS = ["tbots-foodfuel-tick.timer", "tbots-foodfuel-verify-capture.timer", "tbots-foodfuel-verify-research.timer", "tbots-foodfuel-expire.timer"]
ALL_FOODFUEL_TIMERS = [RESEARCH_TIMER] + OTHER_TIMERS
VALIDATION_FILES = ["phase_state.json", "validation_result.json", "sealed_test_result.json", "world_admission_validation.json", "world_admission_sealed.json"]
SNAPSHOT_FILES = ["slots.json", "batches.jsonl", "latest_result.json", "status.json", "publication.json", "training_world_hashes.json", "world_admission_training.json",
                  "evolution/leaderboard_latest.json", "evolution/heartbeat.json"]
BAD_IMPORT = re.compile(r"^\s*(?:from|import)\s+(alpaca\w*|socket|requests|urllib\S*|http\S*|ssl|boto3|botocore|ftplib|smtplib|subprocess|asyncio|aiohttp|websocket\S*)\b", re.M)
BAD_TEXT = re.compile(r"alpaca|APCA_|api\.alpaca", re.I)
ENV_BAD = re.compile(r"ALPACA|APCA|SECRET|TOKEN|API_?KEY|PASSWORD|AWS_", re.I)
JOURNAL_BAD = re.compile(r"alpaca|broker|order submit", re.I)   # unchanged keyword set; it is now applied ONLY to the research invocation's own journal
INVOCATION_RE = re.compile(r"^[0-9a-f]{32}$")
INVOCATION_CHECK = "no_broker_alpaca_order_lines_in_research_invocation"


class Ctx:
    def __init__(self, root="/"):
        r = str(root).rstrip("/")
        P = (lambda p: Path(p)) if r == "" else (lambda p: Path(r + p))
        self.ff = P("/opt/tbots-foodfuel"); self.state = P("/var/lib/tbots-foodfuel-research-v5"); self.gdir = P("/var/lib/tbots-v5-guard")
        self.run = P("/run/tbots-v5-guard"); self.exp = P("/etc/tbots-v5-guard/expected.json"); self.sysd = P("/etc/systemd/system")
        self.gsrc = P("/usr/local/lib/tbots-v5-guard/guard.py"); self.gym = P("/opt/tbots-gym/src"); self.ffstate = P("/var/lib/tbots-foodfuel")


class Sys:
    """all host interaction; replaced by a fake in tests"""
    def _run(self, cmd, timeout=20):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    def show(self, unit, props):
        out = self._run(["systemctl", "show", unit] + [a for p in props for a in ("-p", p)]).stdout
        d = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1); d[k] = v
        return d
    def is_enabled(self, unit): return self._run(["systemctl", "is-enabled", unit]).stdout.strip()
    def is_active(self, unit): return self._run(["systemctl", "is-active", unit]).stdout.strip()
    def failed_units(self): return [l for l in self._run(["systemctl", "--failed", "--no-legend", "--plain"]).stdout.splitlines() if "tbots" in l]
    def procs(self):
        out = []
        for p in Path("/proc").iterdir():
            if p.name.isdigit() and int(p.name) != os.getpid():
                try: c = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
                except OSError: continue
                first = os.path.basename(c.split(" ")[0]) if c else ""
                if first.startswith("python") and re.search(r"-m gym\.batch|pytest|synthetic_gym|batch_v\d|run_isolated", c) and "guard.py" not in c:
                    out.append((int(p.name), c))
        return out
    def journal_json(self, args, timeout=40):
        """journalctl JSON reader. Any non-zero exit / unparsable line raises -> callers fail closed."""
        r = self._run(["journalctl"] + list(args) + ["--utc", "-o", "json", "--no-pager"], timeout)
        if r.returncode != 0: raise RuntimeError(f"journalctl rc={r.returncode}: {r.stderr.strip()[-160:]}")
        out = []
        for line in r.stdout.splitlines():
            if not line.strip(): continue
            e = json.loads(line); m = e.get("MESSAGE", "")
            if isinstance(m, list): m = bytes(m).decode("utf-8", "replace")
            out.append({"msg": str(m), "unit": e.get("_SYSTEMD_UNIT"), "inv": e.get("_SYSTEMD_INVOCATION_ID")})
        return out
    def journal_by_invocation(self, inv): return self.journal_json(["_SYSTEMD_INVOCATION_ID=" + inv])
    def journal_unit_window(self, unit, since_utc, until_utc):
        """Bounds must be timezone-aware and are sent to journalctl as explicit UTC. A bare timestamp is read in the PROCESS timezone, and the research/guard
        units run with TZ=America/New_York, which shifted the window ~4h into the future and made it empty (2026-10-07 slot 07 false stop)."""
        if since_utc.tzinfo is None or until_utc.tzinfo is None: raise ValueError("journal window bounds must be timezone-aware")
        a, b = since_utc.astimezone(timezone.utc), until_utc.astimezone(timezone.utc)
        if a > b: raise ValueError("journal window bounds reversed")
        f = "%Y-%m-%d %H:%M:%S UTC"
        return self.journal_json(["_SYSTEMD_UNIT=" + unit, "--since", a.strftime(f), "--until", b.strftime(f)])
    def disk_free_gib(self, path): u = shutil.disk_usage(path); return u.free / 2**30
    def ntp_synced(self): return self._run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]).stdout.strip()
    def import_probe(self, env_path):
        code = ("import sys;import gym.batch_v5;bad=[m for m in sys.modules if any(k in m.lower() for k in ('alpaca','socket','request','urllib','ssl','http','boto'))];print(','.join(sorted(bad)))")
        r = self._run(["runuser", "-u", "tbotsgym", "--", "env", "PYTHONDONTWRITEBYTECODE=1", "PYTHONPATH=" + env_path, "/opt/python-3.13.5-isolated/bin/python3.13", "-c", code], 60)
        return (r.returncode, r.stdout.strip(), r.stderr.strip()[-200:])
    def cred_access(self):
        hits = []
        for p in ("/home/ec2-user/.aws", "/home/ec2-user/.config", "/home/ec2-user"):
            r = self._run(["runuser", "-u", "tbotsgym", "--", "test", "-r", p])
            hits.append((p, r.returncode == 0))
        return hits
    def disable_research_timer(self): return self._run(["systemctl", "disable", "--now", RESEARCH_TIMER], 60)
    def log_crit(self, msg): self._run(["logger", "-p", "user.crit", "-t", "tbots-v5-guard", msg])


def chk(name, ok, detail=""): return {"name": name, "ok": bool(ok), "detail": detail if isinstance(detail, str) else json.dumps(detail, default=str)[:600]}
def sha(p: Path): return hashlib.sha256(p.read_bytes()).hexdigest()
def jl(p: Path): return json.loads(p.read_text())
def now_utc(): return datetime.now(timezone.utc)
def wjson(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_name(path.name + ".tmp"); tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n"); os.replace(tmp, path)


def tree_manifest(root: Path):
    lines = []
    for p in sorted((q for q in root.rglob("*") if q.is_file() and "__pycache__" not in q.parts), key=lambda q: str(q.relative_to(root)).encode()):
        lines.append(f"{sha(p)}  {p.relative_to(root.parent)}\n")
    return hashlib.sha256("".join(lines).encode()).hexdigest(), len(lines)


# ----------------------------------------------------------------------------------------------- checks
def check_identity(ctx, exp, sysx):
    R = []; rel = ctx.ff / "releases" / exp["release_sha"]
    cur = ctx.ff / "current"
    R.append(chk("current_symlink_is_frozen_release", cur.is_symlink() and os.path.realpath(cur) == os.path.realpath(rel), os.path.realpath(cur)))
    ds = (rel / "DEPLOYED_SHA").read_text().strip() if (rel / "DEPLOYED_SHA").exists() else None
    R.append(chk("deployed_sha_matches", ds == exp["release_sha"], str(ds)))
    rb = (ctx.ff / "ROLLBACK_TO").read_text().strip() if (ctx.ff / "ROLLBACK_TO").exists() else None
    R.append(chk("rollback_target_unchanged", rb == str(ctx.ff / "releases" / exp["rollback_sha"]) or (rb or "").endswith("/releases/" + exp["rollback_sha"]), str(rb)))
    try:
        m, n = tree_manifest(rel / "research")
        R.append(chk("release_tree_matches_archive_manifest", m == exp["tree_manifest_sha"] and n == exp["tree_file_count"], f"{n} files {m[:16]}"))
    except Exception as e:
        R.append(chk("release_tree_matches_archive_manifest", False, repr(e)))
    sg = rel / "research" / "synthetic_gym"
    try:
        R.append(chk("freeze_file_hash", sha(sg / "configs" / "FROZEN_HASHES_V5.json") == exp["freeze_file_sha"], ""))
        fr = jl(sg / "configs" / "FROZEN_HASHES_V5.json")["files"]
        bad = [f for f, h in fr.items() if sha(sg / f) != h]
        R.append(chk("frozen_file_hashes_recomputed", not bad and len(fr) == exp["frozen_file_count"], f"{len(fr)} files bad={bad}"))
        kb = {f: sha(sg / f) for f in exp["key_hashes"]}
        R.append(chk("key_config_and_admission_hashes", kb == exp["key_hashes"], ""))
        adm = jl(sg / "configs" / "ADMISSION_V5.json")
        R.append(chk("admission_pass_record", adm.get("generator_admission_pass") is True and adm.get("experiment_id") == "SYNGYM-20261005-V5" and adm.get("report_sha256") == exp["admission_report_sha256"], ""))
    except Exception as e:
        R.append(chk("freeze_and_admission", False, repr(e)))
    links = {k: os.path.realpath(rel / k) == os.path.realpath(ctx.gym / k) for k in ("data", "evolution", "experiments", "scripts")}
    R.append(chk("release_symlinks_to_gym_export", all(links.values()), links))
    dh = {}
    for f, h in exp["dataset_hashes"].items():
        p = ctx.gym / f; dh[f] = sha(p) == h if p.exists() else False
    R.append(chk("dev_dataset_hashes", all(dh.values()) and len(dh) == len(exp["dataset_hashes"]), {k: v for k, v in dh.items() if not v}))
    interp = Path(exp["interpreter"]["path"])
    R.append(chk("interpreter_identity", interp.exists() and sha(interp) == exp["interpreter"]["sha256"], str(interp)))
    gs = sha(ctx.gsrc) if ctx.gsrc.exists() else None
    R.append(chk("guard_script_unchanged", gs == exp.get("guard_sha256"), str(gs)))
    ubad = {}
    for u, h in exp.get("unit_sha", {}).items():
        p = ctx.sysd / u; ubad[u] = (sha(p) == h) if p.exists() else False
    R.append(chk("unit_files_unchanged", all(ubad.values()) and len(ubad) > 0, {k: v for k, v in ubad.items() if not v}))
    return R


def check_boundary(ctx, exp, sysx):
    R = []
    props = ["ExecStart", "User", "PrivateNetwork", "NoNewPrivileges", "ProtectHome", "ProtectSystem", "MemoryMax", "CPUQuotaPerSecUSec", "ReadWritePaths", "Environment", "EnvironmentFiles", "Requires", "After", "ExecStartPre", "ExecStopPost", "TimeoutStartUSec", "TimeoutStopUSec"]
    s = sysx.show(RESEARCH_SERVICE, props)
    es = s.get("ExecStart", "")
    R.append(chk("research_execstart_v5_state_path", "gym.batch_v5" in es and "--state /var/lib/tbots-foodfuel-research-v5" in es and "--expires 2026-10-09T17:00:00" in es
                 and not re.search(r"--final|--slot|--no-session-guard|batch_v[0-4]", es), es[:300]))
    R.append(chk("research_unit_sandbox", s.get("User") == "tbotsgym" and s.get("PrivateNetwork") == "yes" and s.get("NoNewPrivileges") == "yes" and s.get("ProtectHome") == "yes"
                 and s.get("ProtectSystem") == "strict" and "/var/lib/tbots-foodfuel-research-v5" in s.get("ReadWritePaths", "") and "v4" not in s.get("ReadWritePaths", "")
                 and s.get("MemoryMax") == str(800 * 1024 * 1024) and s.get("CPUQuotaPerSecUSec", "").startswith("1s"), {k: s.get(k) for k in ("User", "PrivateNetwork", "ProtectSystem", "ReadWritePaths", "MemoryMax", "CPUQuotaPerSecUSec")}))
    R.append(chk("research_unit_wall_time_cap_15min", s.get("TimeoutStartUSec") == exp["timeout_start"] and s.get("TimeoutStopUSec") == exp["timeout_stop"],
                 {k: s.get(k) for k in ("TimeoutStartUSec", "TimeoutStopUSec")}))
    R.append(chk("research_unit_no_credentials_in_env", not ENV_BAD.search(s.get("Environment", "")) and not s.get("EnvironmentFiles", "").strip(), s.get("EnvironmentFiles", "")))
    R.append(chk("research_unit_guard_wired", "tbots-v5-guard-preflight@research.service" in s.get("Requires", "") and "tbots-v5-guard-preflight@research.service" in s.get("After", "")
                 and "require-token" in s.get("ExecStartPre", "") and "postslot" in s.get("ExecStopPost", ""), {k: s.get(k, "")[:200] for k in ("Requires", "ExecStartPre", "ExecStopPost")}))
    sg = ctx.ff / "releases" / exp["release_sha"] / "research" / "synthetic_gym"
    try:
        fr = jl(sg / "configs" / "FROZEN_HASHES_V5.json")["files"]; hits = []
        for f in fr:
            if f.endswith(".py"):
                t = (sg / f).read_text()
                if BAD_IMPORT.search(t) or BAD_TEXT.search(t): hits.append(f)
        R.append(chk("frozen_modules_no_network_or_broker_imports", not hits, hits))
    except Exception as e:
        R.append(chk("frozen_modules_no_network_or_broker_imports", False, repr(e)))
    try:
        rc, bad, err = sysx.import_probe(f"{sg}:{ctx.gym}/scripts:{ctx.gym}/scripts/lib:{ctx.gym}/experiments/sam_dev_staging_20261001")
        R.append(chk("runtime_import_probe_no_network_modules", rc == 0 and bad == "", f"rc={rc} bad={bad} err={err}"))
    except Exception as e:
        R.append(chk("runtime_import_probe_no_network_modules", False, repr(e)))
    ca = sysx.cred_access()
    R.append(chk("research_user_cannot_read_credentials", not any(ok for _, ok in ca), ca))
    return R


def schedule_checks(ctx, exp, sysx, now, slot, window=True):
    R = []; et = now.astimezone(NY)
    R.append(chk("slot_is_planned_06_to_09", slot in PLANNED[1:] and et.date().isoformat() == slot, f"slot={slot} et={et.isoformat()}"))
    if window:
        lo, hi = et.replace(hour=5, minute=0, second=0, microsecond=0), et.replace(hour=5, minute=10, second=0, microsecond=0)
        R.append(chk("within_schedule_window_0500_0510_ET", lo <= et <= hi, et.isoformat()))
    R.append(chk("before_expiry", et < datetime.fromisoformat(exp["expiry_et"]).replace(tzinfo=NY), exp["expiry_et"]))
    t = sysx.show(RESEARCH_TIMER, ["TimersCalendar", "Persistent", "UnitFileState", "ActiveState"])
    R.append(chk("research_timer_calendar_unchanged", exp["research_calendar"] in t.get("TimersCalendar", "") and t.get("Persistent") == "no" and t.get("UnitFileState") == "enabled" and t.get("ActiveState") == "active", t))
    tb = {}
    for tm in OTHER_TIMERS:
        tb[tm] = (sysx.is_enabled(tm), sysx.is_active(tm))
    R.append(chk("capture_verify_expiry_timers_enabled_active", all(v == ("enabled", "active") for v in tb.values()), tb))
    gt = {tm: (sysx.is_enabled(tm), sysx.is_active(tm)) for tm in GUARD_TIMERS}
    R.append(chk("guard_check_timers_enabled_active", all(v == ("enabled", "active") for v in gt.values()), gt))
    ex = sysx.show("tbots-foodfuel-expire.timer", ["TimersCalendar"])
    R.append(chk("expiry_timer_calendar_unchanged", exp["expire_calendar"] in ex.get("TimersCalendar", ""), ex))
    return R


def read_ledger(ctx):
    st = ctx.state; L = {}
    L["slots"] = jl(st / "slots.json") if (st / "slots.json").exists() else {}
    ck = st / "evolution" / "checkpoints"
    L["gens"] = sorted(int(p.stem.split("_")[1]) for p in ck.glob("gen_*.json")) if ck.exists() else []
    return L


def check_state(ctx, exp, k, post):
    """k = index of the slot in PLANNED; post=False: state before slot k runs; post=True: after slot k completed"""
    R = []; st = ctx.state; n = k + (1 if post else 0)  # completed slots expected
    L = read_ledger(ctx); slots = L["slots"]
    R.append(chk("ledger_slots_exact_set", sorted(slots) == PLANNED[:n], sorted(slots)))
    R.append(chk("ledger_all_completed", all(slots.get(d, {}).get("status") == "COMPLETED" for d in PLANNED[:n]), {d: slots.get(d, {}).get("status") for d in PLANNED[:n]}))
    R.append(chk("ledger_generation_ranges", all(slots.get(d, {}).get("first_generation") == 5 * j and slots.get(d, {}).get("target_generation_exclusive") == 5 * j + 5 and slots.get(d, {}).get("evaluations_completed", 0) > 0 for j, d in enumerate(PLANNED[:n])), ""))
    R.append(chk("checkpoints_contiguous", L["gens"] == list(range(5 * n)), L["gens"][-3:]))
    ck = st / "evolution" / "checkpoints"; badck = []
    for g in L["gens"]:
        j, s = ck / f"gen_{g:03d}.json", ck / f"gen_{g:03d}.sha256"
        if not (j.exists() and s.exists() and sha(j) == s.read_text().strip()): badck.append(g)
    R.append(chk("checkpoint_hashes_verify", not badck, badck))
    bl = [json.loads(x) for x in (st / "batches.jsonl").read_text().splitlines() if x.strip()] if (st / "batches.jsonl").exists() else []
    R.append(chk("batches_jsonl_one_completed_line_per_slot", [b.get("slot") for b in bl] == PLANNED[:n] and all(b.get("outcome") == "COMPLETED" and b.get("generations_this_batch") == 5 for b in bl), [b.get("outcome") for b in bl]))
    if n:
        sj = jl(st / "status.json") if (st / "status.json").exists() else {}
        last = (ck / f"gen_{5 * n - 1:03d}.sha256").read_text().strip() if L["gens"] and (ck / f"gen_{5 * n - 1:03d}.sha256").exists() else None
        R.append(chk("status_matches_last_checkpoint", sj.get("generation") == 5 * n - 1 and (sj.get("checkpoint_identity") or {}).get("sha256") == last and sj.get("stop_reason") == "COMPLETED"
                     and sj.get("promotion_enabled") is False and sj.get("deployed_revision") == exp["release_sha"], {k: sj.get(k) for k in ("generation", "stop_reason", "deployed_revision")}))
        tw = jl(st / "training_world_hashes.json") if (st / "training_world_hashes.json").exists() else {}
        R.append(chk("training_world_hashes_identical", tw == exp["training_world_hashes"], len(tw)))
    return R


def check_validation_policy(ctx, exp, k, post):
    st = ctx.state; present = [f for f in VALIDATION_FILES if (st / f).exists()]
    friday = PLANNED[k] == PLANNED[-1]
    if not (friday and post):
        return [chk("validation_sealed", not present, present)]
    R = []
    lr = jl(st / "latest_result.json") if (st / "latest_result.json").exists() else {}
    q = str(lr.get("qualification", ""))
    if not (st / "phase_state.json").exists():
        R.append(chk("friday_no_nominee_validation_unopened", q.startswith("UNAVAILABLE") and not present, {"qualification": q, "present": present}))
        return R
    ph = jl(st / "phase_state.json")
    ck = sorted((st / "evolution" / "checkpoints").glob("gen_*.sha256")); sel = ck[-1].read_text().strip() if ck else None
    R.append(chk("friday_validation_exactly_once", ph.get("validation_started") is True and ph.get("validation_done") is True and int(ph.get("validation_resume_count", 0)) == 0 and ph.get("selection_checkpoint_sha256") == sel and bool(ph.get("nominee_genome_id")),
                 {k2: ph.get(k2) for k2 in ("validation_started", "validation_done", "validation_resume_count")}))
    R.append(chk("friday_validation_result_present", (st / "validation_result.json").exists(), ""))
    passed = bool(ph.get("validation_pass"))
    sealed_expected = passed
    R.append(chk("friday_sealed_only_if_validation_passed", ((st / "sealed_test_result.json").exists() == sealed_expected) and ((st / "world_admission_sealed.json").exists() == sealed_expected), {"validation_pass": passed}))
    return R


def check_workers_and_units(sysx, allow_research_running=False):
    R = []
    pr = sysx.procs()
    R.append(chk("no_unexpected_workers", not pr, pr[:5]))
    R.append(chk("no_failed_units", not sysx.failed_units(), sysx.failed_units()[:5]))
    return R


def check_space_time(ctx, sysx):
    return [chk("disk_free_ge_2GiB", sysx.disk_free_gib(str(ctx.state)) >= 2.0, ""), chk("clock_ntp_synchronized", sysx.ntp_synced() == "yes", sysx.ntp_synced())]


# ----------------------------------------------------------------------------------------------- research invocation identity
def invocation_path(ctx, slot): return ctx.gdir / "invocations" / f"research_{slot}.json"


def record_invocation(ctx, slot, now, exp):
    """Called from ExecStartPre (inside the research unit's own invocation). Write-once; records the systemd invocation ID of THIS slot's batch."""
    inv = os.environ.get("INVOCATION_ID", "")
    if not INVOCATION_RE.match(inv): return False, "INVOCATION_ID missing or malformed in ExecStartPre"
    p = invocation_path(ctx, slot); p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "x") as f:
            f.write(json.dumps({"slot": slot, "invocation_id": inv, "unit": RESEARCH_SERVICE, "recorded_utc": now.isoformat(), "release_sha": exp["release_sha"]}, indent=1, sort_keys=True) + "\n")
    except FileExistsError:
        return False, "an invocation is already recorded for this slot (second start refused)"
    return True, inv


def check_research_invocation_clean(ctx, sysx, slot, sl_entry, now):
    """No broker/Alpaca/order-submission line in the journal of EXACTLY the V5 research invocation of this slot.
    Fail closed unless the invocation is recorded, well-formed, matches ExecStopPost's own INVOCATION_ID (when run there), has journal entries,
    belongs only to the research unit, and is the ONLY research-unit invocation in the slot window. No unit-name or keyword exemptions."""
    n = INVOCATION_CHECK
    try:
        p = invocation_path(ctx, slot)
        if not p.exists(): return chk(n, False, "no recorded research invocation for this slot (fail closed)")
        rec = jl(p); inv = rec.get("invocation_id", "")
        if not INVOCATION_RE.match(str(inv)) or rec.get("slot") != slot or rec.get("unit") != RESEARCH_SERVICE: return chk(n, False, f"malformed invocation record: {str(rec)[:160]}")
        if os.environ.get("SERVICE_RESULT") is not None and os.environ.get("INVOCATION_ID") != inv:   # running as ExecStopPost of the research unit itself
            return chk(n, False, f"ExecStopPost INVOCATION_ID {os.environ.get('INVOCATION_ID')} != recorded {inv}")
        by_id = sysx.journal_by_invocation(inv)
        if not by_id: return chk(n, False, f"no journal entries for recorded invocation {inv}")
        other_units = sorted({str(e["unit"]) for e in by_id} - {RESEARCH_SERVICE})
        if other_units: return chk(n, False, f"invocation {inv} has entries from unexpected units {other_units}")
        since = datetime.fromisoformat(rec["recorded_utc"]) - timedelta(minutes=2)
        ended = sl_entry.get("ended_utc") if sl_entry else None
        until = (datetime.fromisoformat(ended) if ended else now) + timedelta(minutes=2)
        ids = {e["inv"] for e in sysx.journal_unit_window(RESEARCH_SERVICE, since, until)}
        if ids != {inv}: return chk(n, False, f"ambiguous/mismatched research invocations in slot window: {sorted(map(str, ids))} vs recorded {inv}")
        hits = [e["msg"][:120] for e in by_id if JOURNAL_BAD.search(e["msg"])]
        return chk(n, not hits, f"invocation={inv} entries={len(by_id)} hits={len(hits)} {hits[:3]}")
    except Exception as ex:   # journal unreadable, JSON error, bad timestamps, ... -> cannot establish identity -> stop
        return chk(n, False, f"cannot establish research invocation identity: {type(ex).__name__}: {ex}"[:300])


# ----------------------------------------------------------------------------------------------- actions
def hard_stop(ctx, sysx, kind, slot, results, reason):
    rec = {"kind": kind, "slot": slot, "utc": now_utc().isoformat(), "reason": reason, "failed": [r for r in results if not r["ok"]],
           "action": "disable ONLY tbots-foodfuel-research.timer; STOP sentinel written (future preflights fail)"}
    ctx.gdir.mkdir(parents=True, exist_ok=True)
    wjson(ctx.gdir / "STOP", rec)
    r = sysx.disable_research_timer()
    rec["disable_rc"] = r.returncode; rec["disable_out"] = (r.stdout + r.stderr).strip()[-300:]
    wjson(ctx.gdir / "receipts" / f"STOP_{kind}_{slot}_{now_utc().strftime('%Y%m%dT%H%M%SZ')}.json", rec)
    sysx.log_crit(f"V5 GUARD STOP {kind} slot={slot}: {reason}")
    return rec


def write_receipt(ctx, name, slot, results, extra=None):
    ok = all(r["ok"] for r in results)
    rec = {"receipt": name, "slot": slot, "utc": now_utc().isoformat(), "overall": "PASS" if ok else "FAIL", "checks": results}
    rec.update(extra or {})
    wjson(ctx.gdir / "receipts" / f"{name}_{slot}_{now_utc().strftime('%Y%m%dT%H%M%SZ')}.json", rec)
    return rec


def preflight(ctx, sysx, target, now_override=None):
    exp = jl(ctx.exp); now = now_override or now_utc()
    if (ctx.gdir / "STOP").exists():
        res = [chk("no_STOP_sentinel", False, (ctx.gdir / "STOP").read_text()[:300])]
        slot = now.astimezone(NY).date().isoformat()
        write_receipt(ctx, f"preflight_{target}", slot, res)
        return False, res
    slot = now.astimezone(NY).date().isoformat(); k = PLANNED.index(slot) if slot in PLANNED else None
    res = schedule_checks(ctx, exp, sysx, now, slot) + check_identity(ctx, exp, sysx) + check_boundary(ctx, exp, sysx)
    if k is not None and k >= 1:
        res += check_state(ctx, exp, k, post=False) + check_validation_policy(ctx, exp, k, post=False)
    else:
        res.append(chk("slot_index_resolved", False, slot))
    res += check_workers_and_units(sysx) + check_space_time(ctx, sysx)
    ok = all(r["ok"] for r in res)
    rec = write_receipt(ctx, f"preflight_{target}", slot, res, {"target": target, "simulated_clock": bool(now_override)})
    if ok and target == "research":
        ctx.run.mkdir(parents=True, exist_ok=True)
        wjson(ctx.run / "token_research.json", {"slot": slot, "issued_utc": now.isoformat(), "expires_utc": (now + timedelta(minutes=15)).isoformat(), "release_sha": exp["release_sha"]})
    elif ok and target == "selftest":
        ctx.run.mkdir(parents=True, exist_ok=True)
        wjson(ctx.run / "token_selftest.json", {"slot": slot, "issued_utc": now.isoformat(), "expires_utc": (now + timedelta(minutes=15)).isoformat(), "release_sha": exp["release_sha"]})
    elif target == "research":
        try: (ctx.run / "token_research.json").unlink()
        except FileNotFoundError: pass
        hard_stop(ctx, sysx, "PREFLIGHT_FAIL", slot, res, "preflight failed: slot did not start; research timer disabled (fail closed, no catch-up)")
    return ok, res


def selftest_now(ctx):
    p = ctx.gdir / "SELFTEST_NOW"
    return datetime.fromisoformat(p.read_text().strip()).astimezone(timezone.utc) if p.exists() else None


def require_token(ctx, sysx, target="research"):
    exp = jl(ctx.exp); tp = ctx.run / f"token_{target}.json"
    if not tp.exists(): print("NO TOKEN"); return False
    t = jl(tp); tp.unlink()
    now = (selftest_now(ctx) if target == "selftest" else None) or now_utc(); slot = now.astimezone(NY).date().isoformat()
    ok = t.get("slot") == slot and now < datetime.fromisoformat(t["expires_utc"]) and t.get("release_sha") == exp["release_sha"] and os.path.realpath(ctx.ff / "current").endswith(exp["release_sha"]) and not (ctx.gdir / "STOP").exists()
    if ok and target == "research":
        ok, why = record_invocation(ctx, slot, now, exp)
        if not ok: print("INVOCATION NOT RECORDED:", why)
    print("TOKEN OK" if ok else "TOKEN INVALID", json.dumps(t)); return ok


def snapshot(ctx, slot, tag):
    d = ctx.gdir / "evidence" / slot / tag; d.mkdir(parents=True, exist_ok=True); man = {}
    for f in SNAPSHOT_FILES:
        p = ctx.state / f
        if p.exists(): (d / f.replace("/", "__")).write_bytes(p.read_bytes()); man[f] = sha(p)
    for p in sorted((ctx.state / "evolution").glob("batch_*.json")): (d / p.name).write_bytes(p.read_bytes()); man["evolution/" + p.name] = sha(p)
    ck = ctx.state / "evolution" / "checkpoints"
    for p in sorted(ck.glob("gen_*.sha256")): man["evolution/checkpoints/" + p.name] = p.read_text().strip()
    wjson(d / "MANIFEST.json", man); return man


def check_postslot(ctx, sysx, mode, now_override=None):
    exp = jl(ctx.exp); now = now_override or now_utc(); slot = now.astimezone(NY).date().isoformat()
    if (ctx.gdir / "STOP").exists():
        res = [chk("already_stopped", False, (ctx.gdir / "STOP").read_text()[:300])]
        write_receipt(ctx, f"postslot_{mode}_after_stop", slot, res); return False, res
    if slot not in PLANNED[1:]:
        res = [chk("slot_is_planned_06_to_09", False, slot)]
        write_receipt(ctx, f"{mode}", slot, res); hard_stop(ctx, sysx, "POSTSLOT_FAIL", slot, res, "post-slot check outside planned slots"); return False, res
    k = PLANNED.index(slot)
    res = []
    running = sysx.is_active(RESEARCH_SERVICE) in ("active", "activating", "deactivating")
    sl = jl(ctx.state / "slots.json") if (ctx.state / "slots.json").exists() else {}
    if mode == "watch" and sl.get(slot, {}).get("status") == "RUNNING" and running:
        r = write_receipt(ctx, "watch_running", slot, [chk("slot_running_within_bound", True, "RUNNING; ExecStopPost will record completion")]); return True, r["checks"]
    res += check_identity(ctx, exp, sysx)
    sr = os.environ.get("SERVICE_RESULT")
    if sr is not None:   # set by systemd for ExecStopPost; anything but "success" (e.g. "timeout") fails the batch even if state looks complete
        res.append(chk("unit_result_success", sr == "success", f"SERVICE_RESULT={sr} EXIT_CODE={os.environ.get('EXIT_CODE')} EXIT_STATUS={os.environ.get('EXIT_STATUS')}"))
    res.append(chk("slot_completed", sl.get(slot, {}).get("status") == "COMPLETED", sl.get(slot, {}).get("status")))
    bf = ctx.state / "evolution" / f"batch_SYNGYM-20261005-V5-B{k + 1}.json"
    b = jl(bf) if bf.exists() else {}
    res.append(chk("batch_completed_clean", b.get("outcome") == "COMPLETED" and b.get("halt_reason") is None and b.get("evaluations_this_invocation", 0) > 0 and b.get("rejected_worlds", 1) == 0, {x: b.get(x) for x in ("outcome", "halt_reason", "evaluations_this_invocation", "rejected_worlds")}))
    res += check_state(ctx, exp, k, post=True) + check_validation_policy(ctx, exp, k, post=True)
    res.append(check_research_invocation_clean(ctx, sysx, slot, sl.get(slot), now))
    if mode != "watch" or not running:
        res += [r for r in check_workers_and_units(sysx)]
    tb = {tm: (sysx.is_enabled(tm), sysx.is_active(tm)) for tm in OTHER_TIMERS}
    res.append(chk("capture_verify_expiry_timers_unchanged", all(v == ("enabled", "active") for v in tb.values()), tb))
    tag = f"{now.strftime('%Y%m%dT%H%M%SZ')}_{mode}"
    man = snapshot(ctx, slot, tag)
    # immutability vs the first (ExecStopPost) snapshot of this slot
    firsts = sorted(p for p in (ctx.gdir / "evidence" / slot).iterdir() if p.is_dir() and p.name.endswith("_postslot"))
    if mode in ("postslot_timer", "watch") and firsts:
        base = jl(firsts[0] / "MANIFEST.json"); diff = [f for f in base if f in man and base[f] != man[f]]
        res.append(chk("slot_evidence_unchanged_since_first_snapshot", not diff, diff))
    ok = all(r["ok"] for r in res)
    rec = write_receipt(ctx, f"postslot_{mode}", slot, res, {"evidence_dir": str(ctx.gdir / "evidence" / slot / tag), "state_manifest": man, "service_result": os.environ.get("SERVICE_RESULT"), "exit_status": os.environ.get("EXIT_STATUS")})
    if not ok:
        hard_stop(ctx, sysx, "POSTSLOT_FAIL", slot, res, "post-slot check failed")
    return ok, res


def check_final(ctx, sysx, now_override=None):
    exp = jl(ctx.exp); now = now_override or now_utc(); res = []
    et = now.astimezone(NY)
    res.append(chk("after_expiry", et >= datetime.fromisoformat(exp["expiry_et"]).replace(tzinfo=NY), et.isoformat()))
    tb = {tm: (sysx.is_enabled(tm), sysx.is_active(tm)) for tm in ALL_FOODFUEL_TIMERS}
    res.append(chk("systemd_disabled_all_foodfuel_timers_including_research", all(v[0] == "disabled" and v[1] == "inactive" for v in tb.values()), tb))
    res.append(chk("expired_marker_written", (ctx.ffstate / "EXPIRED.txt").exists(), ""))
    res += check_workers_and_units(sysx)
    res += check_identity(ctx, exp, sysx)
    stopped = (ctx.gdir / "STOP").exists()
    sl = jl(ctx.state / "slots.json") if (ctx.state / "slots.json").exists() else {}
    if not stopped:
        res.append(chk("all_planned_slots_completed", sorted(sl) == PLANNED and all(v.get("status") == "COMPLETED" for v in sl.values()), {d: v.get("status") for d, v in sl.items()}))
        res += check_state(ctx, exp, 4, post=True) + check_validation_policy(ctx, exp, 4, post=True)
    else:
        res.append(chk("validation_not_opened_unless_friday_path", True, "stopped earlier; see STOP receipt"))
    ok = all(r["ok"] for r in res)
    snapshot(ctx, "final", now.strftime("%Y%m%dT%H%M%SZ"))
    if ok:
        rec = write_receipt(ctx, "HOLD", "2026-10-09", res, {"state": "HOLD", "stopped_earlier": stopped, "note": "expiry verified; research timer disabled by systemd; no unattended research remains scheduled"})
        wjson(ctx.gdir / "HOLD", {"state": "HOLD", "utc": now_utc().isoformat()})
    else:
        write_receipt(ctx, "FINAL_FAIL", "2026-10-09", res)
        hard_stop(ctx, sysx, "FINAL_FAIL", "2026-10-09", res, "post-expiry verification failed; research timer force-disabled")
    return ok, res


def seal_local(ctx, sysx):
    exp = jl(ctx.exp)
    exp["interpreter"]["sha256"] = sha(Path(exp["interpreter"]["path"]))
    exp["guard_sha256"] = sha(ctx.gsrc)
    exp["unit_sha"] = {u: sha(ctx.sysd / u) for u in exp["unit_files"]}
    wjson(ctx.exp, exp); print(json.dumps({"guard_sha256": exp["guard_sha256"], "unit_sha": {k: v[:16] for k, v in exp["unit_sha"].items()}, "interpreter_sha": exp["interpreter"]["sha256"][:16]}, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preflight"); p.add_argument("--target", required=True, choices=["research", "selftest"]); p.add_argument("--now")
    rt = sub.add_parser("require-token"); rt.add_argument("--target", default="research", choices=["research", "selftest"])
    c = sub.add_parser("check"); c.add_argument("mode", choices=["postslot", "postslot_timer", "watch", "final"]); c.add_argument("--now")
    sub.add_parser("seal-local")
    a = ap.parse_args(argv); ctx = Ctx(os.environ.get("GUARD_TEST_ROOT", "/")); sysx = Sys()
    if getattr(a, "now", None) and not (a.cmd == "preflight" and a.target == "selftest") and not os.environ.get("GUARD_TEST_ROOT"):
        print("--now is only honoured for target=selftest"); return 2
    now = datetime.fromisoformat(a.now).astimezone(timezone.utc) if getattr(a, "now", None) else None
    if a.cmd == "preflight":
        if a.target == "selftest" and (ctx.gdir / "SELFTEST_FORCE_FAIL").exists():
            res = [chk("selftest_forced_failure", False, "SELFTEST_FORCE_FAIL present")]; write_receipt(ctx, "preflight_selftest", "selftest", res); print("PREFLIGHT FAIL (forced)"); return 1
        if a.target == "selftest" and now is None: now = selftest_now(ctx)
        ok, res = preflight(ctx, sysx, a.target, now); print("PREFLIGHT", "PASS" if ok else "FAIL", json.dumps([r for r in res if not r["ok"]])); return 0 if ok else 1
    if a.cmd == "require-token": return 0 if require_token(ctx, sysx, a.target) else 1
    if a.cmd == "check":
        if a.mode == "final": ok, res = check_final(ctx, sysx, now)
        else: ok, res = check_postslot(ctx, sysx, a.mode, now)
        print("CHECK", a.mode, "PASS" if ok else "FAIL", json.dumps([r for r in res if not r["ok"]])); return 0 if ok else 1
    if a.cmd == "seal-local": seal_local(ctx, sysx); return 0


if __name__ == "__main__":
    sys.exit(main())
