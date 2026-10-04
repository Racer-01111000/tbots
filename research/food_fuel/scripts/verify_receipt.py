"""Read-only instance-side verification receipt. python verify_receipt.py --store capture|research --root DIR [--now ISO]
Writes DIR/receipts/<ET date>_<HHMM>_<store>.json durably. Never modifies pipeline/research state, never fetches the network, never fabricates: it reports what the stores contain.
A run more than 10 minutes after the nearest planned check time (09:41 / 16:27 ET) is labelled LATE_INSPECTION."""
import argparse, json, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
NY = ZoneInfo("America/New_York"); PLANNED = [(9, 41), (16, 27)]
ap = argparse.ArgumentParser(); ap.add_argument("--store", required=True); ap.add_argument("--root", required=True); ap.add_argument("--now"); a = ap.parse_args()
now = datetime.fromisoformat(a.now).astimezone(timezone.utc) if a.now else datetime.now(timezone.utc); et = now.astimezone(NY); day = et.date().isoformat(); root = Path(a.root)
plan = min(PLANNED, key=lambda p: abs((et.hour * 60 + et.minute) - (p[0] * 60 + p[1]))); late_min = (et.hour * 60 + et.minute) - (plan[0] * 60 + plan[1])
r = {"store": a.store, "et_date": day, "inspected_at_utc": now.isoformat(timespec="seconds"), "nearest_planned_et": "%02d:%02d" % plan, "minutes_after_planned": late_min,
     "label": "LATE_INSPECTION" if late_min > 10 else "ON_TIME_INSPECTION", "read_only": True}
if a.store == "capture":
    d = sqlite3.connect(f"file:{root}/state/pipeline.sqlite3?mode=ro", uri=True)
    r["ticks_today"] = dict(d.execute("select outcome, count(*) from ticks where substr(started_utc,1,10) in (?, date(?,'+1 day')) group by 1", (day, day)).fetchall())
    r["captures_today_by_source"] = {s: {"n": n, "first_retrieved_utc": f, "last_retrieved_utc": l} for s, n, f, l in d.execute("select substr(source,1,instr(source||':',':')-1), count(*), min(retrieved_at_utc), max(retrieved_at_utc) from captures where substr(retrieved_at_utc,1,10) in (?, date(?,'+1 day')) group by 1", (day, day))}
    r["features"] = d.execute("select feature, commodity, obs_key, round(value,3), publication_time from features").fetchall()
    r["editions"] = [dict(zip(("mode", "edition_key", "status", "target_time_utc", "generated_at_utc", "late_seconds", "path"), row)) for row in d.execute("select mode, edition_key, status, target_time_utc, generated_at_utc, late_seconds, path from editions where edition_key like ?", (day + "%",))]
    r["source_status"] = [dict(zip(("source", "last_ok_utc", "fail_count", "last_error"), row)) for row in d.execute("select source, last_ok_utc, fail_count, last_error from source_state where source not like 'quote:%'")]
    r["expected_editions_present"] = sorted(e["edition_key"][-4:] for e in r["editions"] if e["status"] in ("WRITTEN", "WRITTEN_LATE", "RECOVERED_EXISTING_FILE"))
else:
    for name in ("slots.json", "status.json", "publication.json"):
        p = root / name; r[name[:-5]] = json.loads(p.read_text()) if p.exists() else None
    b = root / "batches.jsonl"; r["batches_tail"] = [json.loads(l) for l in b.read_text().splitlines()[-3:]] if b.exists() else []
    r["note"] = "an armed timer is not evidence that its future job completed; read the slot ledger and status for what actually ran"
out = root / "receipts"; out.mkdir(exist_ok=True)
p = out / f"{day}_{et.strftime('%H%M')}_{a.store}.json"; tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(r, indent=1, default=str)); os.replace(tmp, p); print(p)
