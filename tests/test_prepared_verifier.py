"""Strict A prepared identity/context verification and end-to-end synthetic smoke."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amazon_eval_agent.adapters.a_prepared_v1 import (
    PreparedImportError,
    import_a_prepared,
    verify_a_prepared,
)
from amazon_eval_agent.config_v1 import parse_config_bytes
from amazon_eval_agent.confirmation_v1 import (
    create_confirmation,
    parse_confirmation_bytes,
    serialize_confirmation,
    verify_confirmation,
)
from amazon_eval_agent.preflight_v1 import run_preflight
from amazon_eval_agent.scoring_v1 import score_confirmed_plan
from amazon_eval_agent.task_manifest_v1 import parse_task_manifest_file
from tests import batch1_helpers


def _import(tmp_path: Path):
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    manifest = tmp_path / "manifest.json"
    labels = tmp_path / "labels.json"
    result = import_a_prepared(
        prepared, "synthetic_verifier", manifest, labels
    )
    return prepared, manifest, labels, result


def _rewrite_jsonl(path: Path, mutate) -> None:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    mutate(rows)
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def _rewrite_manifest(path: Path, mutate) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def test_import_recomputes_manifest_and_runtime_contexts(tmp_path) -> None:
    prepared, manifest, _labels, result = _import(tmp_path)
    parsed = parse_task_manifest_file(manifest)
    assert result.task_count == 9
    assert result.context_verification["history_selection_verified"] == 9
    assert result.context_verification["manifest_context_verified"] == 9
    assert result.context_verification["target_copy_absence_verified"] == 9
    assert all(row.run_context_sha256 for row in parsed.tasks)
    assert all(row.history_max_date for row in parsed.tasks)
    verified = verify_a_prepared(prepared, manifest)
    assert verified["status"] == "verified"
    assert verified["context"]["history_selection_verified"] == 9


def test_groundtruth_identity_mismatch_is_rejected(tmp_path) -> None:
    prepared, out_manifest, out_labels = (
        tmp_path / "p",
        tmp_path / "m.json",
        tmp_path / "l.json",
    )
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    _rewrite_jsonl(
        prepared / "groundtruth" / "p0.jsonl",
        lambda rows: rows[0].update(user_id="wrong-synthetic-user"),
    )
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(prepared, "identity_bad", out_manifest, out_labels)
    assert exc.value.code == "a_prepared_identity_mismatch"


@pytest.mark.parametrize("field", ["item_id", "target_review_id"])
def test_other_identity_fields_are_rejected(tmp_path, field) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    _rewrite_jsonl(
        prepared / "groundtruth" / "p0.jsonl",
        lambda rows: rows[0].update({field: "wrong-synthetic-value"}),
    )
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "identity_bad", tmp_path / "m.json", tmp_path / "l.json"
        )
    assert exc.value.code == "a_prepared_identity_mismatch"


def test_identity_field_types_and_same_date_are_rejected(tmp_path) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    _rewrite_jsonl(
        prepared / "tasks" / "p0.jsonl",
        lambda rows: rows[0].update(user_id=["not", "a", "scalar"]),
    )
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "type_bad", tmp_path / "m.json", tmp_path / "l.json"
        )
    assert exc.value.code == "a_prepared_user_id_invalid"

    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path / "date")
    _rewrite_jsonl(
        prepared / "groundtruth" / "p0.jsonl",
        lambda rows: rows[0].update(target_date="2020-03-02"),
    )
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "date_bad", tmp_path / "m2.json", tmp_path / "l2.json"
        )
    assert exc.value.code == "a_prepared_target_date_mismatch"

    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path / "duplicate_date")
    _rewrite_jsonl(
        prepared / "tasks" / "p0.jsonl",
        lambda rows: rows[1].update(
            user_id=rows[0]["user_id"], target_date=rows[0]["target_date"]
        ),
    )
    _rewrite_jsonl(
        prepared / "groundtruth" / "p0.jsonl",
        lambda rows: rows[1].update(
            user_id=rows[0]["user_id"], target_date=rows[0]["target_date"]
        ),
    )
    _rewrite_manifest(
        prepared / "manifest.json",
        lambda d: d["tasks"][1].update(user_group=d["tasks"][0]["user_group"]),
    )
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "dup_date", tmp_path / "m3.json", tmp_path / "l3.json"
        )
    assert exc.value.code == "a_prepared_user_date_duplicate"


def test_duplicate_target_review_id_is_rejected(tmp_path) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    _rewrite_jsonl(
        prepared / "groundtruth" / "p0.jsonl",
        lambda rows: rows[1].update(target_review_id=rows[0]["target_review_id"]),
    )
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "dup_review", tmp_path / "m.json", tmp_path / "l.json"
        )
    assert exc.value.code in (
        "a_prepared_identity_mismatch",
        "a_prepared_target_review_id_duplicate",
    )


def test_manifest_identity_hashes_are_required(tmp_path) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    _rewrite_manifest(
        prepared / "manifest.json",
        lambda d: d["tasks"][0].update(source_task_index_sha256="f" * 64),
    )
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "source_hash_bad", tmp_path / "m.json", tmp_path / "l.json"
        )
    assert exc.value.code == "a_prepared_source_task_hash_mismatch"

    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path / "second")
    _rewrite_manifest(
        prepared / "manifest.json",
        lambda d: d["tasks"][0].update(target_timestamp_sha256="f" * 64),
    )
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "timestamp_hash_bad", tmp_path / "m2.json", tmp_path / "l2.json"
        )
    assert exc.value.code == "a_prepared_target_timestamp_hash_mismatch"


def test_runtime_context_or_selection_drift_is_rejected(tmp_path) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    _rewrite_jsonl(
        prepared / "runtime_reviews.jsonl",
        lambda rows: rows[0].update(text="(synthetic prepared changed history)"),
    )
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "context_bad", tmp_path / "m.json", tmp_path / "l.json"
        )
    assert exc.value.code == "prepared_context_invalid"
    assert "manifest_context_mismatch" in exc.value.errors

    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path / "second")
    _rewrite_manifest(
        prepared / "manifest.json",
        lambda d: d["tasks"][0].update(visible_history_row_indexes=[1]),
    )
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "selection_bad", tmp_path / "m2.json", tmp_path / "l2.json"
        )
    assert exc.value.code == "prepared_context_invalid"
    assert "visible_history_selection_mismatch" in exc.value.errors


def test_same_day_hour_target_mismatch_is_rejected(tmp_path) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    _rewrite_jsonl(
        prepared / "runtime_reviews.jsonl",
        lambda rows: next(
            row
            for row in rows
            if row["review_id"] == "synthetic-review-0"
        ).update(timestamp="2020-03-01T12:00:00+00:00"),
    )
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "same_day_hour", tmp_path / "m.json", tmp_path / "l.json"
        )
    assert exc.value.code == "prepared_context_invalid"
    assert "target_review_time_mismatch" in exc.value.errors


def test_missing_runtime_fails_closed(tmp_path) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    (prepared / "runtime_reviews.jsonl").unlink()
    with pytest.raises(PreparedImportError) as exc:
        import_a_prepared(
            prepared, "runtime_missing", tmp_path / "m.json", tmp_path / "l.json"
        )
    assert exc.value.code == "prepared_context_invalid"
    assert "runtime_reviews_unreadable" in exc.value.errors


def test_independent_verifier_rejects_label_value_mismatch(tmp_path) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    manifest = tmp_path / "manifest.json"
    labels = tmp_path / "labels.json"
    import_a_prepared(prepared, "labels_verify", manifest, labels)
    document = json.loads(labels.read_text(encoding="utf-8"))
    document["tasks"][0]["stars"] = 1
    labels.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    with pytest.raises(PreparedImportError) as exc:
        verify_a_prepared(prepared, manifest, labels_path=labels)
    assert exc.value.code == "labels_value_mismatch"


def test_run_context_mismatch_is_rejected_by_independent_verifier(tmp_path) -> None:
    files = batch1_helpers.prepared_bundle(tmp_path)
    prepared = tmp_path / "prepared"
    manifest = tmp_path / "b_manifest.json"
    run = json.loads(files["run_candidate"].decode("utf-8"))
    run["records"][0]["context_sha256"] = "0" * 64
    run_path = tmp_path / "run_tampered.json"
    run_path.write_text(json.dumps(run, sort_keys=True), encoding="utf-8")
    with pytest.raises(PreparedImportError) as exc:
        verify_a_prepared(prepared, manifest, run_path)
    assert exc.value.code == "prepared_context_invalid"
    assert "run_context_mismatch" in exc.value.errors


def test_full_synthetic_prepared_chain_scores_with_test_tasks(tmp_path) -> None:
    files = batch1_helpers.prepared_bundle(tmp_path)
    config = parse_config_bytes(files["evaluation_config"])
    result = run_preflight(config, batch1_helpers.bindings_from(files))
    assert result.ready is True, result.report["blockers"]
    assert result.report["comparison"]["holdout_tasks"] == 2
    confirmation = create_confirmation(
        result, confirmed_at="2026-10-26T00:00:00Z"
    )
    parsed = parse_confirmation_bytes(serialize_confirmation(confirmation))
    verification = verify_confirmation(parsed, result)
    report = score_confirmed_plan(result, parsed, verification)
    assert report["status"] == "computed"
    assert report["full_cohort"]["requested_holdout_tasks"] == 2
    assert report["full_cohort"]["model_success_only"]["eligible"] == 2
    assert report["full_cohort"]["model_success_only"]["coverage"] == 1.0
    assert report["comparison"] is None
