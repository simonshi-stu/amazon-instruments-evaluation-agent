"""Report-semantics tests for stage-1 validation.

The central rule: ``artifact_validated`` (file contract) and
``all_predictions_succeeded`` (record outcomes) are reported separately, a
failed record is never counted as a success, declared source metadata is never
presented as a verified real measurement, and reports never contain review
text, user text, paths or echoed field values.
"""

from __future__ import annotations

import json

import pytest

from amazon_eval_agent.contracts import simulation_run_v1 as contract
from amazon_eval_agent.samples import synthetic_sample_payload
from amazon_eval_agent.validate import ValidationError, validate_bytes, validate_file
from tests import helpers


def test_valid_report_keeps_contract_and_measurement_claims_separate() -> None:
    report = validate_bytes(helpers.payload_bytes(helpers.artifact()))
    assert report["artifact_validated"] is True
    assert report["validation_errors"] == []
    assert report["declared_source_kind"] == "fake_on_synthetic"
    assert report["declared_model_source"] == "offline_injected_fake_callable"
    assert report["simulated_content"] is True
    assert report["all_predictions_succeeded"] is True
    assert report["prediction_success_count"] == 1
    assert report["real_measurement_status"] == "not_verified_or_not_run"
    assert report["fingerprint_verification"] == "declared_only_not_independently_verified"
    assert report["internal_consistency_note"]
    assert "self-consistency only" in report["internal_consistency_note"]
    assert report["accuracy_claim"] == "none"
    assert report["scoring_available"] is False
    assert report["fingerprints"]["dataset_fingerprint_sha256"] == helpers.HEX64_A
    assert report["stage1_scope_note"]


def test_valid_artifact_with_failed_record_is_not_all_success() -> None:
    document = helpers.artifact(
        [
            helpers.success_record(task_index=0, execution_order=0),
            helpers.failed_record(task_index=1, execution_order=1),
        ]
    )
    report = validate_bytes(helpers.payload_bytes(document))
    assert report["artifact_validated"] is True
    assert report["all_predictions_succeeded"] is False
    assert report["prediction_success_count"] == 1
    assert report["status_counts"] == {
        "success": 1,
        "degraded": 0,
        "refused": 0,
        "failed": 1,
    }


def test_real_model_declared_is_validated_but_never_verified() -> None:
    document = helpers.artifact(source={"kind": "real_model_declared"})
    report = validate_bytes(helpers.payload_bytes(document))
    assert report["artifact_validated"] is True
    assert report["declared_source_kind"] == "real_model_declared"
    assert report["real_measurement_status"] == "not_verified_or_not_run"
    assert "not independent" in report["real_measurement_note"]


def test_sample_check_report_covers_all_four_states() -> None:
    report = validate_bytes(synthetic_sample_payload())
    assert report["artifact_validated"] is True
    assert report["record_count"] == 4
    assert report["status_counts"] == {
        "success": 1,
        "degraded": 1,
        "refused": 1,
        "failed": 1,
    }
    assert report["all_predictions_succeeded"] is False


def test_invalid_report_contains_only_safe_codes() -> None:
    document = helpers.artifact()
    document["PRIVATE_KEY_NAME"] = "PRIVATE_VALUE"
    document["source"]["dataset_fingerprint_sha256"] = "SECRET_FINGERPRINT"
    report = validate_bytes(helpers.payload_bytes(document))
    assert report["artifact_validated"] is False
    assert "unknown_field" in report["validation_errors"]
    assert "invalid_sha256" in report["validation_errors"]
    serialized = json.dumps(report, ensure_ascii=False)
    assert "PRIVATE_KEY_NAME" not in serialized
    assert "PRIVATE_VALUE" not in serialized
    assert "SECRET_FINGERPRINT" not in serialized
    assert report["scoring_available"] is False


def test_valid_report_contains_no_review_or_user_text() -> None:
    document = helpers.artifact(
        [helpers.success_record(review="PRIVATE_MARKER_REVIEW_TEXT")]
    )
    report = validate_bytes(helpers.payload_bytes(document))
    serialized = json.dumps(report, ensure_ascii=False)
    assert "PRIVATE_MARKER_REVIEW_TEXT" not in serialized
    assert "ug_0000000000000001" not in serialized


def test_file_size_gate_stats_before_any_parse(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def spy(text: str):  # pragma: no cover - must never run
        calls.append(text)
        raise AssertionError("parser must not run for oversized files")

    monkeypatch.setattr(contract, "_json_loads", spy)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (contract.MAX_ARTIFACT_BYTES + 1))
    report = validate_file(oversized)
    assert report["artifact_validated"] is False
    assert report["validation_errors"] == ["artifact_too_large"]
    assert calls == []


def test_missing_or_unreadable_file_is_a_safe_error(tmp_path) -> None:
    with pytest.raises(ValidationError) as exc:
        validate_file(tmp_path / "missing.json")
    assert exc.value.code == "input_unreadable"


def test_non_bytes_payload_rejected() -> None:
    report = validate_bytes(123)  # type: ignore[arg-type]
    assert report["artifact_validated"] is False
    assert report["validation_errors"] == ["artifact_type_invalid"]
