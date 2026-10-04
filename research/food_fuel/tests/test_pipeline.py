import hashlib, json, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
import pytest
from ffmod import brief as B
from ffmod.pipeline import Pipeline, NY
R = Path(__file__).resolve().parents[1]; FX = R / "tests" / "fixtures"
WASDE_URL = "https://esmis.nal.usda.gov/sites/default/release-files/x/wasde0926.txt"
CP_A, CP_B = "https://esmis.nal.usda.gov/sites/default/release-files/x/prog3826.txt", "https://esmis.nal.usda.gov/sites/default/release-files/x/prog3926.txt"


def listing(rows):
    return ("<table>" + "".join(f'<tr><td><time datetime="{t}">x</time></td><td><a href="{u}">f</a></td></tr>' for t, u in rows) + "</table>").encode()


class Net:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)
        cp = (FX / "crop_progress_2012-07-16_CropProg-07-16-2012.txt").read_bytes()
        self.routes = {WASDE_URL: (FX / "wasde_2026-09-11_wasde0926.txt").read_bytes(), CP_A: cp, CP_B: cp.replace(b"    31          27     ", b"    29          27     ")}

    def __call__(self, url, timeout=20):
        self.calls.append(url)
        if any(f in url for f in self.fail): return 503, None
        if "finance/chart" in url:
            sym = url.split("chart/")[1].split("?")[0]
            body = {"chart": {"result": [{"meta": {"regularMarketPrice": 100.0, "previousClose": 99.0, "regularMarketTime": 1790000000, "exchangeTimezoneName": "America/New_York"}}]}}
            return 200, json.dumps(body).encode()
        if "pet_stoc_wstk" in url: return 200, (FX / "eia_stocks.html").read_bytes()
        if "pet_pnp_wiup" in url: return 200, (FX / "eia_util.html").read_bytes()
        if "world-agricultural" in url: return 200, listing([("2026-09-11T12:00:00Z", WASDE_URL)])
        if "crop-progress" in url: return 200, listing([("2026-09-21T12:00:00Z", CP_A), ("2026-09-28T12:00:00Z", CP_B)])
        if "usdmdataservices" in url:
            rows = "MapDate,AreaOfInterest,None,D0,D1,D2,D3,D4,ValidStart,ValidEnd,StatisticFormatID\n20260929,IA,0,0,10.5,0,0,0,x,y,2\n20260922,IA,0,0,9.5,0,0,0,x,y,2\n"
            return 200, rows.encode()
        return (200, self.routes[url]) if url in self.routes else (404, None)


def at(h, m, s=10, day=5):
    return datetime(2026, 10, day, h, m, s, tzinfo=NY).astimezone(timezone.utc)


def tick(root, t, net=None, mode="production", **kw):
    return Pipeline(str(root), now=t, http_get=net or Net(), mode=mode, sleep=lambda x: None, **kw).run()


def db(root): return sqlite3.connect(str(Path(root) / "state" / "pipeline.sqlite3"))


def cycle(root, net=None):
    for t in (at(4, 0), at(9, 10), at(9, 15), at(9, 29)): assert tick(root, t, net).startswith("OK")


def test_full_cycle_capture_timestamp_feature_brief(tmp_path):
    cycle(tmp_path); d = db(tmp_path)
    caps = dict(d.execute("select source, count(*) from captures where source not like 'quote:%' group by 1").fetchall())
    assert caps == {"wasde": 1, "crop_progress": 2, "eia_stocks": 1, "eia_util": 1, "usdm": 1}
    feats = {(f, c): v for f, c, v in d.execute("select feature, commodity, value from features")}
    assert feats[("w_stu_chg_pp", "corn")] == pytest.approx(-0.438, abs=1e-3) and feats[("e_dist_stock_surprise_kbbl", "distillate")] == pytest.approx(-1088.2, abs=0.1)
    ed = d.execute("select edition_key, status from editions order by 1").fetchall()
    assert ed == [("2026-10-05_0915", "WRITTEN"), ("2026-10-05_0929", "WRITTEN")]
    p = json.loads((tmp_path / "briefs" / "production_food_fuel_brief_2026-10-05_0915.json").read_text())
    assert p["mode"] == "production" and p["items"] and all(s["retrieval_time_utc"] for s in p["sources"])
    for r in d.execute("select retrieved_at_utc, sha256 from captures"): assert r[0].endswith("+00:00") and len(r[1]) == 64


def test_duplicate_prevention_and_restart_idempotence(tmp_path):
    cycle(tmp_path); d = db(tmp_path)
    before = (d.execute("select count(*) from captures").fetchone()[0], d.execute("select count(*) from features").fetchone()[0], d.execute("select count(*) from editions").fetchone()[0])
    files = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in (tmp_path / "briefs").iterdir()}
    for t in (at(9, 15, 40), at(9, 29, 40), at(9, 30)): tick(tmp_path, t)                      # re-run the same window (as after a restart)
    after = (d.execute("select count(*) from captures").fetchone()[0], d.execute("select count(*) from features").fetchone()[0], d.execute("select count(*) from editions").fetchone()[0])
    # quotes legitimately add captures only when the venue print changed; documents/features/editions must not duplicate
    assert before[1:] == after[1:]
    assert d.execute("select count(*) from captures where source in ('wasde','crop_progress','eia_stocks','eia_util','usdm')").fetchone()[0] == 6
    assert files == {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in (tmp_path / "briefs").iterdir()}


def test_crash_after_file_before_db_row_is_recovered_not_duplicated(tmp_path):
    tick(tmp_path, at(4, 0))
    p = Pipeline(str(tmp_path), now=at(9, 15), http_get=Net(), sleep=lambda x: None)
    assert p.open(); p.latest_sources(); json.dump(p.latest_sources(), open(tmp_path / "cache" / "latest_sources.json", "w"))
    st, path = B.write_edition(p.et.date(), "0915", str(tmp_path / "briefs"), str(tmp_path / "cache"), quote_cache=p.quote_cache(), mode="production")   # file written, process 'dies' before the DB row
    p.lock_f.close(); p.db.close()
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    tick(tmp_path, at(9, 16))
    row = db(tmp_path).execute("select status from editions where edition_key='2026-10-05_0915'").fetchone()
    assert row == ("RECOVERED_EXISTING_FILE",) and hashlib.sha256(open(path, "rb").read()).hexdigest() == h


def test_midtick_exception_leaves_consistent_state_and_next_tick_completes(tmp_path):
    class Boom(Pipeline):
        def compute_features(self): raise RuntimeError("simulated crash")
    out = Boom(str(tmp_path), now=at(4, 0), http_get=Net(), sleep=lambda x: None).run()
    assert out.startswith("OK_WITH_STEP_ERRORS")
    assert tick(tmp_path, at(9, 15)).startswith("OK")
    assert db(tmp_path).execute("select count(*) from features").fetchone()[0] > 0 and db(tmp_path).execute("select count(*) from editions").fetchone()[0] >= 1


def test_degraded_brief_when_a_source_is_down_nothing_fabricated(tmp_path):
    net = Net(fail=("pet_stoc_wstk", "pet_pnp_wiup", "usdmdataservices"))
    cycle(tmp_path, net)
    p = json.loads((tmp_path / "briefs" / "production_food_fuel_brief_2026-10-05_0929.json").read_text())
    assert not [i for i in p["items"] if i["area"] == "fuel"] and any("eia" in d for d in p["degraded"]) and any("usdm" in d for d in p["degraded"])
    assert [i for i in p["items"] if i["area"] == "food"]
    assert (tmp_path / "briefs" / "production_food_fuel_brief_2026-10-05_0929.md").read_text().count("DEGRADED") == 1


def test_bounded_retries_and_backoff(tmp_path):
    net = Net(fail=("pet_stoc_wstk",)); tick(tmp_path, at(4, 0), net)
    eia = [c for c in net.calls if "pet_stoc_wstk" in c]; assert len(eia) == 2                  # exactly 2 attempts
    n = len(net.calls); tick(tmp_path, at(4, 5), net)
    assert len([c for c in net.calls[n:] if "pet_stoc_wstk" in c]) == 0                          # inside the 2-minute backoff -> no hammering


def test_missed_edition_is_not_backfilled(tmp_path):
    tick(tmp_path, at(4, 0)); tick(tmp_path, at(9, 31))
    rows = dict(db(tmp_path).execute("select edition_key, status from editions").fetchall())
    assert rows["2026-10-05_0915"] == "MISSED_NOT_BACKFILLED" and rows["2026-10-05_0929"] in ("WRITTEN", "WRITTEN_LATE")
    assert not (tmp_path / "briefs" / "production_food_fuel_brief_2026-10-05_0915.json").exists()


def test_gates_weekend_holiday_window_expiry(tmp_path):
    assert tick(tmp_path, at(9, 15, day=4)) == "NOT_A_TRADING_DAY"
    assert tick(tmp_path, at(16, 40)) == "OUTSIDE_COLLECTION_WINDOW" and tick(tmp_path, at(3, 59)) == "OUTSIDE_COLLECTION_WINDOW"
    assert tick(tmp_path, at(9, 15, day=9), expires_et="2026-10-09T09:00:00") == "EXPIRED_NO_COLLECTION"
    assert tick(tmp_path, at(16, 15, day=9)).startswith("OK")                                       # Friday final flush still runs
    assert tick(tmp_path, datetime(2026, 10, 9, 17, 0, 1, tzinfo=NY).astimezone(timezone.utc)) == "EXPIRED_NO_COLLECTION"


def test_overlapping_tick_is_skipped_single_writer(tmp_path):
    a = Pipeline(str(tmp_path), now=at(4, 0), http_get=Net()); assert a.open()
    assert Pipeline(str(tmp_path), now=at(4, 0, 20), http_get=Net()).run() == "SKIPPED_LOCKED_OTHER_TICK_RUNNING"


def test_sample_or_rehearsal_never_suppresses_real_edition(tmp_path):
    cycle(tmp_path / "x", Net()); (tmp_path / "briefs").mkdir()
    for n in ("sample_food_fuel_brief_2026-10-05_0915.json", "rehearsal_food_fuel_brief_2026-10-05_0915.json", "food_fuel_brief_2026-10-05_0915.json"): (tmp_path / "briefs" / n).write_text("{}")
    cycle(tmp_path); assert (tmp_path / "briefs" / "production_food_fuel_brief_2026-10-05_0915.json").stat().st_size > 100


def test_brief_never_fetches_live_in_pipeline_mode(tmp_path):
    net = Net(); cycle(tmp_path, net)
    n_before = len([c for c in net.calls if 'finance/chart' in c])
    B.write_edition(__import__('datetime').date(2026, 10, 5), '0915', str(tmp_path / 'briefs2'), str(tmp_path / 'cache'), quote_cache={}, mode='production')
    assert len([c for c in net.calls if 'finance/chart' in c]) == n_before
    q = json.loads((tmp_path / "briefs" / "production_food_fuel_brief_2026-10-05_0915.json").read_text())["markets"]
    assert all("available" in x for rows in q.values() for x in rows)
