"""Preflight checks: independent hashes, run join, time/cohort and baseline coverage."""

from __future__ import annotations

import json

from amazon_eval_agent.config_v1 import parse_config_bytes
from amazon_eval_agent.preflight_v1 import run_preflight
from amazon_eval_agent.safety import deep_thaw
from amazon_eval_agent.samples_batch1 import synthetic_files
from tests import batch1_helpers


def _ready(files=None):
    files = files or synthetic_files()
    config = parse_config_bytes(files["evaluation_config"])
    return run_preflight(config, batch1_helpers.bindings_from(files))


def _run_mutator(mutate):
    def apply(payload: bytes) -> bytes:
        document = json.loads(payload.decode("utf-8"))
        mutate(document)
        document["record_count"] = len(document["records"])
        return (
            json.dumps(document, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")

    return apply


def test_ready_preflight_reports_denominators_without_metrics() -> None:
    result = _ready()
    report = result.report
    assert result.ready is True
    assert report["status"] == "ready_for_scoring"
    assert report["comparison"]["holdout_tasks"] == 3
    assert report["comparison"]["common_model_holdout_tasks"] == 2
    assert report["comparison"]["common_system_holdout_tasks"] == 3
    assert report["comparison"]["metrics_status"] == "not_computed_in_preflight"
    assert report["scoring_available"] is False
    assert report["metric_computation"] == "not_implemented_in_preflight"
    assert report["real_measurement_status"] == "not_verified_or_not_run"
    assert report["publication_status"] == "synthetic_only"
    assert list(report["warnings"]) == [] or "user_splits_overlap_not_user_disjoint" in report["warnings"]


def test_report_contains_no_private_markers_or_paths() -> None:
    report = _ready().report
    text = json.dumps(deep_thaw(report), ensure_ascii=False)
    for marker in ("ug_", "synthetic-user", "PRIVATE", "E:\\", "/tmp/", "fabricated review"):
        assert marker not in text


def test_plan_fingerprint_is_deterministic() -> None:
    assert _ready().plan_fingerprint == _ready().plan_fingerprint


def test_source_hash_mismatch_blocks_before_parse() -> None:
    files = synthetic_files()
    files["run_candidate"] = files["run_candidate"].replace(
        b'"stars": 4', b'"stars": 5', 1
    )
    config = parse_config_bytes(files["evaluation_config"])
    result = run_preflight(config, batch1_helpers.bindings_from(files))
    assert result.ready is False
    assert "resource_hash_mismatch" in result.report["blockers"]
    assert "run_contract_invalid" not in result.report["blockers"]


def test_wrong_expected_size_blocks() -> None:
    files = synthetic_files()
    config_doc = json.loads(files["evaluation_config"].decode("utf-8"))
    for resource in config_doc["resources"]:
        if resource["resource_id"] == "labels":
            resource["expected_size_bytes"] += 1
    files["evaluation_config"] = (
        json.dumps(config_doc, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    config = parse_config_bytes(files["evaluation_config"])
    result = run_preflight(config, batch1_helpers.bindings_from(files))
    assert result.ready is False
    assert "resource_size_mismatch" in result.report["blockers"]


def test_extra_run_task_blocks() -> None:
    def mutate(document):
        extra = dict(document["records"][0])
        extra["task_index"] = 99
        extra["execution_order"] = 12
        extra["split"] = "test"
        document["records"].append(extra)

    files = batch1_helpers.bundle_with(
        file_mutators={"run_candidate": _run_mutator(mutate)}
    )
    result = _ready(files)
    assert result.ready is False
    assert "run_task_not_in_manifest" in result.report["blockers"]


def test_run_split_and_order_and_group_mismatches_block() -> None:
    def split_mutate(document):
        document["records"][0]["split"] = "test"

    files = batch1_helpers.bundle_with(
        file_mutators={"run_candidate": _run_mutator(split_mutate)}
    )
    assert "run_split_mismatch" in _ready(files).report["blockers"]

    def order_mutate(document):
        document["records"][0]["execution_order"] = 1
        document["records"][1]["execution_order"] = 0

    files = batch1_helpers.bundle_with(
        file_mutators={"run_candidate": _run_mutator(order_mutate)}
    )
    assert "run_execution_order_mismatch" in _ready(files).report["blockers"]

    def group_mutate(document):
        document["records"][0]["user_group"] = "ug_ffffffffffffffff"

    files = batch1_helpers.bundle_with(
        file_mutators={"run_candidate": _run_mutator(group_mutate)}
    )
    assert "run_cohort_group_mismatch" in _ready(files).report["blockers"]


def test_manifest_context_mismatch_blocks_when_available() -> None:
    def mutate(payload: bytes) -> bytes:
        document = json.loads(payload.decode("utf-8"))
        document["tasks"][0]["run_context_sha256"] = "f" * 64
        return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")

    files = batch1_helpers.bundle_with(
        file_mutators={"task_manifest": mutate}
    )
    result = _ready(files)
    assert result.ready is False
    assert "run_context_hash_mismatch" in result.report["blockers"]


def test_declared_dataset_and_manifest_fingerprints_are_bound_to_files() -> None:
    def dataset_mutate(document):
        document["source"]["dataset_fingerprint_sha256"] = "f" * 64

    files = batch1_helpers.bundle_with(
        file_mutators={"run_candidate": _run_mutator(dataset_mutate)}
    )
    result = _ready(files)
    assert result.ready is False
    assert "run_dataset_fingerprint_mismatch" in result.report["blockers"]

    def manifest_mutate(document):
        document["source"]["manifest_fingerprint_sha256"] = "f" * 64

    files = batch1_helpers.bundle_with(
        file_mutators={"run_candidate": _run_mutator(manifest_mutate)}
    )
    result = _ready(files)
    assert result.ready is False
    assert "run_manifest_fingerprint_mismatch" in result.report["blockers"]


def test_no_holdout_records_blocks_with_missing_counts() -> None:
    def mutate(document):
        document["records"] = [
            record for record in document["records"] if record["split"] != "test"
        ]

    files = batch1_helpers.bundle_with(
        file_mutators={"run_candidate": _run_mutator(mutate)}
    )
    result = _ready(files)
    assert result.ready is False
    assert "cohort_insufficient_model_holdout_tasks" in result.report["blockers"]
    assert result.report["comparison"]["missing_candidate_holdout_tasks"] == 3
    assert result.report["run"]["full_cohort_status_counts"]["missing"] == 3


def test_labels_mismatch_blocks() -> None:
    def mutate(payload: bytes) -> bytes:
        document = json.loads(payload.decode("utf-8"))
        document["tasks"] = document["tasks"][:-1]
        return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")

    files = batch1_helpers.bundle_with(file_mutators={"labels": mutate})
    result = _ready(files)
    assert result.ready is False
    assert "labels_task_set_mismatch" in result.report["blockers"]


def test_incomplete_baseline_blocks() -> None:
    def mutate(payload: bytes) -> bytes:
        rows = [
            line
            for line in payload.decode("utf-8").splitlines()
            if '"task_index": 10' not in line
        ]
        return ("\n".join(rows) + "\n").encode("utf-8")

    files = batch1_helpers.bundle_with(
        file_mutators={"baseline_predictions": mutate}
    )
    result = _ready(files)
    assert result.ready is False
    assert "baseline_holdout_incomplete" in result.report["blockers"]


def test_user_overlap_disallowed_blocks_current_protocol() -> None:
    files = batch1_helpers.bundle_with(
        config_mutator=lambda d: d["cohort"].update(allow_user_overlap=False)
    )
    result = _ready(files)
    assert result.ready is False
    assert "cohort_user_overlap_disallowed" in result.report["blockers"]


def test_empty_run_artifact_is_contract_invalid() -> None:
    def mutate(document):
        document["records"] = []

    files = batch1_helpers.bundle_with(
        file_mutators={"run_candidate": _run_mutator(mutate)}
    )
    result = _ready(files)
    assert result.ready is False
    assert "run_contract_invalid" in result.report["blockers"]


def test_source_kind_mismatch_blocks() -> None:
    files = batch1_helpers.bundle_with(
        config_mutator=lambda d: d["condition"].update(origin="fake_on_real_context")
    )
    result = _ready(files)
    assert result.ready is False
    assert "run_source_kind_mismatch" in result.report["blockers"]
