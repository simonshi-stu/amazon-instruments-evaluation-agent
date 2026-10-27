"""B-owned task manifest (``task_manifest.v1``).

This is the evaluation side's canonical, text-free task list. It is built from
a synthetic fixture or imported from an already verified A prepared directory
through an explicit adapter. It carries task identity, split, cohort pseudonym,
target dates and context fingerprints -- never review text, groundtruth stars,
raw user/item ids, prompts or local paths.

Time protocol checks are deliberately split by strength:

- target dates present: dates are checked for cut, order and same-day
  indivisibility;
- target dates absent (older imports): only the declared execution order and
  split sequence can be checked, and the report must say so.

History bounds are a manifest declaration, not an independent re-reading of
the source. The report says ``not_recomputed`` when the manifest does not carry
them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from .safety import (
    MAX_MANIFEST_BYTES,
    deep_freeze,
    exact_keys,
    finite_number,
    is_plain_relative_name,
    is_safe_id,
    is_sha256,
    load_json_bytes,
    sha256_bytes,
)

TASK_MANIFEST_SCHEMA_VERSION = "task_manifest.v1"
TASK_MANIFEST_CONTRACT_VERSION = 1
TASK_MANIFEST_ADAPTER_VERSION = "task_manifest.v1.reader"
MAX_TASKS = 500
SPLITS = ("train", "validation", "test")
ORIGIN_KINDS = ("synthetic_fixture", "a_prepared_v1")
COHORT_GROUP_BINDINGS = ("direct", "run_user_group_hash_v1")
TIMESTAMP_SEMANTICS = (
    "synthetic_declared",
    "caller_asserted_amazon_2014_unix_review_time",
    "unavailable",
)
HISTORY_VISIBILITIES = (
    "strictly_before_target_date",
    "strictly_before_target_timestamp",
)
TARGET_REGIONS = ("on_or_after_cut_date",)
USER_HOLDOUT_POLICIES = ("allow_overlap_not_user_disjoint", "user_disjoint")

TOP_FIELDS = (
    "schema_version",
    "manifest_id",
    "origin",
    "protocol",
    "cohort_group_binding",
    "split_counts",
    "tasks",
)
ORIGIN_FIELDS = (
    "kind",
    "source_manifest_sha256",
    "source_task_count",
    "adapter",
)
PROTOCOL_FIELDS = (
    "history_cut_date",
    "timestamp_semantics",
    "split_policy",
    "history_visibility",
    "target_region",
    "user_holdout_policy",
)
SPLIT_POLICY_FIELDS = ("train_ratio", "validation_ratio", "ties")
TASK_FIELDS = (
    "task_index",
    "execution_order",
    "split",
    "cohort_group",
    "target_date",
    "target_timestamp_sha256",
    "manifest_context_sha256",
    "run_context_sha256",
    "history_max_date",
)
SPLIT_COUNT_FIELDS = SPLITS


class TaskManifestError(ValueError):
    """Safe manifest failure; errors never contain field values."""

    def __init__(self, code: str, errors: Sequence[str] = ()):
        self.code = code
        self.errors = tuple(sorted(set(errors))) if errors else (code,)
        super().__init__(code)


@dataclass(frozen=True)
class TaskRow:
    task_index: int
    execution_order: int
    split: str
    cohort_group: str
    target_date: str | None
    target_timestamp_sha256: str | None
    manifest_context_sha256: str
    run_context_sha256: str | None
    history_max_date: str | None

    def target_date_value(self) -> date | None:
        return date.fromisoformat(self.target_date) if self.target_date else None

    def history_max_date_value(self) -> date | None:
        return (
            date.fromisoformat(self.history_max_date)
            if self.history_max_date
            else None
        )


@dataclass(frozen=True)
class TaskManifest:
    manifest_id: str
    origin_kind: str
    source_manifest_sha256: str | None
    source_task_count: int | None
    adapter: str
    history_cut_date: str
    timestamp_semantics: str
    train_ratio: float
    validation_ratio: float
    history_visibility: str
    target_region: str
    user_holdout_policy: str
    cohort_group_binding: str
    split_counts: Mapping[str, int]
    tasks: tuple[TaskRow, ...]
    raw_sha256: str
    date_strength: str
    history_strength: str
    user_overlap: Mapping[str, int]
    schema_version: str = TASK_MANIFEST_SCHEMA_VERSION

    @property
    def task_by_index(self) -> dict[int, TaskRow]:
        return {row.task_index: row for row in self.tasks}

    @property
    def tasks_by_split(self) -> dict[str, tuple[TaskRow, ...]]:
        return {
            split: tuple(row for row in self.tasks if row.split == split)
            for split in SPLITS
        }

    @property
    def users_by_split(self) -> dict[str, set[str]]:
        return {
            split: {row.cohort_group for row in self.tasks if row.split == split}
            for split in SPLITS
        }

    def safe_summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_version": TASK_MANIFEST_CONTRACT_VERSION,
            "adapter_version": TASK_MANIFEST_ADAPTER_VERSION,
            "manifest_id": self.manifest_id,
            "origin_kind": self.origin_kind,
            "adapter": self.adapter,
            "manifest_sha256": self.raw_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "source_task_count": self.source_task_count,
            "task_count": len(self.tasks),
            "split_counts": dict(self.split_counts),
            "user_cluster_counts": {
                split: len(users) for split, users in self.users_by_split.items()
            },
            "user_overlap_counts": dict(self.user_overlap),
            "history_cut_date": self.history_cut_date,
            "timestamp_semantics": self.timestamp_semantics,
            "split_policy": {
                "train_ratio": self.train_ratio,
                "validation_ratio": self.validation_ratio,
                "ties": "indivisible_group",
            },
            "date_strength": self.date_strength,
            "history_strength": self.history_strength,
            "cohort_group_binding": self.cohort_group_binding,
            "user_holdout_policy": self.user_holdout_policy,
        }


class _Collector:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def add(self, code: str) -> None:
        if code not in self.errors and len(self.errors) < 32:
            self.errors.append(code)


def _closed(obj: Mapping[str, Any], fields: Sequence[str], collector: _Collector) -> None:
    roles = exact_keys(obj, fields, code="manifest_schema")
    for role in roles:
        collector.add(f"manifest_{role}")


def _expect_object(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise TaskManifestError(code)
    return value


def _date(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat()


def _ratio(value: Any) -> float | None:
    number = finite_number(value)
    if number is None or not 0.0 <= number < 1.0:
        return None
    return number


def _user_overlap(manifest_rows: Sequence[TaskRow]) -> dict[str, int]:
    groups = {
        split: {row.cohort_group for row in manifest_rows if row.split == split}
        for split in SPLITS
    }
    return {
        "train_validation": len(groups["train"] & groups["validation"]),
        "train_test": len(groups["train"] & groups["test"]),
        "validation_test": len(groups["validation"] & groups["test"]),
    }


def parse_task_manifest_bytes(payload: bytes | bytearray | str) -> TaskManifest:
    """Parse and internally check a B task manifest; no scoring."""
    loaded = load_json_bytes(payload, max_bytes=MAX_MANIFEST_BYTES)
    if not loaded.valid:
        raise TaskManifestError(loaded.code or "manifest_invalid")
    if not isinstance(loaded.document, dict):
        raise TaskManifestError("manifest_top_level_not_object")
    document = loaded.document
    collector = _Collector()
    _closed(document, TOP_FIELDS, collector)
    if document.get("schema_version") != TASK_MANIFEST_SCHEMA_VERSION:
        collector.add("manifest_schema_version_mismatch")
    manifest_id = document.get("manifest_id")
    if not is_safe_id(manifest_id):
        collector.add("manifest_id_invalid")

    origin = _expect_object(document.get("origin"), "manifest_origin_invalid")
    _closed(origin, ORIGIN_FIELDS, collector)
    origin_kind = origin.get("kind")
    if origin_kind not in ORIGIN_KINDS:
        collector.add("manifest_origin_kind_invalid")
    source_sha = origin.get("source_manifest_sha256")
    if source_sha is not None and not is_sha256(source_sha):
        collector.add("manifest_source_sha256_invalid")
    if origin_kind == "a_prepared_v1" and not is_sha256(source_sha):
        collector.add("manifest_source_sha256_required")
    source_task_count = origin.get("source_task_count")
    if source_task_count is not None and (
        type(source_task_count) is not int
        or not 1 <= source_task_count <= MAX_TASKS
    ):
        collector.add("manifest_source_task_count_invalid")
    adapter = origin.get("adapter")
    if not is_plain_relative_name(adapter, max_length=100):
        collector.add("manifest_adapter_invalid")

    protocol = _expect_object(document.get("protocol"), "manifest_protocol_invalid")
    _closed(protocol, PROTOCOL_FIELDS, collector)
    cut = _date(protocol.get("history_cut_date"))
    if cut is None:
        collector.add("manifest_history_cut_invalid")
    semantics = protocol.get("timestamp_semantics")
    if semantics not in TIMESTAMP_SEMANTICS:
        collector.add("manifest_timestamp_semantics_invalid")
    split_policy = _expect_object(
        protocol.get("split_policy"), "manifest_split_policy_invalid"
    )
    _closed(split_policy, SPLIT_POLICY_FIELDS, collector)
    train_ratio = _ratio(split_policy.get("train_ratio"))
    validation_ratio = _ratio(split_policy.get("validation_ratio"))
    if train_ratio is None or train_ratio <= 0:
        collector.add("manifest_train_ratio_invalid")
    if validation_ratio is None:
        collector.add("manifest_validation_ratio_invalid")
    if (
        train_ratio is not None
        and validation_ratio is not None
        and train_ratio + validation_ratio >= 1.0
    ):
        collector.add("manifest_split_ratios_invalid")
    if split_policy.get("ties") != "indivisible_group":
        collector.add("manifest_tie_policy_invalid")
    visibility = protocol.get("history_visibility")
    if visibility not in HISTORY_VISIBILITIES:
        collector.add("manifest_history_visibility_invalid")
    target_region = protocol.get("target_region")
    if target_region not in TARGET_REGIONS:
        collector.add("manifest_target_region_invalid")
    holdout_policy = protocol.get("user_holdout_policy")
    if holdout_policy not in USER_HOLDOUT_POLICIES:
        collector.add("manifest_user_holdout_policy_invalid")

    binding = document.get("cohort_group_binding")
    if binding not in COHORT_GROUP_BINDINGS:
        collector.add("manifest_cohort_binding_invalid")

    split_counts_raw = _expect_object(
        document.get("split_counts"), "manifest_split_counts_invalid"
    )
    _closed(split_counts_raw, SPLIT_COUNT_FIELDS, collector)
    split_counts: dict[str, int] = {}
    for split in SPLITS:
        value = split_counts_raw.get(split)
        if type(value) is not int or not 0 <= value <= MAX_TASKS:
            collector.add("manifest_split_counts_invalid")
        else:
            split_counts[split] = value

    tasks_raw = document.get("tasks")
    rows: list[TaskRow] = []
    if not isinstance(tasks_raw, list) or not 1 <= len(tasks_raw) <= MAX_TASKS:
        collector.add("manifest_tasks_invalid")
    else:
        seen_indices: set[int] = set()
        seen_orders: set[int] = set()
        for raw in tasks_raw:
            if not isinstance(raw, dict):
                collector.add("manifest_task_invalid")
                continue
            _closed(raw, TASK_FIELDS, collector)
            index = raw.get("task_index")
            order = raw.get("execution_order")
            if (
                type(index) is not int
                or not 0 <= index < MAX_TASKS
                or index in seen_indices
            ):
                collector.add("manifest_task_index_invalid")
            else:
                seen_indices.add(index)
            if (
                type(order) is not int
                or not 0 <= order < MAX_TASKS
                or order in seen_orders
            ):
                collector.add("manifest_execution_order_invalid")
            else:
                seen_orders.add(order)
            split = raw.get("split")
            if split not in SPLITS:
                collector.add("manifest_split_invalid")
            group = raw.get("cohort_group")
            if not (
                isinstance(group, str)
                and (
                    (group.startswith("U") and group[1:].isdigit() and 1 <= len(group[1:]) <= 12)
                    or (
                        group.startswith("ug_")
                        and len(group) == 19
                        and all(char in "0123456789abcdef" for char in group[3:])
                    )
                )
            ):
                collector.add("manifest_cohort_group_invalid")
            target_date = raw.get("target_date")
            if target_date is not None and _date(target_date) is None:
                collector.add("manifest_target_date_invalid")
            timestamp_sha = raw.get("target_timestamp_sha256")
            if timestamp_sha is not None and not is_sha256(timestamp_sha):
                collector.add("manifest_target_timestamp_sha256_invalid")
            if target_date is None and timestamp_sha is None:
                collector.add("manifest_target_time_missing")
            context_sha = raw.get("manifest_context_sha256")
            if not is_sha256(context_sha):
                collector.add("manifest_context_sha256_invalid")
            run_context_sha = raw.get("run_context_sha256")
            if run_context_sha is not None and not is_sha256(run_context_sha):
                collector.add("manifest_run_context_sha256_invalid")
            history_max = raw.get("history_max_date")
            if history_max is not None and _date(history_max) is None:
                collector.add("manifest_history_max_date_invalid")
            rows.append(
                TaskRow(
                    task_index=index,
                    execution_order=order,
                    split=split,
                    cohort_group=group,
                    target_date=_date(target_date) if target_date else None,
                    target_timestamp_sha256=timestamp_sha,
                    manifest_context_sha256=context_sha,
                    run_context_sha256=run_context_sha,
                    history_max_date=_date(history_max) if history_max else None,
                )
            )

    if collector.errors:
        raise TaskManifestError("manifest_invalid", collector.errors)

    rows.sort(key=lambda row: row.execution_order)
    if [row.execution_order for row in rows] != list(range(len(rows))):
        raise TaskManifestError("manifest_execution_order_not_contiguous")
    computed_counts = {
        split: sum(row.split == split for row in rows) for split in SPLITS
    }
    if computed_counts != split_counts:
        raise TaskManifestError("manifest_split_counts_mismatch")
    if source_task_count is not None and source_task_count < len(rows):
        raise TaskManifestError("manifest_source_task_count_mismatch")

    split_rank = {split: position for position, split in enumerate(SPLITS)}
    previous_rank = 0
    previous_date: date | None = None
    date_values: list[date | None] = []
    history_values: list[date | None] = []
    same_date_splits: dict[date, set[str]] = {}
    timestamp_splits: dict[str, set[str]] = {}
    for row in rows:
        rank = split_rank[row.split]
        if rank < previous_rank:
            raise TaskManifestError("manifest_split_order_invalid")
        previous_rank = rank
        target = row.target_date_value()
        date_values.append(target)
        history_values.append(row.history_max_date_value())
        if target is not None:
            if previous_date is not None and target < previous_date:
                raise TaskManifestError("manifest_target_date_order_invalid")
            previous_date = target
            if cut is not None and target < date.fromisoformat(cut):
                raise TaskManifestError("manifest_target_before_cut")
            same_date_splits.setdefault(target, set()).add(row.split)
        if row.target_timestamp_sha256 is not None:
            # A target timestamp hash is not an ordering value; only equality
            # grouping is checked. Do not infer order from it.
            timestamp_splits.setdefault(row.target_timestamp_sha256, set()).add(
                row.split
            )
        if row.history_max_date_value() is not None and target is not None:
            if row.history_max_date_value() >= target:
                raise TaskManifestError("manifest_history_overlap")
    if any(value is not None for value in date_values) and any(
        value is None for value in date_values
    ):
        raise TaskManifestError("manifest_mixed_target_dates")
    if any(len(splits) > 1 for splits in same_date_splits.values()):
        raise TaskManifestError("manifest_same_date_spans_splits")
    if any(len(splits) > 1 for splits in timestamp_splits.values()):
        raise TaskManifestError("manifest_timestamp_group_spans_splits")

    date_strength = (
        "target_dates_checked" if all(value is not None for value in date_values)
        else "declared_execution_order_only"
    )
    if all(value is not None for value in history_values):
        history_strength = "declared_history_bound"
    elif any(value is not None for value in history_values):
        history_strength = "partial_declared_history_bound"
    else:
        history_strength = "not_recomputed"
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    return TaskManifest(
        manifest_id=manifest_id,
        origin_kind=origin_kind,
        source_manifest_sha256=source_sha,
        source_task_count=source_task_count,
        adapter=adapter,
        history_cut_date=cut or "",
        timestamp_semantics=semantics,
        train_ratio=train_ratio or 0.0,
        validation_ratio=validation_ratio or 0.0,
        history_visibility=visibility,
        target_region=target_region,
        user_holdout_policy=holdout_policy,
        cohort_group_binding=binding,
        split_counts=deep_freeze(computed_counts),
        tasks=tuple(rows),
        raw_sha256=sha256_bytes(raw),
        date_strength=date_strength,
        history_strength=history_strength,
        user_overlap=deep_freeze(_user_overlap(rows)),
    )


def parse_task_manifest_file(path: str) -> TaskManifest:
    from .safety import read_path_bytes

    payload = read_path_bytes(path, max_bytes=MAX_MANIFEST_BYTES)
    return parse_task_manifest_bytes(payload)


def serialize_task_manifest(document: Mapping[str, Any]) -> bytes:
    """Pretty, deterministic JSON for a newly imported B manifest."""
    return (
        json.dumps(
            document,
            sort_keys=True,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
