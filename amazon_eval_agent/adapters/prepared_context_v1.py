"""Independent, stdlib-only verifier for A prepared runtime contexts.

The verifier re-reads only local prepared files and reimplements the small,
documented A algorithms needed to check:

- the target review row's user/item/date/stars/text identity;
- the exact visible-history and item-reference row selections from runtime
  reviews (strictly earlier event time; target rows/texts/copies excluded);
- the manifest context hash from those row fingerprints;
- the runtime context digest used by A's ``simulation_run.v1`` records;
- history max dates and target-copy absence.

It never imports A, calls a model, downloads data or emits review text,
identifiers or paths. A source version is pinned in the adapter version.

Bounded scope: this verifier models the resolved-target case (each task has a
unique target review row). Ambiguous/missing target rows fail closed rather
than being called verified. The original A timestamp-semantics assertion is
still a caller assertion; code verifies ordering, not the domain meaning of
the timestamp field.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..safety import (
    MAX_SOURCE_BYTES,
    SafetyError,
    deep_freeze,
    load_jsonl_bytes,
    read_path_bytes,
)
from .a_temporal_manifest_v2 import AManifestProjection

PREPARED_CONTEXT_ADAPTER_VERSION = "b_prepared_context_v1.reader_v1"
MAX_RUNTIME_ROWS = 100_000
MAX_RUNTIME_BYTES = MAX_SOURCE_BYTES


class PreparedContextError(ValueError):
    """Safe verifier failure; never contains source values or paths."""

    def __init__(self, code: str, errors: Sequence[str] = ()):
        self.code = code
        self.errors = tuple(sorted(set(errors))) if errors else (code,)
        super().__init__(code)


@dataclass(frozen=True)
class PreparedIdentity:
    task_index: int
    execution_order: int
    split: str
    cohort_group: str
    user_id: str
    item_id: str
    target_review_id: str
    target_date: str
    target_time: datetime
    target_stars: float
    target_text_digest: str | None
    source_task_index_sha256: str | None
    target_timestamp_sha256: str
    manifest_context_sha256: str
    visible_history_row_indexes: tuple[int, ...]
    target_item_reference_row_indexes: tuple[int, ...]
    target_text_absent_from_context: bool


@dataclass(frozen=True)
class PreparedContextResult:
    runtime_sha256: str
    runtime_rows: int
    task_count: int
    history_selection_verified: int
    reference_selection_verified: int
    target_copy_absence_verified: int
    manifest_context_verified: int
    runtime_context_checked_tasks: int
    runtime_context_verified: int
    run_context_mismatch_tasks: tuple[int, ...]
    history_max_by_task: Mapping[int, str | None]
    expected_run_context_by_task: Mapping[int, str]
    adapter_version: str = PREPARED_CONTEXT_ADAPTER_VERSION

    def safe_summary(self) -> dict[str, Any]:
        return {
            "adapter_version": self.adapter_version,
            "runtime_sha256": self.runtime_sha256,
            "runtime_rows": self.runtime_rows,
            "task_count": self.task_count,
            "history_selection_verified": self.history_selection_verified,
            "reference_selection_verified": self.reference_selection_verified,
            "target_copy_absence_verified": self.target_copy_absence_verified,
            "manifest_context_verified": self.manifest_context_verified,
            "runtime_context_checked_tasks": self.runtime_context_checked_tasks,
            "runtime_context_verified": self.runtime_context_verified,
            "run_context_mismatch_count": len(self.run_context_mismatch_tasks),
            "history_max_available": sum(
                1 for value in self.history_max_by_task.values() if value
            ),
            "source_review_text_reemitted": False,
            "raw_ids_reemitted": False,
        }


@dataclass(frozen=True)
class _RuntimeRow:
    row_index: int
    review_id: str
    user_id: str
    item_id: str
    event_time: datetime
    text_digest: str | None
    stars: float
    row_key: str
    useful: Any = None
    funny: Any = None
    cool: Any = None


def _text_digest(text: str | None) -> str | None:
    if not text:
        return None
    normalized = " ".join(text.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PreparedContextError("runtime_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PreparedContextError("runtime_timestamp_invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_id(value: Any) -> str:
    if value is None or isinstance(value, (bool, dict, list)):
        raise PreparedContextError("runtime_identity_invalid")
    normalized = str(value).strip()
    if not normalized or len(normalized) > 256:
        raise PreparedContextError("runtime_identity_invalid")
    if any(ord(char) < 32 for char in normalized):
        raise PreparedContextError("runtime_identity_invalid")
    return normalized


def _stars(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PreparedContextError("runtime_stars_invalid")
    number = float(value)
    if not math.isfinite(number) or not 1.0 <= number <= 5.0:
        raise PreparedContextError("runtime_stars_invalid")
    return number


def _manifest_row_fingerprint(row: _RuntimeRow) -> dict[str, Any]:
    """Fingerprint used by A's manifest builder (`_review_row` raw fields)."""
    return {
        "row_index": row.row_index,
        "event_time": row.event_time.isoformat(),
        "text_sha256": row.text_digest,
        "stars": row.stars,
        "useful": row.useful,
        "funny": row.funny,
        "cool": row.cool,
    }


def _runtime_row_fingerprint(row: _RuntimeRow) -> dict[str, Any]:
    """Fingerprint used by A's runtime context (`_load_context_rows` 0.0 fill)."""
    return {
        "source_row_index": row.row_index,
        "event_time": row.event_time.isoformat(),
        "text_sha256": row.text_digest,
        "stars": row.stars,
        "useful": 0.0,
        "funny": 0.0,
        "cool": 0.0,
    }


def _load_runtime_rows(prepared_dir) -> tuple[tuple[_RuntimeRow, ...], str]:
    path = prepared_dir / "runtime_reviews.jsonl"
    try:
        payload = read_path_bytes(path, max_bytes=MAX_RUNTIME_BYTES)
    except SafetyError as exc:
        if exc.code == "resource_unreadable":
            raise PreparedContextError("runtime_reviews_unreadable") from exc
        if exc.code == "resource_too_large":
            raise PreparedContextError("runtime_reviews_too_large") from exc
        raise PreparedContextError(exc.code) from exc
    loaded = load_jsonl_bytes(
        payload,
        max_bytes=MAX_RUNTIME_BYTES,
        max_rows=MAX_RUNTIME_ROWS,
    )
    if not loaded.valid:
        raise PreparedContextError(loaded.code or "runtime_reviews_invalid")
    rows: list[_RuntimeRow] = []
    for index, raw in enumerate(loaded.rows):
        if not isinstance(raw, dict):
            raise PreparedContextError("runtime_row_invalid")
        review_id = _safe_id(raw.get("review_id"))
        user_id = _safe_id(raw.get("user_id"))
        item_id = _safe_id(raw.get("item_id"))
        event_time = _parse_time(raw.get("timestamp"))
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise PreparedContextError("runtime_text_invalid")
        digest = _text_digest(text)
        stars = _stars(raw.get("stars"))
        row_key = hashlib.sha256(f"id\0{review_id}".encode("utf-8")).hexdigest()
        rows.append(
            _RuntimeRow(
                row_index=index,
                review_id=review_id,
                user_id=user_id,
                item_id=item_id,
                event_time=event_time,
                text_digest=digest,
                stars=stars,
                row_key=row_key,
                useful=raw.get("useful"),
                funny=raw.get("funny"),
                cool=raw.get("cool"),
            )
        )
    if len({row.review_id for row in rows}) != len(rows):
        raise PreparedContextError("runtime_review_id_duplicate")
    return tuple(rows), hashlib.sha256(payload).hexdigest()


def _target_rows(
    identities: Sequence[PreparedIdentity], rows: Sequence[_RuntimeRow]
) -> dict[int, _RuntimeRow]:
    by_review_id: dict[str, _RuntimeRow] = {row.review_id: row for row in rows}
    targets: dict[int, _RuntimeRow] = {}
    for identity in identities:
        target = by_review_id.get(identity.target_review_id)
        if target is None:
            raise PreparedContextError("target_review_row_missing")
        if target.event_time != identity.target_time:
            # This adapter supports the Amazon UTC-day resolved-target layout:
            # runtime rows must be exactly at the manifest's target timestamp
            # (midnight UTC), not merely on the same calendar day.
            raise PreparedContextError("target_review_time_mismatch")
        if (
            target.user_id != identity.user_id
            or target.item_id != identity.item_id
            or abs(target.stars - identity.target_stars) > 1e-9
            or target.text_digest != identity.target_text_digest
        ):
            raise PreparedContextError("target_review_identity_mismatch")
        targets[identity.task_index] = target
    if len({target.row_key for target in targets.values()}) != len(targets):
        raise PreparedContextError("target_review_row_duplicate")
    return targets


def _expected_context_rows(
    identity: PreparedIdentity,
    rows: Sequence[_RuntimeRow],
    targets: Mapping[int, _RuntimeRow],
) -> tuple[list[_RuntimeRow], list[_RuntimeRow]]:
    heldout_keys = {target.row_key for target in targets.values()}
    heldout_review_ids = {target.review_id for target in targets.values()}
    heldout_texts = {
        target.text_digest for target in targets.values() if target.text_digest
    }
    heldout_user_times = {
        (target.user_id, target.event_time) for target in targets.values()
    }
    heldout_item_times = {
        (target.item_id, target.event_time) for target in targets.values()
    }
    target_time = identity.target_time
    history = [
        row
        for row in rows
        if row.user_id == identity.user_id
        and row.event_time < target_time
        and row.row_key not in heldout_keys
        and row.review_id not in heldout_review_ids
        and row.text_digest not in heldout_texts
        and (row.user_id, row.event_time) not in heldout_user_times
    ]
    references = [
        row
        for row in rows
        if row.item_id == identity.item_id
        and row.event_time < target_time
        and row.user_id != identity.user_id
        and row.row_key not in heldout_keys
        and row.review_id not in heldout_review_ids
        and row.text_digest not in heldout_texts
        and (row.item_id, row.event_time) not in heldout_item_times
    ]
    history.sort(key=lambda row: (row.event_time, row.row_index))
    references.sort(key=lambda row: (row.event_time, row.row_index))
    return history, references


def verify_prepared_context(
    prepared_dir,
    projection: AManifestProjection,
    identities: Sequence[PreparedIdentity],
    run_view=None,
) -> PreparedContextResult:
    """Recompute A manifest/runtime context evidence from local prepared files."""
    rows, runtime_sha = _load_runtime_rows(prepared_dir)
    if len(identities) != len(projection.tasks):
        raise PreparedContextError("identity_count_mismatch")
    identity_by_index = {item.task_index: item for item in identities}
    if set(identity_by_index) != {row.task_index for row in projection.tasks}:
        raise PreparedContextError("identity_task_set_mismatch")
    targets = _target_rows(identities, rows)
    run_by_task = run_view.by_task if run_view is not None else {}
    history_max: dict[int, str | None] = {}
    expected_run_context: dict[int, str] = {}
    mismatches: list[int] = []
    checked_run_tasks = 0
    for a_task in projection.tasks:
        identity = identity_by_index[a_task.task_index]
        if _sha256_text(identity.target_time.isoformat()) != (
            identity.target_timestamp_sha256
        ):
            raise PreparedContextError("target_timestamp_hash_mismatch")
        history, references = _expected_context_rows(identity, rows, targets)
        if tuple(row.row_index for row in history) != identity.visible_history_row_indexes:
            raise PreparedContextError("visible_history_selection_mismatch")
        if tuple(row.row_index for row in references) != (
            identity.target_item_reference_row_indexes
        ):
            raise PreparedContextError("item_reference_selection_mismatch")
        if not identity.target_text_absent_from_context:
            raise PreparedContextError("target_text_not_absent_declared")
        if identity.target_text_digest and any(
            row.text_digest == identity.target_text_digest
            for row in (*history, *references)
        ):
            raise PreparedContextError("target_text_copy_in_context")
        history_fingerprints = [_manifest_row_fingerprint(row) for row in history]
        reference_fingerprints = [
            _manifest_row_fingerprint(row) for row in references
        ]
        context_payload = {
            "target_timestamp_sha256": _sha256_text(identity.target_time.isoformat()),
            "history_rows": history_fingerprints,
            "item_reference_rows": reference_fingerprints,
        }
        manifest_context = hashlib.sha256(
            json.dumps(context_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if manifest_context != identity.manifest_context_sha256:
            raise PreparedContextError("manifest_context_mismatch")
        runtime_context = _sha256_json(
            {
                "target_timestamp": identity.target_time.isoformat(),
                "manifest_context_sha256": identity.manifest_context_sha256,
                "history": [
                    _runtime_row_fingerprint(row) for row in history
                ],
                "item_references": [
                    _runtime_row_fingerprint(row) for row in references
                ],
            }
        )
        expected_run_context[identity.task_index] = runtime_context
        if identity.task_index in run_by_task:
            checked_run_tasks += 1
            if run_by_task[identity.task_index].context_sha256 != runtime_context:
                mismatches.append(identity.task_index)
        history_max[identity.task_index] = (
            max(row.event_time.date() for row in history).isoformat()
            if history
            else None
        )
    if mismatches:
        raise PreparedContextError("run_context_mismatch")
    return PreparedContextResult(
        runtime_sha256=runtime_sha,
        runtime_rows=len(rows),
        task_count=len(identities),
        history_selection_verified=len(identities),
        reference_selection_verified=len(identities),
        target_copy_absence_verified=len(identities),
        manifest_context_verified=len(identities),
        runtime_context_checked_tasks=checked_run_tasks,
        runtime_context_verified=checked_run_tasks - len(mismatches),
        run_context_mismatch_tasks=tuple(sorted(mismatches)),
        history_max_by_task=deep_freeze(history_max),
        expected_run_context_by_task=deep_freeze(expected_run_context),
    )
