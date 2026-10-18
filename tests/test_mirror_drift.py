"""Mirror-drift tests: canonical reader vs. user-simulation mirror.

``schema_hash()`` only proves the *shape spec* is identical, so these tests
compare the shared core implementation directly:

- normalized AST comparison: module docstring, the mirror-only ``MIRROR_NOTE``
  constant, the mirror-only ``build_document`` helper and typing-import
  supersets are excluded; everything else must match node for node;
- hostile behaviour matrix: both readers are fed the same counterexample
  payloads (the reviewed defects included) and must return identical
  ``(valid, errors)`` outcomes.

All cross-repo access is stdlib/subprocess only; production packages never
import each other.
"""

from __future__ import annotations

import ast
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from amazon_eval_agent.contracts import simulation_run_v1 as contract
from tests import helpers

USER_SIM_REPO = Path(__file__).resolve().parents[2] / "llm-user-simulation-agent-dual"
MIRROR_FILE = USER_SIM_REPO / "user_simulator_project" / "simulation_run_v1.py"
CANONICAL_FILE = Path(contract.__file__).resolve()

_MIRROR_PROBE = """
import base64, json, sys
from user_simulator_project import simulation_run_v1 as contract
with open(sys.argv[1], encoding="utf-8") as handle:
    payloads = json.load(handle)
out = []
for encoded in payloads:
    result = contract.validate_payload(base64.b64decode(encoded))
    out.append({"valid": result.valid, "errors": list(result.errors)})
print(json.dumps(out))
"""


def _normalized_core(path: Path) -> tuple[str, set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = list(tree.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # module docstring differs by design
    kept = []
    typing_names: set[str] = set()
    for node in body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MIRROR_NOTE"
            for target in node.targets
        ):
            continue  # mirror-only provenance constant
        if isinstance(node, ast.FunctionDef) and node.name == "build_document":
            continue  # mirror-only export helper
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            typing_names |= {alias.name for alias in node.names}
            continue  # mirror may import a superset of typing names
        kept.append(node)
    return ast.dump(ast.Module(body=kept, type_ignores=[])), typing_names


def _counterexamples() -> list[tuple[str, bytes]]:
    cases: list[tuple[str, bytes]] = []

    def add(name: str, document: dict) -> None:
        cases.append((name, helpers.payload_bytes(document)))

    add("valid_success", helpers.artifact())
    document = helpers.artifact([helpers.degraded_record()])
    document["records"][0]["stars"] = 10**400
    add("huge_degraded_stars", document)
    document = helpers.artifact()
    document["records"][0]["stars"] = 10**400
    add("huge_success_stars", document)
    cases.append(("deep_1500_arrays", b"[" * 1500 + b"]" * 1500))
    document = helpers.artifact()
    document["records"][0]["review"] = "   "
    add("blank_review", document)
    document = helpers.artifact()
    document["records"][0]["diagnostics"]["llm_attempts"] = 0
    add("success_zero_attempts", document)
    document = helpers.artifact()
    document["records"][0]["diagnostics"]["memory"]["selected"] = 3
    add("memory_funnel_violation", document)
    document = helpers.artifact()
    document["records"][0]["evidence_refs"] = [
        "ev_0000000000000001",
        "ev_0000000000000001",
    ]
    add("duplicate_refs", document)
    add("valid_repaired_success", helpers.artifact([helpers.repaired_success_record()]))
    document = helpers.artifact([helpers.degraded_record()])
    document["records"][0]["diagnostics"].update(
        {"llm_attempts": 1, "repair": "not_requested"}
    )
    add("degraded_blank_repair", document)
    document = helpers.artifact([helpers.refused_record()])
    document["records"][0]["diagnostics"]["llm_attempts"] = 1
    add("refused_with_attempt", document)
    document = helpers.artifact()
    document["records"][0]["review"] = "ok\ud800ok"
    cases.append(("escaped_surrogate_review", json.dumps(document).encode("ascii")))
    document = helpers.artifact([helpers.failed_record()])
    document["records"][0]["diagnostics"]["llm_attempts"] = 0
    add("failed_transport_zero_attempts", document)
    add(
        "valid_internal_failed_zero_attempts",
        helpers.artifact(
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
        ),
    )
    add("empty_records", helpers.artifact([]))
    too_many = [
        helpers.success_record(task_index=index, execution_order=index)
        for index in range(contract.MAX_RECORDS + 1)
    ]
    add("too_many_records", helpers.artifact(too_many))
    cases.append(("duplicate_json_key", b'{"a":1,"a":2}'))
    cases.append(("nonfinite", b'{"value": NaN}'))
    cases.append(("oversize", b"x" * (contract.MAX_ARTIFACT_BYTES + 1)))
    return cases


def test_shared_core_normalized_ast_matches_mirror() -> None:
    if not MIRROR_FILE.is_file():
        pytest.skip("sibling user-simulation mirror not present")
    canonical_dump, canonical_typing = _normalized_core(CANONICAL_FILE)
    mirror_dump, mirror_typing = _normalized_core(MIRROR_FILE)
    assert canonical_typing <= mirror_typing, (
        "the mirror may only add typing imports, never drop canonical ones"
    )
    assert canonical_dump == mirror_dump, (
        "shared core implementation drifted between canonical and mirror"
    )


def test_hostile_behaviour_matrix_matches_mirror(tmp_path) -> None:
    if not MIRROR_FILE.is_file():
        pytest.skip("sibling user-simulation mirror not present")
    cases = _counterexamples()
    local = []
    for _, payload in cases:
        result = contract.validate_payload(payload)
        local.append({"valid": result.valid, "errors": list(result.errors)})

    by_name = {name: row for (name, _), row in zip(cases, local)}
    assert by_name["huge_degraded_stars"]["errors"] == ["invalid_stars"]
    assert by_name["huge_success_stars"]["errors"] == ["invalid_stars"]
    assert by_name["deep_1500_arrays"]["errors"] == ["json_too_deep"]
    assert by_name["blank_review"]["errors"] == ["invalid_review"]
    assert by_name["success_zero_attempts"]["errors"] == [
        "diagnostic_attempts_mismatch"
    ]
    assert by_name["memory_funnel_violation"]["errors"] == [
        "memory_funnel_mismatch"
    ]
    assert by_name["duplicate_refs"]["errors"] == ["duplicate_evidence_ref"]
    assert by_name["escaped_surrogate_review"]["errors"] == ["invalid_review"]
    assert by_name["failed_transport_zero_attempts"]["errors"] == [
        "diagnostic_attempts_mismatch"
    ]
    assert by_name["valid_repaired_success"]["valid"] is True
    assert by_name["valid_internal_failed_zero_attempts"]["valid"] is True
    assert by_name["oversize"]["errors"] == ["artifact_too_large"]

    payloads_file = tmp_path / "payloads.json"
    payloads_file.write_text(
        json.dumps(
            [base64.b64encode(payload).decode("ascii") for _, payload in cases]
        ),
        encoding="utf-8",
    )
    probe = tmp_path / "mirror_probe.py"
    probe.write_text(_MIRROR_PROBE, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(USER_SIM_REPO), env.get("PYTHONPATH", "")) if part
    )
    proc = subprocess.run(
        [sys.executable, str(probe), str(payloads_file)],
        cwd=USER_SIM_REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    mirror = json.loads(proc.stdout)
    assert mirror == local, (
        "canonical and mirror readers disagree on the counterexample matrix"
    )
