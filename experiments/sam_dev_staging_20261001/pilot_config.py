"""Loader/validator for the paper-pilot configuration. A pilot config can
only TIGHTEN what the genome already allows; it can never loosen a limit,
and it can never enable submission (that stays a hardcoded constant in
alpaca_adapter plus an environment flag)."""
from __future__ import annotations

import json
from pathlib import Path


class PilotConfigError(RuntimeError):
    pass


REQUIRED = {
    "genome_id", "max_orders_per_session", "drawdown_halt_pct", "max_asset_weight",
    "gross_exposure_ceiling", "min_cash_reserve_pct", "time_in_force", "kill_switch_file",
    "expiry_date", "session_zero_date", "rebalance_every_n_sessions",
}


def load_pilot_config(path: Path, genome: dict, genome_id: str) -> dict:
    cfg = json.loads(Path(path).read_text())
    missing = REQUIRED - set(cfg)
    if missing:
        raise PilotConfigError(f"pilot config missing keys: {sorted(missing)}")
    if cfg["genome_id"] != genome_id:
        raise PilotConfigError(f"pilot config genome {cfg['genome_id']} != selected {genome_id}")
    if not (0 < cfg["drawdown_halt_pct"] <= genome["drawdown_halt_pct"]):
        raise PilotConfigError("drawdown_halt_pct must be in (0, genome's]")
    if not (0 < cfg["max_asset_weight"] <= genome["max_asset_weight"]):
        raise PilotConfigError("max_asset_weight must be in (0, genome's]")
    if not (0 < cfg["gross_exposure_ceiling"] <= genome["target_max_exposure"]):
        raise PilotConfigError("gross_exposure_ceiling must be in (0, genome's target_max_exposure]")
    if not (0 <= cfg["min_cash_reserve_pct"] < 1):
        raise PilotConfigError("min_cash_reserve_pct must be in [0,1)")
    if not (1 <= int(cfg["max_orders_per_session"]) <= len(genome["universe"])):
        raise PilotConfigError("max_orders_per_session must be in [1, universe size]")
    if cfg["time_in_force"] != "day":
        raise PilotConfigError("time_in_force must be 'day' (opg is rejected 09:28-19:00 ET)")
    if cfg["rebalance_every_n_sessions"] != genome["rebalance_every_n_sessions"]:
        raise PilotConfigError("rebalance_every_n_sessions must equal the genome's")
    return cfg
