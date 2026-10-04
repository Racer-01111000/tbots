from pathlib import Path

from gym import engine as E, history as H, strategies as S, world as W


def test_historical_windows_are_coherent_worlds_inside_the_development_lane():
    ws = H.historical_windows(Path(__file__).resolve().parents[3] / "data" / "normalized")
    assert len(ws) == 6
    for w in ws:
        assert W.check_invariants(w) == [] and w.n_eval == 1260 and w.n_warmup == 400
        assert w.dates[0].isoformat() >= "2007-02-07" and w.dates[-1].isoformat() <= "2018-12-31"


def test_reference_strategies_run_cleanly_on_history():
    ws = H.historical_windows(Path(__file__).resolve().parents[3] / "data" / "normalized")
    refs = S.load_references()
    r = E.run(ws[0], S.strategy_for("sam", "D", refs["sam"]["genome"]), E.EngineParams(interest=False))
    assert r.ok and r.fills > 0
