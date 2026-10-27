"""Bounded JSON, hashing and file helpers for the evaluation pipeline.

Everything here is deliberately stdlib-only and fail-closed. Public failures
carry stable codes, never field values, paths, review text or identifiers.
Resource gates run before parsing: byte length is checked before UTF-8
encoding/copy, JSON parsing rejects duplicate keys and non-finite numbers,
and an iterative depth walk rejects deeply nested documents without recursion.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

MAX_CONFIG_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_LABELS_BYTES = 1024 * 1024
MAX_BASELINE_BYTES = 8 * 1024 * 1024
MAX_BASELINE_ROWS = 4000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_JSON_DEPTH = 12
MAX_JSONL_LINE_BYTES = 1024 * 1024
MAX_REPORTED_ERRORS = 32

_SHA256_CHARS = set("0123456789abcdef")


class SafetyError(ValueError):
    """A safe, categorized failure that never echoes an input value."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class JsonLoad:
    valid: bool
    code: str | None
    document: Any


@dataclass(frozen=True)
class JsonLinesLoad:
    valid: bool
    code: str | None
    rows: tuple[Any, ...]


@dataclass(frozen=True)
class FileFingerprint:
    sha256: str
    size_bytes: int


class _DuplicateJSONField(ValueError):
    pass


class _NonFiniteJSONConstant(ValueError):
    pass


def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise _DuplicateJSONField("duplicate_json_field")
        seen.add(key)
    return dict(pairs)


def _reject_constant(_name: str) -> Any:
    raise _NonFiniteJSONConstant("json_nonfinite_number")


def _json_loads(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_pairs_hook,
        parse_constant=_reject_constant,
    )


def _depth_ok(value: Any, max_depth: int) -> bool:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            return False
        if isinstance(node, dict):
            for child in node.values():
                stack.append((child, depth + 1))
        elif isinstance(node, list):
            for child in node:
                stack.append((child, depth + 1))
    return True


def _has_nonfinite(value: Any) -> bool:
    stack: list[Any] = [value]
    while stack:
        node = stack.pop()
        if type(node) is float and not math.isfinite(node):
            return True
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False


def _decode(payload: bytes | bytearray | str, max_bytes: int) -> tuple[bytes, str | None]:
    if isinstance(payload, str):
        if len(payload) > max_bytes:
            return b"", "artifact_too_large"
        try:
            data = payload.encode("utf-8")
        except UnicodeEncodeError:
            return b"", "artifact_encoding_error"
    elif isinstance(payload, (bytes, bytearray)):
        if len(payload) > max_bytes:
            return b"", "artifact_too_large"
        data = bytes(payload)
    else:
        return b"", "artifact_type_invalid"
    if len(data) > max_bytes:
        return b"", "artifact_too_large"
    return data, None


def load_json_bytes(
    payload: bytes | bytearray | str,
    *,
    max_bytes: int,
    max_depth: int = MAX_JSON_DEPTH,
) -> JsonLoad:
    """Strict, bounded JSON object/array parser; never raises for bad input."""
    data, error = _decode(payload, max_bytes)
    if error:
        return JsonLoad(False, error, None)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return JsonLoad(False, "artifact_encoding_error", None)
    try:
        document = _json_loads(text)
    except _DuplicateJSONField:
        return JsonLoad(False, "json_duplicate_field", None)
    except _NonFiniteJSONConstant:
        return JsonLoad(False, "json_nonfinite_number", None)
    except RecursionError:
        return JsonLoad(False, "json_too_deep", None)
    except json.JSONDecodeError:
        return JsonLoad(False, "json_syntax_error", None)
    except (ValueError, OverflowError):
        return JsonLoad(False, "json_syntax_error", None)
    except Exception:  # noqa: BLE001 - hostile input must not escape
        return JsonLoad(False, "json_syntax_error", None)
    if not _depth_ok(document, max_depth):
        return JsonLoad(False, "json_too_deep", None)
    if _has_nonfinite(document):
        return JsonLoad(False, "json_nonfinite_number", None)
    return JsonLoad(True, None, document)


def load_jsonl_bytes(
    payload: bytes | bytearray | str,
    *,
    max_bytes: int,
    max_rows: int,
    max_line_bytes: int = MAX_JSONL_LINE_BYTES,
    max_depth: int = MAX_JSON_DEPTH,
) -> JsonLinesLoad:
    """Strict bounded JSONL reader; blank lines are ignored, values never echoed."""
    data, error = _decode(payload, max_bytes)
    if error:
        return JsonLinesLoad(False, error, ())
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return JsonLinesLoad(False, "artifact_encoding_error", ())
    rows: list[Any] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > max_line_bytes:
            return JsonLinesLoad(False, "jsonl_line_too_large", ())
        if len(rows) >= max_rows:
            return JsonLinesLoad(False, "too_many_records", ())
        loaded = load_json_bytes(
            line, max_bytes=max_line_bytes, max_depth=max_depth
        )
        if not loaded.valid:
            return JsonLinesLoad(False, loaded.code or "json_syntax_error", ())
        rows.append(loaded.document)
    if not rows:
        return JsonLinesLoad(False, "empty_records", ())
    return JsonLinesLoad(True, None, tuple(rows))


def deep_freeze(value: Any) -> Any:
    """Return a recursively immutable copy with no caller-owned dict/list refs.

    MappingProxyType protects each mapping; lists become tuples. The underlying
    dict passed to MappingProxyType is created here and never exposed.
    """
    if isinstance(value, MappingABC):
        return MappingProxyType({key: deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(deep_freeze(item) for item in value)
    return value


def deep_thaw(value: Any) -> Any:
    """Return a plain JSON-friendly copy for output, never for trust decisions."""
    if isinstance(value, MappingABC):
        return {key: deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [deep_thaw(item) for item in value]
    if isinstance(value, list):
        return [deep_thaw(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Deterministic UTF-8 JSON used for fingerprints and binding payloads."""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in _SHA256_CHARS for char in value)
    )


def is_safe_id(value: Any, *, min_length: int = 3, max_length: int = 64) -> bool:
    if not isinstance(value, str) or not min_length <= len(value) <= max_length:
        return False
    if not value[0].islower() or not value[0].isascii():
        return False
    return all(
        char.islower() or char.isdigit() or char == "_" for char in value
    )


def is_plain_relative_name(value: Any, *, max_length: int = 200) -> bool:
    """A display/release label: no path separators, control chars or dot-dot."""
    if not isinstance(value, str) or not 1 <= len(value) <= max_length:
        return False
    if any(char in value for char in ("/", "\\", "\x00", "\n", "\r", "\t")):
        return False
    if value in (".", ".."):
        return False
    return all(ord(char) >= 32 for char in value)


def read_path_bytes(path: str | Path, *, max_bytes: int) -> bytes:
    """Read a new file with a stat gate before any read; no path is returned."""
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise SafetyError("resource_unreadable") from exc
    if size > max_bytes:
        raise SafetyError("resource_too_large")
    try:
        with source.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        raise SafetyError("resource_unreadable") from exc
    if len(data) > max_bytes:
        raise SafetyError("resource_too_large")
    return data


def fingerprint_path(path: str | Path, *, max_bytes: int) -> FileFingerprint:
    """Stream a bounded file hash; the path never appears in an error."""
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise SafetyError("resource_unreadable") from exc
    if size > max_bytes:
        raise SafetyError("resource_too_large")
    digest = hashlib.sha256()
    total = 0
    try:
        with source.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise SafetyError("resource_too_large")
                digest.update(chunk)
    except SafetyError:
        raise
    except OSError as exc:
        raise SafetyError("resource_unreadable") from exc
    if total != size:
        raise SafetyError("resource_changed_during_read")
    return FileFingerprint(digest.hexdigest(), total)


def exact_keys(
    obj: Mapping[str, Any], expected: Sequence[str], *, code: str
) -> set[str]:
    """Return safe field-role names for a closed schema check.

    The returned set contains only ``unknown_field`` / ``missing_field`` roles,
    never an actual key.
    """
    roles: set[str] = set()
    expected_set = set(expected)
    for key in obj:
        if key not in expected_set:
            roles.add("unknown_field")
    for key in expected:
        if key not in obj:
            roles.add("missing_field")
    return roles


def bounded_int(value: Any, *, minimum: int = 0, maximum: int = 2**31 - 1) -> bool:
    return type(value) is int and minimum <= value <= maximum


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
