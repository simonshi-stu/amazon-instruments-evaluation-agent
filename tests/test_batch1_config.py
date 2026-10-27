"""Typed evaluation config (`evaluation_config.v1`) tests.

All fixtures are synthetic. The config never stores paths, labels or IDs.
"""

from __future__ import annotations

import json

import pytest

from amazon_eval_agent.config_v1 import (
    CONFIG_SCHEMA_VERSION,
    ConfigError,
    describe_config,
    parse_config_bytes,
)
from amazon_eval_agent.samples_batch1 import synthetic_files
from tests import batch1_helpers


def _config() -> dict:
    return json.loads(synthetic_files()["evaluation_config"].decode("utf-8"))


def _parse(document: dict):
    return parse_config_bytes(
        json.dumps(document, sort_keys=True).encode("utf-8")
    )


def test_valid_config_is_typed_and_path_free() -> None:
    config = parse_config_bytes(synthetic_files()["evaluation_config"])
    assert config.purpose == "synthetic_contract_check"
    assert config.condition.role == "candidate"
    assert config.baseline is not None
    assert config.baseline.mode == "global"
    assert config.cohort.development_splits == ("train", "validation")
    assert config.cohort.holdout_splits == ("test",)
    assert len(config.resources) == 5
    description = describe_config(config)
    serialized = json.dumps(description, sort_keys=True)
    assert "path" not in serialized.lower()
    assert "ug_" not in serialized
    assert config.config_sha256 != config.semantic_sha256


def test_config_fingerprint_changes_when_semantics_change() -> None:
    first = parse_config_bytes(synthetic_files()["evaluation_config"])
    document = _config()
    document["cohort"]["min_common_holdout_tasks"] = 3
    second = _parse(document)
    assert first.semantic_sha256 != second.semantic_sha256


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda d: d.update(schema_version="evaluation_config.v2"), "config_schema_version_mismatch"),
        (lambda d: d.update(purpose="synthetic_everything"), "config_purpose_invalid"),
        (lambda d: d["condition"].update(role="baseline"), "condition_role_invalid"),
        (lambda d: d["condition"].update(origin="made_up"), "condition_origin_invalid"),
        (lambda d: d["baseline"].update(mode="median"), "baseline_mode_invalid"),
        (lambda d: d["resources"][0].update(expected_sha256="A" * 64), "resource_sha256_invalid"),
        (lambda d: d["resources"][0].update(expected_size_bytes=-1), "resource_size_invalid"),
        (lambda d: d["resources"][0].update(license_status="public_domain"), "resource_license_invalid"),
        (lambda d: d["resources"][0].update(contains_personal_data="no"), "resource_personal_flag_invalid"),
        (lambda d: d["cohort"].update(holdout_splits=["validation"]), "cohort_split_protocol_invalid"),
        (lambda d: d["cohort"].update(min_common_holdout_tasks=0), "cohort_min_tasks_invalid"),
        (
            lambda d: (
                d.update(purpose="real_evaluation_plan"),
                d["temporal"].update(require_verified_target_dates=False),
            ),
            "temporal_dates_required_for_public_plan",
        ),
    ],
)
def test_config_rejects_invalid_fields(mutate, code) -> None:
    document = _config()
    mutate(document)
    with pytest.raises(ConfigError) as exc:
        _parse(document)
    assert exc.value.code == code


def test_unknown_and_missing_fields_are_rejected_without_values() -> None:
    document = _config()
    document["SECRET_FIELD"] = "PRIVATE_VALUE"
    with pytest.raises(ConfigError) as exc:
        _parse(document)
    assert exc.value.code == "config_unknown_field"
    assert "SECRET_FIELD" not in str(exc.value)
    assert "PRIVATE_VALUE" not in str(exc.value)

    missing = _config()
    del missing["resources"]
    with pytest.raises(ConfigError) as exc:
        _parse(missing)
    assert exc.value.code == "config_unknown_field"
    assert "missing_field" in exc.value.roles


def test_duplicate_resource_ids_rejected() -> None:
    document = _config()
    duplicate = dict(document["resources"][0])
    document["resources"].append(duplicate)
    with pytest.raises(ConfigError) as exc:
        _parse(document)
    assert exc.value.code == "resource_id_duplicate"


def test_missing_resource_reference_rejected() -> None:
    document = _config()
    document["condition"]["run_resource_id"] = "no_such_resource"
    with pytest.raises(ConfigError) as exc:
        _parse(document)
    assert exc.value.code == "resource_reference_missing"


def test_real_plan_requires_verified_license_before_any_run() -> None:
    document = _config()
    document["purpose"] = "real_evaluation_plan"
    with pytest.raises(ConfigError) as exc:
        _parse(document)
    assert exc.value.code == "license_unverified_for_public_plan"


def test_real_plan_cannot_opt_into_declared_only_time() -> None:
    document = _config()
    document["purpose"] = "real_evaluation_plan"
    document["temporal"]["allow_declared_order_only_time"] = True
    with pytest.raises(ConfigError) as exc:
        _parse(document)
    assert exc.value.code == "temporal_declared_only_not_allowed_for_real_input"


def test_local_audit_requires_confirmation() -> None:
    document = _config()
    document["purpose"] = "local_audit_only"
    document["confirmation"]["required"] = False
    with pytest.raises(ConfigError) as exc:
        _parse(document)
    assert exc.value.code == "confirmation_required_for_real_input"


def test_duplicate_json_keys_and_oversize_config_rejected() -> None:
    with pytest.raises(ConfigError) as exc:
        parse_config_bytes(b'{"schema_version":"evaluation_config.v1","schema_version":"evaluation_config.v1"}')
    assert exc.value.code == "json_duplicate_field"
    with pytest.raises(ConfigError) as exc:
        parse_config_bytes(b"x" * (256 * 1024 + 1))
    assert exc.value.code == "artifact_too_large"


def test_config_fingerprint_is_deterministic_and_rebound_bundle_valid() -> None:
    files = batch1_helpers.bundle_with(
        config_mutator=lambda d: d["cohort"].update(min_common_holdout_tasks=2)
    )
    first = parse_config_bytes(files["evaluation_config"])
    second = parse_config_bytes(files["evaluation_config"])
    assert first.semantic_sha256 == second.semantic_sha256
    assert parse_config_bytes(files["evaluation_config"]).config_id == "batch1_synthetic"
    assert CONFIG_SCHEMA_VERSION == "evaluation_config.v1"
