"""Import a verified A prepared directory into B-owned manifest and labels.

This adapter reads local, already prepared A files read-only:

- ``manifest.json`` (A schema 2, verified by the public projection reader);
- ``tasks/*.jsonl`` and ``groundtruth/*.jsonl`` (identity and target date);
- ``p0_policy.json`` (fixed cut day and license status).

It writes only two new sanitized files:

1. a ``task_manifest.v1`` with task identity, split, cohort pseudonym, target
   dates and context hashes;
2. a ``local_labels.v1`` with task index and integer target stars.

It never writes review text, raw user/item ids, prompts, source rows or local
paths. It does not call A code, download data or run a model. A missing or
unreadable source fails closed; no file is fabricated to imitate A.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..contracts import simulation_run_v1 as contract
from ..labels_v1 import (
    build_labels_document,
    parse_labels_bytes,
    serialize_labels,
    verify_labels_cover_manifest,
)
from ..safety import (
    MAX_CONFIG_BYTES,
    MAX_LABELS_BYTES,
    MAX_MANIFEST_BYTES,
    SafetyError,
    is_safe_id,
    load_json_bytes,
    load_jsonl_bytes,
    read_path_bytes,
)
from ..task_manifest_v1 import (
    TASK_MANIFEST_SCHEMA_VERSION,
    SPLITS,
    parse_task_manifest_bytes,
    serialize_task_manifest,
)
from .a_temporal_manifest_v2 import (
    A_MANIFEST_ADAPTER_VERSION,
    AManifestProjection,
    parse_a_manifest_bytes,
)
from .prepared_context_v1 import (
    PreparedContextError,
    PreparedIdentity,
    _text_digest,
    verify_prepared_context,
)
from .simulation_run_v1 import load_run_artifact

A_PREPARED_ADAPTER_VERSION = "a_prepared_v1.reader_v1"
A_POLICY_ADAPTER_VERSION = "a_p0_policy_v1.reader_v1"
_SUPPORTED_A_ADAPTER = "amazon-mi-p0-v1"
_MAX_PREPARED_FILES = 32


class PreparedImportError(ValueError):
    """Safe import failure; no values, identifiers or paths are echoed."""

    def __init__(self, code: str, errors: Sequence[str] = ()):
        self.code = code
        self.errors = tuple(sorted(set(errors))) if errors else (code,)
        super().__init__(code)


@dataclass(frozen=True)
class PreparedImportResult:
    manifest_sha256: str
    labels_sha256: str
    task_count: int
    label_count: int
    split_counts: Mapping[str, int]
    history_cut_date: str
    context_verification: Mapping[str, Any]
    adapter_version: str = A_PREPARED_ADAPTER_VERSION

    def safe_summary(self) -> dict[str, Any]:
        return {
            "adapter_version": self.adapter_version,
            "source_manifest_adapter": A_MANIFEST_ADAPTER_VERSION,
            "policy_adapter_version": A_POLICY_ADAPTER_VERSION,
            "task_manifest_sha256": self.manifest_sha256,
            "labels_sha256": self.labels_sha256,
            "task_count": self.task_count,
            "label_count": self.label_count,
            "split_counts": dict(self.split_counts),
            "history_cut_date": self.history_cut_date,
            "context_verification": dict(self.context_verification),
            "converted_fields": [
                "task_index",
                "execution_order",
                "split",
                "cohort_group",
                "target_date",
                "target_timestamp_sha256",
                "manifest_context_sha256",
                "stars",
            ],
            "excluded_fields": [
                "review_text",
                "raw_user_id",
                "raw_item_id",
                "target_review_id",
                "prompt",
                "source_row",
            ],
        }


def _files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise PreparedImportError("a_prepared_directory_missing")
    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in (".json", ".jsonl")
    )
    if not files:
        raise PreparedImportError("a_prepared_files_missing")
    if len(files) > _MAX_PREPARED_FILES:
        raise PreparedImportError("a_prepared_files_too_many")
    return files


def _read_jsonl(directory: Path, code: str) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for path in _files(directory):
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise PreparedImportError(code) from exc
        if total_bytes + size > MAX_MANIFEST_BYTES:
            raise PreparedImportError("a_prepared_total_bytes_exceeded")
        try:
            payload = read_path_bytes(path, max_bytes=MAX_MANIFEST_BYTES)
        except SafetyError as exc:
            if exc.code == "resource_unreadable":
                raise PreparedImportError(code) from exc
            if exc.code == "resource_too_large":
                raise PreparedImportError("a_prepared_file_too_large") from exc
            raise PreparedImportError(exc.code) from exc
        total_bytes += len(payload)
        if total_bytes > MAX_MANIFEST_BYTES:
            raise PreparedImportError("a_prepared_total_bytes_exceeded")
        loaded = load_jsonl_bytes(
            payload, max_bytes=MAX_MANIFEST_BYTES, max_rows=500
        )
        if not loaded.valid:
            raise PreparedImportError(code, (loaded.code or code,))
        for row in loaded.rows:
            if not isinstance(row, dict):
                raise PreparedImportError(code)
            rows.append(row)
            if len(rows) > 500:
                raise PreparedImportError("a_prepared_rows_too_many")
    return tuple(rows)


def _date(value: Any) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise PreparedImportError("a_prepared_target_date_invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PreparedImportError("a_prepared_target_date_invalid") from exc


def _read_policy(prepared: Path) -> tuple[str, str]:
    path = prepared / "p0_policy.json"
    try:
        payload = read_path_bytes(path, max_bytes=MAX_CONFIG_BYTES)
    except SafetyError as exc:
        if exc.code == "resource_unreadable":
            raise PreparedImportError("a_policy_unreadable") from exc
        if exc.code == "resource_too_large":
            raise PreparedImportError("a_policy_too_large") from exc
        raise PreparedImportError(exc.code) from exc
    loaded = load_json_bytes(payload, max_bytes=MAX_CONFIG_BYTES)
    if not loaded.valid or not isinstance(loaded.document, dict):
        raise PreparedImportError("a_policy_invalid")
    document = loaded.document
    if document.get("schema_version") != 1:
        raise PreparedImportError("a_policy_schema_version_mismatch")
    if document.get("adapter_version") != _SUPPORTED_A_ADAPTER:
        raise PreparedImportError("a_policy_adapter_version_mismatch")
    protocol = document.get("protocol")
    fields = document.get("fields")
    if not isinstance(protocol, dict) or not isinstance(fields, dict):
        raise PreparedImportError("a_policy_invalid")
    cut = protocol.get("cut_day")
    if not isinstance(cut, str) or _date(cut) is None:
        raise PreparedImportError("a_policy_cut_day_invalid")
    semantics_note = fields.get("timestamp_semantics")
    if not isinstance(semantics_note, str):
        semantics_note = ""
    if "caller_asserted" not in semantics_note:
        # The policy shape is versioned; missing semantics is not upgraded to a
        # stronger claim. The B manifest records the caller assertion only.
        raise PreparedImportError("a_policy_timestamp_semantics_invalid")
    return cut, "caller_asserted_amazon_2014_unix_review_time"


def _index_rows(
    rows: Sequence[Mapping[str, Any]], code: str
) -> dict[int, Mapping[str, Any]]:
    indexed: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        index = row.get("task_index")
        if type(index) is not int or not 0 <= index < 500 or index in indexed:
            raise PreparedImportError(code)
        indexed[index] = row
    return indexed


def _stars(value: Any) -> int:
    if isinstance(value, bool):
        raise PreparedImportError("a_prepared_label_invalid")
    if type(value) is int:
        number = value
    elif type(value) is float and value.is_integer():
        number = int(value)
    else:
        raise PreparedImportError("a_prepared_label_invalid")
    if not 1 <= number <= 5:
        raise PreparedImportError("a_prepared_label_invalid")
    return number


def _safe_identity_id(value: Any, code: str) -> str:
    if value is None or isinstance(value, (bool, dict, list)):
        raise PreparedImportError(code)
    normalized = str(value).strip()
    if not normalized or len(normalized) > 256:
        raise PreparedImportError(code)
    if any(ord(char) < 32 for char in normalized):
        raise PreparedImportError(code)
    return normalized


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _target_datetime(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def load_prepared_identities(
    prepared: Path,
    projection: AManifestProjection,
    cut: str,
) -> tuple[PreparedIdentity, ...]:
    """Strictly join task files, groundtruth, and A manifest identities.

    All three sources must agree on task index, user, item, target review id,
    target date and label. The A manifest's source-task hash and target-time
    hash are recomputed from the joined task/date. Duplicate target review ids
    and duplicate (user, date) targets fail closed.
    """
    task_rows = _index_rows(
        _read_jsonl(prepared / "tasks", "a_prepared_tasks_invalid"),
        "a_prepared_tasks_invalid",
    )
    gt_rows = _index_rows(
        _read_jsonl(prepared / "groundtruth", "a_prepared_groundtruth_invalid"),
        "a_prepared_groundtruth_invalid",
    )
    a_indexes = {row.task_index for row in projection.tasks}
    if set(task_rows) != a_indexes or set(gt_rows) != a_indexes:
        raise PreparedImportError("a_prepared_task_set_mismatch")
    cut_day = date.fromisoformat(cut)
    group_by_user: dict[str, str] = {}
    user_by_group: dict[str, str] = {}
    seen_review_ids: set[str] = set()
    seen_user_dates: set[tuple[str, str]] = set()
    identities: list[PreparedIdentity] = []
    for a_task in projection.tasks:
        task_row = task_rows[a_task.task_index]
        gt_row = gt_rows[a_task.task_index]
        user_id = _safe_identity_id(
            task_row.get("user_id"), "a_prepared_user_id_invalid"
        )
        gt_user_id = _safe_identity_id(
            gt_row.get("user_id"), "a_prepared_user_id_invalid"
        )
        item_id = _safe_identity_id(
            task_row.get("item_id"), "a_prepared_item_id_invalid"
        )
        gt_item_id = _safe_identity_id(
            gt_row.get("item_id"), "a_prepared_item_id_invalid"
        )
        target_review_id = _safe_identity_id(
            task_row.get("target_review_id"), "a_prepared_target_review_id_invalid"
        )
        gt_target_review_id = _safe_identity_id(
            gt_row.get("target_review_id"), "a_prepared_target_review_id_invalid"
        )
        if (user_id, item_id, target_review_id) != (
            gt_user_id,
            gt_item_id,
            gt_target_review_id,
        ):
            raise PreparedImportError("a_prepared_identity_mismatch")
        task_date = _date(task_row.get("target_date"))
        gt_date = _date(gt_row.get("target_date"))
        if task_date != gt_date:
            raise PreparedImportError("a_prepared_target_date_mismatch")
        if task_date < cut_day:
            raise PreparedImportError("a_prepared_target_before_cut")
        if target_review_id in seen_review_ids:
            raise PreparedImportError("a_prepared_target_review_id_duplicate")
        seen_review_ids.add(target_review_id)
        if (user_id, task_date.isoformat()) in seen_user_dates:
            raise PreparedImportError("a_prepared_user_date_duplicate")
        seen_user_dates.add((user_id, task_date.isoformat()))
        group = a_task.cohort_group
        if user_id in group_by_user and group_by_user[user_id] != group:
            raise PreparedImportError("a_prepared_user_group_conflict")
        if group in user_by_group and user_by_group[group] != user_id:
            raise PreparedImportError("a_prepared_user_group_conflict")
        group_by_user[user_id] = group
        user_by_group[group] = user_id

        expected_source_hash = _sha256_text(str(a_task.task_index))
        if a_task.source_task_index_sha256 != expected_source_hash:
            raise PreparedImportError("a_prepared_source_task_hash_mismatch")
        target_time = _target_datetime(task_date)
        expected_target_hash = _sha256_text(target_time.isoformat())
        if a_task.target_timestamp_sha256 != expected_target_hash:
            raise PreparedImportError("a_prepared_target_timestamp_hash_mismatch")
        stars = _stars(gt_row.get("stars"))
        text = gt_row.get("text")
        if text is not None and not isinstance(text, str):
            raise PreparedImportError("a_prepared_target_text_invalid")
        identities.append(
            PreparedIdentity(
                task_index=a_task.task_index,
                execution_order=a_task.execution_order,
                split=a_task.split,
                cohort_group=group,
                user_id=user_id,
                item_id=item_id,
                target_review_id=target_review_id,
                target_date=task_date.isoformat(),
                target_time=target_time,
                target_stars=float(stars),
                target_text_digest=_text_digest(text),
                source_task_index_sha256=a_task.source_task_index_sha256,
                target_timestamp_sha256=a_task.target_timestamp_sha256,
                manifest_context_sha256=a_task.manifest_context_sha256,
                visible_history_row_indexes=a_task.visible_history_row_indexes,
                target_item_reference_row_indexes=(
                    a_task.target_item_reference_row_indexes
                ),
                target_text_absent_from_context=(
                    a_task.target_text_absent_from_context
                ),
            )
        )
    assigned: dict[str, str] = {}
    for identity in sorted(identities, key=lambda item: item.execution_order):
        if identity.user_id not in assigned:
            assigned[identity.user_id] = f"U{len(assigned) + 1:04d}"
        if assigned[identity.user_id] != identity.cohort_group:
            raise PreparedImportError("a_prepared_user_group_assignment_mismatch")
    return tuple(identities)


def _build_documents(
    projection: AManifestProjection,
    cut: str,
    semantics: str,
    manifest_id: str,
    identities: Sequence[PreparedIdentity],
    context_result,
) -> tuple[dict[str, Any], dict[str, Any]]:
    labels_by_task: dict[int, int] = {}
    task_documents: list[dict[str, Any]] = []
    for identity in identities:
        labels_by_task[identity.task_index] = int(identity.target_stars)
        task_documents.append(
            {
                "task_index": identity.task_index,
                "execution_order": identity.execution_order,
                "split": identity.split,
                "cohort_group": identity.cohort_group,
                "target_date": identity.target_date,
                "target_timestamp_sha256": identity.target_timestamp_sha256,
                "manifest_context_sha256": identity.manifest_context_sha256,
                "run_context_sha256": context_result.expected_run_context_by_task.get(
                    identity.task_index
                ),
                "history_max_date": context_result.history_max_by_task.get(
                    identity.task_index
                ),
            }
        )

    manifest_document = {
        "schema_version": TASK_MANIFEST_SCHEMA_VERSION,
        "manifest_id": manifest_id,
        "origin": {
            "kind": "a_prepared_v1",
            "source_manifest_sha256": projection.manifest_sha256,
            "source_task_count": projection.task_count,
            "adapter": A_PREPARED_ADAPTER_VERSION,
        },
        "protocol": {
            "history_cut_date": cut,
            "timestamp_semantics": semantics,
            "split_policy": {
                "train_ratio": projection.train_ratio,
                "validation_ratio": projection.validation_ratio,
                "ties": "indivisible_group",
            },
            "history_visibility": "strictly_before_target_timestamp",
            "target_region": "on_or_after_cut_date",
            "user_holdout_policy": "allow_overlap_not_user_disjoint",
        },
        "cohort_group_binding": "run_user_group_hash_v1",
        "split_counts": {
            split: sum(row["split"] == split for row in task_documents)
            for split in SPLITS
        },
        "tasks": task_documents,
    }
    labels_document = build_labels_document(
        ("labels_" + manifest_id)[:64], labels_by_task
    )
    return manifest_document, labels_document


def _write_new(path: Path, data: bytes) -> None:
    try:
        parent = path.parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError as exc:
        raise PreparedImportError("output_file_exists") from exc
    except OSError as exc:
        raise PreparedImportError("output_file_unavailable") from exc


def import_a_prepared(
    prepared_dir: str | Path,
    manifest_id: str,
    manifest_output: str | Path,
    labels_output: str | Path,
) -> PreparedImportResult:
    """Import a prepared directory into B manifest + labels, refusing to overwrite."""
    if not is_safe_id(manifest_id):
        raise PreparedImportError("manifest_id_invalid")
    prepared = Path(prepared_dir)
    manifest_path = prepared / "manifest.json"
    try:
        a_payload = read_path_bytes(manifest_path, max_bytes=MAX_MANIFEST_BYTES)
    except SafetyError as exc:
        if exc.code == "resource_unreadable":
            raise PreparedImportError("a_manifest_unreadable") from exc
        if exc.code == "resource_too_large":
            raise PreparedImportError("a_manifest_too_large") from exc
        raise PreparedImportError(exc.code) from exc
    try:
        projection = parse_a_manifest_bytes(a_payload)
    except ValueError as exc:
        errors = getattr(exc, "errors", (getattr(exc, "code", "a_manifest_invalid"),))
        raise PreparedImportError("a_manifest_invalid", errors) from exc
    cut, semantics = _read_policy(prepared)
    identities = load_prepared_identities(prepared, projection, cut)
    try:
        context_result = verify_prepared_context(
            prepared, projection, identities
        )
    except PreparedContextError as exc:
        raise PreparedImportError("prepared_context_invalid", exc.errors) from exc
    manifest_document, labels_document = _build_documents(
        projection, cut, semantics, manifest_id, identities, context_result
    )
    manifest_bytes = serialize_task_manifest(manifest_document)
    labels_bytes = serialize_labels(labels_document)
    try:
        parsed_manifest = parse_task_manifest_bytes(manifest_bytes)
        parsed_labels = parse_labels_bytes(labels_bytes)
        verify_labels_cover_manifest(parsed_labels, parsed_manifest)
    except ValueError as exc:
        raise PreparedImportError("imported_document_invalid") from exc

    manifest_target = Path(manifest_output)
    labels_target = Path(labels_output)
    created: list[Path] = []
    try:
        _write_new(manifest_target, manifest_bytes)
        created.append(manifest_target)
        _write_new(labels_target, labels_bytes)
        created.append(labels_target)
    except PreparedImportError:
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    return PreparedImportResult(
        manifest_sha256=parsed_manifest.raw_sha256,
        labels_sha256=parsed_labels.raw_sha256,
        task_count=len(parsed_manifest.tasks),
        label_count=len(parsed_labels.by_task),
        split_counts=dict(parsed_manifest.split_counts),
        history_cut_date=parsed_manifest.history_cut_date,
        context_verification=context_result.safe_summary(),
    )


def verify_a_prepared(
    prepared_dir: str | Path,
    manifest_path: str | Path,
    run_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> dict[str, Any]:
    """Independently verify a B manifest (and optional A run) against prepared files.

    The check fails closed if the B manifest was not imported from the same A
    manifest, if any task identity/date/context/history bound is missing, or if
    the optional run artifact's context digest does not match the recomputed
    runtime context for every task it claims.
    """
    prepared = Path(prepared_dir)
    try:
        a_payload = read_path_bytes(
            prepared / "manifest.json", max_bytes=MAX_MANIFEST_BYTES
        )
    except SafetyError as exc:
        if exc.code == "resource_unreadable":
            raise PreparedImportError("a_manifest_unreadable") from exc
        if exc.code == "resource_too_large":
            raise PreparedImportError("a_manifest_too_large") from exc
        raise PreparedImportError(exc.code) from exc
    projection = parse_a_manifest_bytes(a_payload)
    cut, _semantics = _read_policy(prepared)
    identities = load_prepared_identities(prepared, projection, cut)
    try:
        b_payload = read_path_bytes(
            manifest_path, max_bytes=MAX_MANIFEST_BYTES
        )
    except SafetyError as exc:
        if exc.code == "resource_unreadable":
            raise PreparedImportError("task_manifest_unreadable") from exc
        if exc.code == "resource_too_large":
            raise PreparedImportError("task_manifest_too_large") from exc
        raise PreparedImportError(exc.code) from exc
    try:
        task_manifest = parse_task_manifest_bytes(b_payload)
    except ValueError as exc:
        raise PreparedImportError("task_manifest_invalid") from exc
    if task_manifest.origin_kind != "a_prepared_v1":
        raise PreparedImportError("task_manifest_not_a_prepared")
    if task_manifest.source_manifest_sha256 != projection.manifest_sha256:
        raise PreparedImportError("task_manifest_source_hash_mismatch")

    run_view = None
    if run_path is not None:
        try:
            run_payload = read_path_bytes(
                run_path, max_bytes=contract.MAX_ARTIFACT_BYTES
            )
        except SafetyError as exc:
            if exc.code == "resource_unreadable":
                raise PreparedImportError("run_artifact_unreadable") from exc
            if exc.code == "resource_too_large":
                raise PreparedImportError("run_artifact_too_large") from exc
            raise PreparedImportError(exc.code) from exc
        loaded = load_run_artifact(run_payload)
        if not loaded.valid or loaded.view is None:
            raise PreparedImportError(
                "run_contract_invalid", tuple(loaded.errors or ("run_contract_invalid",))
            )
        run_view = loaded.view
        if not set(run_view.by_task).issubset(
            {row.task_index for row in task_manifest.tasks}
        ):
            raise PreparedImportError("run_task_not_in_manifest")

    try:
        context_result = verify_prepared_context(
            prepared, projection, identities, run_view
        )
    except PreparedContextError as exc:
        raise PreparedImportError("prepared_context_invalid", exc.errors) from exc

    b_by_index = task_manifest.task_by_index
    if set(b_by_index) != {item.task_index for item in identities}:
        raise PreparedImportError("task_manifest_task_set_mismatch")
    for identity in identities:
        row = b_by_index[identity.task_index]
        if (
            row.execution_order != identity.execution_order
            or row.split != identity.split
            or row.cohort_group != identity.cohort_group
            or row.target_date != identity.target_date
            or row.target_timestamp_sha256 != identity.target_timestamp_sha256
            or row.manifest_context_sha256 != identity.manifest_context_sha256
            or row.run_context_sha256
            != context_result.expected_run_context_by_task.get(identity.task_index)
            or row.history_max_date
            != context_result.history_max_by_task.get(identity.task_index)
        ):
            raise PreparedImportError("task_manifest_context_binding_mismatch")

    labels_verified = 0
    if labels_path is not None:
        try:
            labels = parse_labels_bytes(
                read_path_bytes(labels_path, max_bytes=MAX_LABELS_BYTES)
            )
        except SafetyError as exc:
            if exc.code == "resource_unreadable":
                raise PreparedImportError("labels_unreadable") from exc
            if exc.code == "resource_too_large":
                raise PreparedImportError("labels_too_large") from exc
            raise PreparedImportError(exc.code) from exc
        except ValueError as exc:
            raise PreparedImportError("labels_invalid") from exc
        if set(labels.by_task) != {item.task_index for item in identities}:
            raise PreparedImportError("labels_task_set_mismatch")
        for identity in identities:
            if labels.by_task[identity.task_index] != int(identity.target_stars):
                raise PreparedImportError("labels_value_mismatch")
        labels_verified = len(identities)

    return {
        "schema_version": "prepared_verification.v1",
        "status": "verified",
        "adapter_version": A_PREPARED_ADAPTER_VERSION,
        "a_manifest_sha256": projection.manifest_sha256,
        "task_manifest_sha256": task_manifest.raw_sha256,
        "run_context_checked_tasks": context_result.runtime_context_checked_tasks,
        "run_context_verified": context_result.runtime_context_verified,
        "labels_verified": labels_verified,
        "context": context_result.safe_summary(),
        "real_measurement_status": "not_verified_or_not_run",
        "source_review_text_reemitted": False,
        "raw_ids_reemitted": False,
    }
