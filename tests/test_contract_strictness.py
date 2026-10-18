"""Strictness tests for the canonical ``SimulationRunV1`` reader.

Covers the resource gates (input length before encode/copy, byte size, parse
recursion, depth, record count before record validation), JSON hygiene
(duplicate keys, non-finite numbers), the closed schema, the four-state matrix,
the diagnostic invariants (attempt/repair cycles, memory funnel, unique refs)
and the fail-closed separation between "file contract valid" and record
outcomes. ``validate_payload`` must never raise for hostile input.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from amazon_eval_agent.contracts import simulation_run_v1 as contract
from amazon_eval_agent.samples import synthetic_sample_payload
from tests import helpers

EXPECTED_SCHEMA_HASH = "70256fa957a3103e2892f3e3a22e67b23b2a85c1a9ac66c05b18a7418f0da85c"
USER_SIM_REPO = Path(__file__).resolve().parents[2] / "llm-user-simulation-agent-dual"


def _validate(document: dict) -> contract.ArtifactValidation:
    return contract.validate_payload(helpers.payload_bytes(document))


def _assert_invalid(document: dict, code: str) -> None:
    result = _validate(document)
    assert result.valid is False
    assert result.document is None
    assert code in result.errors


def _single_record(factory, overrides=None) -> contract.ArtifactValidation:
    return _validate(helpers.artifact([factory(**overrides or {})]))


def test_schema_hash_pinned() -> None:
    digest = contract.schema_hash()
    assert digest == EXPECTED_SCHEMA_HASH
    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)


def test_handwritten_sample_validates() -> None:
    result = contract.validate_payload(synthetic_sample_payload())
    assert result.valid is True, result.errors
    document = result.document
    assert document is not None
    assert document.record_count == 4
    assert document.status_counts == {
        "success": 1,
        "degraded": 1,
        "refused": 1,
        "failed": 1,
    }
    assert document.prediction_success_count == 1
    assert document.all_predictions_succeeded is False
    assert document.source_kind == "fake_on_synthetic"


def test_mirror_schema_hash_matches_user_simulation_mirror() -> None:
    if not (USER_SIM_REPO / "user_simulator_project" / "simulation_run_v1.py").is_file():
        pytest.skip("sibling simulation contract mirror not present (an empty directory is not source recovery)")
    code = (
        "from user_simulator_project import simulation_run_v1 as c; "
        "print(c.schema_hash())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=USER_SIM_REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == contract.schema_hash()


@pytest.mark.parametrize(
    "factory,overrides",
    [
        (helpers.success_record, {}),
        (helpers.repaired_success_record, {}),
        (helpers.degraded_record, {}),
        (helpers.degraded_record, {"stars": 3}),
        (helpers.refused_record, {}),
        (helpers.failed_record, {}),
    ],
)
def test_status_matrix_valid_combinations(factory, overrides) -> None:
    result = _single_record(factory, overrides)
    assert result.valid is True, result.errors


@pytest.mark.parametrize(
    "factory,overrides,code",
    [
        (helpers.success_record, {"stars": True}, "invalid_stars"),
        (helpers.success_record, {"stars": 4.0}, "invalid_stars"),
        (helpers.success_record, {"stars": 0}, "invalid_stars"),
        (helpers.success_record, {"stars": 6}, "invalid_stars"),
        (helpers.success_record, {"review": None}, "invalid_review"),
        (helpers.success_record, {"review": ""}, "invalid_review"),
        (helpers.success_record, {"review": "   "}, "invalid_review"),
        (helpers.success_record, {"review": " \t\n "}, "invalid_review"),
        (helpers.success_record, {"review": "x" * 8001}, "invalid_review"),
        (helpers.success_record, {"prediction_origin": "none"}, "status_origin_mismatch"),
        (
            helpers.success_record,
            {"reason": "statistical_baseline_fallback"},
            "status_reason_mismatch",
        ),
        (helpers.success_record, {"evidence_refs": []}, "invalid_evidence_ref"),
        (helpers.success_record, {"evidence_refs": ["ev_X"]}, "invalid_evidence_ref"),
        (helpers.degraded_record, {"stars": None}, "invalid_stars"),
        (helpers.degraded_record, {"stars": True}, "invalid_stars"),
        (helpers.degraded_record, {"stars": 5.5}, "invalid_stars"),
        (helpers.degraded_record, {"review": "leaked"}, "invalid_review"),
        (helpers.degraded_record, {"prediction_origin": "llm"}, "status_origin_mismatch"),
        (helpers.refused_record, {"stars": 3}, "invalid_stars"),
        (
            helpers.refused_record,
            {"reason": "llm_transport_error"},
            "status_reason_mismatch",
        ),
        (helpers.refused_record, {"evidence_refs": ["ev_0000000000000001"]}, "invalid_evidence_ref"),
        (helpers.failed_record, {"prediction_origin": "statistical_baseline"}, "status_origin_mismatch"),
        (helpers.success_record, {"reason": "no_rendered_evidence"}, "status_reason_mismatch"),
    ],
)
def test_status_matrix_invalid_combinations(factory, overrides, code) -> None:
    _assert_invalid(helpers.artifact([factory(**overrides)]), code)


def test_write_state_must_match_status() -> None:
    _assert_invalid(
        helpers.artifact(
            [helpers.success_record(diagnostics=helpers.diagnostics(memory=helpers.memory(write="skipped")))]
        ),
        "write_status_mismatch",
    )
    _assert_invalid(
        helpers.artifact(
            [helpers.degraded_record(diagnostics=helpers.diagnostics(memory=helpers.memory(write="written")))]
        ),
        "write_status_mismatch",
    )


def _with_first_record(mutate) -> dict:
    document = helpers.artifact()
    mutate(document)
    return document


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["records"][0]["diagnostics"].update({"llm_attempts": 0}),
        lambda d: d["records"][0]["diagnostics"].update({"llm_attempts": 2, "repair": "attempted"}),
        lambda d: d["records"][0].update({"reason": "validated_model_output_repaired"}),
        lambda d: d["records"][0].update(
            {"reason": "validated_model_output_repaired"}
        )
        or d["records"][0]["diagnostics"].update({"llm_attempts": 1}),
        lambda d: d["records"][0]["diagnostics"].update({"repair": "attempted"}),
        lambda d: d["records"][0]["diagnostics"].update({"repair": "requested"}),
        lambda d: d["records"][0]["diagnostics"].update({"llm_attempts": 0, "repair": "requested"}),
        lambda d: d["records"][0]["diagnostics"].update({"llm_attempts": 2}),
        lambda d: d["records"][0]["diagnostics"].update({"llm_attempts": 2, "repair": "reserve_failed"}),
    ],
)
def test_success_attempt_repair_invariants(mutate) -> None:
    _assert_invalid(_with_first_record(mutate), "diagnostic_attempts_mismatch")


def test_refused_and_degraded_and_failed_attempt_repair_invariants() -> None:
    refused = helpers.artifact([helpers.refused_record()])
    refused["records"][0]["diagnostics"]["llm_attempts"] = 1
    _assert_invalid(refused, "diagnostic_attempts_mismatch")

    refused_repair = helpers.artifact([helpers.refused_record()])
    refused_repair["records"][0]["diagnostics"].update(
        {"repair": "requested", "llm_attempts": 1}
    )
    _assert_invalid(refused_repair, "diagnostic_attempts_mismatch")

    degraded_blank = helpers.artifact([helpers.degraded_record()])
    degraded_blank["records"][0]["diagnostics"].update(
        {"llm_attempts": 1, "repair": "not_requested"}
    )
    _assert_invalid(degraded_blank, "diagnostic_attempts_mismatch")

    degraded_two = helpers.artifact([helpers.degraded_record()])
    degraded_two["records"][0]["diagnostics"].update({"llm_attempts": 2})
    _assert_invalid(degraded_two, "diagnostic_attempts_mismatch")

    failed_gap = helpers.artifact([helpers.failed_record()])
    failed_gap["records"][0]["diagnostics"].update({"repair": "attempted"})
    _assert_invalid(failed_gap, "diagnostic_attempts_mismatch")


def test_failed_reason_attempt_invariants() -> None:
    transport_zero = helpers.artifact([helpers.failed_record()])
    transport_zero["records"][0]["diagnostics"]["llm_attempts"] = 0
    _assert_invalid(transport_zero, "diagnostic_attempts_mismatch")

    invalid_return_zero = helpers.artifact(
        [helpers.failed_record(reason="invalid_llm_return")]
    )
    invalid_return_zero["records"][0]["diagnostics"]["llm_attempts"] = 0
    _assert_invalid(invalid_return_zero, "diagnostic_attempts_mismatch")

    internal_zero = helpers.artifact(
        [
            helpers.failed_record(
                reason="internal_error",
                diagnostics={
                    "llm_attempts": 0,
                    "repair": "not_requested",
                    "prompt_bytes": 0,
                    "memory": helpers.memory(),
                },
            )
        ]
    )
    result = _validate(internal_zero)
    assert result.valid is True, result.errors


def test_escaped_surrogate_review_rejected() -> None:
    document = helpers.artifact()
    document["records"][0]["review"] = "ok\ud800ok"
    payload = json.dumps(document).encode("ascii")  # default escaping
    assert b"\\ud800" in payload
    result = contract.validate_payload(payload)
    assert result.valid is False
    assert result.errors == ("invalid_review",)


@pytest.mark.parametrize(
    "memory_overrides",
    [
        {"candidates": 0, "time_eligible": 0, "selected": 3},
        {"candidates": 1, "time_eligible": 1, "selected": 1, "prompt_included": 2},
        {
            "candidates": 1,
            "time_eligible": 1,
            "selected": 1,
            "prompt_included": 1,
            "filtered_injection": 1,
        },
        {"candidates": 0, "time_eligible": 2, "selected": 2},
    ],
)
def test_memory_funnel_invariants(memory_overrides) -> None:
    document = helpers.artifact(
        [
            helpers.success_record(
                diagnostics=helpers.diagnostics(memory=helpers.memory(**memory_overrides))
            )
        ]
    )
    _assert_invalid(document, "memory_funnel_mismatch")


def test_memory_funnel_valid_boundaries() -> None:
    valid_memory = helpers.memory(
        candidates=4,
        time_eligible=3,
        selected=2,
        prompt_included=1,
        filtered_injection=1,
        write="written",
    )
    result = _single_record(
        helpers.success_record, {"diagnostics": helpers.diagnostics(memory=valid_memory)}
    )
    assert result.valid is True, result.errors


def test_duplicate_evidence_refs_rejected() -> None:
    for factory in (helpers.success_record, helpers.degraded_record):
        document = helpers.artifact([factory()])
        record = document["records"][0]
        record["evidence_refs"] = [record["evidence_refs"][0], record["evidence_refs"][0]]
        _assert_invalid(document, "duplicate_evidence_ref")


def test_duplicate_json_keys_rejected() -> None:
    raw = (
        b'{"schema_version":"simulation_run.v1",'
        b'"schema_version":"simulation_run.v1"}'
    )
    result = contract.validate_payload(raw)
    assert result.valid is False
    assert "json_duplicate_field" in result.errors

    document = helpers.artifact()
    record = copy.deepcopy(document["records"][0])
    payload = json.dumps(document).replace(
        '"stars": 4',
        '"stars": 4, "stars": 5',
    )
    assert '"stars": 4, "stars": 5' in payload
    nested = contract.validate_payload(payload.encode("utf-8"))
    assert nested.valid is False
    assert "json_duplicate_field" in nested.errors
    assert record["stars"] == 4


def test_nonfinite_numbers_rejected() -> None:
    for raw in (b'{"value": NaN}', b'{"value": Infinity}', b'{"value": -Infinity}'):
        result = contract.validate_payload(raw)
        assert result.valid is False
        assert "json_nonfinite_number" in result.errors
    overflow = contract.validate_payload(b'{"value": 1e999}')
    assert overflow.valid is False
    assert "json_nonfinite_number" in overflow.errors


def test_huge_numbers_never_overflow() -> None:
    document = helpers.artifact([helpers.degraded_record()])
    document["records"][0]["stars"] = 10**400
    _assert_invalid(document, "invalid_stars")

    document = helpers.artifact()
    document["records"][0]["stars"] = 10**400
    _assert_invalid(document, "invalid_stars")

    document = helpers.artifact()
    document["records"][0]["diagnostics"]["prompt_bytes"] = 10**40
    _assert_invalid(document, "invalid_integer")
    document = helpers.artifact()
    document["records"][0]["diagnostics"]["prompt_bytes"] = (
        contract.MAX_PROMPT_BYTES_FIELD + 1
    )
    _assert_invalid(document, "value_out_of_bounds")
    document = helpers.artifact()
    document["records"][0]["task_index"] = 10**40
    _assert_invalid(document, "invalid_integer")


def test_deep_json_rejected_before_schema_checks() -> None:
    document = helpers.artifact()
    nested: object = "leaf"
    for _ in range(contract.MAX_ARTIFACT_DEPTH + 5):
        nested = [nested]
    document["too_deep_value"] = nested
    _assert_invalid(document, "json_too_deep")

    deep_array = b"[" * 1500 + b"]" * 1500
    result = contract.validate_payload(deep_array)
    assert result.valid is False
    assert result.errors == ("json_too_deep",)

    deep_object = b'{"x": ' + b"[" * 1200 + b"]" * 1200 + b"}"
    nested_result = contract.validate_payload(deep_object)
    assert nested_result.valid is False
    assert nested_result.errors == ("json_too_deep",)


def test_input_length_gate_precedes_encoding_and_copy() -> None:
    huge_with_surrogate = "\ud800" + "x" * (contract.MAX_ARTIFACT_BYTES + 1)
    result = contract.validate_payload(huge_with_surrogate)
    assert result.valid is False
    assert result.errors == ("artifact_too_large",)

    small_surrogate = contract.validate_payload("\ud800")
    assert small_surrogate.valid is False
    assert small_surrogate.errors == ("artifact_encoding_error",)

    large_bytearray = bytearray(b"x" * (contract.MAX_ARTIFACT_BYTES + 1))
    bytearray_result = contract.validate_payload(large_bytearray)
    assert bytearray_result.valid is False
    assert bytearray_result.errors == ("artifact_too_large",)

    valid_bytearray = contract.validate_payload(bytearray(synthetic_sample_payload()))
    assert valid_bytearray.valid is True

    unsupported = contract.validate_payload(12345)
    assert unsupported.valid is False
    assert unsupported.errors == ("artifact_type_invalid",)


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff\xfe\x00",
        b"",
        b"{",
        b"[]",
        b'"text"',
        b"null",
        b"true",
        b"0",
        b"x" * 32,
        b"[" * 5000 + b"]" * 5000,
        "\udfff",
        bytearray(b"{}"),
        12345,
        None,
        {"already": "a dict"},
    ],
)
def test_validate_payload_never_raises_for_hostile_inputs(payload) -> None:
    result = contract.validate_payload(payload)
    assert isinstance(result, contract.ArtifactValidation)
    assert result.valid is False
    assert result.document is None
    assert result.errors


def test_byte_size_gate_runs_before_parsing(monkeypatch) -> None:
    calls: list[str] = []

    def spy(text: str):  # pragma: no cover - must never run
        calls.append(text)
        raise AssertionError("the JSON parser must not run for oversized input")

    monkeypatch.setattr(contract, "_json_loads", spy)
    payload = b"x" * (contract.MAX_ARTIFACT_BYTES + 1)
    result = contract.validate_payload(payload)
    assert result.valid is False
    assert result.errors == ("artifact_too_large",)
    assert calls == []


def test_record_count_gate_rejects_before_record_validation(monkeypatch) -> None:
    calls: list[int] = []
    original = contract._validate_record

    def spy(record, errors):
        calls.append(1)
        return original(record, errors)

    monkeypatch.setattr(contract, "_validate_record", spy)
    too_many = [
        helpers.success_record(task_index=index, execution_order=index)
        for index in range(contract.MAX_RECORDS + 1)
    ]
    result = _validate(helpers.artifact(too_many))
    assert result.valid is False
    assert "too_many_records" in result.errors
    assert calls == []

    single = _validate(helpers.artifact([helpers.success_record()]))
    assert single.valid is True
    assert calls == [1]


def test_record_count_identities_and_mismatch() -> None:
    empty = helpers.artifact([])
    _assert_invalid(empty, "empty_records")

    duplicate_index = helpers.artifact(
        [
            helpers.success_record(task_index=1, execution_order=0),
            helpers.success_record(task_index=1, execution_order=1),
        ]
    )
    _assert_invalid(duplicate_index, "duplicate_task_identity")

    duplicate_order = helpers.artifact(
        [
            helpers.success_record(task_index=0, execution_order=2),
            helpers.success_record(task_index=1, execution_order=2),
        ]
    )
    _assert_invalid(duplicate_order, "duplicate_task_identity")

    mismatched = helpers.artifact([helpers.success_record()])
    mismatched["record_count"] = 2
    _assert_invalid(mismatched, "record_count_mismatch")


def test_closed_schema_rejects_unknown_and_missing_fields() -> None:
    extra_top = helpers.artifact()
    extra_top["unexpected"] = 1
    _assert_invalid(extra_top, "unknown_field")

    missing_top = helpers.artifact()
    del missing_top["simulated"]
    _assert_invalid(missing_top, "missing_field")

    extra_record = helpers.artifact()
    extra_record["records"][0]["secret_extra"] = "value"
    _assert_invalid(extra_record, "unknown_field")

    extra_diag = helpers.artifact()
    extra_diag["records"][0]["diagnostics"]["attempts_text"] = "one"
    _assert_invalid(extra_diag, "unknown_field")

    extra_memory = helpers.artifact()
    extra_memory["records"][0]["diagnostics"]["memory"]["raw_prompt"] = "no"
    _assert_invalid(extra_memory, "unknown_field")


def test_source_enum_simulated_flag_and_fingerprints() -> None:
    _assert_invalid(
        helpers.artifact(source={"kind": "real_model_v2"}), "invalid_enum"
    )
    _assert_invalid(
        helpers.artifact(source={"producer": "someone-else"}), "invalid_enum"
    )
    _assert_invalid(
        helpers.artifact(source={"model_source": "gpt-real"}), "invalid_enum"
    )
    _assert_invalid(
        helpers.artifact(source={"dataset_fingerprint_sha256": "zz"}), "invalid_sha256"
    )
    _assert_invalid(
        helpers.artifact(source={"manifest_fingerprint_sha256": "A" * 64}),
        "invalid_sha256",
    )
    not_simulated = helpers.artifact()
    not_simulated["simulated"] = 1
    _assert_invalid(not_simulated, "simulated_not_true")


def test_real_model_declared_shape_is_still_only_declarative() -> None:
    result = _validate(helpers.artifact(source={"kind": "real_model_declared"}))
    assert result.valid is True
    assert result.document is not None
    assert result.document.source_kind == "real_model_declared"


def test_invariants_are_part_of_the_schema_description() -> None:
    spec = contract._schema_spec()
    assert spec["diagnostic_invariants"] == list(contract.DIAGNOSTIC_INVARIANTS)
    assert set(contract.DIAGNOSTIC_INVARIANTS) >= {
        "success_first_pass_attempts_exact",
        "success_repaired_attempts_and_repair_exact",
        "repair_state_attempts_consistent",
        "refused_zero_attempts",
        "failed_call_error_requires_attempt",
        "degraded_statistical_cycle",
        "memory_funnel_ordering",
        "memory_filtered_plus_included_bound",
        "evidence_refs_unique",
        "review_utf8_encodable",
    }
