"""Fail-closed identity for the Kim paper pilot. Loads Kim ONLY: the genome is verified four independent ways (canonical-JSON
sha256 written here, lib.ids.genome_id, the selected-bot record, the sealed config) and against the frozen champion file's
sha256; the decision-function source files must hash to the pinned values. Any other genome -- Sam, lineage D, a V5 winner,
anything -- is refused by id (denylist) AND by mismatch with the pinned id. There is no fallback and no override."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

KIM_GENOME_ID = "gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155"


class IdentityError(RuntimeError):
    pass


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def independent_genome_id(genome: dict) -> str:
    return "gen_" + hashlib.sha256(json.dumps(genome, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_kim(dev_dir: Path, repo_dir: Path, cfg: dict) -> tuple[dict, dict]:
    """Returns (genome, identity_evidence). Raises IdentityError on ANY discrepancy."""
    from lib.ids import genome_id as lib_genome_id
    want = cfg.get("genome_id")
    if want != KIM_GENOME_ID or cfg.get("strategy") != "kim":
        raise IdentityError(f"sealed config names {want!r}/{cfg.get('strategy')!r}; this pilot runs only Kim")
    if KIM_GENOME_ID in cfg.get("forbidden_genome_ids", []):
        raise IdentityError("Kim is in the forbidden list")
    sel_path = Path(dev_dir) / "selected_development_bot.json"
    try:
        sel = json.loads(sel_path.read_text())
        genome = sel["selected_genome_definition"]
    except (OSError, ValueError, KeyError) as e:
        raise IdentityError(f"selected-bot record unreadable: {e!r}") from e
    ids = {"independent_sha256": independent_genome_id(genome), "lib_ids": lib_genome_id(genome),
           "selected_bot_record": sel.get("selected_genome_id"), "sealed_config": want}
    if any(v != KIM_GENOME_ID for v in ids.values()):
        raise IdentityError(f"genome identity mismatch: {ids}")
    if ids["independent_sha256"] in cfg.get("forbidden_genome_ids", []):
        raise IdentityError("genome is on the forbidden list")
    fz = Path(repo_dir) / cfg["frozen_champion_file"]
    try:
        fz_sha = _sha256_file(fz)
    except OSError as e:
        raise IdentityError(f"frozen champion file missing: {e!r}") from e
    if fz_sha != cfg["frozen_champion_file_sha256"] or sel.get("source_file_sha256") != fz_sha:
        raise IdentityError("frozen champion file hash mismatch")
    code = {}
    for rel, h in cfg["decision_function_sha256"].items():
        got = _sha256_file(Path(repo_dir) / rel)
        if got != h:
            raise IdentityError(f"decision-function file {rel} hash {got} != pinned {h}")
        code[rel] = got
    return genome, {"genome_ids": ids, "frozen_champion_file_sha256": fz_sha, "decision_function_sha256": code}
