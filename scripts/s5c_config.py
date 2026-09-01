"""Frozen, pre-exposure S5C championship protocols and identities."""
import hashlib
import json
from pathlib import Path

import execution
from lib.ids import canonical_json
import s5a_config
from s5b_config import required_price_bars

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_DIR = ROOT / "evolution" / "protocol"
LOCK_PATH = PROTOCOL_DIR / "s5c_championship_preparation_lock.json"

ACCEPTED_STARTING_REVISION = "6283651f6332f36b5ae4f2e9e9b65edefb8a5bcc"
ADVANCEMENT_MANIFEST_HASH = "s5c_advancement_manifest_060da29f7b96405681da005983d70a267b24d51b31f200ce51e859ce97c4f7fa"
ADVANCEMENT_MANIFEST_PATH = PROTOCOL_DIR / f"advancement_manifest_{ADVANCEMENT_MANIFEST_HASH}.json"
ADVANCEMENT_PROTOCOL_HASH = "s5c_advancement_protocol_7ee46de082b7529832e10bc147192c4edacd5fca37dd31316e4dceee742df3f3"
FINALIST_IDS = (
    "gen_3e06a2be7814b193c9da1725ea559f8154bbce006a6ab227a72f18aa5c7eb30f",
    "gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155",
    "gen_a8a5c9f1078a8e446bd3c55b6dc5d201b016c78d7f67ac2e047c76355936bf70",
)
DATASET_REVISION = s5a_config.DATASET_REVISION
CHAMPIONSHIP_START = "2023-01-01"
CHAMPIONSHIP_END = "2025-12-31"
EXPECTED_FINAL_TRADING_SESSION = "2025-12-31"
FINAL_RESERVE_START = "2026-01-01"
STARTING_CASH_CENTS = 100_000_000

EPISODE_PROTOCOL = {
    "schema_version": 1,
    "name": "s5c_frozen_championship_episodes_v1",
    "dataset_revision": DATASET_REVISION,
    "accepted_starting_revision": ACCEPTED_STARTING_REVISION,
    "advancement_manifest_hash": ADVANCEMENT_MANIFEST_HASH,
    "advancement_protocol_hash": ADVANCEMENT_PROTOCOL_HASH,
    "finalist_ids": list(FINALIST_IDS),
    "lane_start": CHAMPIONSHIP_START,
    "lane_end": CHAMPIONSHIP_END,
    "final_trading_session": EXPECTED_FINAL_TRADING_SESSION,
    "episode_rule": "three independent full calendar years",
    "episodes": [
        {"episode_index": 0, "start_date": "2023-01-01", "end_date": "2023-12-31"},
        {"episode_index": 1, "start_date": "2024-01-01", "end_date": "2024-12-31"},
        {"episode_index": 2, "start_date": "2025-01-01", "end_date": "2025-12-31"},
    ],
    "starting_cash_cents": STARTING_CASH_CENTS,
    "fresh_state_each_episode": ["cash", "portfolio", "peak_equity", "drawdown", "halt"],
    "drawdown_halt_scope": "episode_only",
    "masked_time": True,
    "genome_source": "accepted_s5c_advancement_manifest_only",
    "genome_mutation_allowed": False,
    "evolutionary_feedback_allowed": False,
    "historical_s5a_performance_inputs_allowed": False,
    "historical_s5b_performance_inputs_allowed": False,
    "final_reserve_information_allowed": False,
}

SCORE_PROTOCOL = {
    "schema_version": 1,
    "name": "s5c_frozen_championship_score_and_winner_v1",
    "episode_protocol_name": EPISODE_PROTOCOL["name"],
    "complete_episode_count": 3,
    "complete_finalist_count": 3,
    "metric_definitions_source": s5a_config.FITNESS_HASH,
    "episode_sharpe": s5a_config.FITNESS_PROTOCOL["episode_sharpe"],
    "episode_sortino_reporting_only": "mean daily return / sample stdev of negative daily returns * sqrt(252); zero when fewer than 2 negative returns or downside stdev is zero",
    "daily_return_annualization": s5a_config.FITNESS_PROTOCOL["daily_return_annualization"],
    "return_dispersion": s5a_config.FITNESS_PROTOCOL["return_dispersion"],
    "consistency_score": s5a_config.FITNESS_PROTOCOL["consistency_score"],
    "worst_drawdown": s5a_config.FITNESS_PROTOCOL["worst_drawdown"],
    "halt_rate": s5a_config.FITNESS_PROTOCOL["halt_rate"],
    "turnover": s5a_config.FITNESS_PROTOCOL["turnover"],
    "transaction_cost_rate": s5a_config.FITNESS_PROTOCOL["transaction_cost_rate"],
    "performance_concentration": s5a_config.FITNESS_PROTOCOL["performance_concentration"],
    "formula": "3.00*median_episode_return + 0.20*median_episode_sharpe - 2.00*abs(worst_drawdown) + 0.50*consistency_score - 0.25*halt_rate - 0.02*median_episode_turnover - 5.00*median_transaction_cost_rate - 0.25*performance_concentration",
    "weights": dict(s5a_config.FITNESS_PROTOCOL["weights"]),
    "score_round_decimal_places": 12,
    "ranking": {
        "primary": "championship_score descending",
        "tie_break": "genome_id ascending",
        "ranking_begins_only_after_all_three_complete_all_three_episodes": True,
    },
    "winner_rule": {
        "winner": "rank_1_after_all_three_finalists_complete_all_three_episodes",
        "discretionary_override_allowed": False,
        "post_hoc_weighting_allowed": False,
        "bad_episode_dropping_allowed": False,
        "final_reserve_information_allowed": False,
    },
    "purpose": "championship_examination_and_winner_selection_only",
    "evolutionary_feedback_allowed": False,
    "championship_score_as_evolutionary_fitness": "PROHIBITED",
    "historical_s5a_performance_inputs_allowed": False,
    "historical_s5b_performance_inputs_allowed": False,
}


class S5CProtocolError(ValueError):
    pass


def content_hash(prefix: str, content: dict) -> str:
    return prefix + hashlib.sha256(canonical_json(content).encode()).hexdigest()


EPISODE_HASH = content_hash("s5c_championship_episode_", EPISODE_PROTOCOL)
SCORE_HASH = content_hash("s5c_championship_score_", SCORE_PROTOCOL)
EPISODE_PATH = PROTOCOL_DIR / f"episode_manifest_{EPISODE_HASH}.json"
SCORE_PATH = PROTOCOL_DIR / f"score_formula_{SCORE_HASH}.json"


def envelope_bytes(content: dict, manifest_hash: str) -> bytes:
    return (json.dumps({"content": content, "manifest_hash": manifest_hash}, sort_keys=True, indent=2) + "\n").encode()


def validate_protocol_file(path: Path, content: dict, manifest_hash: str, prefix: str) -> None:
    try:
        envelope = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise S5CProtocolError(f"frozen championship protocol is unreadable: {path}") from exc
    if set(envelope) != {"content", "manifest_hash"}:
        raise S5CProtocolError("invalid frozen championship protocol envelope")
    if (envelope["content"] != content or envelope["manifest_hash"] != manifest_hash
            or content_hash(prefix, envelope["content"]) != manifest_hash
            or manifest_hash not in Path(path).name):
        raise S5CProtocolError("frozen championship protocol content, hash, or filename changed")


def validate_protocols_on_disk() -> None:
    validate_protocol_file(EPISODE_PATH, EPISODE_PROTOCOL, EPISODE_HASH, "s5c_championship_episode_")
    validate_protocol_file(SCORE_PATH, SCORE_PROTOCOL, SCORE_HASH, "s5c_championship_score_")


def validate_advancement_envelope(envelope: dict) -> dict:
    if set(envelope) != {"content", "manifest_hash"}:
        raise S5CProtocolError("invalid S5C advancement envelope")
    content = envelope["content"]
    if (envelope["manifest_hash"] != ADVANCEMENT_MANIFEST_HASH
            or content_hash("s5c_advancement_manifest_", content) != ADVANCEMENT_MANIFEST_HASH
            or content.get("protocol_hash") != ADVANCEMENT_PROTOCOL_HASH
            or content.get("selection_complete") is not True
            or content.get("advancement_count") != 3):
        raise S5CProtocolError("accepted S5C advancement identity changed")
    selected = content.get("selected_finalists")
    if (not isinstance(selected, list) or len(selected) != 3
            or tuple(row.get("genome_id") for row in selected) != FINALIST_IDS
            or tuple(row.get("selection_order") for row in selected) != (1, 2, 3)):
        raise S5CProtocolError("accepted S5C finalist identities changed")
    for row in selected:
        if row["genome_id"] != "gen_" + row.get("genome_content_sha256", ""):
            raise S5CProtocolError("accepted finalist genome hash binding changed")
    return content


def load_advancement_manifest() -> dict:
    try:
        envelope = json.loads(ADVANCEMENT_MANIFEST_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise S5CProtocolError("accepted S5C advancement manifest is unreadable") from exc
    return validate_advancement_envelope(envelope)


def assert_frozen_execution_semantics(genome: dict) -> None:
    s5a_config.validate_genome(genome)
    immutable = s5a_config.MUTATION_PROTOCOL["immutable"]
    for name in ("universe", "direction", "leverage", "shorting", "drawdown_halt_pct"):
        if genome[name] != immutable[name]:
            raise S5CProtocolError(f"accepted immutable changed: {name}")
    if execution.COMMISSION_BPS != immutable["commission_bps"]:
        raise S5CProtocolError("commission model changed")
    if execution.SLIPPAGE_BPS != immutable["slippage_bps"]:
        raise S5CProtocolError("adverse slippage model changed")


def load_preparation_lock() -> dict:
    validate_protocols_on_disk()
    advancement = load_advancement_manifest()
    try:
        envelope = json.loads(LOCK_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise S5CProtocolError("S5C preparation lock is unreadable") from exc
    if set(envelope) != {"content", "manifest_hash"}:
        raise S5CProtocolError("invalid S5C preparation lock envelope")
    content = envelope["content"]
    if envelope["manifest_hash"] != content_hash("s5c_prep_lock_", content):
        raise S5CProtocolError("S5C preparation lock content hash mismatch")
    required = {
        "dataset_revision": DATASET_REVISION,
        "advancement_manifest_hash": ADVANCEMENT_MANIFEST_HASH,
        "advancement_protocol_hash": ADVANCEMENT_PROTOCOL_HASH,
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "finalist_ids": list(FINALIST_IDS),
    }
    if any(content.get(key) != value for key, value in required.items()):
        raise S5CProtocolError("S5C preparation lock is not bound to accepted inputs")
    if tuple(row["genome_id"] for row in advancement["selected_finalists"]) != FINALIST_IDS:
        raise S5CProtocolError("S5C preparation lock finalist binding changed")
    return content
