"""Projection reader for A's public temporal manifest schema 2.

Source interface: ``memory_audit._build_temporal_manifest_with_state`` in the
recovered A work snapshot (schema version 2, manifest type
``strict_temporal_no_groundtruth_context``). This is an explicit adapter, not a
copy of A's code and not a claim that B can rebuild A's manifest.

The adapter verifies the manifest's internal header, task identity, split
order, same-timestamp indivisibility, count fields and privacy declaration.
It does **not** verify A's ``source_review_sha256`` (a canonical parsed-record
fingerprint, not a raw file hash), target dates (only timestamp hashes are
present), or runtime context digests. Those limits are reported, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..safety import (
    MAX_MANIFEST_BYTES,
    exact_keys,
    finite_number,
    is_sha256,
    load_json_bytes,
    sha256_bytes,
)

A_MANIFEST_ADAPTER_VERSION = "a_temporal_manifest_v2.reader_v1"
A_MANIFEST_SCHEMA_VERSION = 2
A_MANIFEST_TYPE = "strict_temporal_no_groundtruth_context"
A_PRIVACY_STATEMENT = (
    "no raw IDs, groundtruth ratings, review text, or prompts included"
)
SPLITS = ("train", "validation", "test")
_TOP_FIELDS = (
    "schema_version",
    "manifest_type",
    "pairing",
    "source_review_files",
    "source_review_rows",
    "source_review_bytes",
    "source_review_sha256",
    "source_review_fingerprint_kind",
    "task_count",
    "eligible_task_count",
    "ineligible_task_count",
    "ineligible_reasons",
    "manifest_audit_status",
    "runtime_eligible",
    "runtime_rejection_reasons",
    "target_timestamp_semantics_verified",
    "target_timestamp_semantics_caller_asserted",
    "timestamp_semantics_confirmation",
    "split_policy",
    "timestamp_semantics_note",
    "target_review_row_match_counts",
    "review_context_suppression",
    "split_counts",
    "order_policy",
    "split_boundary_policy",
    "history_policy",
    "ablation_policy",
    "tasks",
    "privacy",
)
_TASK_FIELDS = (
    "task_index",
    "source_task_index_sha256",
    "target_timestamp_sha256",
    "execution_order",
    "split",
    "user_group",
    "user_repeat_stratum",
    "timestamp_available",
    "timestamp_source_field",
    "target_review_row_matched",
    "target_review_match_ambiguous",
    "target_timestamp_consistent",
    "visible_history_row_indexes",
    "target_item_reference_row_indexes",
    "visible_history_count",
    "target_item_reference_count",
    "target_text_absent_from_context",
    "context_sha256",
)
_FORBIDDEN_KEYS = {
    "user_id",
    "item_id",
    "target_review_id",
    "review_text",
    "text",
    "stars",
    "actual_stars",
    "target_stars",
    "review",
    "prompt",
}


class AManifestError(ValueError):
    """Safe adapter failure; no values or paths are echoed."""

    def __init__(self, code: str, errors: Sequence[str] = ()):
        self.code = code
        self.errors = tuple(sorted(set(errors))) if errors else (code,)
        super().__init__(code)


@dataclass(frozen=True)
class AManifestTask:
    task_index: int
    execution_order: int
    split: str
    cohort_group: str
    target_timestamp_sha256: str
    manifest_context_sha256: str
    visible_history_count: int
    target_item_reference_count: int
    user_repeat_stratum: str
    source_task_index_sha256: str | None
    visible_history_row_indexes: tuple[int, ...]
    target_item_reference_row_indexes: tuple[int, ...]
    target_text_absent_from_context: bool


@dataclass(frozen=True)
class AManifestProjection:
    manifest_sha256: str
    task_count: int
    eligible_task_count: int
    ineligible_task_count: int
    source_review_rows: int
    source_review_files: int
    source_review_bytes: int
    source_review_sha256: str
    split_counts: Mapping[str, int]
    train_ratio: float
    validation_ratio: float
    tasks: tuple[AManifestTask, ...]
    adapter_version: str = A_MANIFEST_ADAPTER_VERSION

    def safe_summary(self) -> dict[str, Any]:
        return {
            "adapter_version": self.adapter_version,
            "manifest_sha256": self.manifest_sha256,
            "task_count": self.task_count,
            "eligible_task_count": self.eligible_task_count,
            "ineligible_task_count": self.ineligible_task_count,
            "source_review_rows": self.source_review_rows,
            "source_review_files": self.source_review_files,
            "source_review_bytes": self.source_review_bytes,
            "source_review_sha256_declared": self.source_review_sha256,
            "source_review_verification": "declared_not_recomputed",
            "split_counts": dict(self.split_counts),
            "target_dates_available": False,
            "target_time_visibility": "sha256_only",
        }


def _contains_forbidden_key(value: Any) -> bool:
    stack: list[Any] = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, child in node.items():
                if key in _FORBIDDEN_KEYS:
                    return True
                stack.append(child)
        elif isinstance(node, list):
            stack.extend(node)
    return False


def _closed(obj: Mapping[str, Any], fields: Sequence[str], errors: list[str]) -> None:
    for role in sorted(exact_keys(obj, fields, code="a_manifest_schema")):
        errors.append(f"a_manifest_{role}")


def parse_a_manifest_bytes(payload: bytes | bytearray | str) -> AManifestProjection:
    loaded = load_json_bytes(payload, max_bytes=MAX_MANIFEST_BYTES)
    if not loaded.valid:
        raise AManifestError(loaded.code or "a_manifest_invalid")
    if not isinstance(loaded.document, dict):
        raise AManifestError("a_manifest_top_level_not_object")
    document = loaded.document
    errors: list[str] = []
    _closed(document, _TOP_FIELDS, errors)
    if _contains_forbidden_key(document):
        errors.append("a_manifest_private_field")
    if document.get("schema_version") != A_MANIFEST_SCHEMA_VERSION:
        errors.append("a_manifest_schema_version_mismatch")
    if document.get("manifest_type") != A_MANIFEST_TYPE:
        errors.append("a_manifest_type_mismatch")
    if document.get("privacy") != A_PRIVACY_STATEMENT:
        errors.append("a_manifest_privacy_statement_missing")
    if document.get("target_timestamp_semantics_verified") is not False:
        errors.append("a_manifest_timestamp_semantics_claim_invalid")
    if document.get("target_timestamp_semantics_caller_asserted") is not True:
        errors.append("a_manifest_timestamp_semantics_assertion_missing")
    if document.get("timestamp_semantics_confirmation") != "caller_asserted":
        errors.append("a_manifest_timestamp_confirmation_invalid")
    if document.get("source_review_fingerprint_kind") != "canonical_parsed_records_v1":
        errors.append("a_manifest_source_fingerprint_kind_invalid")
    if document.get("manifest_audit_status") != "ready_for_runtime_review":
        errors.append("a_manifest_not_runtime_ready")
    if document.get("runtime_eligible") is not True:
        errors.append("a_manifest_not_runtime_eligible")

    counts: dict[str, int] = {}
    for field in (
        "task_count",
        "eligible_task_count",
        "ineligible_task_count",
        "source_review_rows",
        "source_review_files",
        "source_review_bytes",
    ):
        value = document.get(field)
        if type(value) is not int or value < 0:
            errors.append("a_manifest_count_invalid")
        else:
            counts[field] = value
    if counts.get("source_review_rows", 0) <= 0:
        errors.append("a_manifest_source_rows_empty")
    if counts.get("source_review_files", 0) <= 0:
        errors.append("a_manifest_source_files_empty")
    if not is_sha256(document.get("source_review_sha256")):
        errors.append("a_manifest_source_review_sha256_invalid")

    split_policy = document.get("split_policy")
    train_ratio = validation_ratio = 0.0
    if not isinstance(split_policy, dict):
        errors.append("a_manifest_split_policy_invalid")
    else:
        _closed(split_policy, ("train_ratio", "validation_ratio", "timestamp_ties"), errors)
        train_ratio = finite_number(split_policy.get("train_ratio")) or 0.0
        validation_ratio = finite_number(split_policy.get("validation_ratio")) or 0.0
        if not 0.0 < train_ratio < 1.0 or not 0.0 <= validation_ratio < 1.0:
            errors.append("a_manifest_split_ratio_invalid")
        if train_ratio + validation_ratio >= 1.0:
            errors.append("a_manifest_split_ratio_invalid")
        if split_policy.get("timestamp_ties") != "indivisible_group":
            errors.append("a_manifest_tie_policy_invalid")

    split_counts_raw = document.get("split_counts")
    split_counts: dict[str, int] = {}
    if not isinstance(split_counts_raw, dict):
        errors.append("a_manifest_split_counts_invalid")
    else:
        _closed(split_counts_raw, SPLITS, errors)
        for split in SPLITS:
            value = split_counts_raw.get(split)
            if type(value) is not int or value < 0:
                errors.append("a_manifest_split_counts_invalid")
            else:
                split_counts[split] = value

    match_counts = document.get("target_review_row_match_counts")
    if not isinstance(match_counts, dict):
        errors.append("a_manifest_target_match_counts_invalid")
    else:
        _closed(match_counts, ("unique", "ambiguous", "unmatched"), errors)
        values = [match_counts.get(key) for key in ("unique", "ambiguous", "unmatched")]
        if any(type(value) is not int or value < 0 for value in values):
            errors.append("a_manifest_target_match_counts_invalid")
        elif "task_count" in counts and sum(values) != counts["task_count"]:
            errors.append("a_manifest_target_match_counts_mismatch")

    tasks_raw = document.get("tasks")
    tasks: list[AManifestTask] = []
    if not isinstance(tasks_raw, list) or not 1 <= len(tasks_raw) <= 500:
        errors.append("a_manifest_tasks_invalid")
    else:
        seen_indexes: set[int] = set()
        seen_orders: set[int] = set()
        for raw in tasks_raw:
            if not isinstance(raw, dict):
                errors.append("a_manifest_task_invalid")
                continue
            _closed(raw, _TASK_FIELDS, errors)
            index = raw.get("task_index")
            order = raw.get("execution_order")
            if (
                type(index) is not int
                or not 0 <= index < 500
                or index in seen_indexes
            ):
                errors.append("a_manifest_task_index_invalid")
            else:
                seen_indexes.add(index)
            if (
                type(order) is not int
                or not 0 <= order < 500
                or order in seen_orders
            ):
                errors.append("a_manifest_execution_order_invalid")
            else:
                seen_orders.add(order)
            split = raw.get("split")
            if split not in SPLITS:
                errors.append("a_manifest_split_invalid")
            cohort_group = raw.get("user_group")
            if not (
                isinstance(cohort_group, str)
                and cohort_group.startswith("U")
                and cohort_group[1:].isdigit()
                and 1 <= len(cohort_group[1:]) <= 12
            ):
                errors.append("a_manifest_user_group_invalid")
            timestamp_sha = raw.get("target_timestamp_sha256")
            if not is_sha256(timestamp_sha):
                errors.append("a_manifest_target_timestamp_sha256_invalid")
            context_sha = raw.get("context_sha256")
            if not is_sha256(context_sha):
                errors.append("a_manifest_context_sha256_invalid")
            if raw.get("target_text_absent_from_context") is not True:
                errors.append("a_manifest_target_text_not_absent")
            if raw.get("target_review_row_matched") is not True:
                errors.append("a_manifest_target_review_not_matched")
            if raw.get("target_review_match_ambiguous") is not False:
                errors.append("a_manifest_target_review_ambiguous")
            if raw.get("target_timestamp_consistent") is not True:
                errors.append("a_manifest_target_timestamp_inconsistent")
            if raw.get("timestamp_available") is not True:
                errors.append("a_manifest_target_timestamp_unavailable")
            history_indexes = raw.get("visible_history_row_indexes")
            reference_indexes = raw.get("target_item_reference_row_indexes")
            if not isinstance(history_indexes, list) or not all(
                type(value) is int and value >= 0 for value in history_indexes
            ):
                errors.append("a_manifest_history_indexes_invalid")
            if not isinstance(reference_indexes, list) or not all(
                type(value) is int and value >= 0 for value in reference_indexes
            ):
                errors.append("a_manifest_reference_indexes_invalid")
            if isinstance(history_indexes, list) and raw.get(
                "visible_history_count"
            ) != len(history_indexes):
                errors.append("a_manifest_history_count_mismatch")
            if isinstance(reference_indexes, list) and raw.get(
                "target_item_reference_count"
            ) != len(reference_indexes):
                errors.append("a_manifest_reference_count_mismatch")
            stratum = raw.get("user_repeat_stratum")
            if stratum not in ("first_seen", "repeat"):
                errors.append("a_manifest_stratum_invalid")
            source_task_sha = raw.get("source_task_index_sha256")
            if source_task_sha is not None and not is_sha256(source_task_sha):
                errors.append("a_manifest_source_task_sha_invalid")
            timestamp_field = raw.get("timestamp_source_field")
            if not isinstance(timestamp_field, str) or not timestamp_field:
                errors.append("a_manifest_timestamp_source_field_invalid")
            tasks.append(
                AManifestTask(
                    task_index=index,
                    execution_order=order,
                    split=split,
                    cohort_group=cohort_group,
                    target_timestamp_sha256=timestamp_sha,
                    manifest_context_sha256=context_sha,
                    visible_history_count=(
                        len(history_indexes)
                        if isinstance(history_indexes, list)
                        else 0
                    ),
                    target_item_reference_count=(
                        len(reference_indexes)
                        if isinstance(reference_indexes, list)
                        else 0
                    ),
                    user_repeat_stratum=stratum,
                    source_task_index_sha256=source_task_sha,
                    visible_history_row_indexes=tuple(history_indexes)
                    if isinstance(history_indexes, list)
                    else (),
                    target_item_reference_row_indexes=tuple(reference_indexes)
                    if isinstance(reference_indexes, list)
                    else (),
                    target_text_absent_from_context=(
                        raw.get("target_text_absent_from_context") is True
                    ),
                )
            )

    if errors:
        raise AManifestError("a_manifest_invalid", errors)

    tasks.sort(key=lambda row: row.execution_order)
    if [row.execution_order for row in tasks] != list(range(len(tasks))):
        raise AManifestError("a_manifest_execution_order_not_contiguous")
    computed_split_counts = {
        split: sum(row.split == split for row in tasks) for split in SPLITS
    }
    if computed_split_counts != split_counts:
        raise AManifestError("a_manifest_split_counts_mismatch")
    if counts.get("eligible_task_count") != len(tasks):
        raise AManifestError("a_manifest_eligible_count_mismatch")
    if counts.get("task_count") != (
        counts.get("eligible_task_count", 0) + counts.get("ineligible_task_count", 0)
    ):
        raise AManifestError("a_manifest_task_count_mismatch")
    rank = {split: index for index, split in enumerate(SPLITS)}
    previous_rank = 0
    timestamp_splits: dict[str, set[str]] = {}
    for row in tasks:
        if rank[row.split] < previous_rank:
            raise AManifestError("a_manifest_split_order_invalid")
        previous_rank = rank[row.split]
        timestamp_splits.setdefault(row.target_timestamp_sha256, set()).add(row.split)
    if any(len(splits) > 1 for splits in timestamp_splits.values()):
        raise AManifestError("a_manifest_timestamp_group_spans_splits")

    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    return AManifestProjection(
        manifest_sha256=sha256_bytes(raw),
        task_count=counts["task_count"],
        eligible_task_count=counts["eligible_task_count"],
        ineligible_task_count=counts["ineligible_task_count"],
        source_review_rows=counts["source_review_rows"],
        source_review_files=counts["source_review_files"],
        source_review_bytes=counts["source_review_bytes"],
        source_review_sha256=document["source_review_sha256"],
        split_counts=computed_split_counts,
        train_ratio=train_ratio,
        validation_ratio=validation_ratio,
        tasks=tuple(tasks),
    )
