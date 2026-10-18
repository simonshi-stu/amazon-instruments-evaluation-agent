"""Handwritten synthetic sample artifact for ``validate --check``.

Everything in this module is fabricated for contract checks: the review texts
are clearly marked ``(synthetic sample)`` and are not real reviews, the
fingerprints are placeholder hex strings, and the status rows only exercise
the four-state matrix. The sample lets the CLI prove standalone operation
without any real artifact, data directory, credential or producer context.
"""

from __future__ import annotations

import json
from typing import Any

_SAMPLE_NOTE = "(synthetic sample) fabricated text for contract validation"


def _memory(
    *,
    candidates: int = 0,
    time_eligible: int = 0,
    selected: int = 0,
    prompt_included: int = 0,
    filtered_injection: int = 0,
    write: str = "not_attempted",
) -> dict[str, Any]:
    return {
        "candidates": candidates,
        "time_eligible": time_eligible,
        "selected": selected,
        "prompt_included": prompt_included,
        "filtered_injection": filtered_injection,
        "write": write,
    }


def synthetic_sample_document() -> dict[str, Any]:
    """A small valid ``simulation_run.v1`` document covering all four states."""
    return {
        "schema_version": "simulation_run.v1",
        "source": {
            "producer": "llm-user-simulation-agent",
            "kind": "fake_on_synthetic",
            "model_source": "offline_injected_fake_callable",
            "dataset_fingerprint_sha256": "a" * 64,
            "manifest_fingerprint_sha256": "b" * 64,
            "config_fingerprint_sha256": "c" * 64,
        },
        "simulated": True,
        "record_count": 4,
        "records": [
            {
                "task_index": 0,
                "execution_order": 0,
                "split": "train",
                "context_sha256": "d" * 64,
                "user_group": "ug_0000000000000001",
                "status": "success",
                "reason": "validated_model_output",
                "prediction_origin": "llm",
                "stars": 4,
                "review": f"{_SAMPLE_NOTE}: grounded sample review text",
                "evidence_refs": ["ev_0000000000000001"],
                "diagnostics": {
                    "llm_attempts": 1,
                    "repair": "not_requested",
                    "prompt_bytes": 2048,
                    "memory": _memory(write="written"),
                },
            },
            {
                "task_index": 1,
                "execution_order": 1,
                "split": "validation",
                "context_sha256": "e" * 64,
                "user_group": "ug_0000000000000001",
                "status": "degraded",
                "reason": "statistical_baseline_fallback",
                "prediction_origin": "statistical_baseline",
                "stars": 3.5,
                "review": None,
                "evidence_refs": ["ev_0000000000000002"],
                "diagnostics": {
                    "llm_attempts": 1,
                    "repair": "reserve_failed",
                    "prompt_bytes": 1900,
                    "memory": _memory(write="skipped"),
                },
            },
            {
                "task_index": 2,
                "execution_order": 2,
                "split": "test",
                "context_sha256": "f" * 64,
                "user_group": "ug_0000000000000002",
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
                    "memory": _memory(),
                },
            },
            {
                "task_index": 3,
                "execution_order": 3,
                "split": "test",
                "context_sha256": "1" * 64,
                "user_group": "ug_0000000000000002",
                "status": "failed",
                "reason": "llm_transport_error",
                "prediction_origin": "none",
                "stars": None,
                "review": None,
                "evidence_refs": [],
                "diagnostics": {
                    "llm_attempts": 1,
                    "repair": "not_requested",
                    "prompt_bytes": 1500,
                    "memory": _memory(write="skipped"),
                },
            },
        ],
    }


def synthetic_sample_payload() -> bytes:
    """Canonical sample bytes; always validates under the contract."""
    return (
        json.dumps(synthetic_sample_document(), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
