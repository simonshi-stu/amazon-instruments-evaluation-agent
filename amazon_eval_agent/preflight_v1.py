"""Batch-1 preflight: typed config -> independently checked evaluation plan.

This stage answers *whether a comparison is executable*, not *which scheme is
better*. It parses the B task manifest and labels, verifies every declared
resource by hashing local bytes, validates the A run artifact against the
canonical contract, binds task identity/split/order/cohort labels, checks the
time protocol, verifies baseline prediction coverage and computes the common
holdout denominators. It computes **no** RMSE/MAE/interval/significance.

Schema validation is not source truth: a contract-valid artifact may declare
``real_model_declared`` while remaining unverified, and A's manifest
``source_review_sha256`` is never presented as a raw-file hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .adapters.a_p0_predictions_v1 import (
    BaselinePredictionTable,
    parse_baseline_predictions_bytes,
)
from .adapters.simulation_run_v1 import (
    RUN_ADAPTER_VERSION,
    RunArtifactView,
    expected_user_group,
    load_run_artifact,
)
from .config_v1 import EvaluationConfig
from .labels_v1 import (
    LABELS_CONTRACT_VERSION,
    LabelSet,
    parse_labels_bytes,
    verify_labels_cover_manifest,
)
from .resources_v1 import ResourceVerification, verify_resources
from .safety import canonical_json_bytes, deep_freeze, sha256_bytes
from .task_manifest_v1 import (
    SPLITS,
    TASK_MANIFEST_CONTRACT_VERSION,
    TaskManifest,
    TaskManifestError,
    parse_task_manifest_bytes,
)

PREFLIGHT_SCHEMA_VERSION = "evaluation_preflight.v1"
PREFLIGHT_STAGE = "batch1_preflight"
MAX_REPORTED_CODES = 32
_STATUSES = ("success", "degraded", "refused", "failed")


class PreflightError(ValueError):
    """Safe preflight failure for invalid calls, never content errors."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PreflightResult:
    report: Mapping[str, Any]
    plan_fingerprint: str
    ready: bool
    config: EvaluationConfig
    manifest: TaskManifest | None
    labels: LabelSet | None
    run: RunArtifactView | None
    baseline: BaselinePredictionTable | None
    source_bindings: tuple[tuple[str, str | None], ...]


class _Collector:
    def __init__(self) -> None:
        self.blockers: list[str] = []
        self.warnings: list[str] = []

    def block(self, code: str) -> None:
        if code not in self.blockers and len(self.blockers) < MAX_REPORTED_CODES:
            self.blockers.append(code)

    def warn(self, code: str) -> None:
        if code not in self.warnings and len(self.warnings) < MAX_REPORTED_CODES:
            self.warnings.append(code)


def _verification_blocker(verification: ResourceVerification) -> str:
    return {
        "missing_binding": "resource_missing_binding",
        "hash_mismatch": "resource_hash_mismatch",
        "size_mismatch": "resource_size_mismatch",
        "too_large": "resource_too_large",
    }.get(verification.integrity, "resource_integrity_failed")


def _split_counts_from_records(view: RunArtifactView) -> dict[str, dict[str, int]]:
    counts = {
        split: {status: 0 for status in _STATUSES} for split in SPLITS
    }
    for record in view.records:
        if record.split in counts and record.status in counts[record.split]:
            counts[record.split][record.status] += 1
    return counts


def _status_counts_full_cohort(
    manifest: TaskManifest, view: RunArtifactView | None, split: str
) -> dict[str, int]:
    counts = {status: 0 for status in _STATUSES}
    counts["missing"] = 0
    by_task = view.by_task if view is not None else {}
    for row in manifest.tasks:
        if row.split != split:
            continue
        record = by_task.get(row.task_index)
        if record is None:
            counts["missing"] += 1
        else:
            counts[record.status] += 1
    return counts


def _common_sets(
    manifest: TaskManifest,
    view: RunArtifactView | None,
    baseline: BaselinePredictionTable | None,
    mode: str | None,
) -> tuple[set[int], set[int], dict[str, int]]:
    holdout = {row.task_index for row in manifest.tasks if row.split == "test"}
    by_task = view.by_task if view is not None else {}
    candidate_model = {
        index
        for index in holdout
        if index in by_task and by_task[index].status == "success"
    }
    candidate_system = {
        index
        for index in holdout
        if index in by_task and by_task[index].status in ("success", "degraded")
    }
    excluded = {
        "missing_artifact": 0,
        "failed": 0,
        "refused": 0,
        "degraded_model_excluded": 0,
        "success": 0,
        "baseline_missing": 0,
    }
    for index in sorted(holdout):
        record = by_task.get(index)
        if record is None:
            excluded["missing_artifact"] += 1
        elif record.status == "failed":
            excluded["failed"] += 1
        elif record.status == "refused":
            excluded["refused"] += 1
        elif record.status == "degraded":
            excluded["degraded_model_excluded"] += 1
        else:
            excluded["success"] += 1
    if baseline is None or mode is None:
        return candidate_model, candidate_system, excluded
    predictions = baseline.select(mode)
    excluded["baseline_missing"] = sum(
        1 for index in holdout if index not in predictions
    )
    return (
        candidate_model & set(predictions),
        candidate_system & set(predictions),
        excluded,
    )


def _cluster_count(view: RunArtifactView | None, indexes: set[int]) -> int:
    if view is None:
        return 0
    by_task = view.by_task
    return len(
        {
            by_task[index].user_group
            for index in indexes
            if index in by_task
        }
    )


def _plan_binding_payload(report: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(
        {
            "preflight_schema_version": report["preflight_schema_version"],
            "config": {
                "config_sha256": report["evaluation_config"]["config_sha256"],
                "semantic_config_sha256": report["evaluation_config"][
                    "semantic_config_sha256"
                ],
            },
            "task_manifest": report["task_manifest"],
            "labels": report["labels"],
            "baseline": report["baseline"],
            "run": report["run"],
            "sources": report["sources"],
            "temporal": report["temporal"],
            "cohort": report["cohort"],
            "comparison": report["comparison"],
            "fingerprint_bindings": report["fingerprint_bindings"],
            "publication_status": report["publication_status"],
            "status": report["status"],
            "blockers": report["blockers"],
        }
    )


def run_preflight(
    config: EvaluationConfig,
    resource_bindings: Mapping[str, bytes],
) -> PreflightResult:
    """Run batch-1 checks over local resource bytes; never scores."""
    collector = _Collector()
    verifications = verify_resources(config.resources, resource_bindings)
    source_rows = [
        verifications[item.resource_id].safe_summary()
        for item in config.resources
    ]
    for verification in verifications.values():
        if not verification.verified:
            collector.block(_verification_blocker(verification))

    manifest: TaskManifest | None = None
    labels: LabelSet | None = None
    run: RunArtifactView | None = None
    baseline: BaselinePredictionTable | None = None
    manifest_errors: list[str] = []
    label_errors: list[str] = []
    run_errors: list[str] = []
    baseline_errors: list[str] = []

    manifest_resource = verifications[config.task_manifest.resource_id]
    if manifest_resource.verified:
        try:
            manifest = parse_task_manifest_bytes(
                resource_bindings[config.task_manifest.resource_id]
            )
        except TaskManifestError as exc:
            manifest_errors = list(exc.errors)
            collector.block("task_manifest_invalid")
    else:
        collector.block("task_manifest_unavailable")

    labels_resource = verifications[config.labels.resource_id]
    if labels_resource.verified:
        try:
            labels = parse_labels_bytes(resource_bindings[config.labels.resource_id])
        except ValueError as exc:
            label_errors = list(getattr(exc, "errors", ("labels_invalid",)))
            collector.block("labels_invalid")
    else:
        collector.block("labels_unavailable")

    if manifest is not None and labels is not None:
        try:
            verify_labels_cover_manifest(labels, manifest)
        except ValueError:
            collector.block("labels_task_set_mismatch")

    if config.baseline is not None:
        baseline_resource = verifications[config.baseline.resource_id]
        if baseline_resource.verified:
            try:
                baseline = parse_baseline_predictions_bytes(
                    resource_bindings[config.baseline.resource_id]
                )
            except ValueError as exc:
                baseline_errors = list(
                    getattr(exc, "errors", ("baseline_invalid",))
                )
                collector.block("baseline_invalid")
        else:
            collector.block("baseline_unavailable")

    run_resource = verifications[config.condition.run_resource_id]
    if run_resource.verified:
        loaded = load_run_artifact(resource_bindings[config.condition.run_resource_id])
        run = loaded.view
        if not loaded.valid:
            run_errors = list(loaded.errors)
            collector.block("run_contract_invalid")
    else:
        collector.block("run_unavailable")

    fingerprint_bindings: dict[str, Any] = {
        "dataset": {
            "resource_id": config.run_source_binding.dataset_resource_id,
            "policy": "actual_file_sha256",
            "actual_sha256": None,
            "run_declared_sha256": (
                run.dataset_fingerprint_sha256 if run else None
            ),
            "matches": False,
        },
        "manifest": {
            "resource_id": config.run_source_binding.manifest_resource_id,
            "policy": "actual_file_sha256",
            "actual_sha256": None,
            "run_declared_sha256": run.manifest_fingerprint_sha256 if run else None,
            "matches": False,
        },
        "config_fingerprint": {
            "run_declared_sha256": run.config_fingerprint_sha256 if run else None,
            "verification": "caller_declaration_not_independently_verified",
        },
    }
    dataset_verification = verifications[
        config.run_source_binding.dataset_resource_id
    ]
    manifest_binding_verification = verifications[
        config.run_source_binding.manifest_resource_id
    ]
    if manifest_binding_verification.verified:
        fingerprint_bindings["manifest"]["actual_sha256"] = (
            manifest_binding_verification.actual_sha256
        )
    if (
        manifest is not None
        and manifest_binding_verification.kind == "task_manifest"
        and manifest.raw_sha256 != manifest_binding_verification.actual_sha256
    ):
        collector.block("run_manifest_binding_resource_mismatch")
    if manifest is not None and manifest_binding_verification.kind == "a_manifest_source":
        if manifest.origin_kind != "a_prepared_v1":
            collector.block("manifest_source_binding_incompatible")
        elif manifest.source_manifest_sha256 != manifest_binding_verification.actual_sha256:
            collector.block("manifest_source_hash_mismatch")
    if dataset_verification.verified:
        fingerprint_bindings["dataset"]["actual_sha256"] = (
            dataset_verification.actual_sha256
        )
    if run is not None and dataset_verification.actual_sha256 is not None:
        fingerprint_bindings["dataset"]["matches"] = (
            run.dataset_fingerprint_sha256
            == dataset_verification.actual_sha256
        )
        if not fingerprint_bindings["dataset"]["matches"]:
            collector.block("run_dataset_fingerprint_mismatch")
    if (
        run is not None
        and fingerprint_bindings["manifest"]["actual_sha256"] is not None
    ):
        fingerprint_bindings["manifest"]["matches"] = (
            run.manifest_fingerprint_sha256
            == fingerprint_bindings["manifest"]["actual_sha256"]
        )
        if not fingerprint_bindings["manifest"]["matches"]:
            collector.block("run_manifest_fingerprint_mismatch")

    expected_source_kind = {
        "synthetic_fake_run": "fake_on_synthetic",
        "fake_on_real_context": "fake_on_real_context",
        "real_model_run_declared": "real_model_declared",
    }[config.condition.origin]
    if run is not None and run.source_kind != expected_source_kind:
        collector.block("run_source_kind_mismatch")
    if config.purpose == "real_evaluation_plan" and (
        run is None or run.source_kind != "real_model_declared"
    ):
        collector.block("candidate_not_real_model_declared")

    run_joins: dict[str, Any] = {
        "matched_task_count": 0,
        "extra_task_count": 0,
        "split_mismatch_count": 0,
        "execution_order_mismatch_count": 0,
        "cohort_group_mismatch_count": 0,
        "context_mismatch_count": 0,
        "context_binding": "not_available",
        "cohort_group_binding": manifest.cohort_group_binding if manifest else None,
    }
    if run is not None and manifest is not None:
        manifest_by_task = manifest.task_by_index
        context_checked = 0
        context_mismatch = 0
        for record in run.records:
            row = manifest_by_task.get(record.task_index)
            if row is None:
                run_joins["extra_task_count"] += 1
                continue
            run_joins["matched_task_count"] += 1
            if record.split != row.split:
                run_joins["split_mismatch_count"] += 1
            if record.execution_order != row.execution_order:
                run_joins["execution_order_mismatch_count"] += 1
            if manifest.cohort_group_binding == "direct":
                expected_group = row.cohort_group
            else:
                expected_group = expected_user_group(row.cohort_group)
            if record.user_group != expected_group:
                run_joins["cohort_group_mismatch_count"] += 1
            if row.run_context_sha256 is not None:
                context_checked += 1
                if record.context_sha256 != row.run_context_sha256:
                    context_mismatch += 1
        if run_joins["extra_task_count"]:
            collector.block("run_task_not_in_manifest")
        if run_joins["split_mismatch_count"]:
            collector.block("run_split_mismatch")
        if run_joins["execution_order_mismatch_count"]:
            collector.block("run_execution_order_mismatch")
        if run_joins["cohort_group_mismatch_count"]:
            collector.block("run_cohort_group_mismatch")
        if context_checked and context_mismatch:
            collector.block("run_context_hash_mismatch")
        run_joins["context_mismatch_count"] = context_mismatch
        run_joins["context_binding"] = (
            "verified_against_manifest_projection"
            if context_checked == len(run.records)
            else "not_recomputed_manifest_has_no_run_context"
        )

    temporal: dict[str, Any] = {
        "status": "blocked",
        "date_strength": manifest.date_strength if manifest else None,
        "history_strength": manifest.history_strength if manifest else None,
        "history_bound_strength": None,
        "history_cut_date": manifest.history_cut_date if manifest else None,
        "timestamp_semantics": manifest.timestamp_semantics if manifest else None,
        "target_dates_checked": False,
        "history_bound_checked": False,
        "limitations": [],
    }
    if manifest is not None:
        date_ok = manifest.date_strength == "target_dates_checked"
        if config.temporal.require_verified_target_dates and not date_ok:
            collector.block("temporal_target_dates_unavailable")
        if not config.temporal.allow_declared_order_only_time and not date_ok:
            collector.block("temporal_declared_order_only_not_allowed")
        if not date_ok and config.temporal.allow_declared_order_only_time:
            temporal["status"] = "declared_execution_order_only"
            collector.warn("temporal_declared_order_only")
        elif date_ok:
            temporal["status"] = "target_dates_checked"
            temporal["target_dates_checked"] = True
        temporal["history_bound_strength"] = manifest.history_strength
        temporal["history_bound_checked"] = manifest.history_strength in (
            "declared_history_bound",
            "partial_declared_history_bound",
        )
        if manifest.history_strength == "not_recomputed":
            temporal["limitations"].append("history_bound_not_recomputed")
            collector.warn("history_bound_not_recomputed")
        elif manifest.history_strength == "partial_declared_history_bound":
            temporal["limitations"].append("history_bound_partially_verified")
            collector.warn("history_bound_partially_verified")
        if manifest.user_holdout_policy == "allow_overlap_not_user_disjoint":
            temporal["limitations"].append("user_splits_may_overlap")
        if any(value > 0 for value in manifest.user_overlap.values()):
            collector.warn("user_splits_overlap_not_user_disjoint")
    elif manifest_errors:
        temporal["limitations"].append("task_manifest_not_parsed")

    cohort: dict[str, Any] = {
        "development_splits": list(config.cohort.development_splits),
        "holdout_splits": list(config.cohort.holdout_splits),
        "split_counts": dict(manifest.split_counts) if manifest else None,
        "user_cluster_counts": (
            {split: len(users) for split, users in manifest.users_by_split.items()}
            if manifest
            else None
        ),
        "user_overlap_counts": dict(manifest.user_overlap) if manifest else None,
        "user_holdout_policy": manifest.user_holdout_policy if manifest else None,
        "allow_user_overlap": config.cohort.allow_user_overlap,
    }
    if manifest is not None:
        if not config.cohort.allow_user_overlap and any(
            value > 0 for value in manifest.user_overlap.values()
        ):
            collector.block("cohort_user_overlap_disallowed")
        if manifest.user_holdout_policy == "user_disjoint" and any(
            value > 0 for value in manifest.user_overlap.values()
        ):
            collector.block("cohort_user_disjoint_claim_violated")

    comparison: dict[str, Any] = {
        "requested": config.baseline is not None,
        "baseline_mode": config.baseline.mode if config.baseline else None,
        "holdout_tasks": 0,
        "candidate_artifact_holdout_tasks": 0,
        "candidate_model_holdout_tasks": 0,
        "candidate_system_holdout_tasks": 0,
        "baseline_holdout_tasks": 0,
        "common_model_holdout_tasks": 0,
        "common_system_holdout_tasks": 0,
        "common_model_clusters": 0,
        "common_system_clusters": 0,
        "missing_candidate_holdout_tasks": 0,
        "excluded_reasons": {},
        "metrics_status": "not_computed_in_preflight",
        "significance": "not_computed",
    }
    if manifest is not None:
        holdout = [
            row for row in manifest.tasks if row.split == "test"
        ]
        comparison["holdout_tasks"] = len(holdout)
        if run is not None:
            by_task = run.by_task
            comparison["candidate_artifact_holdout_tasks"] = sum(
                1 for row in holdout if row.task_index in by_task
            )
            comparison["missing_candidate_holdout_tasks"] = (
                len(holdout) - comparison["candidate_artifact_holdout_tasks"]
            )
            comparison["candidate_model_holdout_tasks"] = sum(
                1
                for row in holdout
                if row.task_index in by_task
                and by_task[row.task_index].status == "success"
            )
            comparison["candidate_system_holdout_tasks"] = sum(
                1
                for row in holdout
                if row.task_index in by_task
                and by_task[row.task_index].status in ("success", "degraded")
            )
        if baseline is not None and config.baseline is not None:
            baseline_tasks = set(baseline.select(config.baseline.mode))
            comparison["baseline_holdout_tasks"] = sum(
                1 for row in holdout if row.task_index in baseline_tasks
            )
            missing_baseline = [
                row.task_index for row in holdout if row.task_index not in baseline_tasks
            ]
            if missing_baseline:
                collector.block("baseline_holdout_incomplete")
        baseline_mode = config.baseline.mode if config.baseline else None
        common_model, common_system, excluded = _common_sets(
            manifest, run, baseline, baseline_mode
        )
        comparison["common_model_holdout_tasks"] = len(common_model)
        comparison["common_system_holdout_tasks"] = len(common_system)
        comparison["common_model_clusters"] = _cluster_count(run, common_model)
        comparison["common_system_clusters"] = _cluster_count(run, common_system)
        comparison["excluded_reasons"] = excluded
        required_tasks = max(
            config.cohort.min_common_holdout_tasks,
            1,
        )
        if len(common_model) < required_tasks:
            collector.block("cohort_insufficient_model_holdout_tasks")
        if comparison["common_model_clusters"] < config.cohort.min_common_holdout_clusters:
            collector.block("cohort_insufficient_model_holdout_clusters")
        if config.baseline is not None:
            if len(common_system) < required_tasks:
                collector.block("cohort_insufficient_system_holdout_tasks")
            if (
                comparison["common_system_clusters"]
                < config.cohort.min_common_holdout_clusters
            ):
                collector.block("cohort_insufficient_system_holdout_clusters")

    dataset_resources = [
        verification
        for verification in verifications.values()
        if verification.kind in ("dataset_raw", "dataset_normalized")
    ]
    license_verified = bool(dataset_resources) and all(
        verification.license_verified for verification in dataset_resources
    )
    if config.purpose == "real_evaluation_plan" and not license_verified:
        collector.block("license_unverified_for_public_plan")
    publication_status = (
        "synthetic_only"
        if config.purpose == "synthetic_contract_check"
        else (
            "license_verified_local_evaluation"
            if license_verified
            else "blocked_license_unverified"
        )
    )
    if (
        config.purpose == "local_audit_only"
        and not license_verified
    ):
        collector.warn("license_not_explicitly_verified_local_audit")
    if not license_verified and config.purpose == "local_audit_only":
        publication_status = "blocked_license_unverified"

    if run is not None:
        run_summary = {
            **run.safe_summary(),
            "valid": True,
            "adapter_version": RUN_ADAPTER_VERSION,
            "source_kind_acceptance": (
                "matches_configured_origin"
                if run.source_kind == expected_source_kind
                else "source_kind_mismatch"
            ),
        }
    else:
        run_summary = {
            "adapter_version": RUN_ADAPTER_VERSION,
            "valid": False,
            "validation_errors": run_errors,
            "real_measurement_status": "not_verified_or_not_run",
        }

    if baseline is not None:
        baseline_summary = {
            **baseline.safe_summary(),
            "selected_mode": config.baseline.mode if config.baseline else None,
        }
    elif config.baseline is not None:
        baseline_summary = {
            "resource_id": config.baseline.resource_id,
            "valid": False,
            "selected_mode": config.baseline.mode,
            "errors": baseline_errors,
            "coverage_by_mode": {},
        }
    else:
        baseline_summary = None

    report: dict[str, Any] = {
        "tool": "amazon_eval_agent",
        "stage": PREFLIGHT_STAGE,
        "preflight_schema_version": PREFLIGHT_SCHEMA_VERSION,
        "evaluation_config": {
            "config_id": config.config_id,
            "schema_version": "evaluation_config.v1",
            "purpose": config.purpose,
            "config_sha256": config.config_sha256,
            "semantic_config_sha256": config.semantic_sha256,
        },
        "task_manifest": (
            {
                **manifest.safe_summary(),
                "contract_version": TASK_MANIFEST_CONTRACT_VERSION,
                "resource_id": config.task_manifest.resource_id,
            }
            if manifest is not None
            else {
                "schema_version": "task_manifest.v1",
                "valid": False,
                "resource_id": config.task_manifest.resource_id,
                "errors": manifest_errors,
            }
        ),
        "labels": (
            {
                **labels.safe_summary(),
                "resource_id": config.labels.resource_id,
                "contract_version": LABELS_CONTRACT_VERSION,
                "coverage": "exact_manifest_task_set",
            }
            if labels is not None
            else {
                "resource_id": config.labels.resource_id,
                "valid": False,
                "errors": label_errors,
            }
        ),
        "baseline": baseline_summary,
        "run": run_summary,
        "sources": source_rows,
        "fingerprint_bindings": fingerprint_bindings,
        "run_joins": run_joins,
        "temporal": temporal,
        "cohort": cohort,
        "comparison": comparison,
        "publication_status": publication_status,
        "confirmation": {
            "required": config.confirmation.required,
            "scope": config.confirmation.scope,
            "status": "unconfirmed",
            "plan_fingerprint": None,
        },
        "status": "blocked",
        "blockers": [],
        "warnings": [],
        "real_measurement_status": "not_verified_or_not_run",
        "accuracy_claim": "none",
        "scoring_available": False,
        "metric_computation": "not_implemented_in_preflight",
        "honesty": [
            "schema and internal consistency are not source truth",
            "declared real-model source metadata is not independent evidence",
            "A manifest source_review_sha256 is a producer declaration, not a raw-file hash",
            "target dates prove protocol order only; history context is not re-read",
        ],
    }
    if run is not None:
        report["run"]["status_counts_by_split"] = _split_counts_from_records(run)
        report["run"]["full_cohort_status_counts"] = _status_counts_full_cohort(
            manifest, run, "test"
        ) if manifest is not None else None
    if not collector.blockers:
        report["status"] = "ready_for_scoring"
    report["blockers"] = sorted(collector.blockers)
    report["warnings"] = sorted(collector.warnings)
    plan_fingerprint = sha256_bytes(_plan_binding_payload(report))
    report["confirmation"]["plan_fingerprint"] = plan_fingerprint
    ready = report["status"] == "ready_for_scoring"
    source_bindings = tuple(
        sorted(
            (
                verification.resource_id,
                verification.actual_sha256,
            )
            for verification in verifications.values()
        )
    )
    return PreflightResult(
        report=deep_freeze(report),
        plan_fingerprint=plan_fingerprint,
        ready=ready,
        config=config,
        manifest=manifest,
        labels=labels,
        run=run,
        baseline=baseline,
        source_bindings=source_bindings,
    )
