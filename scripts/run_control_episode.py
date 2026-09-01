#!/usr/bin/env python3
"""S4 orchestration: runs the control organism across a real episode
through the full chain (observation -> indicators -> eligibility ->
ranking -> sizing -> risk gate -> accounting -> audit), with DB
integration (S0's experiments/episodes/decisions/orders/fills tables)
and an independent-verifier cross-check on every rebalance decision.

State machine, one iteration per replay step T:
  1. execute any order decided at T-1, using T's raw OPEN (never the
     close a decision was made from -- S4.8's no-same-bar rule)
  2. mark equity/drawdown using T's raw CLOSE
  3. if drawdown crosses the halt threshold and not already halted:
     halt, and target a full liquidation (decided now, filled at T+1)
  4. else if not halted and this is a rebalance step (step_index % 21
     == 0): run the control agent AND the independent verifier against
     the same observation, require agreement, pass the result through
     the hard risk gate, then set the target (decided now, filled at
     T+1)
  5. record the equity-curve row and advance
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from lib import models
from lib.db import connect
from lib.gitrev import get_code_revision
from lib.replay import AgentView, ReplayEngine

import control_agent
import execution
import risk
import verifier
from genome_control import CONTROL_GENOME

ROOT = Path(__file__).resolve().parents[1]
STARTING_CASH_CENTS = 100_000_000  # $1,000,000.00


class VerifierDisagreement(RuntimeError):
    """A control/verifier mismatch that must fail the experiment closed."""

    def __init__(self, outcome: dict):
        super().__init__("independent verifier disagreed with control decision")
        self.outcomes = [outcome]


def dollars_to_cents(s: str) -> int:
    return round(float(s) * 100)


def ensure_control_agent(conn) -> tuple[str, str]:
    genome_id = models.create_genome(conn, CONTROL_GENOME)
    row = conn.execute(
        "SELECT agent_id FROM agents WHERE genome_id = ? ORDER BY created_at LIMIT 1", (genome_id,)
    ).fetchone()
    if row:
        return row["agent_id"], genome_id
    agent_id = models.create_agent(conn, genome_id, generation=0)
    conn.commit()
    return agent_id, genome_id


def _execute_control_episode(conn, dataset_root, dataset_revision: str, start_date: str,
                             end_date: str, *, experiment_id: str, agent_id: str,
                             genome_id: str, masked_time: bool = False,
                             write_equity_curve: bool = True,
                             data_access_end: str | None = None) -> dict:
    engine = ReplayEngine(dataset_root, dataset_revision, start_date, end_date,
                          masked_time=masked_time, retention_end_date=data_access_end)
    view = AgentView(engine)
    universe = CONTROL_GENOME["universe"]

    episode_id = models.create_episode(
        conn, experiment_id, agent_id, dataset_revision=dataset_revision,
        start_ts=start_date, end_ts=end_date, masked_time=masked_time, random_seed=0,
        label="S4 control organism episode",
    )
    conn.execute("UPDATE episodes SET status = 'RUNNING' WHERE episode_id = ?", (episode_id,))
    conn.commit()

    portfolio = execution.Portfolio(STARTING_CASH_CENTS)
    pending = None  # {"orders": [...], "sizing_note": ...} decided at T-1, filled this step
    rebalance_seq = 0
    verifier_disagreements = []
    rebalance_log = []
    equity_curve = []
    fill_log = []
    step = 0

    while True:
        full = engine.observe()
        obs = view.observe()
        assets = obs["assets"]

        # 1. execute pending order from the previous decision, at THIS step's open
        if pending is not None:
            for order in pending["orders"]:
                sym = order["symbol"]
                open_cents = dollars_to_cents(assets[sym]["open"])
                fill = portfolio.apply_fill(sym, order["side"], order["shares"], open_cents)
                fill["order_db_id"] = order["order_db_id"]
                fill["true_ts"] = full["_true_timestamp"]
                fill_log.append(fill)
                fill_id = models.create_fill(
                    conn, order["order_db_id"], fill_ts=full["_true_timestamp"],
                    fill_price_cents=fill["fill_price_cents"], fill_quantity=fill["shares"],
                    commission_cents=fill["commission_cents"],
                    slippage_cents=fill["slippage_cents"],
                )
                conn.execute("UPDATE orders SET status = 'filled' WHERE order_id = ?",
                             (order["order_db_id"],))
            conn.commit()
            pending = None

        # 2. mark equity/drawdown using this step's raw close
        mark_prices = {sym: dollars_to_cents(assets[sym]["close"])
                        for sym in universe if assets[sym]["available"]}
        equity_cents = portfolio.equity_cents(mark_prices)
        drawdown = portfolio.update_peak_and_drawdown(equity_cents)

        decision_record = None
        # 3. drawdown halt (checked before any new rebalance this step)
        if drawdown <= -CONTROL_GENOME["drawdown_halt_pct"] - 1e-9 and not portfolio.halted:
            portfolio.halted = True
            target_weights = {}
            decision_record = {"kind": "halt_liquidation", "weights": target_weights}
        # 4. scheduled rebalance
        elif not portfolio.halted and step % CONTROL_GENOME["rebalance_every_n_sessions"] == 0:
            control_decision = control_agent.decide(view, CONTROL_GENOME)
            v_evals = verifier.evaluate_universe_v(view, CONTROL_GENOME)
            v_selected = verifier.rank_and_select_v(v_evals, CONTROL_GENOME["max_positions"])
            v_weights = verifier.size_positions_v(v_selected, v_evals, CONTROL_GENOME)
            disagreements = verifier.compare_decision(control_decision, v_evals, v_selected, v_weights)
            verifier_outcome = {
                "status": "disagreed" if disagreements else "agreed",
                "tolerance": verifier.TOLERANCE,
                "step": step,
                "true_ts": full["_true_timestamp"],
                "disagreements": disagreements,
            }
            if disagreements:
                verifier_disagreements.append(verifier_outcome)
                models.create_decision(
                    conn, episode_id, agent_id, simulated_ts=full["_true_timestamp"],
                    payload={"kind": "rebalance_blocked", "selected": control_decision["selected"],
                             "weights": control_decision["weights"], "verifier": verifier_outcome},
                )
                conn.commit()
                raise VerifierDisagreement(verifier_outcome)
            target_weights = control_decision["weights"]
            decision_record = {"kind": "rebalance", "selected": control_decision["selected"],
                                "weights": target_weights, "verifier": verifier_outcome}

        # risk gate + order construction for whatever was just decided
        if decision_record is not None:
            risk.validate(target_weights, universe, max_asset_weight=CONTROL_GENOME["max_asset_weight"],
                          max_total_exposure=CONTROL_GENOME["target_max_exposure"],
                          drawdown_halt_pct=CONTROL_GENOME["drawdown_halt_pct"], current_drawdown=drawdown)

            sizing_prices = {sym: dollars_to_cents(assets[sym]["close"])
                              for sym in universe if assets[sym]["available"]}
            current_shares = {s: portfolio.shares_of(s) for s in universe if portfolio.shares_of(s) > 0}
            orders = execution.compute_orders(target_weights, universe, equity_cents,
                                               sizing_prices, current_shares)

            decision_id = models.create_decision(
                conn, episode_id, agent_id, simulated_ts=full["_true_timestamp"],
                payload=decision_record,
            )
            db_orders = []
            for o in orders:
                order_db_id = models.create_order(
                    conn, decision_id, episode_id, symbol=o["symbol"], side=o["side"],
                    quantity=o["shares"], order_type="market", submitted_ts=full["_true_timestamp"],
                )
                db_orders.append({**o, "order_db_id": order_db_id})
            conn.commit()

            rebalance_seq += 1
            rebalance_log.append({
                "rebalance_seq": rebalance_seq, "step": step, "true_ts": full["_true_timestamp"],
                "masked_day": full["episode_day_index"] if masked_time else None,
                "kind": decision_record["kind"], "weights": target_weights, "orders": orders,
            })
            pending = {"orders": db_orders}

        equity_curve.append({
            "step": step, "true_ts": full["_true_timestamp"],
            "masked_day": full["episode_day_index"] if masked_time else None,
            "cash_cents": portfolio.cash_cents, "equity_cents": equity_cents, "drawdown": drawdown,
            "halted": portfolio.halted, "positions": {s: p["shares"] for s, p in portfolio.positions.items()},
        })

        models.update_episode_progress(conn, episode_id, current_ts=full["_true_timestamp"],
                                        status="RUNNING")
        conn.commit()

        step += 1
        if not engine.advance():
            break

    models.update_episode_progress(conn, episode_id, current_ts=engine.current_timestamp,
                                    status=engine.status)
    conn.commit()

    final_equity_cents = equity_curve[-1]["equity_cents"]
    max_drawdown = min(row["drawdown"] for row in equity_curve)
    daily_returns = []
    for i in range(1, len(equity_curve)):
        prev, cur = equity_curve[i - 1]["equity_cents"], equity_curve[i]["equity_cents"]
        daily_returns.append(cur / prev - 1.0 if prev else 0.0)

    equity_curve_path = None
    if write_equity_curve:
        import csv as csv_module
        reports_dir = ROOT / "reports"
        reports_dir.mkdir(exist_ok=True)
        equity_curve_path = reports_dir / f"equity_curve_{episode_id}.csv"
        with open(equity_curve_path, "w", newline="") as f:
            writer = csv_module.writer(f)
            writer.writerow(["step", "true_ts", "masked_day", "cash_cents", "equity_cents",
                              "drawdown", "halted", "positions_json"])
            for row in equity_curve:
                writer.writerow([row["step"], row["true_ts"], row["masked_day"], row["cash_cents"],
                                  row["equity_cents"], f"{row['drawdown']:.8f}", row["halted"],
                                  json.dumps(row["positions"])])

    return {
        "episode_id": episode_id, "experiment_id": experiment_id, "genome_id": genome_id,
        "step_count": step, "final_status": "COMPLETED",
        "starting_cash_cents": STARTING_CASH_CENTS, "final_equity_cents": final_equity_cents,
        "total_return": final_equity_cents / STARTING_CASH_CENTS - 1.0,
        "max_drawdown": max_drawdown, "daily_returns": daily_returns,
        "rebalance_count": len(rebalance_log), "order_count": sum(len(r["orders"]) for r in rebalance_log),
        "fill_count": portfolio.fill_count, "total_commission_cents": portfolio.total_commission_cents,
        "total_traded_notional_cents": portfolio.total_traded_notional_cents,
        "realized_pl_cents": portfolio.realized_pl_cents, "halted": portfolio.halted,
        "verifier_disagreements": verifier_disagreements, "rebalance_log": rebalance_log,
        "equity_curve": equity_curve, "equity_curve_path": str(equity_curve_path) if equity_curve_path else None,
        "fill_log": fill_log,
    }


def _persisted_result(result: dict) -> dict:
    """Bounded terminal summary; large curves/logs remain in their dedicated records/artifact."""
    return {
        "episode_id": result["episode_id"],
        "status": "completed",
        "step_count": result["step_count"],
        "starting_cash_cents": result["starting_cash_cents"],
        "final_equity_cents": result["final_equity_cents"],
        "total_return": result["total_return"],
        "max_drawdown": result["max_drawdown"],
        "rebalance_count": result["rebalance_count"],
        "order_count": result["order_count"],
        "fill_count": result["fill_count"],
        "total_commission_cents": result["total_commission_cents"],
        "verifier": {
            "status": "agreed",
            "disagreement_count": len(result["verifier_disagreements"]),
        },
        "equity_curve_path": result["equity_curve_path"],
    }


def run_control_episode(dataset_root, dataset_revision: str, start_date: str, end_date: str, *,
                         masked_time: bool = False, db_path=None, write_equity_curve: bool = True,
                         verify_every_rebalance: bool = True,
                         data_access_end: str | None = None) -> dict:
    if not verify_every_rebalance:
        raise ValueError("independent verification cannot be disabled for a control experiment")

    conn = connect(db_path) if db_path else connect()
    experiment_id = None
    try:
        agent_id, genome_id = ensure_control_agent(conn)
        code_revision, code_dirty = get_code_revision(str(ROOT))
        experiment_id = models.create_experiment(
            conn, code_revision=code_revision, code_dirty=code_dirty,
            dataset_revision=dataset_revision, random_seed=0, agent_id=agent_id,
            genome_id_=genome_id,
            start_state={"starting_cash_cents": STARTING_CASH_CENTS},
            replay_window_start=start_date, replay_window_end=end_date,
            execution_assumptions={
                "sizing_price": "same-session raw close",
                "fill_price": "next-session raw open",
                "commission_bps": execution.COMMISSION_BPS,
                "slippage_bps": execution.SLIPPAGE_BPS,
            },
        )
        conn.commit()
        models.mark_experiment_running(conn, experiment_id)
        conn.commit()

        result = _execute_control_episode(
            conn, dataset_root, dataset_revision, start_date, end_date,
            experiment_id=experiment_id, agent_id=agent_id, genome_id=genome_id,
            masked_time=masked_time, write_equity_curve=write_equity_curve,
            data_access_end=data_access_end,
        )
        models.complete_experiment(conn, experiment_id, _persisted_result(result))
        conn.commit()
        return result
    except Exception as exc:
        conn.rollback()
        if experiment_id is not None:
            episode = conn.execute(
                "SELECT episode_id FROM episodes WHERE experiment_id = ? ORDER BY created_at DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
            if episode is not None:
                models.fail_incomplete_episode(conn, episode["episode_id"])
            models.fail_experiment(conn, experiment_id, {
                "status": "failed", "error_type": type(exc).__name__, "error": str(exc),
                "verifier": {
                    "status": "disagreed" if isinstance(exc, VerifierDisagreement) else "unknown",
                    "outcomes": getattr(exc, "outcomes", []),
                },
            })
            conn.commit()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dataset-revision", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--masked", action="store_true")
    args = p.parse_args()

    result = run_control_episode(ROOT, args.dataset_revision, args.start, args.end,
                                  masked_time=args.masked)
    print(f"episode={result['episode_id']} steps={result['step_count']} "
          f"rebalances={result['rebalance_count']} orders={result['order_count']} "
          f"fills={result['fill_count']}")
    print(f"final_equity_cents={result['final_equity_cents']} "
          f"total_return={result['total_return']:.4%} max_drawdown={result['max_drawdown']:.4%} "
          f"halted={result['halted']}")
    print(f"verifier disagreements: {len(result['verifier_disagreements'])}")
    print(f"equity curve: {result['equity_curve_path']}")
