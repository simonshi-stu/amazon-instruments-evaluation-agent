"""Bounded local reads: stat gate before read and adapter-specific size limits."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from amazon_eval_agent.adapters import a_prepared_v1, prepared_context_v1
from amazon_eval_agent.adapters.a_prepared_v1 import PreparedImportError
from amazon_eval_agent.adapters.prepared_context_v1 import PreparedContextError
from amazon_eval_agent.safety import (
    MAX_CONFIG_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_SOURCE_BYTES,
    SafetyError,
    read_path_bytes,
)


def test_oversize_stat_gate_never_opens_the_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "oversized.jsonl"
    path.write_text("{}", encoding="utf-8")
    original_stat = Path.stat
    calls = {"open": 0}

    def fake_stat(self, *args, **kwargs):
        if self == path:
            return SimpleNamespace(st_size=MAX_MANIFEST_BYTES + 1)
        return original_stat(self, *args, **kwargs)

    def forbidden_open(self, *args, **kwargs):
        calls["open"] += 1
        raise AssertionError("read must not run after the stat gate fails")

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(Path, "open", forbidden_open)
    with pytest.raises(SafetyError) as exc:
        read_path_bytes(path, max_bytes=MAX_MANIFEST_BYTES)
    assert exc.value.code == "resource_too_large"
    assert calls["open"] == 0


def test_jsonl_total_bytes_limit_is_enforced(monkeypatch, tmp_path) -> None:
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    first.write_text('{"a":1}\n', encoding="utf-8")
    second.write_text('{"b":2}\n', encoding="utf-8")
    payload = b'{"a":1}\n'
    monkeypatch.setattr(a_prepared_v1, "MAX_MANIFEST_BYTES", 10)
    monkeypatch.setattr(a_prepared_v1, "_files", lambda directory: [first, second])
    monkeypatch.setattr(
        a_prepared_v1,
        "read_path_bytes",
        lambda path, *, max_bytes: payload,
    )
    with pytest.raises(PreparedImportError) as exc:
        a_prepared_v1._read_jsonl(tmp_path, "a_prepared_tasks_invalid")
    assert exc.value.code == "a_prepared_total_bytes_exceeded"


def test_policy_read_uses_config_bound(monkeypatch, tmp_path) -> None:
    calls: list[int] = []

    def spy(path, *, max_bytes):
        calls.append(max_bytes)
        raise SafetyError("resource_too_large")

    monkeypatch.setattr(a_prepared_v1, "read_path_bytes", spy)
    with pytest.raises(PreparedImportError) as exc:
        a_prepared_v1._read_policy(tmp_path)
    assert exc.value.code == "a_policy_too_large"
    assert calls == [MAX_CONFIG_BYTES]


def test_runtime_read_uses_source_bound(monkeypatch, tmp_path) -> None:
    calls: list[int] = []

    def spy(path, *, max_bytes):
        calls.append(max_bytes)
        raise SafetyError("resource_too_large")

    monkeypatch.setattr(prepared_context_v1, "read_path_bytes", spy)
    with pytest.raises(PreparedContextError) as exc:
        prepared_context_v1._load_runtime_rows(tmp_path)
    assert exc.value.code == "runtime_reviews_too_large"
    assert calls == [MAX_SOURCE_BYTES]
