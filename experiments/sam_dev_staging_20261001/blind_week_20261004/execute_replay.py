"""Single execution of the pre-registered replay. Thin driver: all parameters come from preregistration.json; no
strategy logic here. Verifies frozen-file hashes and the data hash first, runs ONCE (two pre-declared cost cases in
one pass), writes results to files, and prints only a completion line (no performance)."""
import hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_blind_week as rb
import shadow_compare as sc

DEV, REPO = HERE.parent, HERE.parents[2]
pre = json.loads((HERE / "preregistration.json").read_text())
failures = []

# 1. nothing frozen may have changed
base = {"blind_week_20261004/": HERE, "scripts/": REPO / "scripts", "": DEV}
for name, expected in pre["frozen_file_sha256"].items():
    if name.startswith("evolution/"):
        path = rb.SAM_FILE
    elif name.startswith("blind_week_20261004/"):
        path = HERE / name.split("/", 1)[1]
    elif name.startswith("scripts/"):
        path = REPO / name
    else:
        path = DEV / name
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        failures.append(f"FROZEN FILE CHANGED: {name}")
if failures:
    sys.exit("\n".join(failures))

# 2. data integrity
raw_bytes = (HERE / "data" / "alpaca_bars_20260301_20261002.json").read_bytes()
data_sha = hashlib.sha256(raw_bytes).hexdigest()
blob = json.loads(raw_bytes)
bars, calendar = blob["bars"], blob["calendar"]

# 3. week + calendar + gaps (stop rules from the pre-registration)
week = pre["week_selection"]["result"]
cal_dates = [d["date"] for d in calendar]
results = {"preregistration_commit": "fbf65ac", "data_sha256": data_sha, "week": week, "stops": []}
if [d for d in cal_dates if week[0] <= d <= week[-1]] != week:
    results["stops"].append("calendar does not show exactly the five pre-registered sessions")
rows, data_report = rb.build_rows(bars["symbols"])
results["data_report"] = data_report
results["data_params"] = bars["params"]
results["retrieval"] = bars["retrieval"]
for s in rb.UNIVERSE:
    ds = [x["timestamp"] for x in rows[s]]
    if not all(d in ds for d in week):
        results["stops"].append(f"{s} missing a bar inside the test week")
    warm = [d for d in ds if d < week[0]]
    if len(warm) < 123:
        results["stops"].append(f"{s} has only {len(warm)} warm-up sessions (<123)")
    results.setdefault("warmup_sessions", {})[s] = len(warm)
if data_report["gaps"]:
    results["stops"].append("data gaps present: " + json.dumps(data_report["gaps"]))

# 4. expiry on the replay clock
expiry = pre["pilot_assumptions_used_for_testing_only"]["expiry"]["date"]
blocked = [d for d in week if d >= expiry]
results["expiry"] = {"date": expiry, "blocked_sessions": blocked}
sessions = [d for d in week if d < expiry]

if results["stops"]:
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "results.json").write_text(json.dumps(results, indent=2))
    print("STOPPED by pre-registered stop rule:", results["stops"])
    sys.exit(2)

# 5. the single pass (two pre-declared cost cases)
halt = pre["pilot_assumptions_used_for_testing_only"]["halt"]["drawdown_halt_pct"]
ceiling = pre["strategy"]["pilot_overlay"]["gross_exposure_ceiling"]
start = pre["capital_and_universe"]["starting_capital_usd"] * 100
results["cases"] = {}
for label, slip in (("primary_slippage_10bps", pre["execution_assumptions"]["slippage_primary_bps"]),
                    ("sensitivity_slippage_5bps", pre["execution_assumptions"]["slippage_sensitivity_bps"])):
    books, recs = rb.run_replay(rows, sessions, halt_pct=halt, slippage_bps=slip, ceiling=ceiling, start_cents=start)
    results["cases"][label] = rb.summarize(books, recs, rows, sessions, start)
(HERE / "results").mkdir(exist_ok=True)
(HERE / "results" / "results.json").write_text(json.dumps(results, indent=2, default=str))
print("completed; results written to results/results.json (not displayed)")
