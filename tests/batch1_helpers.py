"""Synthetic-only helpers for batch-1/batch-2 tests (never real data)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from amazon_eval_agent.adapters.a_prepared_v1 import import_a_prepared
from amazon_eval_agent.adapters.simulation_run_v1 import expected_user_group
from amazon_eval_agent.labels_v1 import parse_labels_file
from amazon_eval_agent.samples_batch1 import (
    synthetic_a_manifest_document,
    synthetic_files,
    synthetic_prepared_data,
)
from amazon_eval_agent.task_manifest_v1 import parse_task_manifest_file


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bundle_with(
    *,
    file_mutators: Mapping[str, Callable[[bytes], bytes]] | None = None,
    config_mutator: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, bytes]:
    """Return a synthetic bundle with optional content changes and rebinding."""
    files = synthetic_files()
    for key, mutator in (file_mutators or {}).items():
        files[key] = mutator(files[key])
    config = json.loads(files["evaluation_config"].decode("utf-8"))
    if config_mutator:
        config_mutator(config)
    for resource in config["resources"]:
        payload = files[resource["resource_id"]]
        resource["expected_sha256"] = sha(payload)
        resource["expected_size_bytes"] = len(payload)
    files["evaluation_config"] = (
        json.dumps(config, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    return files


def bindings_from(files: Mapping[str, bytes]) -> dict[str, bytes]:
    return {
        key: value for key, value in files.items() if key != "evaluation_config"
    }


def synthetic_prepared_dir(tmp_path: Path) -> Path:
    """Create a structurally A-compatible synthetic prepared directory.

    Nine tasks (train/validation/test = 5/2/2), 27 runtime review rows, and
    target dates/timestamps/context hashes that the independent verifier can
    recompute. No real identifiers or review text.
    """
    prepared = tmp_path / "prepared"
    (prepared / "tasks").mkdir(parents=True)
    (prepared / "groundtruth").mkdir()
    data = synthetic_prepared_data()
    a_manifest = synthetic_a_manifest_document()
    (prepared / "manifest.json").write_text(
        json.dumps(a_manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    (prepared / "tasks" / "p0.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in data["tasks"]) + "\n",
        encoding="utf-8",
    )
    (prepared / "groundtruth" / "p0.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in data["groundtruth"])
        + "\n",
        encoding="utf-8",
    )
    (prepared / "runtime_reviews.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in data["runtime"]) + "\n",
        encoding="utf-8",
    )
    policy = {
        "schema_version": 1,
        "adapter": "amazon_p0",
        "adapter_version": "amazon-mi-p0-v1",
        "protocol": {"cut_day": "2020-01-01"},
        "fields": {
            "timestamp_semantics": (
                "caller_asserted_amazon_2014_unix_review_time; synthetic test"
            )
        },
        "source": {"license_status": "not_explicitly_verified"},
    }
    (prepared / "p0_policy.json").write_text(
        json.dumps(policy, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return prepared


def prepared_bundle(tmp_path: Path) -> dict[str, bytes]:
    """Full synthetic structurally A-compatible import + run + config bundle."""
    prepared = synthetic_prepared_dir(tmp_path)
    manifest_path = tmp_path / "b_manifest.json"
    labels_path = tmp_path / "b_labels.json"
    import_a_prepared(
        prepared,
        "synthetic_prepared_smoke",
        manifest_path,
        labels_path,
    )
    manifest = parse_task_manifest_file(manifest_path)
    labels = parse_labels_file(labels_path)
    dataset_bytes = b'{"synthetic": true, "rows": 9}\n'
    records = []
    for row in manifest.tasks:
        stars = labels.by_task[row.task_index]
        records.append(
            {
                "task_index": row.task_index,
                "execution_order": row.execution_order,
                "split": row.split,
                "context_sha256": row.run_context_sha256,
                "user_group": expected_user_group(row.cohort_group),
                "status": "success",
                "reason": "validated_model_output",
                "prediction_origin": "llm",
                "stars": stars,
                "review": "(synthetic prepared smoke) fabricated review",
                "evidence_refs": [f"ev_{row.task_index + 1:016x}"],
                "diagnostics": {
                    "llm_attempts": 1,
                    "repair": "not_requested",
                    "prompt_bytes": 1000 + row.task_index,
                    "memory": {
                        "candidates": 0,
                        "time_eligible": 0,
                        "selected": 0,
                        "prompt_included": 0,
                        "filtered_injection": 0,
                        "write": "written",
                    },
                },
            }
        )
    run_document = {
        "schema_version": "simulation_run.v1",
        "source": {
            "producer": "llm-user-simulation-agent",
            "kind": "fake_on_synthetic",
            "model_source": "offline_injected_fake_callable",
            "dataset_fingerprint_sha256": sha(dataset_bytes),
            "manifest_fingerprint_sha256": manifest.raw_sha256,
            "config_fingerprint_sha256": sha(b"synthetic-prepared-smoke-config"),
        },
        "simulated": True,
        "record_count": len(records),
        "records": records,
    }
    run_bytes = (
        json.dumps(run_document, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    manifest_bytes = manifest_path.read_bytes()
    labels_bytes = labels_path.read_bytes()

    def resource(resource_id: str, kind: str, payload: bytes, release: str) -> dict:
        return {
            "resource_id": resource_id,
            "kind": kind,
            "release": release,
            "expected_sha256": sha(payload),
            "expected_size_bytes": len(payload),
            "license_status": "synthetic_fixture_only",
            "redistribution_status": "synthetic_only",
            "contains_personal_data": False,
        }

    resources = [
        resource("dataset_normalized", "dataset_normalized", dataset_bytes, "synthetic_prepared_dataset"),
        resource("task_manifest", "task_manifest", manifest_bytes, "synthetic_prepared_manifest"),
        resource("labels", "local_labels", labels_bytes, "synthetic_prepared_labels"),
        resource("run_candidate", "run_artifact", run_bytes, "synthetic_prepared_run"),
    ]
    config = {
        "schema_version": "evaluation_config.v1",
        "config_id": "synthetic_prepared_smoke",
        "purpose": "synthetic_contract_check",
        "condition": {
            "condition_id": "candidate_prepared_fake",
            "role": "candidate",
            "origin": "synthetic_fake_run",
            "run_resource_id": "run_candidate",
        },
        "task_manifest": {
            "resource_id": "task_manifest",
            "adapter": "b_native_task_manifest.v1",
        },
        "labels": {"resource_id": "labels", "schema": "local_labels.v1"},
        "baseline": None,
        "cohort": {
            "development_splits": ["train", "validation"],
            "holdout_splits": ["test"],
            "allow_user_overlap": False,
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
        "resources": resources,
        "confirmation": {"required": True, "scope": "preflight_only"},
    }
    return {
        "evaluation_config": (
            json.dumps(config, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8"),
        "dataset_normalized": dataset_bytes,
        "task_manifest": manifest_bytes,
        "labels": labels_bytes,
        "run_candidate": run_bytes,
    }
