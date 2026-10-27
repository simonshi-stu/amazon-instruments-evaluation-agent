"""Local labels and independent resource-hash verification tests."""

from __future__ import annotations

import json

import pytest

from amazon_eval_agent.config_v1 import parse_config_bytes
from amazon_eval_agent.labels_v1 import (
    LabelError,
    parse_labels_bytes,
    verify_labels_cover_manifest,
)
from amazon_eval_agent.resources_v1 import (
    verify_resource_bytes,
    verify_resources,
)
from amazon_eval_agent.samples_batch1 import synthetic_files
from amazon_eval_agent.task_manifest_v1 import parse_task_manifest_bytes
from tests import batch1_helpers


def _labels() -> dict:
    return json.loads(synthetic_files()["labels"].decode("utf-8"))


def test_labels_have_no_private_fields_and_cover_manifest() -> None:
    labels = parse_labels_bytes(synthetic_files()["labels"])
    manifest = parse_task_manifest_bytes(synthetic_files()["task_manifest"])
    verify_labels_cover_manifest(labels, manifest)
    assert len(labels.by_task) == 12
    assert labels.raw_sha256
    serialized = json.dumps(labels.safe_summary(), sort_keys=True)
    assert "ug_" not in serialized
    assert "stars" not in serialized


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda d: d["tasks"][1].update(task_index=0), "labels_task_index_invalid"),
        (lambda d: d["tasks"][1].update(stars=0), "labels_stars_invalid"),
        (lambda d: d["tasks"][1].update(stars=True), "labels_stars_invalid"),
        (lambda d: d["tasks"][1].update(stars=4.0), "labels_stars_invalid"),
        (lambda d: d["tasks"][1].update(review="PRIVATE"), "labels_unknown_field"),
        (lambda d: d.update(schema_version="local_labels.v2"), "labels_schema_version_mismatch"),
    ],
)
def test_labels_reject_bad_values_and_fields(mutate, code) -> None:
    document = _labels()
    mutate(document)
    with pytest.raises(LabelError) as exc:
        parse_labels_bytes(json.dumps(document).encode("utf-8"))
    assert code in exc.value.errors or exc.value.code == code


def test_labels_task_set_must_match_manifest() -> None:
    labels = parse_labels_bytes(synthetic_files()["labels"])
    manifest_document = json.loads(synthetic_files()["task_manifest"].decode("utf-8"))
    manifest_document["tasks"] = manifest_document["tasks"][:-1]
    manifest_document["split_counts"]["test"] -= 1
    manifest = parse_task_manifest_bytes(json.dumps(manifest_document).encode("utf-8"))
    with pytest.raises(LabelError) as exc:
        verify_labels_cover_manifest(labels, manifest)
    assert exc.value.code == "labels_task_set_mismatch"


def test_empty_and_oversize_labels_rejected() -> None:
    document = _labels()
    document["tasks"] = []
    with pytest.raises(LabelError) as exc:
        parse_labels_bytes(json.dumps(document).encode("utf-8"))
    assert "labels_tasks_invalid" in exc.value.errors
    with pytest.raises(LabelError) as exc:
        parse_labels_bytes(b"x" * (1024 * 1024 + 1))
    assert exc.value.code == "artifact_too_large"


def test_resource_hash_and_size_are_independently_computed() -> None:
    files = synthetic_files()
    config = parse_config_bytes(files["evaluation_config"])
    verifications = verify_resources(config.resources, batch1_helpers.bindings_from(files))
    assert all(item.verified for item in verifications.values())
    sample = config.resources[0]
    changed = verify_resource_bytes(sample, b"x" * sample.expected_size_bytes)
    assert changed.integrity == "hash_mismatch"
    wrong_size = verify_resource_bytes(sample, b"x")
    assert wrong_size.integrity == "size_mismatch"
    too_large = verify_resource_bytes(sample, b"x" * 10, max_bytes=5)
    assert too_large.integrity == "too_large"
    assert too_large.actual_sha256 is None


def test_missing_binding_fails_closed_and_license_is_not_truth() -> None:
    files = synthetic_files()
    config = parse_config_bytes(files["evaluation_config"])
    bindings = batch1_helpers.bindings_from(files)
    bindings.pop("run_candidate")
    verifications = verify_resources(config.resources, bindings)
    assert verifications["run_candidate"].integrity == "missing_binding"
    assert verifications["run_candidate"].actual_sha256 is None
    assert verifications["run_candidate"].verified is False
    assert verifications["dataset_normalized"].license_verified is True
