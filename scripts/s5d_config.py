"""Frozen, pre-reserve S5D final examination protocols and identities."""
import hashlib
import json
from pathlib import Path

from lib.ids import canonical_json
import s5a_config
from s5b_config import required_price_bars
from s5c_config import assert_frozen_execution_semantics

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_DIR = ROOT / "evolution" / "protocol"
LOCK_PATH = PROTOCOL_DIR / "s5d_final_reserve_preparation_lock.json"

ACCEPTED_STARTING_REVISION = "08325c0e3fbd9dc2daa60c82f0604a58ea09389a"
ACCEPTED_CHAMPION_ID = (
    "gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155"
)
ACCEPTED_S5C_RUN_ID = (
    "s5c_c4f43cb6bad29918631189eadd456b1cd1ee8149437b55c432221ac4f18bde1d"
)
ACCEPTED_S5C_DIGEST = (
    "ecada3c0a7430515fe01e262d08d7c69454364b141826137d14d659fca50835e"
)
ACCEPTED_S5C_REPORT_SHA256 = (
    "974b911afcd8eed6d981ddf4179eac3244a8eed68f881f5deb529684ae5346c0"
)
ACCEPTED_S5C_REPORT_PATH = ROOT / "reports" / "s5c_championship_08325c0_result.json"
DATASET_REVISION = s5a_config.DATASET_REVISION
FINAL_RESERVE_START = "2026-01-01"
FINAL_RESERVE_END = "2026-08-25"
STARTING_CASH_CENTS = 100_000_000

EPISODE_PROTOCOL = {
    "schema_version": 1,
    "name": "s5d_frozen_final_reserve_episode_v1",
    "dataset_revision": DATASET_REVISION,
    "accepted_starting_revision": ACCEPTED_STARTING_REVISION,
    "accepted_champion_id": ACCEPTED_CHAMPION_ID,
    "accepted_s5c_run_id": ACCEPTED_S5C_RUN_ID,
    "accepted_s5c_deterministic_digest": ACCEPTED_S5C_DIGEST,
    "accepted_s5c_result_sha256": ACCEPTED_S5C_REPORT_SHA256,
    "lane_start": FINAL_RESERVE_START,
    "lane_end": FINAL_RESERVE_END,
    "episode_rule": "one contiguous final reserve episode",
    "episodes": [
        {
            "episode_index": 0,
            "start_date": FINAL_RESERVE_START,
            "end_date": FINAL_RESERVE_END,
        }
    ],
    "starting_cash_cents": STARTING_CASH_CENTS,
    "fresh_state": ["cash", "portfolio", "peak_equity", "drawdown", "halt"],
    "drawdown_halt_scope": "final_reserve_episode_only",
    "masked_time": True,
    "genome_source": "performance_free_frozen_champion_snapshot_only",
    "champion_mutation_allowed": False,
    "competing_genomes_allowed": False,
    "selection_or_optimization_allowed": False,
    "evolutionary_feedback_allowed": False,
    "historical_performance_inputs_allowed": False,
}

OUTCOME_PROTOCOL = {
    "schema_version": 1,
    "name": "s5d_frozen_final_reserve_reporting_v1",
    "episode_protocol_name": EPISODE_PROTOCOL["name"],
    "complete_episode_count": 1,
    "purpose": "reporting_and_validation_only",
    "acceptance_question": (
        "How did the frozen historical champion behave on the last untouched "
        "historical period?"
    ),
    "metric_definitions_source": s5a_config.FITNESS_HASH,
    "episode_sharpe": s5a_config.FITNESS_PROTOCOL["episode_sharpe"],
    "episode_sortino": (
        "mean daily return / sample stdev of negative daily returns * sqrt(252); "
        "zero when fewer than 2 negative returns or downside stdev is zero"
    ),
    "daily_return_annualization": s5a_config.FITNESS_PROTOCOL[
        "daily_return_annualization"
    ],
    "turnover": s5a_config.FITNESS_PROTOCOL["turnover"],
    "transaction_cost_rate": s5a_config.FITNESS_PROTOCOL[
        "transaction_cost_rate"
    ],
    "concentration": {
        "definition_source": s5a_config.FITNESS_PROTOCOL[
            "performance_concentration"
        ],
        "single_episode_value": 0.0,
        "reason": "frozen cross-episode definition returns zero for fewer than two episodes",
    },
    "required_outcome_fields": [
        "total_return",
        "sharpe",
        "sortino",
        "max_drawdown",
        "halted",
        "turnover",
        "commission_cents",
        "slippage_cents",
        "transaction_cost_cents",
        "transaction_cost_rate",
        "performance_concentration",
        "final_equity_cents",
    ],
    "fitness_score": "NOT_APPLICABLE",
    "minimum_return_hurdle": "NONE",
    "replacement_or_retraining_on_negative_return": "PROHIBITED",
    "ranking": "NOT_APPLICABLE_ONE_FROZEN_CHAMPION",
    "evolutionary_feedback_allowed": False,
    "historical_s5a_performance_inputs_allowed": False,
    "historical_s5b_performance_inputs_allowed": False,
    "historical_s5c_performance_inputs_allowed": False,
}


class S5DProtocolError(ValueError):
    pass


def content_hash(prefix: str, content: dict) -> str:
    return prefix + hashlib.sha256(canonical_json(content).encode()).hexdigest()


EPISODE_HASH = content_hash("s5d_final_reserve_episode_", EPISODE_PROTOCOL)
OUTCOME_HASH = content_hash("s5d_final_reserve_outcome_", OUTCOME_PROTOCOL)
EPISODE_PATH = PROTOCOL_DIR / f"episode_manifest_{EPISODE_HASH}.json"
OUTCOME_PATH = PROTOCOL_DIR / f"outcome_protocol_{OUTCOME_HASH}.json"


def envelope_bytes(content: dict, manifest_hash: str) -> bytes:
    return (
        json.dumps(
            {"content": content, "manifest_hash": manifest_hash},
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode()


def validate_protocol_file(
    path: Path, content: dict, manifest_hash: str, prefix: str
) -> None:
    try:
        envelope = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise S5DProtocolError(f"frozen S5D protocol is unreadable: {path}") from exc
    if set(envelope) != {"content", "manifest_hash"}:
        raise S5DProtocolError("invalid frozen S5D protocol envelope")
    if (
        envelope["content"] != content
        or envelope["manifest_hash"] != manifest_hash
        or content_hash(prefix, envelope["content"]) != manifest_hash
        or manifest_hash not in Path(path).name
    ):
        raise S5DProtocolError("frozen S5D protocol content, hash, or filename changed")


def validate_protocols_on_disk() -> None:
    validate_protocol_file(
        EPISODE_PATH,
        EPISODE_PROTOCOL,
        EPISODE_HASH,
        "s5d_final_reserve_episode_",
    )
    validate_protocol_file(
        OUTCOME_PATH,
        OUTCOME_PROTOCOL,
        OUTCOME_HASH,
        "s5d_final_reserve_outcome_",
    )


def load_preparation_lock() -> dict:
    validate_protocols_on_disk()
    try:
        envelope = json.loads(LOCK_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise S5DProtocolError("S5D preparation lock is unreadable") from exc
    if set(envelope) != {"content", "manifest_hash"}:
        raise S5DProtocolError("invalid S5D preparation lock envelope")
    content = envelope["content"]
    if envelope["manifest_hash"] != content_hash("s5d_prep_lock_", content):
        raise S5DProtocolError("S5D preparation lock content hash mismatch")
    required = {
        "dataset_revision": DATASET_REVISION,
        "champion_id": ACCEPTED_CHAMPION_ID,
        "accepted_s5c_run_id": ACCEPTED_S5C_RUN_ID,
        "accepted_s5c_deterministic_digest": ACCEPTED_S5C_DIGEST,
        "accepted_s5c_result_sha256": ACCEPTED_S5C_REPORT_SHA256,
        "episode_protocol_hash": EPISODE_HASH,
        "outcome_protocol_hash": OUTCOME_HASH,
    }
    if any(content.get(key) != value for key, value in required.items()):
        raise S5DProtocolError("S5D preparation lock is not bound to accepted inputs")
    return content
