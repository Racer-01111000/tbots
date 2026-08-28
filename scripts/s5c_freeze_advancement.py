#!/usr/bin/env python3
"""Freeze S5C advancement using only accepted S5B result artifacts."""
import hashlib
import json
from pathlib import Path

from lib.ids import canonical_json, genome_id
from s5b_config import ROOT, load_preparation_lock
from s5b_frozen import load_frozen_top10

ACCEPTED_PREPARED_REVISION = "f2f2c16441e1bf4345845ff3a75d8cea101174e9"
ACCEPTED_S5B_RUN_ID = "s5b_126c5fd6855e59e7db4a588580bd63b5fd401cd4a3a4ce09fa54373ee9504928"
ACCEPTED_S5B_DIGEST = "fb1b8284d4d9cd5f584af14047784ab1cdd27c85c33ae7575c9e685874053b91"
ACCEPTED_S5B_REPORT_SHA256 = "a50c71208bac5e774cb277845c41d5aa9bc46d46a7ddb2077f8edf3a1dad4fb6"
ACCEPTED_S5B_REPORT = ROOT / "reports" / "s5b_qualification_f2f2c164_result.json"
EXPECTED_REPRESENTATIVES = (
    "gen_3e06a2be7814b193c9da1725ea559f8154bbce006a6ab227a72f18aa5c7eb30f",
    "gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155",
    "gen_a8a5c9f1078a8e446bd3c55b6dc5d201b016c78d7f67ac2e047c76355936bf70",
)
PROTOCOL_DIR = ROOT / "evolution" / "protocol"


class S5CAdvancementError(ValueError):
    pass


def content_hash(prefix: str, content: dict) -> str:
    return prefix + hashlib.sha256(canonical_json(content).encode()).hexdigest()


def behavior_identity(row: dict) -> str:
    """Hash complete recorded behavior while excluding organism identity/rank."""
    payload = {
        "episodes": row.get("episode_metrics"),
        "aggregate": row.get("aggregate"),
    }
    if not isinstance(payload["episodes"], list) or len(payload["episodes"]) != 4:
        raise S5CAdvancementError("behavior identity requires four recorded episodes")
    if not isinstance(payload["aggregate"], dict):
        raise S5CAdvancementError("behavior identity requires aggregate results")
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    if row.get("metrics_hash") != digest:
        raise S5CAdvancementError("recorded S5B behavior hash mismatch")
    return digest


def mechanical_select(rows: list[dict], count: int = 3) -> tuple[list[dict], list[dict]]:
    if count != 3 or len(rows) != 10:
        raise S5CAdvancementError("advancement requires three representatives from ten")
    ordered = sorted(
        rows,
        key=lambda row: (
            -row.get("aggregate", {}).get("qualification_score", float("-inf")),
            row.get("genome_id", ""),
        ),
    )
    if [row.get("qualification_rank") for row in ordered] != list(range(1, 11)):
        raise S5CAdvancementError("accepted S5B ranking or tie-break order changed")
    selected = []
    trace = []
    representative_by_behavior = {}
    for row in ordered:
        identity = behavior_identity(row)
        prior = representative_by_behavior.get(identity)
        if prior is None:
            representative_by_behavior[identity] = row["genome_id"]
            selected.append(row)
            action = "selected"
        else:
            action = "skipped_identical_behavior"
        trace.append({
            "qualification_rank": row["qualification_rank"],
            "development_rank": row["development_rank"],
            "genome_id": row["genome_id"],
            "behavior_identity_sha256": identity,
            "action": action,
            "identical_to_selected_genome_id": prior,
        })
        if len(selected) == count:
            break
    if len(selected) != count:
        raise S5CAdvancementError("fewer than three distinct qualification behaviors")
    return selected, trace


ADVANCEMENT_PROTOCOL = {
    "schema_version": 1,
    "name": "s5c_frozen_championship_advancement_v1",
    "accepted_s5b": {
        "prepared_revision": ACCEPTED_PREPARED_REVISION,
        "run_id": ACCEPTED_S5B_RUN_ID,
        "deterministic_digest": ACCEPTED_S5B_DIGEST,
        "result_sha256": ACCEPTED_S5B_REPORT_SHA256,
    },
    "advancement_count": 3,
    "algorithm": [
        "verify accepted S5B result, frozen-top-ten, ranking, and verifier identities",
        "scan organisms in qualification-rank order",
        "select the first organism",
        "skip later organisms matching any selected behavioral identity",
        "select each next behaviorally distinct organism until three are selected",
    ],
    "behavioral_identity": {
        "algorithm": "sha256",
        "canonicalization": "JSON sort_keys=True separators=(',', ':')",
        "payload": {
            "episodes": "exact complete S5B episode_metrics array",
            "aggregate": "exact complete S5B aggregate result object",
        },
        "excluded_identity_fields": [
            "agent_id", "genome_id", "development_rank", "qualification_rank",
        ],
        "not_score_only": True,
        "must_equal_recorded_s5b_metrics_hash": True,
    },
    "ranking_source": "accepted completed S5B qualification ranking",
    "tie_break": "genome_id ascending",
    "discretionary_substitution_allowed": False,
    "genome_mutation_allowed": False,
    "breeding_or_retraining_allowed": False,
    "evolutionary_feedback_allowed": False,
    "sealed_lanes": {
        "championship": {
            "start": "2023-01-01", "end": "2025-12-31", "status": "LOCKED",
        },
        "final_reserve": {
            "start": "2026-01-01", "end": None, "status": "LOCKED",
        },
    },
    "data_inputs_allowed": [
        "accepted S5B qualification result",
        "accepted S5B preparation lock",
        "accepted frozen S5A top-ten snapshot",
    ],
    "championship_or_final_reserve_information_allowed": False,
}
PROTOCOL_HASH = content_hash("s5c_advancement_protocol_", ADVANCEMENT_PROTOCOL)
PROTOCOL_PATH = PROTOCOL_DIR / f"advancement_protocol_{PROTOCOL_HASH}.json"


def _load_accepted_report() -> dict:
    try:
        report_bytes = ACCEPTED_S5B_REPORT.read_bytes()
        report = json.loads(report_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise S5CAdvancementError("accepted S5B report is unreadable") from exc
    if hashlib.sha256(report_bytes).hexdigest() != ACCEPTED_S5B_REPORT_SHA256:
        raise S5CAdvancementError("accepted S5B report hash mismatch")
    if (
        report.get("run_id") != ACCEPTED_S5B_RUN_ID
        or report.get("deterministic_digest") != ACCEPTED_S5B_DIGEST
        or report.get("code_revision") != ACCEPTED_PREPARED_REVISION
    ):
        raise S5CAdvancementError("accepted S5B run identity changed")
    verifier = report.get("independent_verifier", [])
    if len(verifier) != 10 or any(
        row.get("status") != "agreed"
        or row.get("main_result_digest") != row.get("verifier_result_digest")
        for row in verifier
    ):
        raise S5CAdvancementError("accepted S5B independent verification changed")
    conservation = report.get("conservation", {})
    if (
        conservation.get("championship_access") != 0
        or conservation.get("final_reserve_access") != 0
    ):
        raise S5CAdvancementError("accepted S5B sealed-lane conservation changed")
    return report


def build_manifest_content(report: dict, frozen) -> dict:
    lock = load_preparation_lock()
    if report.get("preparation_lock") != lock:
        raise S5CAdvancementError("S5B report preparation lock changed")
    rows = report.get("ranked_results")
    if not isinstance(rows, list):
        raise S5CAdvancementError("S5B ranked results are missing")
    frozen_by_id = {row.genome_id: row for row in frozen}
    if set(frozen_by_id) != {row.get("genome_id") for row in rows}:
        raise S5CAdvancementError("S5B results do not match the frozen top ten")
    for row in rows:
        item = frozen_by_id[row["genome_id"]]
        if (
            row.get("development_rank") != item.development_rank
            or genome_id(item.genome) != row["genome_id"]
        ):
            raise S5CAdvancementError("frozen genome identity or S5A rank changed")

    selected, trace = mechanical_select(rows)
    actual = tuple(row["genome_id"] for row in selected)
    if actual != EXPECTED_REPRESENTATIVES:
        raise S5CAdvancementError(
            f"mechanical finalists differ from expected receipt: {actual}"
        )
    finalists = []
    for row in selected:
        frozen_row = frozen_by_id[row["genome_id"]]
        raw_hash = hashlib.sha256(canonical_json(frozen_row.genome).encode()).hexdigest()
        if row["genome_id"] != "gen_" + raw_hash:
            raise S5CAdvancementError("immutable genome content hash changed")
        finalists.append({
            "selection_order": len(finalists) + 1,
            "genome_id": row["genome_id"],
            "genome_content_sha256": raw_hash,
            "development_rank": row["development_rank"],
            "qualification_rank": row["qualification_rank"],
            "qualification_behavior_identity_sha256": behavior_identity(row),
        })
    return {
        "schema_version": 1,
        "name": "s5c_frozen_championship_advancement_manifest_v1",
        "protocol_hash": PROTOCOL_HASH,
        "accepted_s5b_run_id": ACCEPTED_S5B_RUN_ID,
        "accepted_s5b_deterministic_digest": ACCEPTED_S5B_DIGEST,
        "accepted_s5b_result_sha256": ACCEPTED_S5B_REPORT_SHA256,
        "qualification_bundle_revision": lock["qualification_bundle_revision"],
        "qualification_bundle_manifest_hash": lock["qualification_bundle_manifest_hash"],
        "episode_protocol_hash": lock["episode_protocol_hash"],
        "score_protocol_hash": lock["score_protocol_hash"],
        "frozen_top10_snapshot_hash": lock["frozen_top10_snapshot_hash"],
        "selection_trace": trace,
        "selected_finalists": finalists,
        "selection_complete": True,
        "advancement_count": 3,
        "championship_bundle_constructed": False,
        "championship_observations_accessed": 0,
        "final_reserve_observations_accessed": 0,
        "qualification_organisms_executed": 0,
        "genome_mutations": 0,
        "breeding_or_retraining": 0,
        "evolutionary_feedback": 0,
        "broker_connections": 0,
        "real_orders": 0,
        "live_market_feeds": 0,
        "public_endpoints": 0,
        "alpaca_access": 0,
        "source_paths_read": [
            "reports/s5b_qualification_f2f2c164_result.json",
            lock["frozen_top10_snapshot_path"],
            "evolution/protocol/s5b_preparation_lock.json",
        ],
        "sealed_observation_paths_read": [],
    }


def envelope(content: dict, manifest_hash: str) -> bytes:
    return (
        json.dumps(
            {"content": content, "manifest_hash": manifest_hash},
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode()


def freeze() -> tuple[str, str]:
    report = _load_accepted_report()
    frozen = load_frozen_top10()
    content = build_manifest_content(report, frozen)
    manifest_hash = content_hash("s5c_advancement_manifest_", content)
    manifest_path = PROTOCOL_DIR / f"advancement_manifest_{manifest_hash}.json"
    if PROTOCOL_PATH.exists() or manifest_path.exists():
        raise S5CAdvancementError("refusing to overwrite frozen S5C artifacts")
    PROTOCOL_PATH.write_bytes(envelope(ADVANCEMENT_PROTOCOL, PROTOCOL_HASH))
    manifest_path.write_bytes(envelope(content, manifest_hash))
    return PROTOCOL_HASH, manifest_hash


if __name__ == "__main__":
    protocol_hash, manifest_hash = freeze()
    print(json.dumps({
        "protocol_hash": protocol_hash,
        "manifest_hash": manifest_hash,
    }, sort_keys=True))
