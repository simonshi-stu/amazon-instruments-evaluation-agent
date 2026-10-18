"""Stage-1 artifact validation and safe diagnostic reports.

This layer turns :func:`contracts.simulation_run_v1.validate_payload` outcomes
into a safe report that contains only counts, fixed enums and hex digests. It
never includes review text, user text, file paths or error messages.

Report semantics that must stay separate:

- ``artifact_validated``: the file is a valid ``simulation_run.v1`` artifact.
- ``all_predictions_succeeded``: every record is a validated model success.
  A valid artifact may still contain ``degraded``/``refused``/``failed``
  records; those are reported as their own counts and are never presented as
  successes.
- ``real_measurement_status`` is always ``not_verified_or_not_run``: declared
  source metadata is a caller declaration, not independent evidence of a real
  model run, and stage 1 computes no accuracy metric.

No scoring is available or triggered here; an invalid artifact fails closed
without any implicit continuation into later stages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts.simulation_run_v1 import (
    MAX_ARTIFACT_BYTES,
    SCHEMA_VERSION,
    ArtifactValidation,
    validate_payload,
)

TOOL_NAME = "amazon_eval_agent"
STAGE_NAME = "stage1_artifact_validation"

HONESTY_NOTES: tuple[str, ...] = (
    "stage 1 validates artifact shape and declared metadata only; it does "
    "not verify that a real model ran and computes no accuracy metric",
    "declared source kinds and fingerprints are caller assertions, not "
    "independently verified evidence",
    "a failed or refused record is never counted as a prediction success",
)


class ValidationError(ValueError):
    """Safe, categorized validation failure (stable code only)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _report_skeleton() -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "stage": STAGE_NAME,
        "contract_schema_version": SCHEMA_VERSION,
        "artifact_validated": False,
        "validation_errors": [],
        "declared_source_kind": None,
        "declared_producer": None,
        "declared_model_source": None,
        "simulated_content": None,
        "record_count": None,
        "status_counts": None,
        "prediction_success_count": None,
        "all_predictions_succeeded": None,
        "fingerprints": None,
        "fingerprint_verification": "declared_only_not_independently_verified",
        "real_measurement_status": "not_verified_or_not_run",
        "real_measurement_note": (
            "source metadata is a caller declaration; it is not independent "
            "evidence that a real model ran"
        ),
        "internal_consistency_note": (
            "diagnostic invariants and fingerprint shape checks prove record "
            "self-consistency only, never that a model call or fingerprint "
            "is externally real"
        ),
        "accuracy_claim": "none",
        "scoring_available": False,
        "stage1_scope_note": (
            "shape/safety validation only; full evaluation orchestration, "
            "metrics, LLM explanation and human-confirmed export are later "
            "stages"
        ),
        "honesty": list(HONESTY_NOTES),
    }


def build_report(validation: ArtifactValidation) -> dict[str, Any]:
    """Build the safe diagnostic report from one validation outcome."""
    report = _report_skeleton()
    if not validation.valid or validation.document is None:
        report["validation_errors"] = list(validation.errors)
        return report
    document = validation.document
    report.update(
        {
            "artifact_validated": True,
            "validation_errors": [],
            "declared_source_kind": document.source_kind,
            "declared_producer": document.producer,
            "declared_model_source": document.model_source,
            "simulated_content": True,
            "record_count": document.record_count,
            "status_counts": dict(document.status_counts),
            "prediction_success_count": document.prediction_success_count,
            "all_predictions_succeeded": document.all_predictions_succeeded,
            "fingerprints": {
                "dataset_fingerprint_sha256": document.dataset_fingerprint_sha256,
                "manifest_fingerprint_sha256": document.manifest_fingerprint_sha256,
                "config_fingerprint_sha256": document.config_fingerprint_sha256,
            },
        }
    )
    return report


def validate_bytes(payload: bytes | str) -> dict[str, Any]:
    """Validate raw artifact bytes/text and return a safe report."""
    return build_report(validate_payload(payload))


def validate_file(path: str | Path) -> dict[str, Any]:
    """Validate an artifact file with stat/read gates before parsing.

    The byte-size gate runs on ``stat`` before any read, the read itself is
    bounded to ``MAX_ARTIFACT_BYTES + 1`` bytes, and parsing only happens
    inside the contract validator.
    """
    artifact = Path(path)
    try:
        size = artifact.stat().st_size
    except OSError as exc:
        raise ValidationError("input_unreadable") from exc
    if size > MAX_ARTIFACT_BYTES:
        return build_report(
            ArtifactValidation(valid=False, errors=("artifact_too_large",), document=None)
        )
    try:
        with artifact.open("rb") as handle:
            data = handle.read(MAX_ARTIFACT_BYTES + 1)
    except OSError as exc:
        raise ValidationError("input_unreadable") from exc
    return validate_bytes(data)
