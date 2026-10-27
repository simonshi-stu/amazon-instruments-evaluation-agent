"""Independent verification of declared local resources.

A declaration says what a resource is *expected* to be. This module computes
the actual byte size and SHA-256 of the locally bound bytes, so a report can
distinguish:

- ``verified_local_bytes``: local content matches the declaration;
- ``hash_mismatch`` / ``size_mismatch``: the declaration is false for these
  bytes;
- ``missing_binding``: no local file was supplied, so nothing can pass.

Even a verified file does not prove model authenticity, data legality or that
the source was ever downloaded from the named release.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .config_v1 import ResourceDeclaration
from .safety import MAX_SOURCE_BYTES, sha256_bytes

INTEGRITY_STATES = (
    "verified_local_bytes",
    "missing_binding",
    "hash_mismatch",
    "size_mismatch",
    "too_large",
)
LICENSE_VERIFIED_STATUSES = ("synthetic_fixture_only", "verified_public_use")


@dataclass(frozen=True)
class ResourceVerification:
    resource_id: str
    kind: str
    release: str
    expected_sha256: str
    expected_size_bytes: int
    actual_sha256: str | None
    actual_size_bytes: int | None
    integrity: str
    license_status: str
    redistribution_status: str
    contains_personal_data: bool

    @property
    def verified(self) -> bool:
        return self.integrity == "verified_local_bytes"

    @property
    def license_verified(self) -> bool:
        return self.license_status in LICENSE_VERIFIED_STATUSES

    def safe_summary(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "kind": self.kind,
            "release": self.release,
            "expected_sha256": self.expected_sha256,
            "expected_size_bytes": self.expected_size_bytes,
            "actual_sha256": self.actual_sha256,
            "actual_size_bytes": self.actual_size_bytes,
            "integrity": self.integrity,
            "license_status": self.license_status,
            "license_verified": self.license_verified,
            "redistribution_status": self.redistribution_status,
            "contains_personal_data": self.contains_personal_data,
        }


def verify_resource_bytes(
    declaration: ResourceDeclaration,
    data: bytes,
    *,
    max_bytes: int = MAX_SOURCE_BYTES,
) -> ResourceVerification:
    if len(data) > max_bytes:
        integrity = "too_large"
        actual_sha = None
        actual_size: int | None = len(data)
    elif len(data) != declaration.expected_size_bytes:
        integrity = "size_mismatch"
        actual_sha = sha256_bytes(data)
        actual_size = len(data)
    else:
        actual_sha = sha256_bytes(data)
        actual_size = len(data)
        integrity = (
            "verified_local_bytes"
            if actual_sha == declaration.expected_sha256
            else "hash_mismatch"
        )
    return ResourceVerification(
        resource_id=declaration.resource_id,
        kind=declaration.kind,
        release=declaration.release,
        expected_sha256=declaration.expected_sha256,
        expected_size_bytes=declaration.expected_size_bytes,
        actual_sha256=actual_sha,
        actual_size_bytes=actual_size,
        integrity=integrity,
        license_status=declaration.license_status,
        redistribution_status=declaration.redistribution_status,
        contains_personal_data=declaration.contains_personal_data,
    )


def verify_resources(
    declarations: Sequence[ResourceDeclaration],
    bindings: Mapping[str, bytes],
) -> dict[str, ResourceVerification]:
    """Verify all declared resources; absent bindings fail closed."""
    result: dict[str, ResourceVerification] = {}
    for declaration in declarations:
        data = bindings.get(declaration.resource_id)
        if data is None:
            result[declaration.resource_id] = ResourceVerification(
                resource_id=declaration.resource_id,
                kind=declaration.kind,
                release=declaration.release,
                expected_sha256=declaration.expected_sha256,
                expected_size_bytes=declaration.expected_size_bytes,
                actual_sha256=None,
                actual_size_bytes=None,
                integrity="missing_binding",
                license_status=declaration.license_status,
                redistribution_status=declaration.redistribution_status,
                contains_personal_data=declaration.contains_personal_data,
            )
            continue
        result[declaration.resource_id] = verify_resource_bytes(declaration, data)
    return result
