"""Durable identifier generation for the experiment model."""
import hashlib
import json
import uuid


def new_id(prefix: str) -> str:
    """A random, opaque identifier for experiment_id/agent_id/episode_id/
    decision_id/order_id/fill_id — anything whose identity is not derived
    from its content."""
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def genome_id(genome: dict) -> str:
    """Content-addressed id: identical genomes always hash to the same id,
    so the id itself is proof the genome hasn't changed."""
    digest = hashlib.sha256(canonical_json(genome).encode("utf-8")).hexdigest()
    return f"gen_{digest}"
