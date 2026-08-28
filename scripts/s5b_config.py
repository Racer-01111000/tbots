"""Frozen, pre-data-access S5B qualification protocol."""
import hashlib
import json
from pathlib import Path

import execution
from lib.ids import canonical_json
import s5a_config

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_DIR = ROOT / "evolution" / "protocol"
LOCK_PATH = PROTOCOL_DIR / "s5b_preparation_lock.json"

ACCEPTED_S5A_REVISION = "45dccd8f0259268dddc74b3bcd461261ad85d668"
ACCEPTED_RUN_ID = "evo_cddf2362ef0d5f2844f3c747eb40454358301269c4789c752aa7c4d825f11971"
ACCEPTED_RUN_DIGEST = "15528d4e046a4c6a7cb3412f6634d705532f5dadb8f430cf18fb126e2c771788"
ACCEPTED_RESULT_SHA256 = "c9856a03d22352ef4dc53bc33f859096130e4e0121407c4c9027cb74ca7d62c2"
ACCEPTED_DB_SHA256 = "a7fcfd9cd99e37a3124929e54422afef1dae18b6d8026b10ef77c3e9dcb33f78"
ACCEPTED_RESULT_PATH = ROOT / "reports" / f"{ACCEPTED_RUN_ID}_result.json"
ACCEPTED_DB_PATH = ROOT / "db" / "evolutionary_markets.s5a-clean-reproduction-45dccd8.db"

DATASET_REVISION = s5a_config.DATASET_REVISION
QUALIFICATION_LANE_HASH = "lane_c226205dfa6ff0f5ccd96f26c49a5bb080853fcca5e7d46ef94099fba30da539"
QUALIFICATION_START = "2019-01-01"
QUALIFICATION_END = "2022-12-31"
CHAMPIONSHIP_START = "2023-01-01"
CHAMPIONSHIP_END = "2025-12-31"
FINAL_RESERVE_START = "2026-01-01"
STARTING_CASH_CENTS = 100_000_000

EPISODE_PROTOCOL = {
    "schema_version": 1,
    "name": "s5b_frozen_qualification_episodes_v1",
    "dataset_revision": DATASET_REVISION,
    "lane_manifest_hash": QUALIFICATION_LANE_HASH,
    "lane_start": QUALIFICATION_START,
    "lane_end": QUALIFICATION_END,
    "episode_rule": "four contiguous full calendar years",
    "episodes": [
        {"episode_index": 0, "start_date": "2019-01-01", "end_date": "2019-12-31"},
        {"episode_index": 1, "start_date": "2020-01-01", "end_date": "2020-12-31"},
        {"episode_index": 2, "start_date": "2021-01-01", "end_date": "2021-12-31"},
        {"episode_index": 3, "start_date": "2022-01-01", "end_date": "2022-12-31"},
    ],
    "starting_cash_cents": STARTING_CASH_CENTS,
    "fresh_state_each_episode": ["cash", "portfolio", "peak_equity", "drawdown", "halt"],
    "drawdown_halt_scope": "episode_only",
    "masked_time": True,
    "genome_source": {
        "run_id": ACCEPTED_RUN_ID,
        "result_sha256": ACCEPTED_RESULT_SHA256,
        "reproduction_db_sha256": ACCEPTED_DB_SHA256,
        "selection": "accepted_frozen_top10",
    },
    "evolutionary_feedback_allowed": False,
    "historical_s5a_performance_inputs_allowed": False,
}

SCORE_PROTOCOL = {
    "schema_version": 1,
    "name": "s5b_frozen_qualification_score_v1",
    "episode_protocol_name": EPISODE_PROTOCOL["name"],
    "complete_episode_count": 4,
    "metric_definitions_source": s5a_config.FITNESS_HASH,
    "episode_sharpe": s5a_config.FITNESS_PROTOCOL["episode_sharpe"],
    "episode_sortino_reporting_only": (
        "mean daily return / sample stdev of negative daily returns * sqrt(252); zero when fewer than 2 negative returns or downside stdev is zero"
    ),
    "daily_return_annualization": s5a_config.FITNESS_PROTOCOL["daily_return_annualization"],
    "return_dispersion": s5a_config.FITNESS_PROTOCOL["return_dispersion"],
    "consistency_score": s5a_config.FITNESS_PROTOCOL["consistency_score"],
    "worst_drawdown": s5a_config.FITNESS_PROTOCOL["worst_drawdown"],
    "halt_rate": s5a_config.FITNESS_PROTOCOL["halt_rate"],
    "turnover": s5a_config.FITNESS_PROTOCOL["turnover"],
    "transaction_cost_rate": s5a_config.FITNESS_PROTOCOL["transaction_cost_rate"],
    "performance_concentration": s5a_config.FITNESS_PROTOCOL["performance_concentration"],
    "formula": (
        "3.00*median_episode_return + 0.20*median_episode_sharpe "
        "- 2.00*abs(worst_drawdown) + 0.50*consistency_score "
        "- 0.25*halt_rate - 0.02*median_episode_turnover "
        "- 5.00*median_transaction_cost_rate - 0.25*performance_concentration"
    ),
    "weights": dict(s5a_config.FITNESS_PROTOCOL["weights"]),
    "score_round_decimal_places": 12,
    "ranking": {
        "primary": "qualification_score descending",
        "tie_break": "genome_id ascending",
        "ranking_begins_only_after_all_ten_complete": True,
    },
    "purpose": "examination_and_ranking_only",
    "evolutionary_feedback_allowed": False,
    "qualification_score_as_evolutionary_fitness": "PROHIBITED",
    "historical_s5a_performance_inputs_allowed": False,
}


class S5BProtocolError(ValueError):
    pass


def content_hash(prefix: str, content: dict) -> str:
    return prefix + hashlib.sha256(canonical_json(content).encode()).hexdigest()


EPISODE_HASH = content_hash("s5b_episode_", EPISODE_PROTOCOL)
SCORE_HASH = content_hash("s5b_score_", SCORE_PROTOCOL)
EPISODE_PATH = PROTOCOL_DIR / f"episode_manifest_{EPISODE_HASH}.json"
SCORE_PATH = PROTOCOL_DIR / f"score_formula_{SCORE_HASH}.json"


def envelope_bytes(content: dict, manifest_hash: str) -> bytes:
    return (json.dumps(
        {"content": content, "manifest_hash": manifest_hash},
        sort_keys=True, indent=2,
    ) + "\n").encode()


def validate_protocol_file(path: Path, content: dict, manifest_hash: str) -> None:
    try:
        envelope = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise S5BProtocolError(f"frozen protocol is unreadable: {path}") from exc
    if set(envelope) != {"content", "manifest_hash"}:
        raise S5BProtocolError("invalid frozen protocol envelope")
    prefix = manifest_hash.rsplit("_", 1)[0] + "_"
    if (envelope["content"] != content
            or envelope["manifest_hash"] != manifest_hash
            or content_hash(prefix, envelope["content"]) != manifest_hash
            or manifest_hash not in path.name):
        raise S5BProtocolError("frozen protocol content, hash, or filename changed")


def validate_protocols_on_disk() -> None:
    validate_protocol_file(EPISODE_PATH, EPISODE_PROTOCOL, EPISODE_HASH)
    validate_protocol_file(SCORE_PATH, SCORE_PROTOCOL, SCORE_HASH)


def load_preparation_lock() -> dict:
    validate_protocols_on_disk()
    try:
        envelope = json.loads(LOCK_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise S5BProtocolError("S5B preparation lock is unreadable") from exc
    if set(envelope) != {"content", "manifest_hash"}:
        raise S5BProtocolError("invalid S5B preparation lock envelope")
    content = envelope["content"]
    if envelope["manifest_hash"] != content_hash("s5b_prep_lock_", content):
        raise S5BProtocolError("S5B preparation lock content hash mismatch")
    required = {
        "dataset_revision": DATASET_REVISION,
        "qualification_lane_hash": QUALIFICATION_LANE_HASH,
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "accepted_run_id": ACCEPTED_RUN_ID,
        "accepted_result_sha256": ACCEPTED_RESULT_SHA256,
        "accepted_reproduction_db_sha256": ACCEPTED_DB_SHA256,
    }
    if any(content.get(key) != value for key, value in required.items()):
        raise S5BProtocolError("S5B preparation lock is not bound to accepted inputs")
    return content


def required_price_bars(genome: dict) -> int:
    s5a_config.validate_genome(genome)
    return max(
        genome["momentum_lookbacks"][2] + 1,
        genome["trend_filter_window"],
        genome["volatility_window"] + 1,
    )


def assert_frozen_execution_semantics(genome: dict) -> None:
    s5a_config.validate_genome(genome)
    immutable = s5a_config.MUTATION_PROTOCOL["immutable"]
    for name in ("universe", "direction", "leverage", "shorting", "drawdown_halt_pct"):
        if genome[name] != immutable[name]:
            raise S5BProtocolError(f"accepted immutable changed: {name}")
    if execution.COMMISSION_BPS != immutable["commission_bps"]:
        raise S5BProtocolError("commission model changed")
    if execution.SLIPPAGE_BPS != immutable["slippage_bps"]:
        raise S5BProtocolError("adverse slippage model changed")
