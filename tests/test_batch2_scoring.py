"""Point-scoring tests: denominators, no zero-fill, common-task comparison, no intervals."""

from __future__ import annotations

import copy
import json
import math

import pytest

from amazon_eval_agent.config_v1 import parse_config_bytes
from amazon_eval_agent.confirmation_v1 import (
    create_confirmation,
    parse_confirmation_bytes,
    serialize_confirmation,
    verify_confirmation,
)
from amazon_eval_agent.preflight_v1 import run_preflight
from amazon_eval_agent.samples_batch1 import synthetic_files
from amazon_eval_agent.scoring_v1 import (
    ScoreError,
    audit_baseline_table,
    score_confirmed_plan,
)
from tests import batch1_helpers


def _ready(files=None):
    files = files or synthetic_files()
    config = parse_config_bytes(files["evaluation_config"])
    result = run_preflight(config, batch1_helpers.bindings_from(files))
    confirmation = create_confirmation(result, confirmed_at="2026-10-26T00:00:00Z")
    parsed = parse_confirmation_bytes(serialize_confirmation(confirmation))
    verification = verify_confirmation(parsed, result)
    return result, parsed, verification


def _score(files=None):
    result, confirmation, verification = _ready(files)
    return score_confirmed_plan(result, confirmation, verification)


def test_hand_calculated_model_and_system_denominators() -> None:
    report = _score()
    assert report["status"] == "computed"
    full = report["full_cohort"]
    assert full["requested_holdout_tasks"] == 3
    assert full["model_success_only"]["eligible"] == 2
    assert full["model_success_only"]["coverage"] == pytest.approx(2 / 3)
    assert full["model_success_only"]["metrics"]["mae"] == pytest.approx(0.0)
    assert full["model_success_only"]["metrics"]["rmse"] == pytest.approx(0.0)
    assert full["system_including_degraded"]["eligible"] == 3
    assert full["system_including_degraded"]["coverage"] == pytest.approx(1.0)
    assert full["system_including_degraded"]["metrics"]["mae"] == pytest.approx(1 / 3)
    assert full["system_including_degraded"]["metrics"]["rmse"] == pytest.approx(
        math.sqrt(1 / 3)
    )
    assert full["system_including_degraded"]["metrics"]["bias"] == pytest.approx(-1 / 3)
    assert full["system_including_degraded"]["metrics"]["within_one"] == pytest.approx(1.0)


def test_baseline_common_task_comparison_matches_hand_calculation() -> None:
    report = _score()
    comparison = report["comparison"]
    assert comparison["baseline_mode"] == "global"
    assert comparison["common_model_tasks"] == 2
    assert comparison["common_system_tasks"] == 3
    assert comparison["model_success_only"]["candidate"]["mae"] == pytest.approx(0.0)
    assert comparison["model_success_only"]["baseline"]["mae"] == pytest.approx(0.5)
    assert comparison["model_success_only"]["delta_candidate_minus_baseline"]["mae"] == pytest.approx(-0.5)
    assert comparison["system_including_degraded"]["candidate"]["mae"] == pytest.approx(1 / 3)
    assert comparison["system_including_degraded"]["baseline"]["mae"] == pytest.approx(1.0)
    assert comparison["significance"] == "not_computed"
    assert comparison["interval"] is None


def test_missing_test_task_is_missing_not_zero_filled() -> None:
    def mutate(document):
        document["records"] = [
            record for record in document["records"] if record["task_index"] != 10
        ]

    files = batch1_helpers.bundle_with(
        file_mutators={"run_candidate": lambda payload: _run_without(payload, 10)},
        config_mutator=lambda d: d["cohort"].update(
            min_common_holdout_tasks=1, min_common_holdout_clusters=1
        ),
    )
    report = _score(files)
    full = report["full_cohort"]
    assert full["requested_holdout_tasks"] == 3
    assert full["artifact_present"] == 2
    assert full["missing"] == 1
    assert full["status_counts"]["missing"] == 1
    assert full["model_success_only"]["eligible"] == 1
    assert full["model_success_only"]["coverage"] == pytest.approx(1 / 3)
    assert full["model_success_only"]["metrics"]["mae"] == pytest.approx(0.0)
    assert report["exclusions"]["model"]["excluded_missing"] == 1
    assert report["exclusions"]["model"]["included_success"] == 1


def _run_without(payload: bytes, task_index: int) -> bytes:
    document = json.loads(payload.decode("utf-8"))
    document["records"] = [
        record for record in document["records"] if record["task_index"] != task_index
    ]
    document["record_count"] = len(document["records"])
    return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")


def test_failed_test_task_is_counted_but_not_imputed() -> None:
    def mutate(payload: bytes) -> bytes:
        document = json.loads(payload.decode("utf-8"))
        record = next(row for row in document["records"] if row["task_index"] == 10)
        record.update(
            {
                "status": "failed",
                "reason": "internal_error",
                "prediction_origin": "none",
                "stars": None,
                "review": None,
                "evidence_refs": [],
                "diagnostics": {
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
                },
            }
        )
        return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")

    files = batch1_helpers.bundle_with(
        file_mutators={"run_candidate": mutate},
        config_mutator=lambda d: d["cohort"].update(
            min_common_holdout_tasks=1, min_common_holdout_clusters=1
        ),
    )
    report = _score(files)
    full = report["full_cohort"]
    assert full["status_counts"]["failed"] == 1
    assert full["artifact_present"] == 3
    assert full["system_including_degraded"]["eligible"] == 2
    assert full["system_including_degraded"]["coverage"] == pytest.approx(2 / 3)
    assert report["exclusions"]["system"]["excluded_failed"] == 1
    assert report["diagnostics"]["reason_counts"]["internal_error"] == 1


def test_baseline_audit_reproduces_hand_calculation() -> None:
    files = synthetic_files()
    manifest = __import__(
        "amazon_eval_agent.task_manifest_v1", fromlist=["parse_task_manifest_bytes"]
    ).parse_task_manifest_bytes(files["task_manifest"])
    labels = __import__(
        "amazon_eval_agent.labels_v1", fromlist=["parse_labels_bytes"]
    ).parse_labels_bytes(files["labels"])
    table = __import__(
        "amazon_eval_agent.adapters.a_p0_predictions_v1",
        fromlist=["parse_baseline_predictions_bytes"],
    ).parse_baseline_predictions_bytes(files["baseline_predictions"])
    audit = audit_baseline_table(manifest, labels, table)
    test = audit["modes"]["global"]["test"]
    assert test["n"] == 3
    assert test["mae"] == pytest.approx(1.0)
    assert test["rmse"] == pytest.approx(math.sqrt(5 / 3))
    assert test["bias"] == pytest.approx(-1.0)
    assert audit["real_measurement_status"] == "not_verified_or_not_run"


def test_score_report_has_no_private_payload_and_no_intervals() -> None:
    report = _score()
    text = json.dumps(report, ensure_ascii=False)
    for marker in ("ug_", "E:\\", "fabricated review", "synthetic-user"):
        assert marker not in text
    assert report["measurement"]["intervals"] == "not_computed"
    assert report["measurement"]["significance"] == "not_computed"
    assert report["measurement"]["real_measurement_status"] == "not_verified_or_not_run"
    assert report["measurement"]["latency"] == "unknown_not_in_contract"
    assert report["measurement"]["usage"] == "unknown_not_in_contract"
    assert report["privacy"]["label_values_included"] is False


def test_score_internally_verifies_without_caller_token() -> None:
    result, confirmation, _verification = _ready()
    report = score_confirmed_plan(result, confirmation)
    assert report["status"] == "computed"
    assert report["confirmation"]["confirmation_valid"] is True


def test_invalid_confirmation_verification_is_an_extra_veto() -> None:
    result, confirmation, verification = _ready()
    tampered = copy.deepcopy(verification)
    tampered["confirmation_valid"] = False
    with pytest.raises(ScoreError) as exc:
        score_confirmed_plan(result, confirmation, tampered)
    assert exc.value.code == "confirmation_verification_rejected"


def test_tampered_confirmation_is_rejected_despite_true_caller_verdict() -> None:
    result, confirmation, verification = _ready()
    tampered = copy.deepcopy(confirmation)
    tampered["plan_fingerprint"] = "0" * 64
    tampered["bindings"]["labels_sha256"] = "0" * 64
    with pytest.raises(ScoreError) as exc:
        score_confirmed_plan(result, tampered, {"confirmation_valid": True})
    assert exc.value.code == "confirmation_invalid"


def test_other_plan_confirmation_is_rejected() -> None:
    result, _, _ = _ready()
    changed_files = batch1_helpers.bundle_with(
        config_mutator=lambda d: d["cohort"].update(min_common_holdout_tasks=1)
    )
    other_result, other_confirmation, _ = _ready(changed_files)
    assert other_result.plan_fingerprint != result.plan_fingerprint
    with pytest.raises(ScoreError) as exc:
        score_confirmed_plan(result, other_confirmation, {"confirmation_valid": True})
    assert exc.value.code == "confirmation_invalid"


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda c: c.update(scope="preflight_only"), "confirmation_invalid"),
        (lambda c: c.update(status="draft"), "confirmation_invalid"),
        (lambda c: c.update(schema_version="evaluation_confirmation.v2"), "confirmation_invalid"),
        (lambda c: c.update(confirmation_id="conf_0000000000000000"), "confirmation_invalid"),
        (lambda c: c.update(confirmed_at="2026-10-26T00:00:00+08:00"), "confirmation_invalid"),
    ],
)
def test_tampered_confirmation_metadata_is_rejected(mutate, code) -> None:
    result, confirmation, _ = _ready()
    tampered = copy.deepcopy(confirmation)
    mutate(tampered)
    with pytest.raises(ScoreError) as exc:
        score_confirmed_plan(result, tampered, {"confirmation_valid": True})
    assert exc.value.code == code


def test_result_semantic_mappings_are_deeply_immutable() -> None:
    result, confirmation, verification = _ready()
    with pytest.raises(TypeError):
        result.labels.by_task[0] = 1
    with pytest.raises(TypeError):
        result.run.status_counts["success"] = 999
    with pytest.raises(TypeError):
        result.run.records[0].memory["write"] = "forged"
    with pytest.raises(TypeError):
        result.manifest.split_counts["test"] = 999
    with pytest.raises(TypeError):
        result.baseline.rows_by_mode["global"][0] = 1.0
    with pytest.raises(TypeError):
        result.report["status"] = "ready_for_scoring"
    # The immutable objects still verify and score.
    fresh = score_confirmed_plan(result, confirmation, verification)
    assert fresh["status"] == "computed"
