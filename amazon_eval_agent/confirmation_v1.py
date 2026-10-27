"""Versioned manual confirmation bound to preflight fingerprints.

A confirmation is not a signature and does not prove human identity. It is a
local integrity record: the plan fingerprint and every bound content hash must
still match when the confirmation is verified. Changing the config, manifest,
labels, baseline table, run artifact or source bytes invalidates it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .contracts import simulation_run_v1 as contract
from .preflight_v1 import PreflightResult
from .safety import (
    exact_keys,
    is_plain_relative_name,
    is_sha256,
    load_json_bytes,
    sha256_json,
)

CONFIRMATION_SCHEMA_VERSION = "evaluation_confirmation.v1"
CONFIRMATION_SCOPE = "preflight_only_no_metrics"
CONFIRMATION_STATUS = "confirmed"
CONFIRMATION_FIELDS = (
    "schema_version",
    "confirmation_id",
    "status",
    "scope",
    "confirmed_at",
    "confirmed_by",
    "plan_fingerprint",
    "bindings",
    "contract_schema_hash",
    "real_measurement_status",
    "accuracy_claim",
    "note",
)
BINDING_FIELDS = (
    "config_sha256",
    "task_manifest_sha256",
    "labels_sha256",
    "baseline_predictions_sha256",
    "run_artifact_sha256",
    "source_resources",
    "task_manifest_origin_kind",
    "task_manifest_adapter",
)
SOURCE_BINDING_FIELDS = ("resource_id", "actual_sha256")


class ConfirmationError(ValueError):
    def __init__(self, code: str, errors: Sequence[str] = ()):
        self.code = code
        self.errors = tuple(sorted(set(errors))) if errors else (code,)
        super().__init__(code)


def _parse_confirmed_at(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise ConfirmationError("confirmation_time_invalid")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConfirmationError("confirmation_time_invalid") from exc
    if parsed.tzinfo is None:
        raise ConfirmationError("confirmation_time_requires_utc")
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ConfirmationError("confirmation_time_requires_utc")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_bindings(result: PreflightResult) -> list[dict[str, Any]]:
    return [
        {"resource_id": resource_id, "actual_sha256": actual_sha256}
        for resource_id, actual_sha256 in result.source_bindings
    ]


def _bindings(result: PreflightResult) -> dict[str, Any]:
    manifest = result.manifest
    labels = result.labels
    baseline = result.baseline
    run = result.run
    return {
        "config_sha256": result.config.config_sha256,
        "task_manifest_sha256": manifest.raw_sha256 if manifest else None,
        "labels_sha256": labels.raw_sha256 if labels else None,
        "baseline_predictions_sha256": baseline.raw_sha256 if baseline else None,
        "run_artifact_sha256": run.artifact_sha256 if run else None,
        "source_resources": _source_bindings(result),
        "task_manifest_origin_kind": manifest.origin_kind if manifest else None,
        "task_manifest_adapter": manifest.adapter if manifest else None,
    }


def create_confirmation(
    result: PreflightResult, *, confirmed_at: str
) -> dict[str, Any]:
    """Create a confirmation only for a ready preflight; never for blocked."""
    if not result.ready:
        raise ConfirmationError("preflight_not_ready")
    timestamp = _parse_confirmed_at(confirmed_at)
    bindings = _bindings(result)
    if any(
        bindings[field] is None
        for field in (
            "task_manifest_sha256",
            "labels_sha256",
            "run_artifact_sha256",
        )
    ):
        raise ConfirmationError("preflight_binding_incomplete")
    confirmation_id = "conf_" + sha256_json(
        {
            "plan_fingerprint": result.plan_fingerprint,
            "confirmed_at": timestamp,
        }
    )[:16]
    return {
        "schema_version": CONFIRMATION_SCHEMA_VERSION,
        "confirmation_id": confirmation_id,
        "status": CONFIRMATION_STATUS,
        "scope": CONFIRMATION_SCOPE,
        "confirmed_at": timestamp,
        "confirmed_by": "local_operator",
        "plan_fingerprint": result.plan_fingerprint,
        "bindings": bindings,
        "contract_schema_hash": contract.schema_hash(),
        "real_measurement_status": "not_verified_or_not_run",
        "accuracy_claim": "none",
        "note": (
            "local fingerprint binding only; not a cryptographic signature, "
            "identity proof, data-license approval or real-model verification"
        ),
    }


def serialize_confirmation(document: Mapping[str, Any]) -> bytes:
    import json

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


def parse_confirmation_bytes(payload: bytes | bytearray | str) -> dict[str, Any]:
    loaded = load_json_bytes(payload, max_bytes=512 * 1024)
    if not loaded.valid or not isinstance(loaded.document, dict):
        raise ConfirmationError("confirmation_invalid")
    document = loaded.document
    errors: list[str] = []
    roles = exact_keys(document, CONFIRMATION_FIELDS, code="confirmation_schema")
    errors.extend(f"confirmation_{role}" for role in roles)
    if document.get("schema_version") != CONFIRMATION_SCHEMA_VERSION:
        errors.append("confirmation_schema_version_mismatch")
    if document.get("status") != CONFIRMATION_STATUS:
        errors.append("confirmation_status_invalid")
    if document.get("scope") != CONFIRMATION_SCOPE:
        errors.append("confirmation_scope_invalid")
    if document.get("confirmed_by") != "local_operator":
        errors.append("confirmation_operator_invalid")
    if not is_sha256(document.get("plan_fingerprint")):
        errors.append("confirmation_plan_fingerprint_invalid")
    if not is_sha256(document.get("contract_schema_hash")):
        errors.append("confirmation_contract_hash_invalid")
    if document.get("real_measurement_status") != "not_verified_or_not_run":
        errors.append("confirmation_measurement_status_invalid")
    if document.get("accuracy_claim") != "none":
        errors.append("confirmation_accuracy_claim_invalid")
    confirmation_id = document.get("confirmation_id")
    if not (
        isinstance(confirmation_id, str)
        and confirmation_id.startswith("conf_")
        and len(confirmation_id) == 21
        and all(char in "0123456789abcdef" for char in confirmation_id[5:])
    ):
        errors.append("confirmation_id_invalid")
    try:
        _parse_confirmed_at(document.get("confirmed_at"))
    except ConfirmationError:
        errors.append("confirmation_time_invalid")
    bindings = document.get("bindings")
    if not isinstance(bindings, dict):
        errors.append("confirmation_bindings_invalid")
    else:
        roles = exact_keys(bindings, BINDING_FIELDS, code="confirmation_bindings")
        errors.extend(f"confirmation_binding_{role}" for role in roles)
        for field in (
            "config_sha256",
            "task_manifest_sha256",
            "labels_sha256",
            "run_artifact_sha256",
        ):
            if not is_sha256(bindings.get(field)):
                errors.append("confirmation_binding_sha_invalid")
        baseline_sha = bindings.get("baseline_predictions_sha256")
        if baseline_sha is not None and not is_sha256(baseline_sha):
            errors.append("confirmation_binding_sha_invalid")
        origin_kind = bindings.get("task_manifest_origin_kind")
        if origin_kind not in ("synthetic_fixture", "a_prepared_v1"):
            errors.append("confirmation_manifest_origin_invalid")
        if not is_plain_relative_name(bindings.get("task_manifest_adapter"), max_length=100):
            errors.append("confirmation_manifest_adapter_invalid")
        source_resources = bindings.get("source_resources")
        if not isinstance(source_resources, list) or not source_resources:
            errors.append("confirmation_source_bindings_invalid")
        else:
            for item in source_resources:
                if not isinstance(item, dict):
                    errors.append("confirmation_source_bindings_invalid")
                    continue
                roles = exact_keys(
                    item, SOURCE_BINDING_FIELDS, code="confirmation_source_binding"
                )
                errors.extend(
                    f"confirmation_source_{role}" for role in roles
                )
                if not isinstance(item.get("resource_id"), str) or not is_sha256(
                    item.get("actual_sha256")
                ):
                    errors.append("confirmation_source_bindings_invalid")
    if errors:
        raise ConfirmationError("confirmation_invalid", errors)
    return document


def verify_confirmation(
    confirmation: Mapping[str, Any], result: PreflightResult
) -> dict[str, Any]:
    """Recompute the current plan and compare every bound fingerprint."""
    mismatches: list[str] = []
    if not result.ready:
        mismatches.append("preflight_not_ready")
    if confirmation.get("plan_fingerprint") != result.plan_fingerprint:
        mismatches.append("plan_fingerprint_mismatch")
    expected_id = "conf_" + sha256_json(
        {
            "plan_fingerprint": confirmation.get("plan_fingerprint"),
            "confirmed_at": _parse_confirmed_at(confirmation.get("confirmed_at")),
        }
    )[:16]
    if confirmation.get("confirmation_id") != expected_id:
        mismatches.append("confirmation_id_mismatch")
    expected_bindings = _bindings(result)
    actual_bindings = confirmation.get("bindings")
    if not isinstance(actual_bindings, dict):
        mismatches.append("bindings_invalid")
        actual_bindings = {}
    field_codes = {
        "config_sha256": "config_binding_mismatch",
        "task_manifest_sha256": "task_manifest_binding_mismatch",
        "labels_sha256": "labels_binding_mismatch",
        "baseline_predictions_sha256": "baseline_binding_mismatch",
        "run_artifact_sha256": "run_artifact_binding_mismatch",
        "source_resources": "source_binding_mismatch",
        "task_manifest_origin_kind": "manifest_origin_binding_mismatch",
        "task_manifest_adapter": "manifest_adapter_binding_mismatch",
    }
    for field, code in field_codes.items():
        if actual_bindings.get(field) != expected_bindings[field]:
            mismatches.append(code)
    if confirmation.get("contract_schema_hash") != contract.schema_hash():
        mismatches.append("contract_schema_hash_mismatch")
    return {
        "confirmation_valid": not mismatches,
        "status": "valid" if not mismatches else "invalid",
        "scope": CONFIRMATION_SCOPE,
        "plan_fingerprint_match": (
            confirmation.get("plan_fingerprint") == result.plan_fingerprint
        ),
        "mismatches": sorted(set(mismatches)),
        "real_measurement_status": "not_verified_or_not_run",
        "accuracy_claim": "none",
    }


def confirmation_file_bytes(confirmation: Mapping[str, Any]) -> bytes:
    """Alias used by the CLI; keeps serialization in one place."""
    return serialize_confirmation(confirmation)
