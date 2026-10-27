"""Typed, versioned evaluation configuration (``evaluation_config.v1``).

The configuration is a closed, bounded JSON document. It names *logical*
resources and their expected content fingerprints; it never stores local
paths, review text, labels, prompts or user identifiers. A CLI binding maps a
logical resource id to a local file at verification time, and verification
hashes those bytes independently.

Version 1 intentionally covers one candidate scheme and an optional fixed
baseline prediction table. Metric computation is a later stage; this module
does not score anything.

The distinction that must survive every report:

- a *declared* fingerprint is a caller assertion;
- an *actual* fingerprint is computed by this project from local bytes;
- neither proves that a real model ran or that the data is legally open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .safety import (
    MAX_CONFIG_BYTES,
    canonical_json_bytes,
    exact_keys,
    is_plain_relative_name,
    is_safe_id,
    is_sha256,
    load_json_bytes,
    sha256_bytes,
    sha256_json,
)

CONFIG_SCHEMA_VERSION = "evaluation_config.v1"
CONFIG_CONTRACT_VERSION = 1
CONFIG_ADAPTER_VERSION = "evaluation_config.v1.reader"

PURPOSES = (
    "synthetic_contract_check",
    "local_audit_only",
    "real_evaluation_plan",
)
LICENSE_STATUSES = (
    "synthetic_fixture_only",
    "verified_public_use",
    "not_explicitly_verified",
    "restricted_research_only",
    "unknown",
)
REDISTRIBUTION_STATUSES = (
    "synthetic_only",
    "not_redistributed",
    "allowed_with_conditions",
    "unknown",
)
RESOURCE_KINDS = (
    "dataset_raw",
    "dataset_normalized",
    "task_manifest",
    "a_manifest_source",
    "local_labels",
    "run_artifact",
    "baseline_predictions",
)
CONDITION_ORIGINS = (
    "synthetic_fake_run",
    "fake_on_real_context",
    "real_model_run_declared",
)
BASELINE_MODES = ("global", "user", "item", "combined")
SPLITS = ("train", "validation", "test")
CONFIRMATION_SCOPES = ("preflight_only",)
TASK_MANIFEST_ADAPTERS = ("b_native_task_manifest.v1",)
LABEL_SCHEMAS = ("local_labels.v1",)

TOP_FIELDS = (
    "schema_version",
    "config_id",
    "purpose",
    "condition",
    "task_manifest",
    "labels",
    "baseline",
    "cohort",
    "temporal",
    "run_source_binding",
    "resources",
    "confirmation",
)
CONDITION_FIELDS = ("condition_id", "role", "origin", "run_resource_id")
TASK_MANIFEST_FIELDS = ("resource_id", "adapter")
LABELS_FIELDS = ("resource_id", "schema")
BASELINE_FIELDS = ("resource_id", "kind", "mode")
COHORT_FIELDS = (
    "development_splits",
    "holdout_splits",
    "allow_user_overlap",
    "min_common_holdout_tasks",
    "min_common_holdout_clusters",
)
TEMPORAL_FIELDS = (
    "require_verified_target_dates",
    "allow_declared_order_only_time",
    "require_targets_on_or_after_cut",
    "require_same_date_split_indivisible",
)
RUN_SOURCE_BINDING_FIELDS = (
    "dataset_resource_id",
    "manifest_resource_id",
    "dataset_fingerprint_policy",
    "manifest_fingerprint_policy",
)
RESOURCE_FIELDS = (
    "resource_id",
    "kind",
    "release",
    "expected_sha256",
    "expected_size_bytes",
    "license_status",
    "redistribution_status",
    "contains_personal_data",
)
CONFIRMATION_FIELDS = ("required", "scope")


class ConfigError(ValueError):
    """Safe configuration failure with a stable code and optional field roles."""

    def __init__(self, code: str, roles: Sequence[str] = ()):
        self.code = code
        self.roles = tuple(sorted(set(roles)))
        super().__init__(code)


@dataclass(frozen=True)
class ResourceDeclaration:
    resource_id: str
    kind: str
    release: str
    expected_sha256: str
    expected_size_bytes: int
    license_status: str
    redistribution_status: str
    contains_personal_data: bool


@dataclass(frozen=True)
class Condition:
    condition_id: str
    role: str
    origin: str
    run_resource_id: str


@dataclass(frozen=True)
class TaskManifestRef:
    resource_id: str
    adapter: str


@dataclass(frozen=True)
class LabelsRef:
    resource_id: str
    schema: str


@dataclass(frozen=True)
class BaselineRef:
    resource_id: str
    kind: str
    mode: str


@dataclass(frozen=True)
class CohortPolicy:
    development_splits: tuple[str, ...]
    holdout_splits: tuple[str, ...]
    allow_user_overlap: bool
    min_common_holdout_tasks: int
    min_common_holdout_clusters: int


@dataclass(frozen=True)
class TemporalPolicy:
    require_verified_target_dates: bool
    allow_declared_order_only_time: bool
    require_targets_on_or_after_cut: bool
    require_same_date_split_indivisible: bool


@dataclass(frozen=True)
class RunSourceBinding:
    dataset_resource_id: str
    manifest_resource_id: str
    dataset_fingerprint_policy: str
    manifest_fingerprint_policy: str


@dataclass(frozen=True)
class ConfirmationPolicy:
    required: bool
    scope: str


@dataclass(frozen=True)
class EvaluationConfig:
    config_id: str
    purpose: str
    condition: Condition
    task_manifest: TaskManifestRef
    labels: LabelsRef
    baseline: BaselineRef | None
    cohort: CohortPolicy
    temporal: TemporalPolicy
    run_source_binding: RunSourceBinding
    resources: tuple[ResourceDeclaration, ...]
    confirmation: ConfirmationPolicy
    config_sha256: str
    semantic_sha256: str

    def resource(self, resource_id: str) -> ResourceDeclaration:
        for item in self.resources:
            if item.resource_id == resource_id:
                return item
        raise ConfigError("resource_reference_missing")

    @property
    def resource_ids(self) -> tuple[str, ...]:
        return tuple(item.resource_id for item in self.resources)


def _required(obj: Mapping[str, Any], key: str, code: str) -> Any:
    if key not in obj:
        raise ConfigError(code, ("missing_field",))
    return obj[key]


def _expect_bool(value: Any, code: str) -> bool:
    if type(value) is not bool:
        raise ConfigError(code)
    return value


def _expect_object(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(code)
    return value


def _check_closed(obj: Mapping[str, Any], fields: Sequence[str], code: str) -> None:
    roles = exact_keys(obj, fields, code=code)
    if roles:
        raise ConfigError(code, roles)


def _parse_resource(raw: Any) -> ResourceDeclaration:
    obj = _expect_object(raw, "resource_invalid")
    _check_closed(obj, RESOURCE_FIELDS, "resource_unknown_field")
    resource_id = _required(obj, "resource_id", "resource_missing_field")
    if not is_safe_id(resource_id):
        raise ConfigError("resource_id_invalid")
    kind = _required(obj, "kind", "resource_missing_field")
    if kind not in RESOURCE_KINDS:
        raise ConfigError("resource_kind_invalid")
    release = _required(obj, "release", "resource_missing_field")
    if not is_plain_relative_name(release):
        raise ConfigError("resource_release_invalid")
    digest = _required(obj, "expected_sha256", "resource_missing_field")
    if not is_sha256(digest):
        raise ConfigError("resource_sha256_invalid")
    size = _required(obj, "expected_size_bytes", "resource_missing_field")
    if type(size) is not int or not 0 <= size <= 64 * 1024 * 1024:
        raise ConfigError("resource_size_invalid")
    license_status = _required(obj, "license_status", "resource_missing_field")
    if license_status not in LICENSE_STATUSES:
        raise ConfigError("resource_license_invalid")
    redistribution = _required(
        obj, "redistribution_status", "resource_missing_field"
    )
    if redistribution not in REDISTRIBUTION_STATUSES:
        raise ConfigError("resource_redistribution_invalid")
    personal = _required(obj, "contains_personal_data", "resource_missing_field")
    if type(personal) is not bool:
        raise ConfigError("resource_personal_flag_invalid")
    return ResourceDeclaration(
        resource_id=resource_id,
        kind=kind,
        release=release,
        expected_sha256=digest,
        expected_size_bytes=size,
        license_status=license_status,
        redistribution_status=redistribution,
        contains_personal_data=personal,
    )


def _parse_splits(value: Any, code: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(code)
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        if item not in SPLITS or item in seen:
            raise ConfigError(code)
        seen.add(item)
        result.append(item)
    return tuple(result)


def _parse_config_document(document: Mapping[str, Any]) -> tuple[
    str, str, Condition, TaskManifestRef, LabelsRef, BaselineRef | None,
    CohortPolicy, TemporalPolicy, RunSourceBinding, tuple[ResourceDeclaration, ...],
    ConfirmationPolicy,
]:
    _check_closed(document, TOP_FIELDS, "config_unknown_field")
    if document.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ConfigError("config_schema_version_mismatch")
    config_id = document.get("config_id")
    if not is_safe_id(config_id):
        raise ConfigError("config_id_invalid")
    purpose = document.get("purpose")
    if purpose not in PURPOSES:
        raise ConfigError("config_purpose_invalid")

    condition_raw = _expect_object(document.get("condition"), "condition_invalid")
    _check_closed(condition_raw, CONDITION_FIELDS, "condition_unknown_field")
    condition_id = condition_raw.get("condition_id")
    if not is_safe_id(condition_id):
        raise ConfigError("condition_id_invalid")
    if condition_raw.get("role") != "candidate":
        raise ConfigError("condition_role_invalid")
    origin = condition_raw.get("origin")
    if origin not in CONDITION_ORIGINS:
        raise ConfigError("condition_origin_invalid")
    run_resource_id = condition_raw.get("run_resource_id")
    if not is_safe_id(run_resource_id):
        raise ConfigError("condition_run_resource_invalid")
    condition = Condition(condition_id, "candidate", origin, run_resource_id)

    manifest_raw = _expect_object(
        document.get("task_manifest"), "task_manifest_ref_invalid"
    )
    _check_closed(manifest_raw, TASK_MANIFEST_FIELDS, "task_manifest_ref_unknown_field")
    if manifest_raw.get("adapter") not in TASK_MANIFEST_ADAPTERS:
        raise ConfigError("task_manifest_adapter_invalid")
    manifest_ref = TaskManifestRef(
        manifest_raw.get("resource_id"), manifest_raw.get("adapter")
    )
    if not is_safe_id(manifest_ref.resource_id):
        raise ConfigError("task_manifest_resource_invalid")

    labels_raw = _expect_object(document.get("labels"), "labels_ref_invalid")
    _check_closed(labels_raw, LABELS_FIELDS, "labels_ref_unknown_field")
    if labels_raw.get("schema") not in LABEL_SCHEMAS:
        raise ConfigError("labels_schema_invalid")
    labels_ref = LabelsRef(labels_raw.get("resource_id"), labels_raw.get("schema"))
    if not is_safe_id(labels_ref.resource_id):
        raise ConfigError("labels_resource_invalid")

    baseline_raw = document.get("baseline")
    baseline: BaselineRef | None = None
    if baseline_raw is not None:
        baseline_obj = _expect_object(baseline_raw, "baseline_ref_invalid")
        _check_closed(baseline_obj, BASELINE_FIELDS, "baseline_ref_unknown_field")
        if baseline_obj.get("kind") != "a_p0_predictions_v1":
            raise ConfigError("baseline_kind_invalid")
        if baseline_obj.get("mode") not in BASELINE_MODES:
            raise ConfigError("baseline_mode_invalid")
        resource_id = baseline_obj.get("resource_id")
        if not is_safe_id(resource_id):
            raise ConfigError("baseline_resource_invalid")
        baseline = BaselineRef(resource_id, "a_p0_predictions_v1", baseline_obj["mode"])

    cohort_raw = _expect_object(document.get("cohort"), "cohort_invalid")
    _check_closed(cohort_raw, COHORT_FIELDS, "cohort_unknown_field")
    development = _parse_splits(
        cohort_raw.get("development_splits"), "cohort_development_splits_invalid"
    )
    holdout = _parse_splits(
        cohort_raw.get("holdout_splits"), "cohort_holdout_splits_invalid"
    )
    if development != ("train", "validation") or holdout != ("test",):
        raise ConfigError("cohort_split_protocol_invalid")
    allow_overlap = _expect_bool(
        cohort_raw.get("allow_user_overlap"), "cohort_overlap_flag_invalid"
    )
    min_tasks = cohort_raw.get("min_common_holdout_tasks")
    if type(min_tasks) is not int or not 1 <= min_tasks <= 500:
        raise ConfigError("cohort_min_tasks_invalid")
    min_clusters = cohort_raw.get("min_common_holdout_clusters")
    if type(min_clusters) is not int or not 1 <= min_clusters <= 500:
        raise ConfigError("cohort_min_clusters_invalid")
    cohort = CohortPolicy(
        development, holdout, allow_overlap, min_tasks, min_clusters
    )

    temporal_raw = _expect_object(document.get("temporal"), "temporal_invalid")
    _check_closed(temporal_raw, TEMPORAL_FIELDS, "temporal_unknown_field")
    require_dates = _expect_bool(
        temporal_raw.get("require_verified_target_dates"),
        "temporal_require_dates_invalid",
    )
    allow_declared = _expect_bool(
        temporal_raw.get("allow_declared_order_only_time"),
        "temporal_allow_declared_invalid",
    )
    require_cut = _expect_bool(
        temporal_raw.get("require_targets_on_or_after_cut"),
        "temporal_require_cut_invalid",
    )
    require_same_day = _expect_bool(
        temporal_raw.get("require_same_date_split_indivisible"),
        "temporal_require_same_day_invalid",
    )
    if purpose in ("local_audit_only", "real_evaluation_plan") and allow_declared:
        raise ConfigError("temporal_declared_only_not_allowed_for_real_input")
    if purpose == "real_evaluation_plan" and not require_dates:
        raise ConfigError("temporal_dates_required_for_public_plan")
    temporal = TemporalPolicy(
        require_dates, allow_declared, require_cut, require_same_day
    )

    binding_raw = _expect_object(
        document.get("run_source_binding"), "run_source_binding_invalid"
    )
    _check_closed(
        binding_raw, RUN_SOURCE_BINDING_FIELDS, "run_source_binding_unknown_field"
    )
    dataset_resource_id = binding_raw.get("dataset_resource_id")
    manifest_resource_id = binding_raw.get("manifest_resource_id")
    if not is_safe_id(dataset_resource_id) or not is_safe_id(manifest_resource_id):
        raise ConfigError("run_source_binding_resource_invalid")
    if binding_raw.get("dataset_fingerprint_policy") != "file_sha256":
        raise ConfigError("run_dataset_binding_policy_invalid")
    if binding_raw.get("manifest_fingerprint_policy") != "file_sha256":
        raise ConfigError("run_manifest_binding_policy_invalid")
    run_binding = RunSourceBinding(
        dataset_resource_id,
        manifest_resource_id,
        "file_sha256",
        "file_sha256",
    )

    resources_raw = document.get("resources")
    if not isinstance(resources_raw, list) or not 1 <= len(resources_raw) <= 16:
        raise ConfigError("resources_invalid")
    resources = tuple(_parse_resource(item) for item in resources_raw)
    ids = [item.resource_id for item in resources]
    if len(set(ids)) != len(ids):
        raise ConfigError("resource_id_duplicate")

    refs = {
        condition.run_resource_id,
        manifest_ref.resource_id,
        labels_ref.resource_id,
        run_binding.dataset_resource_id,
        run_binding.manifest_resource_id,
    }
    if baseline is not None:
        refs.add(baseline.resource_id)
    missing = refs.difference(ids)
    if missing:
        raise ConfigError("resource_reference_missing")

    by_id = {item.resource_id: item for item in resources}
    if by_id[condition.run_resource_id].kind != "run_artifact":
        raise ConfigError("condition_run_resource_kind_invalid")
    if by_id[manifest_ref.resource_id].kind != "task_manifest":
        raise ConfigError("task_manifest_resource_kind_invalid")
    if by_id[labels_ref.resource_id].kind != "local_labels":
        raise ConfigError("labels_resource_kind_invalid")
    if by_id[run_binding.dataset_resource_id].kind != "dataset_normalized":
        raise ConfigError("run_dataset_resource_kind_invalid")
    if by_id[run_binding.manifest_resource_id].kind not in (
        "task_manifest",
        "a_manifest_source",
    ):
        raise ConfigError("run_manifest_resource_kind_invalid")
    if baseline is not None and by_id[baseline.resource_id].kind != "baseline_predictions":
        raise ConfigError("baseline_resource_kind_invalid")

    confirmation_raw = _expect_object(
        document.get("confirmation"), "confirmation_policy_invalid"
    )
    _check_closed(
        confirmation_raw, CONFIRMATION_FIELDS, "confirmation_policy_unknown_field"
    )
    required = _expect_bool(
        confirmation_raw.get("required"), "confirmation_required_invalid"
    )
    scope = confirmation_raw.get("scope")
    if scope not in CONFIRMATION_SCOPES:
        raise ConfigError("confirmation_scope_invalid")
    if purpose in ("local_audit_only", "real_evaluation_plan") and not required:
        raise ConfigError("confirmation_required_for_real_input")
    confirmation = ConfirmationPolicy(required, scope)

    if purpose == "real_evaluation_plan":
        for resource in resources:
            if resource.kind in ("dataset_raw", "dataset_normalized"):
                if resource.license_status != "verified_public_use":
                    raise ConfigError("license_unverified_for_public_plan")
    return (
        config_id,
        purpose,
        condition,
        manifest_ref,
        labels_ref,
        baseline,
        cohort,
        temporal,
        run_binding,
        resources,
        confirmation,
    )


def parse_config_bytes(payload: bytes | bytearray | str) -> EvaluationConfig:
    """Parse a closed ``evaluation_config.v1`` document; never scores."""
    loaded = load_json_bytes(payload, max_bytes=MAX_CONFIG_BYTES)
    if not loaded.valid:
        raise ConfigError(loaded.code or "config_invalid")
    if not isinstance(loaded.document, dict):
        raise ConfigError("config_top_level_not_object")
    parsed = _parse_config_document(loaded.document)
    semantic = sha256_json(loaded.document)
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    return EvaluationConfig(
        config_id=parsed[0],
        purpose=parsed[1],
        condition=parsed[2],
        task_manifest=parsed[3],
        labels=parsed[4],
        baseline=parsed[5],
        cohort=parsed[6],
        temporal=parsed[7],
        run_source_binding=parsed[8],
        resources=parsed[9],
        confirmation=parsed[10],
        config_sha256=sha256_bytes(raw),
        semantic_sha256=semantic,
    )


def parse_config_file(path: str) -> EvaluationConfig:
    from .safety import read_path_bytes

    payload = read_path_bytes(path, max_bytes=MAX_CONFIG_BYTES)
    return parse_config_bytes(payload)


def describe_config(config: EvaluationConfig) -> dict[str, Any]:
    """Safe config description; no paths, values or resource bindings."""
    return {
        "config_id": config.config_id,
        "schema_version": CONFIG_SCHEMA_VERSION,
        "contract_version": CONFIG_CONTRACT_VERSION,
        "adapter_version": CONFIG_ADAPTER_VERSION,
        "purpose": config.purpose,
        "config_sha256": config.config_sha256,
        "semantic_config_sha256": config.semantic_sha256,
        "condition": {
            "condition_id": config.condition.condition_id,
            "role": config.condition.role,
            "origin": config.condition.origin,
        },
        "task_manifest_adapter": config.task_manifest.adapter,
        "baseline_mode": config.baseline.mode if config.baseline else None,
        "resource_count": len(config.resources),
        "cohort": {
            "development_splits": list(config.cohort.development_splits),
            "holdout_splits": list(config.cohort.holdout_splits),
            "allow_user_overlap": config.cohort.allow_user_overlap,
            "min_common_holdout_tasks": config.cohort.min_common_holdout_tasks,
            "min_common_holdout_clusters": (
                config.cohort.min_common_holdout_clusters
            ),
        },
        "temporal": {
            "require_verified_target_dates": (
                config.temporal.require_verified_target_dates
            ),
            "allow_declared_order_only_time": (
                config.temporal.allow_declared_order_only_time
            ),
        },
        "confirmation": {
            "required": config.confirmation.required,
            "scope": config.confirmation.scope,
        },
    }


def canonical_config_payload(config: EvaluationConfig) -> bytes:
    """Canonical semantic payload used only for fingerprints (no paths)."""
    return canonical_json_bytes(
        {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "contract_version": CONFIG_CONTRACT_VERSION,
            "config": describe_config(config),
            "resources": [
                {
                    "resource_id": item.resource_id,
                    "kind": item.kind,
                    "release": item.release,
                    "expected_sha256": item.expected_sha256,
                    "expected_size_bytes": item.expected_size_bytes,
                    "license_status": item.license_status,
                    "redistribution_status": item.redistribution_status,
                    "contains_personal_data": item.contains_personal_data,
                }
                for item in config.resources
            ],
        }
    )
