"""Decision + state parity of the shadow books against the ACCEPTED evaluator
(replay_harness.simulate with the accepted primitives: s6a_runtime.decide_D,
control_agent.decide, execution.Portfolio, risk.validate) on identical inputs
from the accepted normalized dataset (data/normalized/*.csv)."""
import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO = HERE.parents[1]
for sub in ("", "alpaca_adapter", "kim_order_simulation_tests"):
    sys.path.insert(0, str(HERE / sub))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))  # replay_harness expects scripts/lib on the path

import execution
import s6a_final as p
import s6a_runtime as r
import lineage_d
import shadow_compare as sc
import shadow_strategies as ss
from lib.ids import genome_id

WINDOW = ("2024-01-02", "2026-08-25")
START = 100_000_000        # same starting cash the accepted harness used
FIELDS = ("open", "high", "low", "close", "adjusted_close", "volume", "corporate_action", "timestamp")


def _load_rows():
    out = {}
    for s in p.UNIVERSE:
        with open(REPO / "data/normalized" / f"{s}.csv") as f:
            out[s] = [{k: row[k] for k in FIELDS} for row in csv.DictReader(f)]
    return out


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("replay_harness", REPO / "experiments/sam_15_month_replay_20261001/replay_harness.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def raw_rows():
    return _load_rows()


@pytest.fixture(scope="module")
def shadow_rows(raw_rows):
    out = {}
    for s, rows in raw_rows.items():
        out[s] = [{"timestamp": r_["timestamp"], "open": float(r_["open"]), "close": float(r_["close"]),
                   "adjusted_close": float(r_["adjusted_close"]),
                   "dividend": execution.dividend_amount_dollars(r_["corporate_action"] or None)} for r_ in rows]
    return out


def _window_dates(shadow_rows):
    base = [r_["timestamp"] for r_ in shadow_rows["SPY"] if WINDOW[0] <= r_["timestamp"] <= WINDOW[1]]
    return base


# ---------------------------------------------------------------- identity
def test_genome_hash_independently_verified_against_both_frozen_runs():
    gid = lineage_d.EXPECTED_ID
    assert gid == "gen_d890eb9faa7bf0309773c85620eb6652ad589ab82d6fbff4423ddcfbbb460f85"
    base = "evolution/s6b_runs/%s/D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca/development_top_eight.json"
    genomes = []
    for run in ("primary", "reproduction"):
        d = json.loads((REPO / (base % run)).read_text())
        e = next(x for x in d["frozen"] if x["genome_id"] == gid)
        assert lineage_d.independent_genome_id(e["genome"]) == gid == genome_id(e["genome"])
        genomes.append(e["genome"])
    loaded = lineage_d.load_verified_genome()
    assert loaded == genomes[0] == genomes[1]
    assert r.validate_genome("D", loaded) == gid


def test_tampered_genome_file_is_refused(tmp_path):
    doc = json.loads(lineage_d.GENOME_FILE.read_text())
    doc["genome"]["breadth_threshold"] = 0.45
    f = tmp_path / "g.json"
    f.write_text(json.dumps(doc))
    with pytest.raises(lineage_d.GenomeIdentityError):
        lineage_d.load_verified_genome(f)


# ---------------------------------------------------------------- parity
def _run_accepted(harness, raw_rows, decide_fn, *, use_d=False, genome=None, bounds):
    calls = []
    orig = r.decide_D
    def spy(g, h, step):
        out = orig(g, h, step)
        calls.append((step, {s: tuple(h[s][-(g["regime_window"] + 1):]) for s in p.UNIVERSE}, out))
        return out
    r.decide_D = spy
    try:
        res = harness.simulate(decide_fn, per_symbol_rows=raw_rows, window_start=WINDOW[0], window_end=WINDOW[1],
                               max_asset_weight=bounds[0], max_total_exposure=bounds[1],
                               starting_cash_cents=START, slippage_bps=5, use_decide_D=use_d, genome_for_D=genome)
    finally:
        r.decide_D = orig
    return res, calls


def _run_shadow(strategy, shadow_rows, dates):
    seen = []
    orig = strategy.decide
    def spy(h, step):
        seen.append((step, {s: tuple(h[s][-112:]) for s in p.UNIVERSE}))
        return orig(h, step)
    strategy.decide = spy
    book = sc.new_book(strategy, START)
    recs = sc.run_sessions({strategy.name: book}, {strategy.name: strategy}, shadow_rows, dates)
    return book, [x["books"][strategy.name] for x in recs], seen


def test_lineage_d_decisions_inputs_and_state_match_accepted_evaluator(harness, raw_rows, shadow_rows):
    g = lineage_d.load_verified_genome()
    acc, calls = _run_accepted(harness, raw_rows, None, use_d=True, genome=g, bounds=lineage_d.risk_bounds(g))
    dates = _window_dates(shadow_rows)
    book, recs, seen = _run_shadow(ss.lineage_d_strategy(), shadow_rows, dates)

    # identical INPUTS to the accepted decision function, step by step
    assert [c[0] for c in calls] == [s[0] for s in seen] == list(range(len(dates)))
    for (st, h_acc, _), (st2, h_shadow) in zip(calls, seen):
        assert h_acc == h_shadow, f"history differs at step {st}"
    # identical OUTPUTS (exact, not approx) at every step incl. the None/hold steps
    rebalances = 0
    for (st, _, out), rec in zip(calls, recs):
        expected = None if out is None else {s: v for s, v in out.items() if s in p.UNIVERSE}
        if rec["halted_now"]:
            continue
        assert rec["new_target"] == expected, f"target differs at step {st}"
        rebalances += expected is not None
    assert rebalances >= 15, "window should contain many rebalances"
    # identical STATE trajectory: portfolio accounting, order flow, halt, turnover
    pf = sc._load(book["portfolio"])
    assert pf.equity_cents({s: sc._cents([r_ for r_ in shadow_rows[s] if r_["timestamp"] == dates[-1]][0]["close"]) for s in p.UNIVERSE}) == acc["final_equity_cents"]
    assert book["order_count"] == acc["order_count"]
    assert pf.fill_count == acc["fill_count"]
    assert pf.halted == acc["halted"]
    assert pf.total_traded_notional_cents / START == acc["turnover"]
    assert book["step"] == acc["step_count"]


def test_champion_decisions_and_state_match_accepted_evaluator(harness, raw_rows, shadow_rows):
    champ = ss.champion_strategy()
    g = json.loads((HERE / "selected_development_bot.json").read_text())["selected_genome_definition"]
    acc, _ = _run_accepted(harness, raw_rows, harness.champion_decide_fn_factory(g), bounds=(champ.max_asset_weight, champ.max_total_exposure))
    dates = _window_dates(shadow_rows)
    book, recs, _ = _run_shadow(champ, shadow_rows, dates)
    pf = sc._load(book["portfolio"])
    last_close = {s: sc._cents([r_ for r_ in shadow_rows[s] if r_["timestamp"] == dates[-1]][0]["close"]) for s in p.UNIVERSE}
    assert pf.equity_cents(last_close) == acc["final_equity_cents"]
    assert book["order_count"] == acc["order_count"] and pf.fill_count == acc["fill_count"]
    assert sum(1 for x in recs if x["rebalanced"]) == len(range(0, len(dates), g["rebalance_every_n_sessions"]))


def test_identical_data_comparison_records_targets_turnover_exposure_and_differences(shadow_rows):
    strategies = ss.both()
    books = {n: sc.new_book(s, START) for n, s in strategies.items()}
    dates = _window_dates(shadow_rows)[:140]
    recs = sc.run_sessions(books, strategies, shadow_rows, dates)
    assert len(recs) == 140 and all(x["comparison"] for x in recs)
    first = recs[0]
    assert first["books"]["champion"]["new_target"] is not None and first["books"]["lineage_d"]["new_target"] is not None
    for x in recs:
        c = x["comparison"]
        for n in ("champion", "lineage_d"):
            b = x["books"][n]
            assert 0.0 <= b["standing_exposure"] <= strategies[n].max_total_exposure + 1e-9
            assert b["target_turnover_one_way"] >= 0.0
        assert {"l1_distance", "only_champion", "only_lineage_d", "both", "same_targets"} <= set(c)
    rebalances = [x for x in recs if x["books"]["lineage_d"]["rebalanced"]]
    assert [x["books"]["lineage_d"]["step"] for x in rebalances] == [0, 31, 62, 93, 124]


def test_resume_from_persisted_state_reproduces_an_uninterrupted_run(shadow_rows, tmp_path):
    dates = _window_dates(shadow_rows)[:120]
    s1 = ss.both()
    books = {n: sc.new_book(s, START) for n, s in s1.items()}
    sc.run_sessions(books, s1, shadow_rows, dates)

    store = sc.ShadowStore(tmp_path)
    s2 = ss.both()
    b2 = {n: store.load_book(s, START) for n, s in s2.items()}
    store.commit(b2, sc.run_sessions(b2, s2, shadow_rows, dates[:60]))
    s3 = ss.both()
    b3 = {n: store.load_book(s, START) for n, s in s3.items()}          # "restart"
    store.commit(b3, sc.run_sessions(b3, s3, shadow_rows, dates))  # re-offers all dates; first 60 are skipped
    assert b3 == books
    lines = (tmp_path / "sessions.jsonl").read_text().splitlines()
    assert len(lines) == 120 and len({json.loads(x)["date"] for x in lines}) == 120   # no duplicate records


def test_data_gap_stops_the_book_before_advancing(shadow_rows):
    strategies = ss.both()
    books = {n: sc.new_book(s, START) for n, s in strategies.items()}
    rows = {s: list(v) for s, v in shadow_rows.items()}
    dates = _window_dates(shadow_rows)[:5]
    rows["GLD"] = [x for x in rows["GLD"] if x["timestamp"] != dates[2]]
    with pytest.raises(sc.DataGap):
        sc.run_sessions(books, strategies, rows, dates)
    assert books["champion"]["last_session"] == dates[1]
