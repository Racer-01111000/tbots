"""Standalone 15/21-month replay harness for Sam vs. champion vs. passive
controls, July2025-Sep2026 and Jan2025-Sep2026 windows. Cites GO -
TBOTS_SAM_15_MONTH_REPLAY_AND_LIVE_SHADOW_20261001.

Reuses accepted primitives UNCHANGED, read-only: execution.Portfolio
(including its existing, previously-uncalled credit_dividend()),
risk.validate, s6a_runtime.decide_D/warmup/validate_genome,
control_agent.decide, indicators.*, lib.normalize.normalize_yahoo_chart,
lib.ids.genome_id, s6b_evaluator.episode_sharpe/risk_bounds/ANNUALIZATION.

Does NOT touch, modify, or bypass authorize_lane()/s5_boundary.py/the
sealed S5A-D manifest system. Builds its own independent, hashed,
range-bounded in-memory dataset and its own minimal AgentView-compatible
engine (same two-method contract as lib.replay.AgentView: observe(),
history()) -- this is the "directly constructed replay engine" the GO
authorizes for this isolated experiment, never the production entrypoint.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots")
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import control_agent  # noqa: E402
import execution  # noqa: E402
import indicators  # noqa: E402
import risk  # noqa: E402
import s6a_final as p  # noqa: E402
import s6a_runtime as r  # noqa: E402
import s6b_evaluator as s6b  # noqa: E402
from lib.ids import genome_id  # noqa: E402
from lib.normalize import normalize_yahoo_chart  # noqa: E402

UNIVERSE = p.UNIVERSE
DRAWDOWN_HALT = p.DRAWDOWN_HALT
ALLOWED_ASSET_FIELDS = ("open", "high", "low", "close", "adjusted_close", "volume", "corporate_action")

SAM_GENOME_ID = "gen_8e7a622b48276bc68b7b70773bb9e2a5250dbb82584c53f6c67416bd6ffdf0cf"
CHAMPION_GENOME_ID = "gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155"

OUT_DIR = Path("/home/ec2-user/sam_15_month_replay_20261001")
DATA_DIR = OUT_DIR / "data_v1"
RAW_GAP_DIR = OUT_DIR / "raw_gap_fetch"


def load_sam_genome() -> dict:
    f = REPO / "evolution/s6b_runs/primary/D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca/development_top_eight.json"
    data = json.loads(f.read_text())
    entry = next(e for e in data["frozen"] if e["genome_id"] == SAM_GENOME_ID)
    recomputed = genome_id(entry["genome"])
    assert recomputed == SAM_GENOME_ID, f"SAM MISMATCH: {recomputed}"
    return entry["genome"]


def load_champion_genome() -> dict:
    f = REPO / "evolution/protocol/frozen_champion_s5d_champion_00ac1019646747ad88b7eac1955dc1067f598f53e77cce01c07de7603dab2672.json"
    data = json.loads(f.read_text())
    champ = data["content"]["champion"]
    recomputed = genome_id(champ["genome"])
    assert recomputed == CHAMPION_GENOME_ID, f"CHAMPION MISMATCH: {recomputed}"
    return champ["genome"]


def fetch_gap(symbol: str, start_date: str, end_date: str) -> bytes:
    p1 = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    p2 = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 86400
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={p1}&period2={p2}&interval=1d&events=div,splits"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read(), url


def build_dataset(gap_start: str, gap_end: str) -> dict:
    """Returns {symbol: [normalized rows sorted by timestamp]} spanning the
    full accepted history plus the newly-fetched gap, and writes the
    frozen/hashed artifacts to DATA_DIR."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_GAP_DIR.mkdir(parents=True, exist_ok=True)
    ingested_at = datetime.now(timezone.utc).isoformat()
    manifest = {"normalization_version": "v1", "ingested_at": ingested_at, "symbols": {}}
    merged = {}

    for symbol in UNIVERSE:
        existing_raw = (REPO / "data/raw" / f"{symbol}.json").read_bytes()
        existing_rows, existing_report = normalize_yahoo_chart(
            existing_raw, symbol, "yahoo_chart_v8_accepted",
            f"accepted:data/raw/{symbol}.json", ingested_at,
        )

        gap_raw, gap_url = fetch_gap(symbol, gap_start, gap_end)
        (RAW_GAP_DIR / f"{symbol}_gap_raw.json").write_bytes(gap_raw)
        gap_rows, gap_report = normalize_yahoo_chart(
            gap_raw, symbol, "yahoo_chart_v8_gapfill_20261001", gap_url, ingested_at,
        )

        existing_by_date = {row["timestamp"]: row for row in existing_rows}
        mismatches = []
        new_rows_only = []
        for row in gap_rows:
            prior = existing_by_date.get(row["timestamp"])
            if prior is not None:
                for field in ("open", "high", "low", "close", "adjusted_close"):
                    if prior[field] != row[field]:
                        mismatches.append({"date": row["timestamp"], "field": field,
                                            "existing": prior[field], "gapfill": row[field]})
            else:
                new_rows_only.append(row)

        combined = sorted(existing_rows + new_rows_only, key=lambda row: row["timestamp"])
        merged[symbol] = combined

        blob = json.dumps(combined, sort_keys=True).encode("utf-8")
        sha = hashlib.sha256(blob).hexdigest()
        (DATA_DIR / f"{symbol}_normalized_v1.json").write_bytes(blob)

        manifest["symbols"][symbol] = {
            "existing_rows": len(existing_rows),
            "gap_rows_fetched": len(gap_rows),
            "new_rows_appended": len(new_rows_only),
            "overlap_mismatches": mismatches,
            "combined_rows": len(combined),
            "combined_start": combined[0]["timestamp"],
            "combined_end": combined[-1]["timestamp"],
            "normalized_sha256": sha,
            "gap_fetch_url": gap_url,
            "existing_normalize_report": existing_report,
            "gap_normalize_report": gap_report,
        }

    manifest["dataset_revision"] = hashlib.sha256(
        json.dumps({s: v["normalized_sha256"] for s, v in manifest["symbols"].items()}, sort_keys=True).encode()
    ).hexdigest()
    (DATA_DIR / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    return merged, manifest


def _asset_fields(row):
    if row is None:
        return {"available": False}
    out = {"available": True}
    for f in ALLOWED_ASSET_FIELDS:
        out[f] = row[f]
    return out


class MiniEngine:
    def __init__(self, per_symbol_rows: dict, window_start: str, window_end: str):
        self.per_symbol_rows = per_symbol_rows
        self.timestamps = {s: [row["timestamp"] for row in rows] for s, rows in per_symbol_rows.items()}
        base = self.timestamps[UNIVERSE[0]]
        self.window_dates = [d for d in base if window_start <= d <= window_end]
        for s in UNIVERSE[1:]:
            if [d for d in self.timestamps[s] if window_start <= d <= window_end] != self.window_dates:
                raise AssertionError(f"calendar mismatch for {s} vs {UNIVERSE[0]} in window")
        self.step_count = len(self.window_dates)
        self.current_index = 0

    @property
    def current_timestamp(self):
        return self.window_dates[self.current_index]

    def observe(self):
        clock = self.current_timestamp
        assets = {}
        for symbol in UNIVERSE:
            idx = self.timestamps[symbol]
            pos = bisect.bisect_left(idx, clock)
            row = self.per_symbol_rows[symbol][pos] if pos < len(idx) and idx[pos] == clock else None
            assets[symbol] = _asset_fields(row)
        return {"assets": assets, "timestamp": clock}

    def history(self, symbol, bars):
        if bars <= 0:
            return []
        clock = self.current_timestamp
        idx = self.timestamps[symbol]
        rows = self.per_symbol_rows[symbol]
        cut = bisect.bisect_right(idx, clock)
        if cut == 0:
            return []
        start = max(0, cut - bars)
        out = []
        for row in rows[start:cut]:
            entry = {f: row[f] for f in ALLOWED_ASSET_FIELDS}
            entry["available"] = True
            entry["timestamp"] = row["timestamp"]
            out.append(entry)
        return out

    def advance(self):
        self.current_index += 1
        return self.current_index < self.step_count


class View:
    def __init__(self, engine):
        self._engine = engine

    def observe(self):
        full = self._engine.observe()
        return {"assets": full["assets"], "timestamp": full["timestamp"]}

    def history(self, symbol, bars):
        return self._engine.history(symbol, bars)


def dollars_to_cents(s) -> int:
    return round(float(s) * 100)


def episode_sortino(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    downside = [min(0.0, x) for x in daily_returns]
    downside_dev = math.sqrt(sum(d * d for d in downside) / len(daily_returns))
    if downside_dev == 0:
        return 0.0
    return statistics.mean(daily_returns) / downside_dev * math.sqrt(s6b.ANNUALIZATION)


def apply_ceiling(weights, ceiling):
    if weights is None:
        return None
    total = sum(weights.values())
    if total <= ceiling + 1e-9 or total <= 0:
        return weights
    scale = ceiling / total
    return {s: w * scale for s, w in weights.items()}


def make_passive_decide_fn(total_exposure: float, rebalance_sessions: int = 31):
    per_asset = total_exposure / len(UNIVERSE)

    def fn(view, step):
        if step % rebalance_sessions != 0:
            return None
        return {s: per_asset for s in UNIVERSE}

    return fn


def _finalize(equity_curve, portfolio, order_count, total_slippage_cents, step,
              starting_cash_cents, start_date, end_date, extra):
    daily_returns = [
        equity_curve[i] / equity_curve[i - 1] - 1.0 if equity_curve[i - 1] else 0.0
        for i in range(1, len(equity_curve))
    ]
    final_equity_cents = equity_curve[-1]
    max_drawdown = min(
        equity / max(equity_curve[: index + 1]) - 1.0
        for index, equity in enumerate(equity_curve)
    )
    transaction_cost_cents = portfolio.total_commission_cents + total_slippage_cents
    out = {
        "start_date": start_date, "end_date": end_date,
        "starting_cash_cents": starting_cash_cents,
        "final_equity_cents": final_equity_cents,
        "total_return": final_equity_cents / starting_cash_cents - 1.0,
        "sharpe": s6b.episode_sharpe(daily_returns),
        "sortino": episode_sortino(daily_returns),
        "max_drawdown": max_drawdown,
        "halted": portfolio.halted,
        "turnover": portfolio.total_traded_notional_cents / starting_cash_cents,
        "commission_cents": portfolio.total_commission_cents,
        "slippage_cents": total_slippage_cents,
        "transaction_cost_cents": transaction_cost_cents,
        "transaction_cost_rate": transaction_cost_cents / starting_cash_cents,
        "dividend_cents": portfolio.total_dividend_cents,
        "order_count": order_count,
        "fill_count": portfolio.fill_count,
        "step_count": step,
        "ever_held_position": portfolio.fill_count > 0,
        "monthly_returns": _monthly_returns(equity_curve, extra.pop("dates", [])),
    }
    out.update(extra)
    return out


def _monthly_returns(equity_curve, dates):
    if not dates or len(dates) != len(equity_curve):
        return {}
    months = {}
    for date, eq in zip(dates, equity_curve):
        ym = date[:7]
        months.setdefault(ym, []).append(eq)
    out = {}
    prev_end = None
    for ym in sorted(months):
        vals = months[ym]
        start_eq = prev_end if prev_end is not None else vals[0]
        end_eq = vals[-1]
        out[ym] = end_eq / start_eq - 1.0 if start_eq else 0.0
        prev_end = end_eq
    return out


def simulate(decide_fn, *, per_symbol_rows, window_start, window_end,
             max_asset_weight, max_total_exposure, starting_cash_cents,
             slippage_bps, needs_history=False, genome_for_warmup=None,
             use_decide_D=False, genome_for_D=None, ceiling=None) -> dict:
    orig_slippage = execution.SLIPPAGE_BPS
    execution.SLIPPAGE_BPS = slippage_bps
    try:
        engine = MiniEngine(per_symbol_rows, window_start, window_end)
        view = View(engine)
        portfolio = execution.Portfolio(starting_cash_cents)
        pending = None
        equity_curve = []
        dates = []
        order_count = 0
        total_slippage_cents = 0
        step = 0

        h = None
        if use_decide_D:
            needed = r.warmup("D", genome_for_D)
            h = {s: [float(row["adjusted_close"]) for row in view.history(s, needed)] for s in UNIVERSE} \
                if needed > 0 else {s: [] for s in UNIVERSE}

        while True:
            obs = view.observe()
            assets = obs["assets"]
            today = obs["timestamp"]

            if use_decide_D and step > 0:
                for s in UNIVERSE:
                    h[s].append(float(assets[s]["adjusted_close"]))

            for s in UNIVERSE:
                div = execution.dividend_amount_dollars(assets[s].get("corporate_action"))
                if div is not None:
                    portfolio.credit_dividend(s, div)

            if pending is not None:
                for order in pending:
                    open_cents = dollars_to_cents(assets[order["symbol"]]["open"])
                    fill = portfolio.apply_fill(order["symbol"], order["side"], order["shares"], open_cents)
                    total_slippage_cents += fill["slippage_cents"]
                pending = None

            mark_prices = {s: dollars_to_cents(assets[s]["close"]) for s in UNIVERSE}
            equity_cents = portfolio.equity_cents(mark_prices)
            drawdown = portfolio.update_peak_and_drawdown(equity_cents)

            target_weights = None
            if drawdown <= -DRAWDOWN_HALT - 1e-9 and not portfolio.halted:
                portfolio.halted = True
                target_weights = {}
            elif not portfolio.halted:
                if use_decide_D:
                    target_weights = r.decide_D(genome_for_D, h, step)
                else:
                    target_weights = decide_fn(view, step)
                if target_weights is not None:
                    target_weights = {s: w for s, w in target_weights.items() if s in UNIVERSE}
                if ceiling is not None:
                    target_weights = apply_ceiling(target_weights, ceiling)

            if target_weights is not None:
                risk.validate(
                    target_weights, UNIVERSE, max_asset_weight=max_asset_weight,
                    max_total_exposure=max_total_exposure,
                    drawdown_halt_pct=DRAWDOWN_HALT, current_drawdown=drawdown,
                )
                current_shares = {s: portfolio.shares_of(s) for s in UNIVERSE if portfolio.shares_of(s) > 0}
                pending = execution.compute_orders(target_weights, UNIVERSE, equity_cents, mark_prices, current_shares)
                order_count += len(pending)

            equity_curve.append(equity_cents)
            dates.append(today)
            step += 1
            if not engine.advance():
                break

        return _finalize(equity_curve, portfolio, order_count, total_slippage_cents, step,
                          starting_cash_cents, window_start, window_end, {"dates": dates, "slippage_bps_used": slippage_bps})
    finally:
        execution.SLIPPAGE_BPS = orig_slippage


def champion_decide_fn_factory(genome: dict):
    def fn(view, step):
        if step % genome["rebalance_every_n_sessions"] != 0:
            return None
        return control_agent.decide(view, genome)["weights"]
    return fn


def no_trade_decide_fn(view, step):
    return None


def main():
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    sam_genome = load_sam_genome()
    champion_genome = load_champion_genome()
    print("Identity verified: Sam + champion both match exactly.", flush=True)

    merged, data_manifest = build_dataset("2026-08-25", "2026-09-30")
    print(f"Dataset built. dataset_revision={data_manifest['dataset_revision']}", flush=True)
    for s, info in data_manifest["symbols"].items():
        print(f"  {s}: existing={info['existing_rows']} +new={info['new_rows_appended']} "
              f"mismatches={len(info['overlap_mismatches'])} end={info['combined_end']}", flush=True)

    windows = {
        "primary_15mo_2025-07-01_to_2026-09-30": ("2025-07-01", "2026-09-30"),
        "supplementary_21mo_2025-01-01_to_2026-09-30": ("2025-01-01", "2026-09-30"),
    }
    cost_cases = {"slippage_5bps": 5, "slippage_10bps_predeclared": 10}
    STARTING_CASH_CENTS = 100_000_000

    sam_max_aw, sam_max_te = s6b.risk_bounds("D", sam_genome)

    results = {}
    for window_name, (w_start, w_end) in windows.items():
        results[window_name] = {}
        for cost_name, slip_bps in cost_cases.items():
            arms = {}

            arms["sam_native_23pct"] = simulate(
                None, per_symbol_rows=merged, window_start=w_start, window_end=w_end,
                max_asset_weight=sam_max_aw, max_total_exposure=sam_max_te,
                starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slip_bps,
                use_decide_D=True, genome_for_D=sam_genome,
            )
            arms["passive_control_23pct_matched"] = simulate(
                make_passive_decide_fn(0.23), per_symbol_rows=merged, window_start=w_start, window_end=w_end,
                max_asset_weight=0.23 / len(UNIVERSE) + 1e-6, max_total_exposure=0.23,
                starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slip_bps,
            )
            arms["champion_native"] = simulate(
                champion_decide_fn_factory(champion_genome), per_symbol_rows=merged,
                window_start=w_start, window_end=w_end,
                max_asset_weight=champion_genome["max_asset_weight"],
                max_total_exposure=champion_genome["target_max_exposure"],
                starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slip_bps,
            )
            arms["passive_control_18pct_matched"] = simulate(
                make_passive_decide_fn(0.18), per_symbol_rows=merged, window_start=w_start, window_end=w_end,
                max_asset_weight=0.18 / len(UNIVERSE) + 1e-6, max_total_exposure=0.18,
                starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slip_bps,
            )
            arms["sam_18pct_ceiling_overlay"] = simulate(
                None, per_symbol_rows=merged, window_start=w_start, window_end=w_end,
                max_asset_weight=0.18, max_total_exposure=0.18,
                starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slip_bps,
                use_decide_D=True, genome_for_D=sam_genome, ceiling=0.18,
            )
            arms["champion_18pct_ceiling_overlay"] = simulate(
                champion_decide_fn_factory(champion_genome), per_symbol_rows=merged,
                window_start=w_start, window_end=w_end,
                max_asset_weight=0.18, max_total_exposure=0.18,
                starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slip_bps, ceiling=0.18,
            )
            arms["no_trade_control"] = simulate(
                no_trade_decide_fn, per_symbol_rows=merged, window_start=w_start, window_end=w_end,
                max_asset_weight=1.0, max_total_exposure=1.0,
                starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slip_bps,
            )

            results[window_name][cost_name] = arms
            print(f"{window_name} / {cost_name}: "
                  f"sam={arms['sam_native_23pct']['total_return']:.4%} "
                  f"passive23={arms['passive_control_23pct_matched']['total_return']:.4%} "
                  f"champion={arms['champion_native']['total_return']:.4%}", flush=True)

    out_path = OUT_DIR / "replay_results.json"
    out_path.write_text(json.dumps({
        "go": "TBOTS_SAM_15_MONTH_REPLAY_AND_LIVE_SHADOW_20261001",
        "dataset_revision": data_manifest["dataset_revision"],
        "sam_genome_id": SAM_GENOME_ID,
        "champion_genome_id": CHAMPION_GENOME_ID,
        "rebalance_anchor": "window's first trading day (step 0)",
        "passive_rebalance_sessions": 31,
        "windows": results,
    }, indent=2, default=str))
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)", flush=True)
    print(f"Total wall time: {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
