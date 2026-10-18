"""Builders for handwritten synthetic contract fixtures (tests only).

Every value here is fabricated for contract tests: no real reviews, no real
fingerprints, no real user data.
"""

from __future__ import annotations

import json
from typing import Any

HEX64_A = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64


def memory(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "candidates": 0,
        "time_eligible": 0,
        "selected": 0,
        "prompt_included": 0,
        "filtered_injection": 0,
        "write": "not_attempted",
    }
    base.update(overrides)
    return base


def diagnostics(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "llm_attempts": 1,
        "repair": "not_requested",
        "prompt_bytes": 1024,
        "memory": memory(),
    }
    base.update(overrides)
    return base


def success_record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "task_index": 0,
        "execution_order": 0,
        "split": "train",
        "context_sha256": HEX64_D,
        "user_group": "ug_0000000000000001",
        "status": "success",
        "reason": "validated_model_output",
        "prediction_origin": "llm",
        "stars": 4,
        "review": "(synthetic test) fabricated review text for contract tests",
        "evidence_refs": ["ev_0000000000000001"],
        "diagnostics": diagnostics(memory=memory(write="written")),
    }
    base.update(overrides)
    return base


def repaired_success_record(**overrides: Any) -> dict[str, Any]:
    base = success_record(
        reason="validated_model_output_repaired",
        diagnostics=diagnostics(
            llm_attempts=2,
            repair="attempted",
            memory=memory(write="written"),
        ),
    )
    base.update(overrides)
    return base


def degraded_record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "task_index": 0,
        "execution_order": 0,
        "split": "train",
        "context_sha256": HEX64_D,
        "user_group": "ug_0000000000000001",
        "status": "degraded",
        "reason": "statistical_baseline_fallback",
        "prediction_origin": "statistical_baseline",
        "stars": 3.5,
        "review": None,
        "evidence_refs": ["ev_0000000000000002"],
        "diagnostics": diagnostics(repair="reserve_failed", memory=memory(write="skipped")),
    }
    base.update(overrides)
    return base


def refused_record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "task_index": 0,
        "execution_order": 0,
        "split": "train",
        "context_sha256": HEX64_D,
        "user_group": "ug_0000000000000001",
        "status": "refused",
        "reason": "insufficient_evidence",
        "prediction_origin": "none",
        "stars": None,
        "review": None,
        "evidence_refs": [],
        "diagnostics": {
            "llm_attempts": 0,
            "repair": "not_requested",
            "prompt_bytes": 0,
            "memory": memory(),
        },
    }
    base.update(overrides)
    return base


def failed_record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "task_index": 0,
        "execution_order": 0,
        "split": "train",
        "context_sha256": HEX64_D,
        "user_group": "ug_0000000000000001",
        "status": "failed",
        "reason": "llm_transport_error",
        "prediction_origin": "none",
        "stars": None,
        "review": None,
        "evidence_refs": [],
        "diagnostics": {
            "llm_attempts": 1,
            "repair": "not_requested",
            "prompt_bytes": 900,
            "memory": memory(write="skipped"),
        },
    }
    base.update(overrides)
    return base


def artifact(
    records: list[dict[str, Any]] | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = records if records is not None else [success_record()]
    document: dict[str, Any] = {
        "schema_version": "simulation_run.v1",
        "source": {
            "producer": "llm-user-simulation-agent",
            "kind": "fake_on_synthetic",
            "model_source": "offline_injected_fake_callable",
            "dataset_fingerprint_sha256": HEX64_A,
            "manifest_fingerprint_sha256": HEX64_B,
            "config_fingerprint_sha256": HEX64_C,
        },
        "simulated": True,
        "record_count": len(rows),
        "records": rows,
    }
    if source:
        document["source"].update(source)
    return document


def payload_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
