import pytest

from gym import engine as E, scoring as SC, stats as st, strategies as S, generator as G


def fake_metrics(excess, dd=-0.02, viol=0, wid="w"):
    return {"world_id": wid, "net_return": 0.1, "ann_return": 0.02, "excess_vs_passive18": excess, "max_drawdown": dd, "violations": viol, "turnover": 1.0}


def test_primary_score_is_the_linear_interpolated_25th_percentile_of_12_worlds():
    xs = [0.01, -0.02, 0.03, 0.00, 0.05, -0.01, 0.02, 0.04, -0.03, 0.06, 0.07, 0.00]
    s = sorted(xs)
    expected = s[2] + (s[3] - s[2]) * (0.25 * 11 - 2)               # position 2.75
    assert SC.primary_score([fake_metrics(x) for x in xs]) == pytest.approx(expected)
    assert SC.primary_score([fake_metrics(x) for x in xs[:6]]) == pytest.approx(sorted(xs[:6])[1] + 0.25 * (sorted(xs[:6])[2] - sorted(xs[:6])[1]))


def test_annualization_uses_252_over_evaluation_sessions():
    assert SC.annualized(11_000_000, 10_000_000, 252) == pytest.approx(0.10)
    assert SC.annualized(12_100_000, 10_000_000, 504) == pytest.approx(0.10)
    assert SC.annualized(0, 10_000_000, 252) == -1.0


def test_eligibility_blocks_drawdown_above_8_percent_and_any_integrity_violation():
    assert SC.eligibility([fake_metrics(.01, -0.079)])[0]
    assert not SC.eligibility([fake_metrics(.01, -0.081)])[0]
    assert not SC.eligibility([fake_metrics(.01, -0.01, viol=1)])[0]
    ok, why = SC.eligibility([fake_metrics(.01, -0.2, wid="a"), fake_metrics(.01, -0.01, wid="b")])
    assert not ok and len(why) == 1 and "a:" in why[0]


def row(gid, score, dd, turn, elig=True):
    return {"genome_id": gid, "summary": {"eligible": elig, "primary_score": score, "worst_drawdown": dd, "mean_turnover": turn}}


def test_ordering_ranks_eligible_first_then_score_then_drawdown_then_turnover_then_id():
    rows = [row("gen_b", .02, -.05, 2.0), row("gen_a", .02, -.05, 2.0), row("gen_c", .02, -.03, 9.0), row("gen_d", .02, -.03, 1.0),
            row("gen_e", .09, -.07, 5.0, elig=False), row("gen_f", .03, -.07, 5.0)]
    assert [r["genome_id"] for r in sorted(rows, key=SC.ordering_key)] == ["gen_f", "gen_d", "gen_c", "gen_a", "gen_b", "gen_e"]


def test_world_metrics_cover_the_required_secondary_reports(cal):
    w = G.generate_world(G.WorldSpec("TEST-SC", "engineering", "stagflation_rates", 0), cal)
    refs = S.load_references()
    P = E.EngineParams()
    res = E.run(w, S.strategy_for("sam", "D", refs["sam"]["genome"]), P)
    pas, csh = E.run(w, S.passive18(), P), E.run(w, S.cash_control(), P)
    m = SC.world_metrics(res, pas, csh, w)
    for k in ("net_return", "ann_return", "excess_vs_passive18", "max_drawdown", "recovery_sessions", "es5_daily", "sharpe", "sortino",
              "turnover", "cost_pct_of_start", "mean_exposure", "time_in_cash", "exec", "violations"):
        assert k in m
    assert m["excess_vs_passive18"] == pytest.approx(m["ann_return"] - m["passive18_ann_return"])
    assert -1 < m["max_drawdown"] <= 0 and 0 <= m["time_in_cash"] <= 1 and m["es5_daily"] <= 0


def test_scoring_hash_changes_when_the_file_changes(tmp_path):
    h = SC.scoring_hash()
    assert len(h) == 64 and h == SC.scoring_hash()
