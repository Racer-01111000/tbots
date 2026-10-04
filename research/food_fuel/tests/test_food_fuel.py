import ast, json, re
from datetime import date
from pathlib import Path
import pytest
from ffmod import stats, brief, events
from ffmod.wasde import parse_txt, parse_xml
from ffmod.cropprog import parse_text
R = Path(__file__).resolve().parents[1]; FX = R / "tests" / "fixtures"


def test_old_format_wasde_june2008_style_and_nov2006():
    r = parse_txt((FX / "wasde_2006-11-09_wasde-11-09-2006.txt").read_text("latin-1"))
    assert r["corn"]["newest_year_label"] == "2006/07"
    c = r["corn"]["items"]
    assert c["ending_stocks"]["cur"] == 935.0 and c["ending_stocks"]["prev"] == 996.0 and c["yield"]["cur"] == 151.2


def test_new_format_wasde_2026():
    r = parse_txt((FX / "wasde_2026-09-11_wasde0926.txt").read_text("latin-1"))
    c = r["corn"]["items"]
    assert (c["ending_stocks"]["prev"], c["ending_stocks"]["cur"]) == (1653.0, 1567.0) and r["corn"]["newest_year_label"] == "2026/27"


def test_xml_parser_strips_star_and_picks_commodity_block():
    def cell(a, y, m, v): return f'<g attribute1="{a}"><y market_year1="{y}"><m forecast_month1="{m}"><Cell cell_value1="{v}"/></m></y></g>'
    t = lambda title, body: f'<table sub_report_title="{title}">{body}</table>'
    feed = "".join(cell(a, "2012/13 Proj.", "Jul", v) for a, v in (("Yield per Harvested Acre", "3.49"), ("Production", "300"), ("Use, Total", "250"), ("Ending Stocks", "50")))
    corn = "".join(cell(a, y, m, v) for a, ys in (("Yield per Harvested Acre", (("2012/13 Proj.", "Jun", "166 *"), ("2012/13 Proj.", "Jul", "146"))),
            ("Production", (("2012/13 Proj.", "Jun", "14,790"), ("2012/13 Proj.", "Jul", "12,970"))), ("Use, Total", (("2012/13 Proj.", "Jun", "13,775"), ("2012/13 Proj.", "Jul", "12,720"))),
            ("Ending Stocks", (("2012/13 Proj.", "Jun", "1,881"), ("2012/13 Proj.", "Jul", "1,183")))) for y, m, v in ys)
    xml = ("<root>" + t("U.S. Feed Grain and Corn Supply and Use  1/", feed + corn) + "</root>").encode()
    out = parse_xml(xml)["corn"]["items"]
    assert out["yield"]["prev"] == 166.0 and out["yield"]["cur"] == 146.0 and out["ending_stocks"]["cur"] == 1183.0


def test_crop_progress_both_layouts():
    a = parse_text((FX / "crop_progress_2008-07-07_CropProg-07-07-2008.txt").read_text("latin-1"), "2008-07-07")
    b = parse_text((FX / "crop_progress_2012-07-16_CropProg-07-16-2012.txt").read_text("latin-1"), "2012-07-16")
    assert {r["crop"] for r in a} >= {"corn"} and a[0]["good_excellent"] == 62
    assert [r for r in b if r["crop"] == "corn"][0]["good_excellent"] == 31


def test_stats():
    assert stats.spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    rej, adj = stats.bh([0.001, 0.02, 0.2, 0.04], 0.10); assert rej == [True, True, False, True]
    x, y = list(range(40)), [i % 7 for i in range(40)]
    assert stats.perm_p(x, y, [i % 4 for i in range(40)], 200, 1) == stats.perm_p(x, y, [i % 4 for i in range(40)], 200, 1)


def test_frozen_spec_hash_recorded_and_unchanged():
    res = json.loads((R / "results" / "frozen_test_results.json").read_text())
    import hashlib
    assert hashlib.sha256((R / "features" / "FEATURE_SPEC_v1.json").read_bytes()).hexdigest() == res["spec_sha256"]


def test_no_release_before_its_observation_time():
    for e in events.wasde_events()[:200] + events.crop_events()[:200]:
        assert e["t0_rule"] in ("same_session", "next_session")
        if e["source"] == "CROP_PROGRESS": assert e["t0_rule"] == "next_session" and e["obs_time"].endswith("16:00")
        if e["source"] == "WASDE": assert e["obs_time"].endswith("12:00")


def test_brief_trading_day_gate_and_idempotence(tmp_path):
    assert brief.is_us_trading_day(date(2026, 10, 4))[0] is False and brief.is_us_trading_day(date(2026, 10, 5))[0] is True
    assert brief.is_us_trading_day(date(2026, 11, 26))[0] is False and brief.is_us_trading_day(date(2030, 1, 2))[0] is None
    s1, p1 = brief.write_edition(date(2026, 10, 4), "0915", tmp_path, tmp_path)
    s2, p2 = brief.write_edition(date(2026, 10, 4), "0915", tmp_path, tmp_path)
    assert (s1, s2) == ("WRITTEN", "EXISTS") and p1 == p2
    assert json.loads(Path(p1).read_text())["status"] == "NO_BRIEF_NOT_A_US_TRADING_DAY"


def test_brief_instruments_match_existing_global_brief_contract():
    src = (R.parents[1] / "experiments" / "sam_dev_staging_20261001" / "kim_shadow_tick.py")
    if not src.exists(): pytest.skip("paper-runner source not present in this checkout")
    tree = ast.parse(src.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "GLOBAL_INSTRUMENTS")
    assert ast.literal_eval(node.value) == {k: [tuple(x) for x in v] for k, v in brief.GLOBAL_INSTRUMENTS.items()}


def test_modules_cannot_issue_orders():
    for f in list((R / "ffmod").glob("*.py")) + list((R / "scripts").glob("*.py")):
        t = f.read_text()
        assert not re.search(r"paper-api\.alpaca|/v2/orders|APCA-API|alpaca-paper\.json|submit_order", t), f
