"""Explicit, versioned cross-project adapters.

Production code never imports Project A. Each adapter reads a documented
artifact shape, projects only safe fields and records its own adapter version
in reports. A missing original remains missing; no adapter writes a Project A
file or a mirror.
"""

from .simulation_run_v1 import (
    RUN_ADAPTER_VERSION,
    SUPPORTED_RUN_SCHEMA,
    load_run_artifact,
)

__all__ = [
    "RUN_ADAPTER_VERSION",
    "SUPPORTED_RUN_SCHEMA",
    "load_run_artifact",
]
