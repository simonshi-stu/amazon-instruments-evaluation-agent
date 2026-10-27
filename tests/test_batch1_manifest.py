"""Task manifest, A schema-2 adapter and A prepared import tests (synthetic only)."""

from __future__ import annotations

import json

import pytest

from amazon_eval_agent.adapters.a_prepared_v1 import (
    PreparedImportError,
    import_a_prepared,
)
from amazon_eval_agent.adapters.a_temporal_manifest_v2 import (
    AManifestError,
    parse_a_manifest_bytes,
)
from amazon_eval_agent.labels_v1 import parse_labels_file
from amazon_eval_agent.samples_batch1 import (
    synthetic_a_manifest_bytes,
    synthetic_a_manifest_document,
    synthetic_files,
)
from amazon_eval_agent.task_manifest_v1 import (
    TaskManifestError,
    parse_task_manifest_bytes,
    parse_task_manifest_file,
)
from tests import batch1_helpers


def _manifest_document() -> dict:
    return json.loads(synthetic_files()["task_manifest"].decode("utf-8"))


def _parse(document: dict):
    return parse_task_manifest_bytes(json.dumps(document).encode("utf-8"))


def _assert_manifest_error(document: dict, code: str) -> None:
    with pytest.raises(TaskManifestError) as exc:
        _parse(document)
    assert code in exc.value.errors or exc.value.code == code


def test_sample_manifest_is_valid_and_explicitly_not_user_disjoint() -> None:
    manifest = parse_task_manifest_bytes(synthetic_files()["task_manifest"])
    assert manifest.split_counts == {"train": 6, "validation": 3, "test": 3}
    assert manifest.date_strength == "target_dates_checked"
    assert manifest.history_strength == "declared_history_bound"
    assert manifest.user_overlap == {
        "train_validation": 0,
        "train_test": 1,
        "validation_test": 0,
    }
    assert manifest.user_holdout_policy == "allow_overlap_not_user_disjoint"


def test_duplicate_and_noncontiguous_identities_rejected() -> None:
    document = _manifest_document()
    document["tasks"][1]["task_index"] = 0
    _assert_manifest_error(document, "manifest_task_index_invalid")

    document = _manifest_document()
    document["tasks"][1]["execution_order"] = 0
    _assert_manifest_error(document, "manifest_execution_order_invalid")

    document = _manifest_document()
    document["tasks"][1]["execution_order"] = 5
    _assert_manifest_error(document, "manifest_execution_order_invalid")


def test_split_order_and_counts_rejected() -> None:
    document = _manifest_document()
    document["tasks"][0]["split"] = "test"
    document["tasks"][11]["split"] = "train"
    _assert_manifest_error(document, "manifest_split_order_invalid")


def test_target_cut_order_and_history_leakage_rejected() -> None:
    document = _manifest_document()
    document["tasks"][0]["target_date"] = "2019-12-31"
    document["tasks"][0]["history_max_date"] = "2019-12-01"
    _assert_manifest_error(document, "manifest_target_before_cut")

    document = _manifest_document()
    document["tasks"][0]["history_max_date"] = document["tasks"][0]["target_date"]
    _assert_manifest_error(document, "manifest_history_overlap")

    document = _manifest_document()
    document["tasks"][0]["target_date"] = "2020-03-01"
    _assert_manifest_error(document, "manifest_target_date_order_invalid")


def test_mixed_target_dates_and_missing_time_rejected() -> None:
    document = _manifest_document()
    document["tasks"][0]["target_date"] = None
    _assert_manifest_error(document, "manifest_mixed_target_dates")

    document = _manifest_document()
    document["tasks"][0]["target_date"] = None
    document["tasks"][0]["target_timestamp_sha256"] = None
    _assert_manifest_error(document, "manifest_target_time_missing")


def test_same_date_and_same_timestamp_group_cannot_span_splits() -> None:
    document = _manifest_document()
    document["tasks"][9]["target_date"] = document["tasks"][8]["target_date"]
    _assert_manifest_error(document, "manifest_same_date_spans_splits")

    document = _manifest_document()
    document["tasks"][9]["target_timestamp_sha256"] = document["tasks"][8][
        "target_timestamp_sha256"
    ]
    _assert_manifest_error(document, "manifest_timestamp_group_spans_splits")


def test_huge_split_ratio_rejected_without_overflow() -> None:
    document = _manifest_document()
    document["protocol"]["split_policy"]["train_ratio"] = 10**400
    _assert_manifest_error(document, "manifest_train_ratio_invalid")


def test_empty_task_list_rejected() -> None:
    document = _manifest_document()
    document["tasks"] = []
    document["split_counts"] = {"train": 0, "validation": 0, "test": 0}
    _assert_manifest_error(document, "manifest_tasks_invalid")


def test_closed_schema_and_source_count() -> None:
    document = _manifest_document()
    document["SECRET"] = "value"
    _assert_manifest_error(document, "manifest_unknown_field")

    document = _manifest_document()
    document["tasks"][0]["raw_user_id"] = "SECRET"
    _assert_manifest_error(document, "manifest_unknown_field")

    document = _manifest_document()
    document["origin"]["source_task_count"] = 1
    _assert_manifest_error(document, "manifest_source_task_count_mismatch")


def test_a_manifest_projection_is_strict_and_has_no_target_dates() -> None:
    projection = parse_a_manifest_bytes(synthetic_a_manifest_bytes())
    assert projection.task_count == 9
    assert projection.split_counts == {"train": 5, "validation": 2, "test": 2}
    assert projection.safe_summary()["target_dates_available"] is False
    assert projection.safe_summary()["source_review_verification"] == "declared_not_recomputed"


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda d: d.update(schema_version=3), "a_manifest_schema_version_mismatch"),
        (lambda d: d.update(privacy="anything"), "a_manifest_privacy_statement_missing"),
        (
            lambda d: d.update(target_timestamp_semantics_verified=True),
            "a_manifest_timestamp_semantics_claim_invalid",
        ),
        (
            lambda d: d.update(target_timestamp_semantics_caller_asserted=False),
            "a_manifest_timestamp_semantics_assertion_missing",
        ),
        (lambda d: d["tasks"][0].update(target_text_absent_from_context=False), "a_manifest_target_text_not_absent"),
        (lambda d: d["tasks"][0].update(target_review_row_matched=False), "a_manifest_target_review_not_matched"),
        (lambda d: d["tasks"][0].update(timestamp_available=False), "a_manifest_target_timestamp_unavailable"),
        (lambda d: d["tasks"][0].update(visible_history_count=99), "a_manifest_history_count_mismatch"),
        (lambda d: d["tasks"][0].update(SECRET="x"), "a_manifest_unknown_field"),
        (lambda d: d.update(split_counts={"train": 2, "validation": 1, "test": 2}), "a_manifest_split_counts_mismatch"),
    ],
)
def test_a_manifest_adapter_rejects_header_and_task_drift(mutate, code) -> None:
    document = synthetic_a_manifest_document()
    mutate(document)
    with pytest.raises(AManifestError) as exc:
        parse_a_manifest_bytes(json.dumps(document).encode("utf-8"))
    assert code in exc.value.errors or exc.value.code == code


def test_a_manifest_private_field_rejected() -> None:
    document = synthetic_a_manifest_document()
    document["tasks"][0]["user_id"] = "synthetic-raw-user"
    with pytest.raises(AManifestError) as exc:
        parse_a_manifest_bytes(json.dumps(document).encode("utf-8"))
    assert "a_manifest_private_field" in exc.value.errors


def test_a_manifest_same_timestamp_group_cannot_span_splits() -> None:
    document = synthetic_a_manifest_document()
    document["tasks"][5]["target_timestamp_sha256"] = document["tasks"][4][
        "target_timestamp_sha256"
    ]
    with pytest.raises(AManifestError) as exc:
        parse_a_manifest_bytes(json.dumps(document).encode("utf-8"))
    assert "a_manifest_timestamp_group_spans_splits" in exc.value.errors


def test_import_a_prepared_is_sanitized_and_refuses_overwrite(tmp_path) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    manifest_out = tmp_path / "out" / "manifest.json"
    labels_out = tmp_path / "out" / "labels.json"
    result = import_a_prepared(
        prepared, "synthetic_import", manifest_out, labels_out
    )
    assert result.task_count == 9
    assert result.label_count == 9
    assert result.split_counts == {"train": 5, "validation": 2, "test": 2}
    manifest = parse_task_manifest_file(manifest_out)
    labels = parse_labels_file(labels_out)
    assert manifest.history_cut_date == "2020-01-01"
    assert manifest.date_strength == "target_dates_checked"
    assert manifest.history_strength == "declared_history_bound"
    assert manifest.origin_kind == "a_prepared_v1"
    assert len(labels.by_task) == 9
    serialized = manifest_out.read_bytes() + labels_out.read_bytes()
    assert b"synthetic-user" not in serialized
    assert b"synthetic-review" not in serialized
    assert b"fabricated review" not in serialized
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "synthetic_import", manifest_out, labels_out
        )
    assert exc.value.code == "output_file_exists"


def test_import_rejects_bad_manifest_id_and_missing_source(tmp_path) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "Bad ID", tmp_path / "m.json", tmp_path / "l.json"
        )
    assert exc.value.code == "manifest_id_invalid"
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            tmp_path / "missing", "synthetic_import", tmp_path / "m2.json", tmp_path / "l2.json"
        )
    assert exc.value.code == "a_manifest_unreadable"
