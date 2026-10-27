"""Reader adapter for the canonical ``simulation_run.v1`` result contract.

Compatibility notes (version 1):

- The canonical contract is owned by this repository
  (``amazon_eval_agent/contracts/simulation_run_v1.py``). The recovered A work
  snapshot carries a mirror with the same schema hash; the older public A
  checkout does not. A missing mirror is not repaired by copying this file.
- A run record's ``context_sha256`` is the producer's runtime context digest.
  A's temporal manifest uses a *different* ``context_sha256`` (manifest
  context digest). The two must not be compared as if they were one field.
- A's exporter maps its manifest ``user_group`` (for example ``U0001``) through
  a deterministic hash to ``ug_<16hex>``. This adapter reimplements that small
  documented mapping so B can check the pairing label without importing A.
- Declared dataset/manifest fingerprints can be compared to independently
  hashed local files when the configuration binds them; the contract itself
  still supplies no evidence that a real model ran.

The adapter projects safe fields only and never retains review text.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from ..contracts import simulation_run_v1 as contract
from ..safety import deep_freeze, sha256_bytes

RUN_ADAPTER_VERSION = "a_run_reader_v1"
SUPPORTED_RUN_SCHEMA = "simulation_run.v1"
A_USER_GROUP_HASH_VERSION = "simulation_run.v1-usergroup.v1"


@dataclass(frozen=True)
class RunRecordView:
    task_index: int
    execution_order: int
    split: str
    user_group: str
    context_sha256: str
    status: str
    reason: str
    prediction_origin: str
    stars: int | float | None
    has_review: bool
    llm_attempts: int
    repair: str
    prompt_bytes: int
    memory: Mapping[str, int | str]
    evidence_ref_count: int


@dataclass(frozen=True)
class RunArtifactView:
    artifact_sha256: str
    schema_version: str
    schema_hash: str
    source_kind: str
    producer: str
    model_source: str
    dataset_fingerprint_sha256: str
    manifest_fingerprint_sha256: str
    config_fingerprint_sha256: str
    record_count: int
    status_counts: Mapping[str, int]
    prediction_success_count: int
    all_predictions_succeeded: bool
    records: tuple[RunRecordView, ...]

    @property
    def by_task(self) -> dict[int, RunRecordView]:
        return {row.task_index: row for row in self.records}

    def safe_summary(self) -> dict[str, Any]:
        return {
            "adapter_version": RUN_ADAPTER_VERSION,
            "schema_version": self.schema_version,
            "schema_hash": self.schema_hash,
            "artifact_sha256": self.artifact_sha256,
            "source_kind": self.source_kind,
            "producer": self.producer,
            "model_source": self.model_source,
            "record_count": self.record_count,
            "status_counts": dict(self.status_counts),
            "prediction_success_count": self.prediction_success_count,
            "all_predictions_succeeded": self.all_predictions_succeeded,
            "declared_fingerprints": {
                "dataset_fingerprint_sha256": self.dataset_fingerprint_sha256,
                "manifest_fingerprint_sha256": self.manifest_fingerprint_sha256,
                "config_fingerprint_sha256": self.config_fingerprint_sha256,
            },
            "fingerprint_verification": "caller_declaration_not_verified_by_shape",
            "real_measurement_status": "not_verified_or_not_run",
        }


@dataclass(frozen=True)
class RunLoad:
    valid: bool
    errors: tuple[str, ...]
    view: RunArtifactView | None


def expected_user_group(cohort_group: str) -> str:
    """Reimplementation of A's documented deterministic pairing-label hash."""
    if not isinstance(cohort_group, str) or not cohort_group:
        raise ValueError("invalid_cohort_group")
    digest = hashlib.sha256(
        b"simulation_run.v1-usergroup\0" + cohort_group.encode("utf-8")
    ).hexdigest()
    return "ug_" + digest[:16]


def _safe_int(value: Any) -> int:
    return value if type(value) is int else 0


def _project_record(record: Mapping[str, Any]) -> RunRecordView:
    diagnostics = record.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    memory = diagnostics.get("memory")
    memory = memory if isinstance(memory, dict) else {}
    safe_memory = {
        key: memory[key]
        for key in (
            "candidates",
            "time_eligible",
            "selected",
            "prompt_included",
            "filtered_injection",
            "write",
        )
        if key in memory and (type(memory[key]) in (int, str))
    }
    review = record.get("review")
    return RunRecordView(
        task_index=record["task_index"],
        execution_order=record["execution_order"],
        split=record["split"],
        user_group=record["user_group"],
        context_sha256=record["context_sha256"],
        status=record["status"],
        reason=record["reason"],
        prediction_origin=record["prediction_origin"],
        stars=record.get("stars"),
        has_review=isinstance(review, str) and bool(review.strip()),
        llm_attempts=_safe_int(diagnostics.get("llm_attempts")),
        repair=diagnostics.get("repair", "not_requested"),
        prompt_bytes=_safe_int(diagnostics.get("prompt_bytes")),
        memory=deep_freeze(safe_memory),
        evidence_ref_count=len(record.get("evidence_refs") or ()),
    )


def load_run_artifact(payload: bytes | bytearray | str) -> RunLoad:
    """Validate a run artifact and project safe fields; never echo its content."""
    validation = contract.validate_payload(payload)
    if not validation.valid or validation.document is None:
        return RunLoad(valid=False, errors=tuple(validation.errors), view=None)
    document = validation.document
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    records = tuple(_project_record(record) for record in document.records)
    view = RunArtifactView(
        artifact_sha256=sha256_bytes(raw),
        schema_version=document.schema_version,
        schema_hash=contract.schema_hash(),
        source_kind=document.source_kind,
        producer=document.producer,
        model_source=document.model_source,
        dataset_fingerprint_sha256=document.dataset_fingerprint_sha256,
        manifest_fingerprint_sha256=document.manifest_fingerprint_sha256,
        config_fingerprint_sha256=document.config_fingerprint_sha256,
        record_count=document.record_count,
        status_counts=deep_freeze(dict(document.status_counts)),
        prediction_success_count=document.prediction_success_count,
        all_predictions_succeeded=document.all_predictions_succeeded,
        records=records,
    )
    return RunLoad(valid=True, errors=(), view=view)
