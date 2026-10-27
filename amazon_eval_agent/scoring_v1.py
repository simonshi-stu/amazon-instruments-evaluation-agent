"""Batch-2 point scoring on an already confirmed preflight plan.

This stage computes **point estimates only**: RMSE, MAE, +/-1-star hit rate and
signed bias. It deliberately has no bootstrap interval, p-value, LLM
explanation, human review or approved-report export.

Core rules:

- labels may be parsed/joined by governance tools, but prediction functions
  never read them; this scorer is the first stage that uses label values for
  error metrics, after the run artifact and confirmation are fixed;
- model success-only and system-with-degraded predictions use separate
  denominators; failures/refusals/missing tasks are never zero-filled;
- the full requested holdout cohort, artifact coverage and conditional
  common-task comparison are reported side by side;
- a missing artifact row is ``missing``, not a success;
- unknown usage/latency/first-format traces are reported as unknown, never
  inferred from the contract.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .adapters.simulation_run_v1 import RunArtifactView
from .confirmation_v1 import (
    CONFIRMATION_SCOPE,
    parse_confirmation_bytes,
    serialize_confirmation,
    verify_confirmation,
)
from .labels_v1 import LabelSet, verify_labels_cover_manifest
from .preflight_v1 import PreflightResult
from .safety import canonical_json_bytes, sha256_bytes
from .task_manifest_v1 import TaskManifest

SCORE_SCHEMA_VERSION = "evaluation_score.v1"
SCORE_STAGE = "batch2_point_scoring"
STATUSES = ("success", "degraded", "refused", "failed")


class ScoreError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class MetricSet:
    n: int
    mae: float | None
    rmse: float | None
    within_one: float | None
    bias: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "mae": self.mae,
            "rmse": self.rmse,
            "within_one": self.within_one,
            "bias": self.bias,
        }


def _metrics(pairs: Sequence[tuple[float, int]]) -> MetricSet:
    if not pairs:
        return MetricSet(0, None, None, None, None)
    errors = []
    for prediction, actual in pairs:
        if isinstance(prediction, bool) or not isinstance(prediction, (int, float)):
            raise ScoreError("prediction_not_numeric")
        value = float(prediction)
        if not math.isfinite(value) or not 1.0 <= value <= 5.0:
            raise ScoreError("prediction_out_of_range")
        if type(actual) is not int or not 1 <= actual <= 5:
            raise ScoreError("label_out_of_range")
        errors.append(value - float(actual))
    n = len(errors)
    return MetricSet(
        n=n,
        mae=sum(abs(error) for error in errors) / n,
        rmse=math.sqrt(sum(error * error for error in errors) / n),
        within_one=sum(abs(error) <= 1.0 for error in errors) / n,
        bias=sum(errors) / n,
    )


def _candidate_pairs(
    view: RunArtifactView,
    labels: LabelSet,
    indexes: set[int],
    allowed_statuses: set[str],
) -> list[tuple[float, int]]:
    pairs = []
    for index in sorted(indexes):
        record = view.by_task.get(index)
        if record is None or record.status not in allowed_statuses:
            continue
        if index not in labels.by_task:
            continue
        if record.stars is None:
            continue
        pairs.append((record.stars, labels.by_task[index]))
    return pairs


def _baseline_pairs(
    predictions: Mapping[int, float],
    labels: LabelSet,
    indexes: set[int],
) -> list[tuple[float, int]]:
    return [
        (predictions[index], labels.by_task[index])
        for index in sorted(indexes)
        if index in predictions and index in labels.by_task
    ]


def _delta(candidate: MetricSet, baseline: MetricSet) -> dict[str, float | None]:
    def _sub(left: float | None, right: float | None) -> float | None:
        return left - right if left is not None and right is not None else None

    return {
        "mae": _sub(candidate.mae, baseline.mae),
        "rmse": _sub(candidate.rmse, baseline.rmse),
        "within_one": _sub(candidate.within_one, baseline.within_one),
        "bias": _sub(candidate.bias, baseline.bias),
    }


def _status_counts_for_holdout(
    manifest: TaskManifest, view: RunArtifactView
) -> tuple[dict[str, int], set[int]]:
    counts = {status: 0 for status in STATUSES}
    counts["missing"] = 0
    present: set[int] = set()
    for row in manifest.tasks:
        if row.split != "test":
            continue
        record = view.by_task.get(row.task_index)
        if record is None:
            counts["missing"] += 1
            continue
        present.add(row.task_index)
        counts[record.status] += 1
    return counts, present


def _diagnostic_aggregate(view: RunArtifactView) -> dict[str, Any]:
    records = list(view.records)
    reason_counts: dict[str, int] = {}
    origin_counts: dict[str, int] = {}
    repair_counts: dict[str, int] = {}
    attempt_counts = {0: 0, 1: 0, 2: 0}
    memory_sums = {
        "candidates": 0,
        "time_eligible": 0,
        "selected": 0,
        "prompt_included": 0,
        "filtered_injection": 0,
    }
    write_counts: dict[str, int] = {}
    prompt_bytes = 0
    for record in records:
        reason_counts[record.reason] = reason_counts.get(record.reason, 0) + 1
        origin_counts[record.prediction_origin] = (
            origin_counts.get(record.prediction_origin, 0) + 1
        )
        repair_counts[record.repair] = repair_counts.get(record.repair, 0) + 1
        attempt_counts[record.llm_attempts] = (
            attempt_counts.get(record.llm_attempts, 0) + 1
        )
        prompt_bytes += record.prompt_bytes
        for key in memory_sums:
            value = record.memory.get(key)
            if type(value) is int:
                memory_sums[key] += value
        write = record.memory.get("write")
        if isinstance(write, str):
            write_counts[write] = write_counts.get(write, 0) + 1
    return {
        "scope": "all_submitted_artifact_records",
        "record_count": len(records),
        "reason_counts": dict(sorted(reason_counts.items())),
        "prediction_origin_counts": dict(sorted(origin_counts.items())),
        "llm_attempts": {
            "distribution": {str(key): value for key, value in sorted(attempt_counts.items())},
            "sum": sum(key * value for key, value in attempt_counts.items()),
        },
        "repair_counts": dict(sorted(repair_counts.items())),
        "prompt_bytes_sum": prompt_bytes,
        "memory_sums": memory_sums,
        "write_counts": dict(sorted(write_counts.items())),
        "unknown_fields_not_in_contract": {
            "latency": "unknown_not_in_contract",
            "usage": "unknown_not_in_contract",
            "first_format_trace": "unknown_not_in_contract",
            "estimated_cost": "unknown_not_in_contract",
        },
    }


def audit_baseline_table(
    manifest: TaskManifest,
    labels: LabelSet,
    table: Any,
) -> dict[str, Any]:
    """Independently recompute A P0 baseline point metrics from a prediction table.

    This is a consistency audit of an A-declared table, not a new experiment
    and not a real-model measurement. It intentionally returns aggregate
    metrics only; no task identifiers or label values are exposed.
    """
    from .adapters.a_p0_predictions_v1 import BASELINE_MODES

    try:
        verify_labels_cover_manifest(labels, manifest)
    except ValueError as exc:
        raise ScoreError("labels_task_set_mismatch") from exc
    manifest_indexes = {row.task_index for row in manifest.tasks}
    if set(table.split_by_task) != manifest_indexes:
        raise ScoreError("baseline_task_set_mismatch")
    for index, row in manifest.task_by_index.items():
        if table.split_by_task.get(index) != row.split:
            raise ScoreError("baseline_split_mismatch")
    modes: dict[str, Any] = {}
    for mode in BASELINE_MODES:
        predictions = table.select(mode)
        split_metrics: dict[str, Any] = {}
        for split in ("train", "validation", "test", "all"):
            indexes = {
                index
                for index, row in manifest.task_by_index.items()
                if split == "all" or row.split == split
            }
            metric = _metrics(_baseline_pairs(predictions, labels, indexes))
            split_metrics[split] = metric.as_dict()
        modes[mode] = split_metrics
    return {
        "schema_version": "baseline_audit.v1",
        "source": "A_declared_p0_prediction_table",
        "table_sha256": table.raw_sha256,
        "task_count": len(manifest.tasks),
        "split_counts": dict(manifest.split_counts),
        "modes": modes,
        "real_measurement_status": "not_verified_or_not_run",
        "note": (
            "independent recomputation from A-declared predictions and B-local "
            "labels; not a new run, not LLM quality and not a significance claim"
        ),
    }


def score_confirmed_plan(
    result: PreflightResult,
    confirmation: Mapping[str, Any],
    confirmation_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute point metrics for a ready, confirmed preflight result.

    The caller-supplied verification mapping is an **extra veto only**. The
    function re-parses the confirmation and recomputes every binding against
    the current immutable PreflightResult; a caller's ``True`` cannot authorize
    a stale/tampered confirmation. The verdict token (``True``) is never used.
    """
    if not result.ready or result.manifest is None or result.labels is None or result.run is None:
        raise ScoreError("preflight_not_ready")
    try:
        parsed_confirmation = parse_confirmation_bytes(
            serialize_confirmation(confirmation)
        )
    except Exception as exc:  # noqa: BLE001 - strict boundary, stable code
        raise ScoreError("confirmation_invalid") from exc
    if parsed_confirmation.get("scope") != CONFIRMATION_SCOPE:
        raise ScoreError("confirmation_scope_invalid")
    internal_verification = verify_confirmation(parsed_confirmation, result)
    if internal_verification.get("confirmation_valid") is not True:
        raise ScoreError("confirmation_invalid")
    if confirmation_verification is not None:
        if not isinstance(confirmation_verification, Mapping):
            raise ScoreError("confirmation_verification_rejected")
        if confirmation_verification.get("confirmation_valid") is not True:
            raise ScoreError("confirmation_verification_rejected")

    manifest = result.manifest
    labels = result.labels
    run = result.run
    holdout_indexes = {
        row.task_index for row in manifest.tasks if row.split == "test"
    }
    status_counts, present = _status_counts_for_holdout(manifest, run)
    model_indexes = {
        index
        for index in present
        if run.by_task[index].status == "success"
    }
    system_indexes = {
        index
        for index in present
        if run.by_task[index].status in ("success", "degraded")
    }
    model_metrics = _metrics(_candidate_pairs(run, labels, model_indexes, {"success"}))
    system_metrics = _metrics(
        _candidate_pairs(run, labels, system_indexes, {"success", "degraded"})
    )
    requested = len(holdout_indexes)
    full_cohort = {
        "requested_holdout_tasks": requested,
        "artifact_present": len(present),
        "missing": status_counts["missing"],
        "status_counts": dict(status_counts),
        "artifact_coverage": len(present) / requested if requested else None,
        "model_success_only": {
            "denominator": requested,
            "eligible": model_metrics.n,
            "coverage": model_metrics.n / requested if requested else None,
            "metrics": model_metrics.as_dict(),
        },
        "system_including_degraded": {
            "denominator": requested,
            "eligible": system_metrics.n,
            "coverage": system_metrics.n / requested if requested else None,
            "metrics": system_metrics.as_dict(),
        },
    }
    exclusions = {
        "requested": requested,
        "model": {
            "included_success": model_metrics.n,
            "excluded_missing": status_counts["missing"],
            "excluded_degraded": status_counts["degraded"],
            "excluded_refused": status_counts["refused"],
            "excluded_failed": status_counts["failed"],
            "excluded_label_missing": 0,
        },
        "system": {
            "included_success_or_degraded": system_metrics.n,
            "excluded_missing": status_counts["missing"],
            "excluded_refused": status_counts["refused"],
            "excluded_failed": status_counts["failed"],
            "excluded_label_missing": 0,
        },
    }

    baseline_report = None
    comparison = None
    blockers: list[str] = []
    if result.baseline is not None and result.config.baseline is not None:
        mode = result.config.baseline.mode
        predictions = result.baseline.select(mode)
        baseline_full = _metrics(_baseline_pairs(predictions, labels, holdout_indexes))
        common_model = model_indexes & set(predictions)
        common_system = system_indexes & set(predictions)
        model_candidate_common = _metrics(
            _candidate_pairs(run, labels, common_model, {"success"})
        )
        model_baseline_common = _metrics(
            _baseline_pairs(predictions, labels, common_model)
        )
        system_candidate_common = _metrics(
            _candidate_pairs(run, labels, common_system, {"success", "degraded"})
        )
        system_baseline_common = _metrics(
            _baseline_pairs(predictions, labels, common_system)
        )
        baseline_report = {
            "adapter_version": result.baseline.adapter_version,
            "table_sha256": result.baseline.raw_sha256,
            "mode": mode,
            "full_test": {
                "denominator": requested,
                "eligible": baseline_full.n,
                "coverage": baseline_full.n / requested if requested else None,
                "metrics": baseline_full.as_dict(),
            },
            "cold_start_counts_all_splits": {
                "user_fallback": result.baseline.cold_user_count.get(mode, 0),
                "item_fallback": result.baseline.cold_item_count.get(mode, 0),
            },
        }
        common_model_clusters = len(
            {
                run.by_task[index].user_group
                for index in common_model
                if index in run.by_task
            }
        )
        common_system_clusters = len(
            {
                run.by_task[index].user_group
                for index in common_system
                if index in run.by_task
            }
        )
        comparison = {
            "requested": True,
            "scope": "common_valid_holdout_tasks",
            "baseline_mode": mode,
            "common_model_tasks": len(common_model),
            "common_system_tasks": len(common_system),
            "common_model_clusters": common_model_clusters,
            "common_system_clusters": common_system_clusters,
            "excluded": {
                "candidate_missing": status_counts["missing"],
                "candidate_degraded_from_model": status_counts["degraded"],
                "candidate_refused": status_counts["refused"],
                "candidate_failed": status_counts["failed"],
                "baseline_missing": len(holdout_indexes - set(predictions)),
                "label_missing": 0,
            },
            "model_success_only": {
                "candidate": model_candidate_common.as_dict(),
                "baseline": model_baseline_common.as_dict(),
                "delta_candidate_minus_baseline": _delta(
                    model_candidate_common, model_baseline_common
                ),
                "status": (
                    "computed"
                    if model_candidate_common.n and model_baseline_common.n
                    else "insufficient_common_tasks"
                ),
            },
            "system_including_degraded": {
                "candidate": system_candidate_common.as_dict(),
                "baseline": system_baseline_common.as_dict(),
                "delta_candidate_minus_baseline": _delta(
                    system_candidate_common, system_baseline_common
                ),
                "status": (
                    "computed"
                    if system_candidate_common.n and system_baseline_common.n
                    else "insufficient_common_tasks"
                ),
            },
            "significance": "not_computed",
            "interval": None,
        }
        if len(common_model) < result.config.cohort.min_common_holdout_tasks:
            blockers.append("common_model_tasks_below_configured_minimum")
        if common_model_clusters < result.config.cohort.min_common_holdout_clusters:
            blockers.append("common_model_clusters_below_configured_minimum")
        if len(common_system) < result.config.cohort.min_common_holdout_tasks:
            blockers.append("common_system_tasks_below_configured_minimum")
        if common_system_clusters < result.config.cohort.min_common_holdout_clusters:
            blockers.append("common_system_clusters_below_configured_minimum")

    if model_metrics.n == 0:
        blockers.append("no_model_success_predictions")
    if system_metrics.n == 0:
        blockers.append("no_system_predictions")

    source_kind = run.source_kind
    if source_kind == "real_model_declared":
        semantics = "declared_real_model_not_independently_verified"
    else:
        semantics = "fake_or_synthetic_declared_not_real_model_quality"
    report = {
        "tool": "amazon_eval_agent",
        "stage": SCORE_STAGE,
        "score_schema_version": SCORE_SCHEMA_VERSION,
        "status": (
            "computed"
            if model_metrics.n and not blockers
            else "insufficient_data"
        ),
        "blockers": sorted(set(blockers)),
        "plan_fingerprint": result.plan_fingerprint,
        "confirmation": {
            "confirmation_id": parsed_confirmation.get("confirmation_id"),
            "scope": parsed_confirmation.get("scope"),
            "confirmation_valid": True,
            "plan_fingerprint_match": True,
        },
        "evaluation_config": {
            "config_id": result.config.config_id,
            "purpose": result.config.purpose,
            "config_sha256": result.config.config_sha256,
            "semantic_config_sha256": result.config.semantic_sha256,
        },
        "task_manifest": {
            "manifest_id": manifest.manifest_id,
            "manifest_sha256": manifest.raw_sha256,
            "task_count": len(manifest.tasks),
            "holdout_tasks": requested,
            "split_counts": dict(manifest.split_counts),
            "user_overlap_counts": dict(manifest.user_overlap),
            "user_holdout_policy": manifest.user_holdout_policy,
        },
        "labels": {
            "label_set_id": labels.label_set_id,
            "label_count": len(labels.by_task),
            "label_set_sha256": labels.raw_sha256,
            "values_disclosed": False,
        },
        "run": {
            "artifact_sha256": run.artifact_sha256,
            "source_kind": source_kind,
            "model_source": run.model_source,
            "record_count": run.record_count,
            "status_counts": dict(run.status_counts),
            "result_semantics": semantics,
        },
        "full_cohort": full_cohort,
        "exclusions": exclusions,
        "baseline": baseline_report,
        "comparison": comparison,
        "diagnostics": _diagnostic_aggregate(run),
        "measurement": {
            "metric_status": "point_estimates_only",
            "real_measurement_status": "not_verified_or_not_run",
            "model_quality_claim": "none",
            "accuracy_claim": "declared_artifact_point_estimates_only",
            "intervals": "not_computed",
            "significance": "not_computed",
            "usage": "unknown_not_in_contract",
            "latency": "unknown_not_in_contract",
        },
        "privacy": {
            "review_text_included": False,
            "user_identifiers_included": False,
            "label_values_included": False,
            "paths_included": False,
        },
        "limitations": [
            "point estimates only; no interval, p-value or significance claim",
            "no model call, human review or approved report export in this stage",
            "model success-only and system-with-degraded use different denominators",
            "missing, refused and failed tasks are excluded from error metrics, not zero-filled",
            "a fake or declared source kind is never promoted to real model quality",
        ],
    }
    report["score_fingerprint"] = sha256_bytes(canonical_json_bytes(report))
    return report
