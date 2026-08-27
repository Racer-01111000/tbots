"""The historical replay engine: the only supported market-data interface
for agents. Two classes on purpose:

  ReplayEngine - full internal state (the complete per-symbol series, the
                 real clock, advance()). Used only by orchestration/audit
                 code, never handed to agent code.

  AgentView    - wraps a ReplayEngine and exposes exactly two methods,
                 observe() and history(). No advance, no seek, no
                 reference to the engine, the dataset bundle, or any raw
                 array. This is the "agent contract."

Every value that reaches an agent (through either method, in either
masked-time mode) is built field-by-field from ALLOWED_ASSET_FIELDS -- a
whitelist, not a filter over a bigger object. Nothing is ever handed to
an agent and then trimmed afterward.

Scoping note: this isolates the SUPPORTED interface. It is not a
sandbox against arbitrary malicious Python code running in the same
process (e.g. reaching into AgentView's name-mangled private attribute
via introspection) -- that would require OS/process-level isolation,
which is out of scope here.
"""
import bisect
import csv
import io
import json
import re
from pathlib import Path

from hashing import sha256_bytes

ALLOWED_ASSET_FIELDS = ("open", "high", "low", "close", "adjusted_close", "volume", "corporate_action")
REQUIRED_NONEMPTY_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class DatasetVerificationError(Exception):
    """Raised when the on-disk dataset does not match the expected,
    accepted dataset_revision. Always fail closed -- never continue
    against data that didn't verify."""


class EpisodeCompletedError(Exception):
    """Raised by observe()/history() once an episode has reached COMPLETED."""


class EvaluationExposureAudit:
    """Counts every market observation crossing the replay boundary."""

    def __init__(self, development_start: str, development_end: str):
        self.development_start = development_start
        self.development_end = development_end
        self.counts = {
            "warmup_observations_exposed": 0,
            "development_observations_exposed": 0,
            "qualification_observations_exposed": 0,
            "championship_observations_exposed": 0,
            "final_reserve_observations_exposed": 0,
            "other_post_development_observations_exposed": 0,
        }

    def record(self, timestamp: str, count: int = 1) -> None:
        if timestamp < self.development_start:
            key = "warmup_observations_exposed"
        elif timestamp <= self.development_end:
            key = "development_observations_exposed"
        elif timestamp <= "2022-12-31":
            key = "qualification_observations_exposed"
        elif timestamp <= "2025-12-31":
            key = "championship_observations_exposed"
        elif timestamp >= "2026-01-01":
            key = "final_reserve_observations_exposed"
        else:
            key = "other_post_development_observations_exposed"
        self.counts[key] += count

    def snapshot(self) -> dict:
        return dict(self.counts)


class DatasetBundle:
    def __init__(self, dataset_revision: str, asset_set: list[str],
                 per_symbol_rows: dict[str, list[dict]], calendar: list[str],
                 exposure_audit: EvaluationExposureAudit | None = None,
                 bundle_revision: str | None = None,
                 bundle_manifest_hash: str | None = None):
        self.dataset_revision = dataset_revision
        self.asset_set = asset_set
        self.per_symbol_rows = per_symbol_rows
        self.calendar = calendar
        self.exposure_audit = exposure_audit
        self.bundle_revision = bundle_revision
        self.bundle_manifest_hash = bundle_manifest_hash


def load_and_verify_dataset(dataset_root, expected_dataset_revision: str,
                            retention_end_date: str | None = None) -> DatasetBundle:
    """Loads exactly the accepted dataset revision and verifies it two
    ways before trusting a single byte of it:
      1. manifest-level: recompute the revision hash from the manifest's
         OWN recorded fields (never trust its self-reported
         dataset_revision string) and require it equals both the
         filename-embedded revision and the caller's expected value.
      2. per-file: re-hash every normalized CSV on disk right now and
         require it matches the manifest's recorded hash for that file.
    Either check failing means someone edited the manifest, edited a
    CSV, or both (even consistently) -- and (1) or (2) respectively
    catches it. Missing files/manifest, or malformed rows, also fail
    closed here rather than downstream."""
    norm_dir = Path(dataset_root) / "data" / "normalized"
    manifest_path = norm_dir / f"manifest_{expected_dataset_revision}.json"
    if not manifest_path.exists():
        raise DatasetVerificationError(f"missing required artifact: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())

    manifest_for_hash = {
        "asset_set": manifest.get("asset_set"),
        "source_identity": manifest.get("source_identity"),
        "source_artifact_hashes": manifest.get("source_artifact_hashes"),
        "normalization_version": manifest.get("normalization_version"),
        "normalized_content_hashes": manifest.get("normalized_content_hashes"),
        "coverage_metadata": manifest.get("coverage_metadata"),
    }
    recomputed = "ds_" + sha256_bytes(canonical_json(manifest_for_hash).encode("utf-8"))
    if recomputed != expected_dataset_revision:
        raise DatasetVerificationError(
            f"unknown dataset revision: recomputed {recomputed} != expected {expected_dataset_revision}"
        )
    if manifest.get("dataset_revision") != expected_dataset_revision:
        raise DatasetVerificationError(
            "manifest's self-reported dataset_revision disagrees with the recomputed/expected value"
        )

    asset_set = manifest_for_hash["asset_set"]
    normalized_content_hashes = manifest_for_hash["normalized_content_hashes"]
    if not asset_set or not normalized_content_hashes:
        raise DatasetVerificationError("manifest missing asset_set or normalized_content_hashes")

    per_symbol_rows = {}
    for symbol in asset_set:
        csv_path = norm_dir / f"{symbol}.csv"
        if not csv_path.exists():
            raise DatasetVerificationError(f"missing required artifact: {csv_path}")
        csv_bytes = csv_path.read_bytes()
        actual_hash = sha256_bytes(csv_bytes)
        expected_hash = normalized_content_hashes.get(symbol)
        if actual_hash != expected_hash:
            raise DatasetVerificationError(
                f"changed normalized artifact: {symbol} on-disk sha256={actual_hash} "
                f"manifest sha256={expected_hash}"
            )

        rows = []
        for r in csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))):
            for field in REQUIRED_NONEMPTY_FIELDS:
                if not r.get(field):
                    raise DatasetVerificationError(
                        f"malformed observation in {symbol} at {r.get('timestamp')}: empty {field}"
                    )
            if not ISO_DATE_RE.match(r["timestamp"]):
                raise DatasetVerificationError(
                    f"malformed observation in {symbol}: timestamp {r['timestamp']!r} is not YYYY-MM-DD"
                )
            for field in ("open", "high", "low", "close", "volume"):
                try:
                    float(r[field])
                except ValueError:
                    raise DatasetVerificationError(
                        f"malformed observation in {symbol} at {r['timestamp']}: "
                        f"non-numeric {field}={r[field]!r}"
                    )
            if r["adjusted_close"] and not _is_float(r["adjusted_close"]):
                raise DatasetVerificationError(
                    f"malformed observation in {symbol} at {r['timestamp']}: "
                    f"non-numeric adjusted_close={r['adjusted_close']!r}"
                )
            rows.append({
                "timestamp": r["timestamp"],
                "open": r["open"], "high": r["high"], "low": r["low"],
                "close": r["close"], "adjusted_close": r["adjusted_close"],
                "volume": r["volume"], "corporate_action": r["corporate_action"],
            })
        rows.sort(key=lambda row: row["timestamp"])
        if retention_end_date is not None:
            rows = [row for row in rows if row["timestamp"] <= retention_end_date]
        per_symbol_rows[symbol] = rows

    calendar = sorted({row["timestamp"] for rows in per_symbol_rows.values() for row in rows})
    if not calendar:
        raise DatasetVerificationError("verified dataset has zero trading dates")

    return DatasetBundle(expected_dataset_revision, asset_set, per_symbol_rows, calendar)


def _asset_fields(row) -> dict:
    """Whitelist, not filter: builds the field set from ALLOWED_ASSET_FIELDS
    regardless of what else the internal row dict happens to carry."""
    if row is None:
        return {"available": False}
    out = {"available": True}
    for f in ALLOWED_ASSET_FIELDS:
        out[f] = row[f]
    return out


class ReplayEngine:
    """Owns the clock. The only way it moves is advance(), called by
    orchestration code -- never by agent code, which never sees this
    class at all."""

    def __init__(self, dataset_root, expected_dataset_revision: str, start_date: str, end_date: str,
                 masked_time: bool = False, random_seed: int = 0, episode_id: str | None = None,
                 retention_end_date: str | None = None, _verified_bundle: DatasetBundle | None = None):
        if retention_end_date is not None and end_date > retention_end_date:
            raise ValueError("episode end exceeds the authorized data-retention boundary")
        if _verified_bundle is None:
            self.bundle = load_and_verify_dataset(
                dataset_root, expected_dataset_revision, retention_end_date=retention_end_date
            )
        else:
            if _verified_bundle.dataset_revision != expected_dataset_revision:
                raise DatasetVerificationError("verified bundle dataset revision changed")
            if (retention_end_date is not None and _verified_bundle.calendar
                    and _verified_bundle.calendar[-1] > retention_end_date):
                raise DatasetVerificationError("verified bundle exceeds retention boundary")
            self.bundle = _verified_bundle
        self.dataset_revision = expected_dataset_revision
        self.masked_time = masked_time
        self.random_seed = random_seed
        self.episode_id = episode_id
        self.start_date = start_date
        self.end_date = end_date

        self.episode_dates = [d for d in self.bundle.calendar if start_date <= d <= end_date]
        if not self.episode_dates:
            raise ValueError(
                f"no trading dates in [{start_date}, {end_date}] within the verified dataset calendar"
            )
        self.step_count = len(self.episode_dates)

        self._symbol_timestamps = {s: [row["timestamp"] for row in rows]
                                    for s, rows in self.bundle.per_symbol_rows.items()}
        self._calendar_index = {d: i for i, d in enumerate(self.bundle.calendar)}
        self._episode_start_calendar_index = self._calendar_index[self.episode_dates[0]]

        self.current_index = 0
        self.status = "RUNNING"

    @property
    def current_timestamp(self) -> str:
        return self.episode_dates[self.current_index]

    def relative_day_of(self, timestamp: str) -> int:
        """1 at episode start; <=0 for dates before episode start (still
        legitimately reachable via history()); never reveals the real
        calendar date."""
        return self._calendar_index[timestamp] - self._episode_start_calendar_index + 1

    def _require_active(self):
        if self.status == "COMPLETED":
            raise EpisodeCompletedError("episode is COMPLETED; no further observations available")
        if self.status == "FAILED":
            raise RuntimeError("episode is FAILED")

    def observe(self) -> dict:
        self._require_active()
        clock = self.current_timestamp
        assets = {}
        for symbol in self.bundle.asset_set:
            idx = self._symbol_timestamps[symbol]
            pos = bisect.bisect_left(idx, clock)
            row = self.bundle.per_symbol_rows[symbol][pos] if pos < len(idx) and idx[pos] == clock else None
            if row is not None and self.bundle.exposure_audit is not None:
                self.bundle.exposure_audit.record(row["timestamp"])
            assets[symbol] = _asset_fields(row)
        return {
            "_true_timestamp": clock,
            "episode_day_index": self.current_index + 1,
            "dataset_revision": self.dataset_revision,
            "assets": assets,
        }

    def history(self, symbol: str, bars: int) -> list[dict]:
        self._require_active()
        if symbol not in self.bundle.asset_set:
            raise ValueError(f"unknown symbol: {symbol}")
        if not isinstance(bars, int) or isinstance(bars, bool):
            raise TypeError(f"bars must be an int, got {type(bars).__name__}")
        if bars <= 0:
            return []
        clock = self.current_timestamp
        timestamps = self._symbol_timestamps[symbol]
        rows = self.bundle.per_symbol_rows[symbol]
        cut = bisect.bisect_right(timestamps, clock)
        if cut == 0:
            return []
        start = max(0, cut - bars)
        selected = rows[start:cut]
        if self.bundle.exposure_audit is not None:
            for row in selected:
                self.bundle.exposure_audit.record(row["timestamp"])
        return [dict(row) for row in selected]

    def advance(self) -> bool:
        """Moves exactly one step forward. Returns True if the episode is
        still RUNNING afterward, False if it just completed. No argument
        is accepted -- there is no way to advance by more than one step
        or move backward through this method."""
        self._require_active()
        self.current_index += 1
        if self.current_index >= self.step_count:
            self.current_index = self.step_count - 1
            self.status = "COMPLETED"
            return False
        return True


class AgentView:
    """The entire agent-facing contract. Two methods. No attribute here
    exposes the engine, the bundle, or any array with future rows in
    it."""

    def __init__(self, engine: ReplayEngine):
        self.__engine = engine

    def observe(self) -> dict:
        full = self.__engine.observe()
        payload = {
            "dataset_revision": full["dataset_revision"],
            "episode_day_index": full["episode_day_index"],
            "assets": full["assets"],
        }
        if self.__engine.masked_time:
            payload["day"] = f"DAY {full['episode_day_index']:04d}"
        else:
            payload["timestamp"] = full["_true_timestamp"]
        return payload

    def history(self, symbol: str, bars: int) -> list[dict]:
        rows = self.__engine.history(symbol, bars)
        out = []
        for row in rows:
            entry = {f: row[f] for f in ALLOWED_ASSET_FIELDS}
            entry["available"] = True
            if self.__engine.masked_time:
                entry["day"] = f"DAY {self.__engine.relative_day_of(row['timestamp']):04d}"
            else:
                entry["timestamp"] = row["timestamp"]
            out.append(entry)
        return out


def observation_hash(payload: dict) -> str:
    return sha256_bytes(canonical_json(payload).encode("utf-8"))
