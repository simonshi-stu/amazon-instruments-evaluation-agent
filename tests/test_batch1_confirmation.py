"""Manual confirmation is bound to every preflight fingerprint and invalidates on drift."""

from __future__ import annotations

import copy
import json

import pytest

from amazon_eval_agent.config_v1 import parse_config_bytes
from amazon_eval_agent.confirmation_v1 import (
    ConfirmationError,
    create_confirmation,
    parse_confirmation_bytes,
    serialize_confirmation,
    verify_confirmation,
)
from amazon_eval_agent.preflight_v1 import run_preflight
from amazon_eval_agent.samples_batch1 import synthetic_files
from tests import batch1_helpers


def _ready(files=None):
    files = files or synthetic_files()
    config = parse_config_bytes(files["evaluation_config"])
    return run_preflight(config, batch1_helpers.bindings_from(files))


def _confirmation(result=None):
    return create_confirmation(
        result or _ready(), confirmed_at="2026-10-26T00:00:00Z"
    )


def test_valid_confirmation_roundtrip_and_no_private_content() -> None:
    result = _ready()
    confirmation = _confirmation(result)
    parsed = parse_confirmation_bytes(serialize_confirmation(confirmation))
    checked = verify_confirmation(parsed, result)
    assert checked["confirmation_valid"] is True
    assert checked["status"] == "valid"
    assert checked["plan_fingerprint_match"] is True
    serialized = json.dumps(confirmation, sort_keys=True)
    assert "ug_" not in serialized
    assert "E:\\" not in serialized
    assert "fabricated" not in serialized
    assert confirmation["scope"] == "preflight_only_no_metrics"
    assert confirmation["accuracy_claim"] == "none"


def test_confirmation_refuses_blocked_plan() -> None:
    files = synthetic_files()
    files["run_candidate"] = files["run_candidate"].replace(
        b'"stars": 4', b'"stars": 5', 1
    )
    config = parse_config_bytes(files["evaluation_config"])
    blocked = run_preflight(config, batch1_helpers.bindings_from(files))
    with pytest.raises(ConfirmationError) as exc:
        create_confirmation(blocked, confirmed_at="2026-10-26T00:00:00Z")
    assert exc.value.code == "preflight_not_ready"


@pytest.mark.parametrize(
    "value,code",
    [
        ("2026-10-26T00:00:00", "confirmation_time_requires_utc"),
        ("2026-10-26T00:00:00+08:00", "confirmation_time_requires_utc"),
        ("not-a-time", "confirmation_time_invalid"),
    ],
)
def test_confirmation_time_must_be_utc(value, code) -> None:
    with pytest.raises(ConfirmationError) as exc:
        create_confirmation(_ready(), confirmed_at=value)
    assert exc.value.code == code


def test_tampered_confirmation_fingerprint_is_invalid() -> None:
    result = _ready()
    confirmation = _confirmation(result)
    tampered = copy.deepcopy(confirmation)
    tampered["plan_fingerprint"] = "f" * 64
    parsed = parse_confirmation_bytes(json.dumps(tampered).encode("utf-8"))
    checked = verify_confirmation(parsed, result)
    assert checked["confirmation_valid"] is False
    assert "plan_fingerprint_mismatch" in checked["mismatches"]
    assert "confirmation_id_mismatch" in checked["mismatches"]


def test_tampered_run_binding_is_invalid() -> None:
    result = _ready()
    confirmation = _confirmation(result)
    tampered = copy.deepcopy(confirmation)
    tampered["bindings"]["run_artifact_sha256"] = "f" * 64
    parsed = parse_confirmation_bytes(json.dumps(tampered).encode("utf-8"))
    checked = verify_confirmation(parsed, result)
    assert checked["confirmation_valid"] is False
    assert "run_artifact_binding_mismatch" in checked["mismatches"]


def test_changed_config_invalidates_confirmation() -> None:
    result = _ready()
    confirmation = _confirmation(result)
    files = batch1_helpers.bundle_with(
        config_mutator=lambda d: d["cohort"].update(min_common_holdout_tasks=1)
    )
    changed = run_preflight(
        parse_config_bytes(files["evaluation_config"]),
        batch1_helpers.bindings_from(files),
    )
    assert changed.ready is True
    assert changed.plan_fingerprint != result.plan_fingerprint
    checked = verify_confirmation(
        parse_confirmation_bytes(serialize_confirmation(confirmation)), changed
    )
    assert checked["confirmation_valid"] is False
    assert "plan_fingerprint_mismatch" in checked["mismatches"]
    assert "config_binding_mismatch" in checked["mismatches"]


def test_changed_manifest_invalidates_confirmation_and_blocks() -> None:
    result = _ready()
    confirmation = _confirmation(result)

    def mutate(payload: bytes) -> bytes:
        document = json.loads(payload.decode("utf-8"))
        document["manifest_id"] = "batch1_synthetic_changed"
        return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")

    files = batch1_helpers.bundle_with(file_mutators={"task_manifest": mutate})
    changed = run_preflight(
        parse_config_bytes(files["evaluation_config"]),
        batch1_helpers.bindings_from(files),
    )
    checked = verify_confirmation(
        parse_confirmation_bytes(serialize_confirmation(confirmation)), changed
    )
    assert checked["confirmation_valid"] is False
    assert "preflight_not_ready" in checked["mismatches"]


def test_confirmation_parse_rejects_unknown_or_missing_fields() -> None:
    confirmation = _confirmation()
    extra = copy.deepcopy(confirmation)
    extra["private"] = "SECRET"
    with pytest.raises(ConfirmationError) as exc:
        parse_confirmation_bytes(json.dumps(extra).encode("utf-8"))
    assert "confirmation_unknown_field" in exc.value.errors
    assert "SECRET" not in str(exc.value)
    missing = copy.deepcopy(confirmation)
    del missing["bindings"]
    with pytest.raises(ConfirmationError) as exc:
        parse_confirmation_bytes(json.dumps(missing).encode("utf-8"))
    assert "confirmation_missing_field" in exc.value.errors
