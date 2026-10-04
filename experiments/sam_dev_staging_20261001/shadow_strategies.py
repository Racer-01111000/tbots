"""The two shadow strategies, both calling ACCEPTED decision code unmodified."""
from __future__ import annotations

import json
from pathlib import Path

import control_agent
import lineage_d
from lib.ids import genome_id
from shadow_compare import Strategy, _HistView

HERE = Path(__file__).resolve().parent


def champion_strategy() -> Strategy:
    sel = json.loads((HERE / "selected_development_bot.json").read_text())
    g = sel["selected_genome_definition"]
    gid = genome_id(g)
    if gid != sel["selected_genome_id"]:
        raise RuntimeError("champion genome hash mismatch")
    n = g["rebalance_every_n_sessions"]

    def decide(adj_hist, step):
        # champion_decide_fn_factory semantics: decide only when step % N == 0
        if step % n != 0:
            return None
        return control_agent.decide(_HistView(adj_hist), g)["weights"]

    return Strategy("champion", gid, decide, g["max_asset_weight"], g["target_max_exposure"], n)


def lineage_d_strategy() -> Strategy:
    g = lineage_d.load_verified_genome()
    mw, mx = lineage_d.risk_bounds(g)
    return Strategy("lineage_d", lineage_d.EXPECTED_ID,
                    lambda adj_hist, step: lineage_d.decide(g, adj_hist, step), mw, mx, g["rebalance_sessions"])


def both() -> dict:
    return {"champion": champion_strategy(), "lineage_d": lineage_d_strategy()}
