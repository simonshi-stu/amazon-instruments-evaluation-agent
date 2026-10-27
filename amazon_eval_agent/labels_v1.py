"""B-local groundtruth labels (``local_labels.v1``).

Labels are intentionally a separate local artifact: the A run contract is
closed against groundtruth fields, and the scorer is the only component that
receives these values. The label file carries task identity and an integer
star rating only -- no review text, user/item ids, target dates or prompts.

Reports expose the label-set fingerprint and count, never the values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .safety import (
    MAX_LABELS_BYTES,
    deep_freeze,
    exact_keys,
    is_safe_id,
    load_json_bytes,
    sha256_bytes,
)
from .task_manifest_v1 import MAX_TASKS, TaskManifest

LABELS_SCHEMA_VERSION = "local_labels.v1"
LABELS_CONTRACT_VERSION = 1
LABELS_ADAPTER_VERSION = "local_labels.v1.reader"

TOP_FIELDS = ("schema_version", "label_set_id", "tasks")
TASK_FIELDS = ("task_index", "stars")


class LabelError(ValueError):
    """Safe label failure; no rating or identifier is echoed."""

    def __init__(self, code: str, errors: Sequence[str] = ()):
        self.code = code
        self.errors = tuple(sorted(set(errors))) if errors else (code,)
        super().__init__(code)


@dataclass(frozen=True)
class LabelSet:
    label_set_id: str
    by_task: Mapping[int, int]
    raw_sha256: str
    schema_version: str = LABELS_SCHEMA_VERSION

    def safe_summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_version": LABELS_CONTRACT_VERSION,
            "adapter_version": LABELS_ADAPTER_VERSION,
            "label_set_id": self.label_set_id,
            "label_count": len(self.by_task),
            "label_set_sha256": self.raw_sha256,
            "values_disclosed": False,
        }


def _closed(obj: Mapping[str, Any], fields: Sequence[str], errors: list[str]) -> None:
    roles = exact_keys(obj, fields, code="labels_schema")
    for role in roles:
        if role not in errors:
            errors.append(f"labels_{role}")


def parse_labels_bytes(payload: bytes | bytearray | str) -> LabelSet:
    loaded = load_json_bytes(payload, max_bytes=MAX_LABELS_BYTES)
    if not loaded.valid:
        raise LabelError(loaded.code or "labels_invalid")
    if not isinstance(loaded.document, dict):
        raise LabelError("labels_top_level_not_object")
    document = loaded.document
    errors: list[str] = []
    _closed(document, TOP_FIELDS, errors)
    if document.get("schema_version") != LABELS_SCHEMA_VERSION:
        errors.append("labels_schema_version_mismatch")
    label_set_id = document.get("label_set_id")
    if not is_safe_id(label_set_id):
        errors.append("labels_label_set_id_invalid")
    tasks = document.get("tasks")
    by_task: dict[int, int] = {}
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_TASKS:
        errors.append("labels_tasks_invalid")
    else:
        for row in tasks:
            if not isinstance(row, dict):
                errors.append("labels_task_invalid")
                continue
            _closed(row, TASK_FIELDS, errors)
            index = row.get("task_index")
            stars = row.get("stars")
            if (
                type(index) is not int
                or not 0 <= index < MAX_TASKS
                or index in by_task
            ):
                errors.append("labels_task_index_invalid")
                continue
            if type(stars) is not int or not 1 <= stars <= 5:
                errors.append("labels_stars_invalid")
                continue
            by_task[index] = stars
    if errors:
        raise LabelError("labels_invalid", errors)
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    return LabelSet(
        label_set_id=label_set_id,
        by_task=deep_freeze(by_task),
        raw_sha256=sha256_bytes(raw),
    )


def parse_labels_file(path: str) -> LabelSet:
    from .safety import read_path_bytes

    return parse_labels_bytes(read_path_bytes(path, max_bytes=MAX_LABELS_BYTES))


def verify_labels_cover_manifest(labels: LabelSet, manifest: TaskManifest) -> None:
    """The v1 label contract must cover exactly the manifest task set."""
    manifest_indexes = {row.task_index for row in manifest.tasks}
    label_indexes = set(labels.by_task)
    if manifest_indexes != label_indexes:
        raise LabelError("labels_task_set_mismatch")


def build_labels_document(
    label_set_id: str, labels_by_task: Mapping[int, int]
) -> dict[str, Any]:
    return {
        "schema_version": LABELS_SCHEMA_VERSION,
        "label_set_id": label_set_id,
        "tasks": [
            {"task_index": index, "stars": labels_by_task[index]}
            for index in sorted(labels_by_task)
        ],
    }


def serialize_labels(document: Mapping[str, Any]) -> bytes:
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
