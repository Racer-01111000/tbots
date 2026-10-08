"""Guard logic tests against a SYNTHETIC fake root (no batch is ever run, no production path touched, no validation worlds generated)."""
import hashlib, json, os, shutil, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import guard as G

X = Path(os.environ.get("GUARD_TEST_FIXTURE") or Path(__file__).resolve().parents[3])   # dir containing research/ of the frozen release; defaults to the repo root when this tree sits at ops/v5_guard of release 0988a26b
SHA = "0988a26b30ae5f2005e31fa1173f8fc49cb0cc07"; RB = "d6a13e51943754c61ed9f346432183eea1419d8c"
ET = G.NY
def at(s): return datetime.fromisoformat(s).replace(tzinfo=ET).astimezone(timezone.utc)
def sh(b): return hashlib.sha256(b).hexdigest()
WORLDS = {f"w{i}": sh(str(i).encode()) for i in range(12)}

INV = "0123456789abcdef0123456789abcdef"; INV_KIM = "fedcba9876543210fedcba9876543210"; INV_2 = "1" * 32
def jl_(unit, inv, msg): return {"msg": msg, "unit": unit, "inv": inv}
def research_lines(inv=INV): return [jl_(G.RESEARCH_SERVICE, inv, m) for m in ('TOKEN OK {"slot": "2026-10-06"}', "batch SYNGYM-20261005-V5-B2 gen 5..9 done", "outcome COMPLETED")]
KIM = [jl_("tbots-other-pilot-open.service", INV_KIM, m) for m in ('"broker_order_id": "00000000"', '"order_states_from_broker": {', "alpaca order submit pending_new")]

class FakeSys(G.Sys):
    def __init__(s): s.timers = {t: ("enabled", "active") for t in G.OTHER_TIMERS + G.GUARD_TIMERS}; s.procs_l = []; s.failed = []; s.disabled = 0; s.logs = []; s.j_inv = {INV: research_lines()}; s.j_win = research_lines(); s.j_err = False
    s_unit = None
    def show(s, unit, props):
        if unit == G.RESEARCH_SERVICE:
            return dict(ExecStart="{ path=/opt/python-3.13.5-isolated/bin/python3.13 ; argv[]=/opt/python-3.13.5-isolated/bin/python3.13 -m gym.batch_v5 --state /var/lib/tbots-foodfuel-research-v5 --expires 2026-10-09T17:00:00 ; }",
                        User="tbotsgym", PrivateNetwork="yes", NoNewPrivileges="yes", ProtectHome="yes", ProtectSystem="strict", MemoryMax=str(800*1024*1024), CPUQuotaPerSecUSec="1s",
                        ReadWritePaths="/var/lib/tbots-foodfuel-research-v5", TimeoutStartUSec="13min", TimeoutStopUSec="1min", Environment="TZ=America/New_York PYTHONDONTWRITEBYTECODE=1", EnvironmentFiles="",
                        Requires="tbots-v5-guard-preflight@research.service sysinit.target", After="tbots-v5-guard-preflight@research.service", ExecStartPre="{ path=/x ; argv[]=/x guard.py require-token }", ExecStopPost="{ path=/x ; argv[]=/x guard.py check postslot }")
        if unit == G.RESEARCH_TIMER: return dict(TimersCalendar="{ OnCalendar=Tue..Fri *-*-* 05:00:00 America/New_York ; next_elapse=x }", Persistent="no", UnitFileState="enabled", ActiveState="active")
        if unit == "tbots-foodfuel-expire.timer": return dict(TimersCalendar="{ OnCalendar=2026-10-09 17:00:00 America/New_York ; }")
        return {}
    def is_enabled(s, u): return s.timers.get(u, ("enabled", "active"))[0]
    def is_active(s, u): return s.timers.get(u, ("enabled", "inactive"))[1]  # research service defaults inactive
    def failed_units(s): return s.failed
    def procs(s): return s.procs_l
    def journal_by_invocation(s, inv):
        if s.j_err: raise RuntimeError("journalctl rc=1")
        return s.j_inv.get(inv, [])
    def journal_unit_window(s, unit, since, until):
        if s.j_err: raise RuntimeError("journalctl rc=1")
        return s.j_win if unit == G.RESEARCH_SERVICE else []
    def disk_free_gib(s, p): return 50.0
    def ntp_synced(s): return "yes"
    def import_probe(s, env): return (0, "", "")
    def cred_access(s): return [("/home/ec2-user", False)]
    def disable_research_timer(s):
        s.disabled += 1
        class R: returncode = 0; stdout = "ok"; stderr = ""
        return R()
    def log_crit(s, m): s.logs.append(m)

def build(n_done):
    """fake root with release + n_done completed V5 slots (synthetic ledger/checkpoints, no real results)"""
    root = Path(tempfile.mkdtemp(prefix="gt_")); ctx = G.Ctx(str(root))
    rel = ctx.ff / "releases" / SHA; rel.mkdir(parents=True); shutil.copytree(X / "research", rel / "research"); (rel / "DEPLOYED_SHA").write_text(SHA + "\n")
    (ctx.ff / "releases" / RB).mkdir(); (ctx.ff / "ROLLBACK_TO").write_text(f"/opt/tbots-foodfuel/releases/{RB}\n"); os.symlink(rel, ctx.ff / "current")
    for d in ("data", "evolution", "experiments", "scripts"):
        (ctx.gym / d).mkdir(parents=True); os.symlink(ctx.gym / d, rel / d)
    (ctx.gym / "data" / "normalized").mkdir(); (ctx.gym / "data" / "normalized" / "SPY.csv").write_text("x\n")
    interp = root / "py"; interp.write_text("py"); ctx.gsrc.parent.mkdir(parents=True); ctx.gsrc.write_text("guard"); ctx.sysd.mkdir(parents=True)
    for u in ("tbots-foodfuel-research.service", "tbots-foodfuel-research.timer"): (ctx.sysd / u).write_text(u)
    tm, tn = G.tree_manifest(rel / "research"); sg = rel / "research" / "synthetic_gym"
    exp = dict(release_sha=SHA, rollback_sha=RB, tree_manifest_sha=tm, tree_file_count=tn, freeze_file_sha=G.sha(sg / "configs/FROZEN_HASHES_V5.json"),
               frozen_file_count=len(G.jl(sg / "configs/FROZEN_HASHES_V5.json")["files"]), key_hashes={f: G.sha(sg / f) for f in ("configs/experiment_v5.json", "configs/ADMISSION_V5.json", "configs/generator_v4.json", "docs/generator_v5_validation_ENG-V5FROZEN.json")},
               admission_report_sha256=G.jl(sg / "configs/ADMISSION_V5.json")["report_sha256"], dataset_hashes={"data/normalized/SPY.csv": G.sha(ctx.gym / "data/normalized/SPY.csv")},
               interpreter={"path": str(interp), "sha256": G.sha(interp)}, guard_sha256=G.sha(ctx.gsrc), unit_sha={u: G.sha(ctx.sysd / u) for u in ("tbots-foodfuel-research.service", "tbots-foodfuel-research.timer")},
               unit_files=["tbots-foodfuel-research.service", "tbots-foodfuel-research.timer"], training_world_hashes=WORLDS, expiry_et="2026-10-09T17:00:00", timeout_start="13min", timeout_stop="1min",
               research_calendar="OnCalendar=Tue..Fri *-*-* 05:00:00 America/New_York", expire_calendar="OnCalendar=2026-10-09 17:00:00 America/New_York")
    ctx.exp.parent.mkdir(parents=True); ctx.exp.write_text(json.dumps(exp))
    st = ctx.state; (st / "evolution/checkpoints").mkdir(parents=True)
    slots, bl = {}, []
    for j in range(n_done):
        d = G.PLANNED[j]; slots[d] = dict(batch_id=f"SYNGYM-20261005-V5-B{j+1}", status="COMPLETED", first_generation=5*j, target_generation_exclusive=5*j+5, evaluations_completed=100, started_utc=f"{d}T09:00:00+00:00")
        bl.append(dict(slot=d, outcome="COMPLETED", generations_this_batch=5))
        (st / "evolution" / f"batch_SYNGYM-20261005-V5-B{j+1}.json").write_text(json.dumps(dict(outcome="COMPLETED", halt_reason=None, evaluations_this_invocation=100, rejected_worlds=0)))
        for g in range(5*j, 5*j+5):
            b = f"gen{g}".encode(); (st / f"evolution/checkpoints/gen_{g:03d}.json").write_bytes(b); (st / f"evolution/checkpoints/gen_{g:03d}.sha256").write_text(sh(b) + "\n")
    (st / "slots.json").write_text(json.dumps(slots)); (st / "batches.jsonl").write_text("".join(json.dumps(b) + "\n" for b in bl)); (st / "training_world_hashes.json").write_text(json.dumps(WORLDS))
    if n_done:
        last = (st / f"evolution/checkpoints/gen_{5*n_done-1:03d}.sha256").read_text().strip()
        (st / "status.json").write_text(json.dumps(dict(generation=5*n_done-1, checkpoint_identity={"sha256": last}, stop_reason="COMPLETED", promotion_enabled=False, deployed_revision=SHA)))
    return ctx

def rec_inv(c, file_slot, inv=INV, **kw):
    d = {"slot": file_slot, "invocation_id": inv, "unit": G.RESEARCH_SERVICE, "recorded_utc": f"{file_slot}T09:00:00+00:00", "release_sha": SHA}; d.update(kw)
    p = G.invocation_path(c, file_slot); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(d))

def res_names(res): return sorted(r["name"] for r in res if not r["ok"])
fails = 0
def expect(label, cond, extra=""):
    global fails; print(("PASS " if cond else "FAIL ") + label, extra if not cond else ""); fails += (not cond)

# ---- 1 positive: slot 06 preflight passes at 05:00:30 ET
ctx = build(1); sy = FakeSys(); ok, res = G.preflight(ctx, sy, "research", at("2026-10-06T05:00:30"))
expect("pass: slot 06 preflight", ok, res_names(res)); expect("pass: token written", (ctx.run / "token_research.json").exists()); expect("pass: no stop, timer not disabled", sy.disabled == 0 and not (ctx.gdir / "STOP").exists())
expect("pass: receipt written", any(p.name.startswith("preflight_research_2026-10-06") for p in (ctx.gdir / "receipts").iterdir()))
os.environ["GUARD_TEST_ROOT"] = str(ctx.exp.parents[2]) if False else ""
# token consumption (slot mismatch because real clock is not 06 -> invalid; proves slot binding + single use)
G_now = G.now_utc; G.now_utc = lambda: at("2026-10-06T05:00:40"); os.environ["INVOCATION_ID"] = INV
expect("token valid within slot window", G.require_token(ctx, sy)); expect("token single-use (second use refused)", not G.require_token(ctx, sy)); del os.environ["INVOCATION_ID"]
G.preflight(ctx, FakeSys(), "research", at("2026-10-06T05:00:30")); G.now_utc = lambda: at("2026-10-06T05:20:00")
expect("token expires after 15 min", not G.require_token(ctx, sy)); G.now_utc = G_now

# ---- 2 negatives (each on a fresh root); research target must STOP + disable only the timer
def neg(label, mutate, when="2026-10-06T05:00:30", n=1, want=None, sysmut=None):
    c = build(n); s = FakeSys(); mutate(c, s) if mutate else None
    ok, res = G.preflight(c, s, "research", at(when)); names = res_names(res)
    good = (not ok) and (want is None or want in names) and s.disabled == 1 and (c.gdir / "STOP").exists() and not (c.run / "token_research.json").exists()
    expect(f"fail-closed: {label}", good, names)
neg("Monday (slot 05 / not planned)", None, "2026-10-05T05:00:30", 1, "slot_is_planned_06_to_09")
neg("outside 05:00-05:10 window (catch-up)", None, "2026-10-06T05:45:00", 1, "within_schedule_window_0500_0510_ET")
neg("after expiry", None, "2026-10-09T17:30:00", 4, "before_expiry")
neg("slot 07 but slot 06 never completed", None, "2026-10-07T05:00:30", 1, "ledger_slots_exact_set")
def m_tamper(c, s): p = c.ff / "releases" / SHA / "research/synthetic_gym/gym/batch_v5.py"; p.write_text(p.read_text() + "\n# x\n")
neg("tampered frozen file", m_tamper, want="release_tree_matches_archive_manifest")
def m_cur(c, s): os.remove(c.ff / "current"); os.symlink(c.ff / "releases" / RB, c.ff / "current")
neg("current symlink points elsewhere", m_cur, want="current_symlink_is_frozen_release")
def m_val(c, s): (c.state / "validation_result.json").write_text("{}")
neg("validation artifact exists before Friday", m_val, want="validation_sealed")
def m_phase(c, s): (c.state / "phase_state.json").write_text("{}")
neg("phase_state exists before Friday", m_phase, want="validation_sealed")
def m_worker(c, s): s.procs_l = [(1, "python3.13 -m gym.batch_v5 --state x")]
neg("unexpected worker", m_worker, want="no_unexpected_workers")
def m_failed(c, s): s.failed = ["tbots-foodfuel-tick.service failed"]
neg("failed tbots unit", m_failed, want="no_failed_units")
def m_timer(c, s): s.timers["tbots-foodfuel-tick.timer"] = ("disabled", "inactive")
neg("capture timer drift", m_timer, want="capture_verify_expiry_timers_enabled_active")
def m_gt(c, s): s.timers["tbots-v5-guard-final.timer"] = ("disabled", "inactive")
neg("guard check timer disabled", m_gt, want="guard_check_timers_enabled_active")
def m_wiring(c, s):
    orig = s.show
    def show(unit, props):
        d = orig(unit, props)
        if unit == G.RESEARCH_SERVICE: d["Requires"] = "sysinit.target"
        return d
    s.show = show
neg("guard dependency missing from research unit", m_wiring, want="research_unit_guard_wired")
def m_v4(c, s):
    orig = s.show
    def show(unit, props):
        d = orig(unit, props)
        if unit == G.RESEARCH_SERVICE: d["ExecStart"] = d["ExecStart"].replace("batch_v5", "batch_v4").replace("research-v5", "research-v4")
        return d
    s.show = show
neg("wrong state path / batch module", m_v4, want="research_execstart_v5_state_path")
def m_cap(c, s):
    orig = s.show
    def show(unit, props):
        d = orig(unit, props)
        if unit == G.RESEARCH_SERVICE: d["TimeoutStartUSec"] = "2h"
        return d
    s.show = show
neg("15-minute wall-time cap removed from unit", m_cap, want="research_unit_wall_time_cap_15min")
def m_net(c, s):
    orig = s.show
    def show(unit, props):
        d = orig(unit, props)
        if unit == G.RESEARCH_SERVICE: d["PrivateNetwork"] = "no"
        return d
    s.show = show
neg("network boundary removed", m_net, want="research_unit_sandbox")
def m_env(c, s):
    orig = s.show
    def show(unit, props):
        d = orig(unit, props)
        if unit == G.RESEARCH_SERVICE: d["Environment"] += " ALPACA_KEY=abc"
        return d
    s.show = show
neg("broker credential in unit env", m_env, want="research_unit_no_credentials_in_env")
def m_cred(c, s): s.cred_access = lambda: [("/home/ec2-user", True)]
neg("research user could read credentials", m_cred, want="research_user_cannot_read_credentials")
def m_imp(c, s): s.import_probe = lambda env: (0, "socket,ssl", "")
neg("runtime import of network module", m_imp, want="runtime_import_probe_no_network_modules")
def m_src(c, s): p = c.ff / "releases" / SHA / "research/synthetic_gym/gym/strategies.py"; p.write_text("import socket\n" + p.read_text())
neg("static network import in frozen module", m_src, want="frozen_modules_no_network_or_broker_imports")
def m_ck(c, s): (c.state / "evolution/checkpoints/gen_002.json").write_text("corrupt")
neg("corrupt checkpoint", m_ck, want="checkpoint_hashes_verify")
def m_world(c, s): (c.state / "training_world_hashes.json").write_text(json.dumps({"w0": "x"}))
neg("training worlds changed", m_world, want="training_world_hashes_identical")
def m_stop(c, s): c.gdir.mkdir(parents=True); (c.gdir / "STOP").write_text("{}")
c = build(1); c.gdir.mkdir(parents=True); (c.gdir / "STOP").write_text("{}"); s = FakeSys(); ok, res = G.preflight(c, s, "research", at("2026-10-06T05:00:30"))
expect("fail-closed: STOP sentinel blocks preflight", not ok and "no_STOP_sentinel" in res_names(res))
def m_unit(c, s): (c.sysd / "tbots-foodfuel-research.service").write_text("changed")
neg("unit file drift", m_unit, want="unit_files_unchanged")

# ---- 3 postslot
def post(n, when, mut=None, mode="postslot"):
    c = build(n); s = FakeSys(); rec_inv(c, at(when).astimezone(ET).date().isoformat()); mut(c, s) if mut else None
    ok, res = G.check_postslot(c, s, mode, at(when)); return c, s, ok, res
c, s, ok, res = post(2, "2026-10-06T05:02:00"); expect("postslot pass (slot 06 complete)", ok, res_names(res)); expect("postslot evidence snapshot written", any((c.gdir / "evidence/2026-10-06").iterdir()))
expect("postslot pass does not disable timer", s.disabled == 0 and not (c.gdir / "STOP").exists())
def pm(label, mut, want, n=2, when="2026-10-06T05:02:00", mode="postslot"):
    c, s, ok, res = post(n, when, mut, mode); expect(f"hard stop: {label}", (not ok) and want in res_names(res) and s.disabled == 1 and (c.gdir / "STOP").exists(), res_names(res))
def p_incomplete(c, s): d = json.loads((c.state / "slots.json").read_text()); d["2026-10-06"]["status"] = "RUNNING"; (c.state / "slots.json").write_text(json.dumps(d))
pm("slot not completed", p_incomplete, "slot_completed")
def p_halt(c, s): (c.state / "evolution/batch_SYNGYM-20261005-V5-B2.json").write_text(json.dumps(dict(outcome="HALTED", halt_reason="x", evaluations_this_invocation=1, rejected_worlds=0)))
pm("batch halted", p_halt, "batch_completed_clean")
def p_val(c, s): (c.state / "validation_result.json").write_text("{}")
pm("validation exposed before Friday", p_val, "validation_sealed")
def p_gap(c, s): os.remove(c.state / "evolution/checkpoints/gen_007.json"); os.remove(c.state / "evolution/checkpoints/gen_007.sha256")
pm("generation gap", p_gap, "checkpoints_contiguous")
def p_work(c, s): s.procs_l = [(9, "python3.13 -m gym.batch_v5")]
pm("worker left running", p_work, "no_unexpected_workers")
def p_broker(c, s): s.j_inv[INV].append(jl_(G.RESEARCH_SERVICE, INV, "POST https://paper-api.alpaca.markets/v2/orders"))
pm("alpaca line inside the research invocation", p_broker, G.INVOCATION_CHECK)
def p_fail(c, s): s.failed = ["tbots-foodfuel-research.service failed"]
pm("failed unit", p_fail, "no_failed_units")
def p_tim(c, s): s.timers["tbots-foodfuel-expire.timer"] = ("disabled", "inactive")
pm("expiry timer drift", p_tim, "capture_verify_expiry_timers_unchanged")
pm("post-check outside planned slots", None, "slot_is_planned_06_to_09", when="2026-10-05T05:02:00")
# unit result: timeout/signal must stop even though ledger/state look completely clean
os.environ["INVOCATION_ID"] = INV
for sr in ("timeout", "exit-code", "signal"):
    os.environ["SERVICE_RESULT"] = sr
    c, s, ok, res = post(2, "2026-10-06T05:02:00"); expect(f"hard stop: SERVICE_RESULT={sr} with otherwise-complete state", (not ok) and res_names(res) == ["unit_result_success"] and s.disabled == 1 and (c.gdir / "STOP").exists(), res_names(res))
os.environ["SERVICE_RESULT"] = "success"; c, s, ok, res = post(2, "2026-10-06T05:02:00"); expect("SERVICE_RESULT=success passes", ok and s.disabled == 0, res_names(res)); del os.environ["SERVICE_RESULT"]; del os.environ["INVOCATION_ID"]
# immutability: second (timer) check after state mutated post-slot
c, s, ok, res = post(2, "2026-10-06T05:02:00", None, "postslot")
p = c.state / "status.json"; d = json.loads(p.read_text()); d["x"] = 1; p.write_text(json.dumps(d))
s2 = FakeSys(); ok2, res2 = G.check_postslot(c, s2, "postslot_timer", at("2026-10-06T16:40:00"))
expect("hard stop: slot evidence mutated after slot end", (not ok2) and "slot_evidence_unchanged_since_first_snapshot" in res_names(res2) and s2.disabled == 1, res_names(res2))
c, s, ok, res = post(2, "2026-10-06T05:02:00", None, "postslot"); s2 = FakeSys(); ok2, res2 = G.check_postslot(c, s2, "postslot_timer", at("2026-10-06T16:40:00"))
expect("timer re-check passes when evidence unchanged", ok2, res_names(res2))
# watch while running
c = build(1); s = FakeSys(); d = json.loads((c.state / "slots.json").read_text()); d["2026-10-06"] = dict(status="RUNNING", started_utc="2026-10-06T09:00:00+00:00"); (c.state / "slots.json").write_text(json.dumps(d))
s.is_active = lambda u: "active" if u == G.RESEARCH_SERVICE else FakeSys.is_active(s, u); ok, res = G.check_postslot(c, s, "watch", at("2026-10-06T05:25:00"))
expect("watch: RUNNING within bound is not a stop", ok and s.disabled == 0, res_names(res))
c = build(1); s = FakeSys(); ok, res = G.check_postslot(c, s, "watch", at("2026-10-06T05:25:00"))
expect("watch: slot never started -> stop", (not ok) and s.disabled == 1 and (c.gdir / "STOP").exists(), res_names(res))


# ---- 3b INVOCATION-SCOPED JOURNAL CHECK (the slot-06 false-positive repair)
import subprocess as _sp
class JournalSim(FakeSys):
    """real G.Sys.journal_json / journal_by_invocation / journal_unit_window code path against a simulated journalctl (store of all units' entries incl. Kim)"""
    def __init__(s, store, rc=0):
        super().__init__(); s.store = store; s.rc = rc; s.calls = []
        s.journal_json = lambda args, timeout=40: G.Sys.journal_json(s, args, timeout)
        s.journal_by_invocation = lambda inv: G.Sys.journal_by_invocation(s, inv)
        s.journal_unit_window = lambda unit, a, b: G.Sys.journal_unit_window(s, unit, a, b)
    def _run(s, cmd, timeout=20):
        s.calls.append(cmd); out = []
        for e in s.store:
            ok = True
            for a in cmd[1:]:
                if a.startswith("_SYSTEMD_INVOCATION_ID="): ok &= e["inv"] == a.split("=", 1)[1]
                if a.startswith("_SYSTEMD_UNIT="): ok &= e["unit"] == a.split("=", 1)[1]
            if ok: out.append(json.dumps({"MESSAGE": e["msg"], "_SYSTEMD_UNIT": e["unit"], "_SYSTEMD_INVOCATION_ID": e["inv"]}))
        return _sp.CompletedProcess(cmd, s.rc, "\n".join(out), "boom" if s.rc else "")
def inv_post(label, store=None, rec=None, mut=None, env=None, rc=0, want_ok=True, n=2, when="2026-10-06T05:02:00", want=G.INVOCATION_CHECK, mode="postslot", sim=None):
    c = build(n); s = (sim or JournalSim)((research_lines() + KIM) if store is None else store, rc); slot = at(when).astimezone(ET).date().isoformat()
    if rec != "missing": rec_inv(c, slot, **(rec or {}))
    if mut: mut(c, s)
    saved = {k: os.environ.get(k) for k in ("SERVICE_RESULT", "INVOCATION_ID")}
    for k in saved: os.environ.pop(k, None)
    for k, v in (env or {}).items(): os.environ[k] = v
    try: ok, res = G.check_postslot(c, s, mode, at(when))
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None: os.environ[k] = v
    if want_ok: expect(f"invocation scope: {label}", ok and s.disabled == 0 and not (c.gdir / "STOP").exists(), res_names(res))
    else: expect(f"invocation scope STOP: {label}", (not ok) and want in res_names(res) and s.disabled == 1 and (c.gdir / "STOP").exists(), res_names(res))
    return c, s, res
# the exact slot-06 shape: Kim broker/alpaca/order lines exist in the journal but outside the research invocation
c, s, res = inv_post("Kim broker/alpaca/order lines outside the invocation do not fail V5 (timer-run check)", mode="postslot_timer", when="2026-10-06T16:40:00")
expect("invocation scope: queries are by exact invocation id / research unit only (never -u tbots-*)", all(("tbots-*" not in " ".join(x)) for x in s.calls) and any("_SYSTEMD_INVOCATION_ID=" + INV in x for x in s.calls), s.calls)
inv_post("Kim lines + ExecStopPost run inside the research invocation (matching env id)", env={"SERVICE_RESULT": "success", "INVOCATION_ID": INV})
inv_post("same-slot guard-check unit is not confused with the research invocation (timer mode ignores own INVOCATION_ID)", env={"INVOCATION_ID": INV_KIM}, mode="postslot_timer", when="2026-10-06T16:40:00")
for kw in ("POST https://paper-api.alpaca.markets/v2/orders", "broker handshake", "order submit client_order_id=x", "ALPACA_KEY seen"):
    inv_post(f"line {kw!r} inside research invocation", store=research_lines() + [jl_(G.RESEARCH_SERVICE, INV, kw)] + KIM, want_ok=False)
inv_post("Kim line attributed to the research invocation id but another unit", store=research_lines() + [jl_("tbots-other-pilot-open.service", INV, "x")], want_ok=False)
inv_post("recorded invocation missing", rec="missing", want_ok=False)
inv_post("recorded invocation id malformed", rec={"invocation_id": "not-an-id"}, want_ok=False)
inv_post("record slot mismatch", rec={"slot": "2026-10-05"}, want_ok=False)
inv_post("record for another unit", rec={"unit": "tbots-other-pilot-open.service"}, want_ok=False)
inv_post("recorded invocation has no journal entries", store=KIM, want_ok=False)
inv_post("ambiguous: second research invocation in slot window", store=research_lines() + research_lines(INV_2) + KIM, want_ok=False)
inv_post("mismatched: window holds only a different research invocation id", store=research_lines(INV_2) + [jl_(G.RESEARCH_SERVICE, INV, "x")] + KIM, rec=None, want_ok=False)
inv_post("ExecStopPost invocation id differs from recorded", env={"SERVICE_RESULT": "success", "INVOCATION_ID": INV_2}, want_ok=False)
inv_post("ExecStopPost without INVOCATION_ID", env={"SERVICE_RESULT": "success"}, want_ok=False)
inv_post("journalctl failure", rc=1, want_ok=False)
c = build(2); s = FakeSys(); rec_inv(c, "2026-10-06"); s.j_err = True; ok, res = G.check_postslot(c, s, "postslot", at("2026-10-06T05:02:00"))
expect("invocation scope STOP: journal reader raises", (not ok) and G.INVOCATION_CHECK in res_names(res) and s.disabled == 1)
# regression: other hard stops still fire in the same check (independent of the journal repair)
for sr in ("timeout", "signal", "exit-code", "oom-kill"):
    inv_post(f"SERVICE_RESULT={sr} stays a hard stop", env={"SERVICE_RESULT": sr, "INVOCATION_ID": INV}, want_ok=False, want="unit_result_success")
inv_post("frozen file hash drift stays a hard stop", mut=lambda c, s: (c.ff / "releases" / SHA / "research/synthetic_gym/gym/batch_v5.py").write_text("# drift\n"), want_ok=False, want="release_tree_matches_archive_manifest")
inv_post("validation exposed early stays a hard stop", mut=lambda c, s: (c.state / "validation_result.json").write_text("{}"), want_ok=False, want="validation_sealed")
# require-token records the invocation exactly once, binds to INVOCATION_ID
def tok(inv_env, pre=None):
    c = build(1); s = FakeSys(); G.preflight(c, s, "research", at("2026-10-06T05:00:30")); G_now = G.now_utc; G.now_utc = lambda: at("2026-10-06T05:00:40")
    os.environ.pop("INVOCATION_ID", None)
    if inv_env: os.environ["INVOCATION_ID"] = inv_env
    if pre: pre(c)
    try: r = G.require_token(c, s)
    finally: G.now_utc = G_now; os.environ.pop("INVOCATION_ID", None)
    return c, r
c, r = tok(INV); expect("require-token records the research invocation id (write-once)", r and G.jl(G.invocation_path(c, "2026-10-06"))["invocation_id"] == INV)
c, r = tok(None); expect("require-token refuses when INVOCATION_ID is missing", not r and not G.invocation_path(c, "2026-10-06").exists())
c, r = tok("zz"); expect("require-token refuses malformed INVOCATION_ID", not r)
c, r = tok(INV, pre=lambda c: rec_inv(c, "2026-10-06", INV_2)); expect("require-token refuses a second invocation in the same slot", not r and G.jl(G.invocation_path(c, "2026-10-06"))["invocation_id"] == INV_2)

# ---- 4 Friday (synthetic phase files only)
def fri(phase=None, vres=True, sealed=False, qual=None):
    c = build(5); s = FakeSys()
    if phase is not None: (c.state / "phase_state.json").write_text(json.dumps(phase))
    if vres: (c.state / "validation_result.json").write_text("{}")
    if sealed: (c.state / "sealed_test_result.json").write_text("{}"); (c.state / "world_admission_sealed.json").write_text("{}")
    if qual: (c.state / "latest_result.json").write_text(json.dumps({"qualification": qual}))
    last = (c.state / "evolution/checkpoints/gen_024.sha256").read_text().strip()
    return c, s, last
c, s, last = fri({"validation_started": True, "validation_done": True, "validation_pass": False, "nominee_genome_id": "g", "selection_checkpoint_sha256": None})
last = (c.state / "evolution/checkpoints/gen_024.sha256").read_text().strip(); ph = {"validation_started": True, "validation_done": True, "validation_pass": False, "nominee_genome_id": "g", "selection_checkpoint_sha256": last}
(c.state / "phase_state.json").write_text(json.dumps(ph)); rec_inv(c, "2026-10-09"); ok, res = G.check_postslot(c, s, "postslot", at("2026-10-09T05:02:00"))
expect("Friday: validation exactly once, failed validation, sealed stays sealed", ok, res_names(res))
c, s, last = fri({"validation_started": True, "validation_done": True, "validation_pass": False, "nominee_genome_id": "g", "selection_checkpoint_sha256": "wrong"})
rec_inv(c, "2026-10-09"); ok, res = G.check_postslot(c, s, "postslot", at("2026-10-09T05:02:00")); expect("Friday hard stop: nominee/checkpoint identity mismatch", (not ok) and "friday_validation_exactly_once" in res_names(res) and s.disabled == 1, res_names(res))
c, s, last = fri({"validation_started": True, "validation_done": True, "validation_pass": False, "nominee_genome_id": "g", "selection_checkpoint_sha256": None}, sealed=True)
(c.state / "phase_state.json").write_text(json.dumps({"validation_started": True, "validation_done": True, "validation_pass": False, "nominee_genome_id": "g", "selection_checkpoint_sha256": last}))
rec_inv(c, "2026-10-09"); ok, res = G.check_postslot(c, s, "postslot", at("2026-10-09T05:02:00")); expect("Friday hard stop: sealed opened although validation failed", (not ok) and "friday_sealed_only_if_validation_passed" in res_names(res), res_names(res))
c, s, last = fri({"validation_started": True, "validation_done": True, "validation_resume_count": 1, "validation_pass": False, "nominee_genome_id": "g", "selection_checkpoint_sha256": None})
(c.state / "phase_state.json").write_text(json.dumps({"validation_started": True, "validation_done": True, "validation_resume_count": 1, "validation_pass": False, "nominee_genome_id": "g", "selection_checkpoint_sha256": last}))
rec_inv(c, "2026-10-09"); ok, res = G.check_postslot(c, s, "postslot", at("2026-10-09T05:02:00")); expect("Friday hard stop: validation resumed/repeated", (not ok) and "friday_validation_exactly_once" in res_names(res), res_names(res))
c, s, last = fri(None, vres=False, qual="UNAVAILABLE: no eligible nominee in the final generation; validation and sealed test were NOT opened (training diagnostics only)")
rec_inv(c, "2026-10-09"); ok, res = G.check_postslot(c, s, "postslot", at("2026-10-09T05:02:00")); expect("Friday: no eligible nominee leaves validation unopened (clean)", ok, res_names(res))

# ---- 5 final / HOLD
def final(mut=None, n=5, stop=False):
    c = build(n); s = FakeSys(); s.timers = {t: ("disabled", "inactive") for t in G.ALL_FOODFUEL_TIMERS}; c.ffstate.mkdir(parents=True); (c.ffstate / "EXPIRED.txt").write_text("x")
    (c.state / "latest_result.json").write_text(json.dumps({"qualification": "UNAVAILABLE: no eligible nominee"}))
    if stop: c.gdir.mkdir(parents=True); (c.gdir / "STOP").write_text("{}")
    mut(c, s) if mut else None
    return c, s, G.check_final(c, s, at("2026-10-09T17:06:00"))
c, s, (ok, res) = final(); expect("final: expiry verified -> HOLD recorded", ok and (c.gdir / "HOLD").exists(), res_names(res))
def f_en(c, s): s.timers[G.RESEARCH_TIMER] = ("enabled", "active")
c, s, (ok, res) = final(f_en); expect("final: research timer still enabled -> STOP + force disable", (not ok) and s.disabled == 1 and (c.gdir / "STOP").exists() and not (c.gdir / "HOLD").exists(), res_names(res))
c, s, (ok, res) = final(None, n=2, stop=True); expect("final after earlier stop: HOLD recorded with stopped_earlier", ok and (c.gdir / "HOLD").exists() and any("stopped_earlier" in open(p).read() and '"stopped_earlier": true' in open(p).read() for p in (c.gdir / "receipts").glob("HOLD_*")), res_names(res))
# ---- 3c UTC JOURNAL WINDOW (slot-07 false-stop repair): the research and guard-check units run with TZ=America/New_York; bare --since/--until are read in the process timezone
from datetime import timedelta
from zoneinfo import ZoneInfo
T0 = datetime(2026, 10, 6, 9, 0, 0, tzinfo=timezone.utc)   # the slot-06-shaped run recorded at 09:00:00Z by rec_inv; ExecStopPost/timer checks run 09:02Z+
def stamp(lines, secs=10): return [dict(e, ts=T0 + timedelta(seconds=secs)) for e in lines]
class TimedJournalSim(JournalSim):
    """journalctl that HONORS --since/--until. A bare timestamp is read in self.tz (the simulated process TZ); a trailing ' UTC' is explicit UTC. Unparseable bound -> rc 1."""
    tz = timezone.utc   # tzinfo object (not a key): fixed-offset zones need no tzdata and an unknown key can never masquerade as a guard failure
    def _run(s, cmd, timeout=20):
        s.calls.append(cmd); lo = hi = None
        def bound(v):
            if v.endswith(" UTC"): return datetime.strptime(v[:-4], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=s.tz).astimezone(timezone.utc)
        try:
            for i, a in enumerate(cmd):
                if a == "--since": lo = bound(cmd[i + 1])
                if a == "--until": hi = bound(cmd[i + 1])
        except ValueError: return _sp.CompletedProcess(cmd, 1, "", "Failed to parse timestamp")
        out = []
        for e in s.store:
            ok = True
            for a in cmd[1:]:
                if a.startswith("_SYSTEMD_INVOCATION_ID="): ok &= e["inv"] == a.split("=", 1)[1]
                if a.startswith("_SYSTEMD_UNIT="): ok &= e["unit"] == a.split("=", 1)[1]
            if lo is not None: ok &= e["ts"] >= lo
            if hi is not None: ok &= e["ts"] <= hi
            if ok: out.append(json.dumps({"MESSAGE": e["msg"], "_SYSTEMD_UNIT": e["unit"], "_SYSTEMD_INVOCATION_ID": e["inv"]}))
        return _sp.CompletedProcess(cmd, s.rc, "\n".join(out), "boom" if s.rc else "")
def legacy_journal_unit_window(s, unit, since_utc, until_utc):
    """the pre-repair a608b3af/c04f1b23 implementation, kept verbatim ONLY to prove it fails"""
    f = "%Y-%m-%d %H:%M:%S"
    return s.journal_json(["_SYSTEMD_UNIT=" + unit, "--since", since_utc.strftime(f), "--until", until_utc.strftime(f)])
def tsim(tz, legacy=False):
    class _S(TimedJournalSim): pass
    _S.tz = tz
    if legacy:
        orig = _S.__init__
        def init(s, store, rc=0): orig(s, store, rc); s.journal_unit_window = lambda unit, a, b: legacy_journal_unit_window(s, unit, a, b)
        _S.__init__ = init
    return _S
NYZ = ZoneInfo("America/New_York")
TZS = {"UTC": timezone.utc, "EDT (America/New_York, Oct)": NYZ, "EST (fixed -05:00)": timezone(timedelta(hours=-5)), "Hanoi (fixed +07:00)": timezone(timedelta(hours=7))}
GOOD = stamp(research_lines()) + stamp(KIM, 20)
for name, tz in TZS.items():
    inv_post(f"UTC window: repaired code passes under process TZ {name}", store=GOOD, sim=tsim(tz))
    if tz is timezone.utc: inv_post(f"UTC window: legacy code also passes under {name} (control: simulator is not rigged)", store=GOOD, sim=tsim(tz, legacy=True))
    else:
        c, s, res = inv_post(f"UTC window: LEGACY code false-stops under process TZ {name} (reproduces slot 07)", store=GOOD, sim=tsim(tz, legacy=True), want_ok=False)
        d = [r["detail"] for r in res if r["name"] == G.INVOCATION_CHECK][0]
        expect(f"UTC window: LEGACY stop under {name} is the EMPTY-WINDOW mismatch (not an exception)", d.startswith("ambiguous/mismatched research invocations in slot window: [] vs "), d)
# direct function: true winter EST (America/New_York, Dec) and bound validation
w = datetime(2026, 12, 15, 14, 0, 0, tzinfo=timezone.utc)
E = [dict(jl_(G.RESEARCH_SERVICE, INV, "x"), ts=w + timedelta(seconds=5))]
def direct(tz, a, b, legacy=False): 
    s = tsim(tz, legacy)(E); return [e["inv"] for e in (G.Sys.journal_unit_window(s, G.RESEARCH_SERVICE, a, b) if not legacy else s.journal_unit_window(G.RESEARCH_SERVICE, a, b))]
for tz in (timezone.utc, NYZ, timezone(timedelta(hours=7))): expect(f"UTC window direct: winter EST date finds the entry under TZ {tz}", direct(tz, w - timedelta(minutes=2), w + timedelta(minutes=2)) == [INV])
expect("UTC window direct: legacy misses it under America/New_York (winter EST)", direct(NYZ, w - timedelta(minutes=2), w + timedelta(minutes=2), legacy=True) == [])
ny = ZoneInfo("America/New_York")
expect("UTC window direct: non-UTC aware bounds are converted (ET-aware input == UTC-aware input)", direct(timezone(timedelta(hours=7)), (w - timedelta(minutes=2)).astimezone(ny), (w + timedelta(minutes=2)).astimezone(ny)) == [INV])
_s = tsim(timezone.utc)(E); G.Sys.journal_unit_window(_s, G.RESEARCH_SERVICE, w, w + timedelta(minutes=1)); _a = _s.calls[-1]
expect("UTC window direct: argv carries explicit ' UTC' on both bounds", _a[_a.index("--since") + 1] == "2026-12-15 14:00:00 UTC" and _a[_a.index("--until") + 1] == "2026-12-15 14:01:00 UTC", _a)
def raises(f):
    try: f(); return False
    except ValueError: return True
expect("UTC window direct: naive 'since' rejected", raises(lambda: G.Sys.journal_unit_window(tsim(timezone.utc)(E), G.RESEARCH_SERVICE, datetime(2026, 12, 15, 14, 0, 0), w)))
expect("UTC window direct: naive 'until' rejected", raises(lambda: G.Sys.journal_unit_window(tsim(timezone.utc)(E), G.RESEARCH_SERVICE, w, datetime(2026, 12, 15, 14, 0, 0))))
expect("UTC window direct: reversed bounds rejected", raises(lambda: G.Sys.journal_unit_window(tsim(timezone.utc)(E), G.RESEARCH_SERVICE, w + timedelta(minutes=1), w)))
# every existing stop condition must still stop V5 when the journal honors time and runs in the unit's real TZ
NY_SIM = tsim(NYZ)
for kw in ("POST https://paper-api.alpaca.markets/v2/orders", "broker handshake", "order submit client_order_id=x"):
    inv_post(f"UTC window STOP: keyword {kw!r} in research invocation (TZ=America/New_York)", store=GOOD + stamp([jl_(G.RESEARCH_SERVICE, INV, kw)]), sim=NY_SIM, want_ok=False)
inv_post("UTC window STOP: empty journal for the recorded invocation", store=stamp(KIM), sim=NY_SIM, want_ok=False)
inv_post("UTC window STOP: duplicate research invocation in the slot window", store=GOOD + stamp(research_lines(INV_2), 30), sim=NY_SIM, want_ok=False)
inv_post("UTC window STOP: window holds only a different research invocation", store=stamp(research_lines(INV_2)) + stamp([jl_(G.RESEARCH_SERVICE, INV, "x")]), sim=NY_SIM, want_ok=False)
inv_post("UTC window STOP: journalctl failure/timeout (rc!=0)", store=GOOD, rc=1, sim=NY_SIM, want_ok=False)
inv_post("UTC window STOP: hash drift", store=GOOD, sim=NY_SIM, mut=lambda c, s: (c.ff / "releases" / SHA / "research/synthetic_gym/gym/batch_v5.py").write_text("# drift\n"), want_ok=False, want="release_tree_matches_archive_manifest")
inv_post("UTC window STOP: premature validation", store=GOOD, sim=NY_SIM, mut=lambda c, s: (c.state / "validation_result.json").write_text("{}"), want_ok=False, want="validation_sealed")
for sr in ("timeout", "exit-code"): inv_post(f"UTC window STOP: SERVICE_RESULT={sr}", store=GOOD, sim=NY_SIM, env={"SERVICE_RESULT": sr, "INVOCATION_ID": INV}, want_ok=False, want="unit_result_success")

print("\nTOTAL FAILS:", fails); sys.exit(1 if fails else 0)
