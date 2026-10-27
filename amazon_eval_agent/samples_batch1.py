"""Handwritten synthetic batch-1 fixture; no real reviews, labels or model runs.

Everything here is fabricated to exercise configuration parsing, source
hashing, manifest/time protocol checks, run joins and confirmation binding.
The review strings are explicitly marked synthetic, the labels are invented,
and the baseline table is a constant. No real Amazon identifiers, text or
target ratings appear here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

_SYNTHETIC_REVIEW = "(synthetic batch1 sample) fabricated review for contract tests"
_CUT = "2020-01-01"
_SPLITS = ("train", "validation", "test")
_TARGET_STARS = (3, 4, 5, 2, 4, 3, 4, 3, 5, 4, 3, 5)
_PREDICTIONS = (4, 3, 4, 2, 5, None, 4, 4, None, 4, 3, 4)
_USERS = (
    "ug_0000000000000001",
    "ug_0000000000000001",
    "ug_0000000000000002",
    "ug_0000000000000002",
    "ug_0000000000000003",
    "ug_0000000000000004",
    "ug_0000000000000005",
    "ug_0000000000000005",
    "ug_0000000000000006",
    "ug_0000000000000007",
    "ug_0000000000000008",
    "ug_0000000000000003",
)
_STATUSES = (
    "success",
    "success",
    "success",
    "success",
    "success",
    "failed",
    "success",
    "degraded",
    "refused",
    "success",
    "success",
    "degraded",
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _task_document() -> dict[str, Any]:
    tasks = []
    split_by_index = {}
    for index in range(12):
        split = "train" if index < 6 else ("validation" if index < 9 else "test")
        split_by_index[index] = split
        day = index + 1
        target_date = f"2020-02-{day:02d}"
        target_hash = _sha(f"synthetic-target-{index}".encode("utf-8"))
        manifest_context = _sha(f"synthetic-manifest-context-{index}".encode("utf-8"))
        run_context = _sha(f"synthetic-run-context-{index}".encode("utf-8"))
        tasks.append(
            {
                "task_index": index,
                "execution_order": index,
                "split": split,
                "cohort_group": _USERS[index],
                "target_date": target_date,
                "target_timestamp_sha256": target_hash,
                "manifest_context_sha256": manifest_context,
                "run_context_sha256": run_context,
                "history_max_date": f"2020-01-{day + 1:02d}",
            }
        )
    return {
        "schema_version": "task_manifest.v1",
        "manifest_id": "batch1_synthetic",
        "origin": {
            "kind": "synthetic_fixture",
            "source_manifest_sha256": None,
            "source_task_count": None,
            "adapter": "synthetic_fixture.v1",
        },
        "protocol": {
            "history_cut_date": _CUT,
            "timestamp_semantics": "synthetic_declared",
            "split_policy": {
                "train_ratio": 0.5,
                "validation_ratio": 0.25,
                "ties": "indivisible_group",
            },
            "history_visibility": "strictly_before_target_date",
            "target_region": "on_or_after_cut_date",
            "user_holdout_policy": "allow_overlap_not_user_disjoint",
        },
        "cohort_group_binding": "direct",
        "split_counts": {
            split: sum(row["split"] == split for row in tasks) for split in _SPLITS
        },
        "tasks": tasks,
    }


def _run_document(
    manifest: Mapping[str, Any], dataset_bytes: bytes, manifest_bytes: bytes
) -> dict[str, Any]:
    records = []
    for index, row in enumerate(manifest["tasks"]):
        status = _STATUSES[index]
        if status == "success":
            diagnostics = {
                "llm_attempts": 1,
                "repair": "not_requested",
                "prompt_bytes": 1000 + index,
                "memory": {
                    "candidates": 0,
                    "time_eligible": 0,
                    "selected": 0,
                    "prompt_included": 0,
                    "filtered_injection": 0,
                    "write": "written",
                },
            }
        elif status == "degraded" and index == 11:
            diagnostics = {
                "llm_attempts": 2,
                "repair": "attempted",
                "prompt_bytes": 1100,
                "memory": {
                    "candidates": 0,
                    "time_eligible": 0,
                    "selected": 0,
                    "prompt_included": 0,
                    "filtered_injection": 0,
                    "write": "skipped",
                },
            }
        elif status == "degraded":
            diagnostics = {
                "llm_attempts": 1,
                "repair": "reserve_failed",
                "prompt_bytes": 1100,
                "memory": {
                    "candidates": 0,
                    "time_eligible": 0,
                    "selected": 0,
                    "prompt_included": 0,
                    "filtered_injection": 0,
                    "write": "skipped",
                },
            }
        elif status == "failed":
            diagnostics = {
                "llm_attempts": 1,
                "repair": "not_requested",
                "prompt_bytes": 900,
                "memory": {
                    "candidates": 0,
                    "time_eligible": 0,
                    "selected": 0,
                    "prompt_included": 0,
                    "filtered_injection": 0,
                    "write": "skipped",
                },
            }
        else:
            diagnostics = {
                "llm_attempts": 0,
                "repair": "not_requested",
                "prompt_bytes": 0,
                "memory": {
                    "candidates": 0,
                    "time_eligible": 0,
                    "selected": 0,
                    "prompt_included": 0,
                    "filtered_injection": 0,
                    "write": "not_attempted",
                },
            }
        records.append(
            {
                "task_index": index,
                "execution_order": index,
                "split": row["split"],
                "context_sha256": row["run_context_sha256"],
                "user_group": row["cohort_group"],
                "status": status,
                "reason": {
                    "success": "validated_model_output",
                    "degraded": "statistical_baseline_fallback",
                    "refused": "insufficient_evidence",
                    "failed": "llm_transport_error",
                }[status],
                "prediction_origin": {
                    "success": "llm",
                    "degraded": "statistical_baseline",
                    "refused": "none",
                    "failed": "none",
                }[status],
                "stars": _PREDICTIONS[index] if status in ("success", "degraded") else None,
                "review": _SYNTHETIC_REVIEW if status == "success" else None,
                "evidence_refs": (
                    [f"ev_{index + 1:016x}"]
                    if status in ("success", "degraded")
                    else []
                ),
                "diagnostics": diagnostics,
            }
        )
    return {
        "schema_version": "simulation_run.v1",
        "source": {
            "producer": "llm-user-simulation-agent",
            "kind": "fake_on_synthetic",
            "model_source": "offline_injected_fake_callable",
            "dataset_fingerprint_sha256": _sha(dataset_bytes),
            "manifest_fingerprint_sha256": _sha(manifest_bytes),
            "config_fingerprint_sha256": _sha(b"synthetic-batch1-config-v1"),
        },
        "simulated": True,
        "record_count": len(records),
        "records": records,
    }


def _baseline_document(manifest: Mapping[str, Any]) -> bytes:
    lines = []
    for mode, value in (
        ("global", 3.0),
        ("user", 3.2),
        ("item", 3.4),
        ("combined", 3.3),
    ):
        for row in manifest["tasks"]:
            lines.append(
                json.dumps(
                    {
                        "task_index": row["task_index"],
                        "split": row["split"],
                        "mode": mode,
                        "prediction": value,
                        "user_cold_start": False,
                        "item_cold_start": False,
                    },
                    sort_keys=True,
                    ensure_ascii=True,
                )
            )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _dataset_document() -> bytes:
    rows = [
        json.dumps(
            {
                "synthetic": True,
                "row": index,
                "note": "fabricated dataset row for hash binding only",
            },
            sort_keys=True,
        )
        for index in range(12)
    ]
    return ("\n".join(rows) + "\n").encode("utf-8")


def _labels_document() -> bytes:
    return _json_bytes(
        {
            "schema_version": "local_labels.v1",
            "label_set_id": "labels_batch1_synthetic",
            "tasks": [
                {"task_index": index, "stars": stars}
                for index, stars in enumerate(_TARGET_STARS)
            ],
        }
    )


def _config_document(files: Mapping[str, bytes]) -> dict[str, Any]:
    def resource(resource_id: str, kind: str, release: str) -> dict[str, Any]:
        payload = files[resource_id]
        return {
            "resource_id": resource_id,
            "kind": kind,
            "release": release,
            "expected_sha256": _sha(payload),
            "expected_size_bytes": len(payload),
            "license_status": "synthetic_fixture_only",
            "redistribution_status": "synthetic_only",
            "contains_personal_data": False,
        }

    return {
        "schema_version": "evaluation_config.v1",
        "config_id": "batch1_synthetic",
        "purpose": "synthetic_contract_check",
        "condition": {
            "condition_id": "candidate_fake",
            "role": "candidate",
            "origin": "synthetic_fake_run",
            "run_resource_id": "run_candidate",
        },
        "task_manifest": {
            "resource_id": "task_manifest",
            "adapter": "b_native_task_manifest.v1",
        },
        "labels": {"resource_id": "labels", "schema": "local_labels.v1"},
        "baseline": {
            "resource_id": "baseline_predictions",
            "kind": "a_p0_predictions_v1",
            "mode": "global",
        },
        "cohort": {
            "development_splits": ["train", "validation"],
            "holdout_splits": ["test"],
            "allow_user_overlap": True,
            "min_common_holdout_tasks": 2,
            "min_common_holdout_clusters": 2,
        },
        "temporal": {
            "require_verified_target_dates": True,
            "allow_declared_order_only_time": False,
            "require_targets_on_or_after_cut": True,
            "require_same_date_split_indivisible": True,
        },
        "run_source_binding": {
            "dataset_resource_id": "dataset_normalized",
            "manifest_resource_id": "task_manifest",
            "dataset_fingerprint_policy": "file_sha256",
            "manifest_fingerprint_policy": "file_sha256",
        },
        "resources": [
            resource("dataset_normalized", "dataset_normalized", "synthetic_dataset_v1"),
            resource("task_manifest", "task_manifest", "synthetic_manifest_v1"),
            resource("labels", "local_labels", "synthetic_labels_v1"),
            resource("run_candidate", "run_artifact", "synthetic_candidate_run_v1"),
            resource(
                "baseline_predictions",
                "baseline_predictions",
                "synthetic_baseline_table_v1",
            ),
        ],
        "confirmation": {"required": True, "scope": "preflight_only"},
    }


def synthetic_files() -> dict[str, bytes]:
    """Return the complete synthetic bundle keyed by logical resource id."""
    manifest = _task_document()
    manifest_bytes = _json_bytes(manifest)
    dataset_bytes = _dataset_document()
    labels_bytes = _labels_document()
    baseline_bytes = _baseline_document(manifest)
    run_bytes = _json_bytes(_run_document(manifest, dataset_bytes, manifest_bytes))
    files = {
        "dataset_normalized": dataset_bytes,
        "task_manifest": manifest_bytes,
        "labels": labels_bytes,
        "baseline_predictions": baseline_bytes,
        "run_candidate": run_bytes,
    }
    config_bytes = _json_bytes(_config_document(files))
    files["evaluation_config"] = config_bytes
    return files


_FILE_NAMES = {
    "evaluation_config": "evaluation_config.json",
    "task_manifest": "task_manifest.json",
    "labels": "local_labels.json",
    "baseline_predictions": "baseline_predictions.jsonl",
    "run_candidate": "run_candidate.json",
    "dataset_normalized": "synthetic_dataset.jsonl",
}


def synthetic_bindings() -> dict[str, bytes]:
    files = synthetic_files()
    return {
        key: value for key, value in files.items() if key != "evaluation_config"
    }


def write_synthetic_files(output: str | Path) -> None:
    target = Path(output)
    target.mkdir(parents=True, exist_ok=True)
    files = synthetic_files()
    for key, name in _FILE_NAMES.items():
        path = target / name
        with path.open("xb") as handle:
            handle.write(files[key])


def _prepared_time(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def _prepared_row_fingerprint(row: Mapping[str, Any], index: int) -> dict[str, Any]:
    text = str(row.get("text") or "")
    normalized = " ".join(text.split()).casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if text else None
    target_time = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
    if target_time.tzinfo is None:
        target_time = target_time.replace(tzinfo=timezone.utc)
    return {
        "row_index": index,
        "event_time": target_time.astimezone(timezone.utc).isoformat(),
        "text_sha256": digest,
        "stars": float(row["stars"]),
        "useful": row.get("useful"),
        "funny": row.get("funny"),
        "cool": row.get("cool"),
    }


def synthetic_prepared_data() -> dict[str, Any]:
    """Structurally A-compatible synthetic prepared task/label/runtime rows."""
    target_stars = (4, 3, 5, 2, 4, 3, 5, 2, 4)
    tasks: list[dict[str, Any]] = []
    groundtruth: list[dict[str, Any]] = []
    runtime: list[dict[str, Any]] = []
    for index in range(9):
        target_day = date(2020, 3, 1) + timedelta(days=index)
        history_day = date(2020, 2, 1) + timedelta(days=index)
        user_id = f"synthetic-user-{index}"
        item_id = f"synthetic-item-{index}"
        target_review_id = f"synthetic-review-{index}"
        history_review_id = f"synthetic-history-review-{index}"
        history_text = f"(synthetic prepared history {index})"
        target_text = f"(synthetic prepared target {index})"
        tasks.append(
            {
                "task_index": index,
                "user_id": user_id,
                "item_id": item_id,
                "target_review_id": target_review_id,
                "target_date": target_day.isoformat(),
            }
        )
        groundtruth.append(
            {
                "task_index": index,
                "user_id": user_id,
                "item_id": item_id,
                "target_review_id": target_review_id,
                "target_date": target_day.isoformat(),
                "stars": float(target_stars[index]),
                "text": target_text,
            }
        )
        runtime.append(
            {
                "review_id": history_review_id,
                "user_id": user_id,
                "item_id": f"synthetic-history-item-{index}",
                "stars": 4.0,
                "timestamp": history_day.isoformat(),
                "text": history_text,
                "source_row": index,
            }
        )
    for index in range(9):
        target_day = date(2020, 3, 1) + timedelta(days=index)
        reference_day = date(2020, 2, 10) + timedelta(days=index)
        runtime.append(
            {
                "review_id": f"synthetic-reference-review-{index}",
                "user_id": f"synthetic-external-{index}",
                "item_id": f"synthetic-item-{index}",
                "stars": 3.0,
                "timestamp": reference_day.isoformat(),
                "text": f"(synthetic prepared reference {index})",
                "source_row": 9 + index,
            }
        )
    for index in range(9):
        target_day = date(2020, 3, 1) + timedelta(days=index)
        runtime.append(
            {
                "review_id": f"synthetic-review-{index}",
                "user_id": f"synthetic-user-{index}",
                "item_id": f"synthetic-item-{index}",
                "stars": float(target_stars[index]),
                "timestamp": target_day.isoformat(),
                "text": f"(synthetic prepared target {index})",
                "source_row": 18 + index,
            }
        )
    return {
        "tasks": tasks,
        "groundtruth": groundtruth,
        "runtime": runtime,
        "target_stars": target_stars,
    }


def synthetic_a_manifest_document() -> dict[str, Any]:
    """A schema-2 fixture with no target dates but A-compatible context hashes."""
    data = synthetic_prepared_data()
    runtime = data["runtime"]
    tasks = []
    for index in range(9):
        target_time = _prepared_time(date(2020, 3, 1) + timedelta(days=index))
        history_rows = [_prepared_row_fingerprint(runtime[index], index)]
        reference_rows = [
            _prepared_row_fingerprint(runtime[9 + index], 9 + index)
        ]
        context_payload = {
            "target_timestamp_sha256": _sha(
                target_time.isoformat().encode("utf-8")
            ),
            "history_rows": history_rows,
            "item_reference_rows": reference_rows,
        }
        split = (
            "train"
            if index < 5
            else ("validation" if index < 7 else "test")
        )
        tasks.append(
            {
                "task_index": index,
                "source_task_index_sha256": _sha(str(index).encode("utf-8")),
                "target_timestamp_sha256": _sha(
                    target_time.isoformat().encode("utf-8")
                ),
                "execution_order": index,
                "split": split,
                "user_group": f"U{index + 1:04d}",
                "user_repeat_stratum": "first_seen",
                "timestamp_available": True,
                "timestamp_source_field": "unixReviewTime",
                "target_review_row_matched": True,
                "target_review_match_ambiguous": False,
                "target_timestamp_consistent": True,
                "visible_history_row_indexes": [index],
                "target_item_reference_row_indexes": [9 + index],
                "visible_history_count": 1,
                "target_item_reference_count": 1,
                "target_text_absent_from_context": True,
                "context_sha256": hashlib.sha256(
                    json.dumps(context_payload, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            }
        )
    return {
        "schema_version": 2,
        "manifest_type": "strict_temporal_no_groundtruth_context",
        "pairing": {"alignment": "natural_filename_order", "warnings": []},
        "source_review_files": 1,
        "source_review_rows": 27,
        "source_review_bytes": 4000,
        "source_review_sha256": _sha(b"synthetic-a-source"),
        "source_review_fingerprint_kind": "canonical_parsed_records_v1",
        "task_count": 9,
        "eligible_task_count": 9,
        "ineligible_task_count": 0,
        "ineligible_reasons": {},
        "manifest_audit_status": "ready_for_runtime_review",
        "runtime_eligible": True,
        "runtime_rejection_reasons": [],
        "target_timestamp_semantics_verified": False,
        "target_timestamp_semantics_caller_asserted": True,
        "timestamp_semantics_confirmation": "caller_asserted",
        "split_policy": {
            "train_ratio": 0.6,
            "validation_ratio": 0.2,
            "timestamp_ties": "indivisible_group",
        },
        "timestamp_semantics_note": "caller assertion only",
        "target_review_row_match_counts": {
            "unique": 9,
            "ambiguous": 0,
            "unmatched": 0,
        },
        "review_context_suppression": {
            "unresolved_target_user_count": 0,
            "unresolved_target_item_count": 0,
            "suppress_all_user_history": False,
            "suppress_all_item_references": False,
            "policy": "synthetic",
        },
        "split_counts": {"train": 5, "validation": 2, "test": 2},
        "order_policy": "synthetic",
        "split_boundary_policy": "synthetic",
        "history_policy": "synthetic",
        "ablation_policy": "synthetic",
        "tasks": tasks,
        "privacy": "no raw IDs, groundtruth ratings, review text, or prompts included",
    }


def synthetic_a_manifest_bytes() -> bytes:
    return _json_bytes(synthetic_a_manifest_document())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Write the synthetic batch-1 example bundle; no model calls."
    )
    parser.add_argument("--output", type=Path, required=True)
    write_synthetic_files(parser.parse_args().output)
