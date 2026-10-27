"""Optional recovered-A checks; paths come only from environment variables.

These tests are intentionally skipped on a machine without the recovered work
snapshot or prepared data. A skip is not source recovery and no mirror is
created by this repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from amazon_eval_agent.adapters.a_p0_predictions_v1 import parse_baseline_predictions_bytes
from amazon_eval_agent.adapters.a_prepared_v1 import import_a_prepared, verify_a_prepared
from amazon_eval_agent.adapters.a_temporal_manifest_v2 import parse_a_manifest_bytes
from amazon_eval_agent.adapters.simulation_run_v1 import (
    expected_user_group,
    load_run_artifact,
)
from amazon_eval_agent.contracts import simulation_run_v1 as contract
from amazon_eval_agent.labels_v1 import parse_labels_file
from amazon_eval_agent.safety import read_path_bytes
from amazon_eval_agent.scoring_v1 import audit_baseline_table
from amazon_eval_agent.task_manifest_v1 import parse_task_manifest_file

A_DIR = Path(os.environ["AMAZON_A_RECOVERED_DIR"]) if os.environ.get("AMAZON_A_RECOVERED_DIR") else None
PREPARED = Path(os.environ["AMAZON_A_PREPARED_DIR"]) if os.environ.get("AMAZON_A_PREPARED_DIR") else None
BASELINE_DIR = (
    Path(os.environ["AMAZON_A_BASELINE_DIR"])
    if os.environ.get("AMAZON_A_BASELINE_DIR")
    else None
)


def test_recovered_mirror_schema_hash_matches_without_copying() -> None:
    if A_DIR is None:
        pytest.skip("recovered A work snapshot not configured")
    mirror = A_DIR / "user_simulator_project" / "simulation_run_v1.py"
    if not mirror.is_file():
        pytest.skip("recovered A mirror not present")
    code = (
        "from user_simulator_project import simulation_run_v1 as c; "
        "print(c.schema_hash())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=A_DIR,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == contract.schema_hash()
    from tests.test_mirror_drift import _normalized_core

    canonical_dump, canonical_typing = _normalized_core(
        Path(contract.__file__).resolve()
    )
    mirror_dump, mirror_typing = _normalized_core(mirror)
    assert canonical_dump == mirror_dump
    assert canonical_typing <= mirror_typing
    assert not (Path(__file__).resolve().parents[1] / "user_simulator_project").exists()


def test_recovered_prepared_manifest_and_artifact_bindings() -> None:
    if PREPARED is None:
        pytest.skip("recovered prepared directory not configured")
    manifest_path = PREPARED / "manifest.json"
    normalized_path = PREPARED / "normalized_reviews.jsonl"
    if not manifest_path.is_file() or not normalized_path.is_file():
        pytest.skip("recovered prepared files not present")
    projection = parse_a_manifest_bytes(manifest_path.read_bytes())
    assert projection.task_count == 400
    assert projection.eligible_task_count == 400
    assert projection.split_counts == {"train": 238, "validation": 82, "test": 80}
    # The A manifest's declared source fingerprint is not compared to the raw
    # file hash; only the manifest file itself was read here.
    assert projection.safe_summary()["source_review_verification"] == (
        "declared_not_recomputed"
    )

    artifact = A_DIR / "results" / "recovery_20261018_prepared" / "simulation_run.v1.json"
    if not artifact.is_file():
        pytest.skip("recovered prepared artifact not present")
    payload = artifact.read_bytes()
    loaded = load_run_artifact(payload)
    assert loaded.valid, loaded.errors
    view = loaded.view
    assert view is not None
    assert view.source_kind == "fake_on_real_context"
    assert view.record_count == 3
    assert hashlib.sha256(payload).hexdigest() == view.artifact_sha256
    # The three runtime user labels are the documented deterministic hash of
    # the manifest cohort labels, not raw identifiers.
    for record in view.records:
        matching = [
            row for row in projection.tasks if row.task_index == record.task_index
        ]
        assert len(matching) == 1
        assert record.user_group == expected_user_group(matching[0].cohort_group)


def test_recovered_prepared_import_counts_and_sanitization(tmp_path) -> None:
    if PREPARED is None:
        pytest.skip("recovered prepared directory not configured")
    manifest_out = tmp_path / "manifest.json"
    labels_out = tmp_path / "labels.json"
    result = import_a_prepared(
        PREPARED,
        "recovered_prepared_audit",
        manifest_out,
        labels_out,
    )
    assert result.task_count == 400
    assert result.label_count == 400
    assert result.split_counts == {"train": 238, "validation": 82, "test": 80}
    assert result.history_cut_date == "2013-08-28"
    manifest = parse_task_manifest_file(manifest_out)
    labels = parse_labels_file(labels_out)
    assert manifest.user_overlap == {
        "train_validation": 35,
        "train_test": 21,
        "validation_test": 11,
    }
    assert set(labels.by_task) == {row.task_index for row in manifest.tasks}
    assert result.context_verification["history_selection_verified"] == 400
    assert result.context_verification["manifest_context_verified"] == 400
    assert result.context_verification["target_copy_absence_verified"] == 400
    assert result.context_verification["history_max_available"] == 234
    payloads = manifest_out.read_bytes() + labels_out.read_bytes()
    for marker in (b"reviewerID", b"asin", b"target_review_id", b"reviewText"):
        assert marker not in payloads
    if A_DIR is not None:
        artifact = (
            A_DIR
            / "results"
            / "recovery_20261018_prepared"
            / "simulation_run.v1.json"
        )
        if artifact.is_file():
            verified = verify_a_prepared(PREPARED, manifest_out, artifact, labels_out)
            assert verified["status"] == "verified"
            assert verified["run_context_checked_tasks"] == 3
            assert verified["run_context_verified"] == 3


def test_recovered_baseline_point_metrics_match_old_report(tmp_path) -> None:
    if PREPARED is None or BASELINE_DIR is None:
        pytest.skip("recovered prepared/baseline directory not configured")
    manifest_out = tmp_path / "manifest.json"
    labels_out = tmp_path / "labels.json"
    import_a_prepared(
        PREPARED,
        "recovered_baseline_audit",
        manifest_out,
        labels_out,
    )
    manifest = parse_task_manifest_file(manifest_out)
    labels = parse_labels_file(labels_out)
    predictions = parse_baseline_predictions_bytes(
        read_path_bytes(
            BASELINE_DIR / "predictions.jsonl", max_bytes=8 * 1024 * 1024
        )
    )
    report = json.loads((BASELINE_DIR / "report.json").read_text(encoding="utf-8"))
    audit = audit_baseline_table(manifest, labels, predictions)
    for mode in ("global", "user", "item", "combined"):
        for split in ("test", "all"):
            for audit_key, report_key in (
                ("mae", "mae"),
                ("rmse", "rmse"),
                ("bias", "mean_signed_bias"),
            ):
                assert audit["modes"][mode][split][audit_key] == pytest.approx(
                    report["baselines"][mode][split][report_key], abs=1e-12
                )
    assert audit["real_measurement_status"] == "not_verified_or_not_run"
