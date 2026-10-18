"""CLI tests for ``python -m amazon_eval_agent validate ...``.

Subprocess-level checks: standalone ``--check`` success without producer
context or credentials, dry-run with zero file writes, invalid artifacts with
nonzero exit and safe codes, missing input, report output that refuses to
overwrite, and the contract-vs-success separation on stdout/JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests import helpers

REPO_ROOT = Path(__file__).resolve().parents[1]


def _clean_env(cache_root: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(
            marker in key.upper() for marker in ("API", "KEY", "TOKEN", "SECRET")
        )
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONPYCACHEPREFIX"] = str(cache_root)
    return env


def _run_cli(args: list[str], cwd: Path, cache_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "amazon_eval_agent", *args],
        cwd=cwd,
        env=_clean_env(cache_root),
        capture_output=True,
        text=True,
    )


def test_check_mode_is_standalone_success(tmp_path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    proc = _run_cli(["validate", "--check"], work, tmp_path / "cache")
    assert proc.returncode == 0, proc.stderr
    assert "artifact_validated=true" in proc.stdout
    assert "records=4" in proc.stdout
    assert "success=1" in proc.stdout
    assert "failed=1" in proc.stdout
    assert "real_measurement_status=not_verified_or_not_run" in proc.stdout
    assert "grounded sample review" not in proc.stdout
    assert "Traceback" not in proc.stderr


def test_check_mode_json_report(tmp_path) -> None:
    proc = _run_cli(
        ["validate", "--check", "--json"], tmp_path, tmp_path / "cache"
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["artifact_validated"] is True
    assert report["scoring_available"] is False
    assert report["accuracy_claim"] == "none"
    assert report["status_counts"]["success"] == 1


def test_dry_run_writes_no_files(tmp_path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    proc = _run_cli(
        ["validate", "--dry-run"], work, tmp_path / "cache"
    )
    assert proc.returncode == 0, proc.stderr
    assert "no artifact is read" in proc.stdout
    assert list(work.iterdir()) == []


def test_invalid_artifact_exits_nonzero_with_safe_codes(tmp_path) -> None:
    artifact = tmp_path / "invalid.json"
    artifact.write_text('{"schema_version": "simulation_run.v1"}', encoding="utf-8")
    proc = _run_cli(
        ["validate", "--input", str(artifact)], tmp_path, tmp_path / "cache"
    )
    assert proc.returncode == 2
    assert "artifact_validated=false" in proc.stdout
    assert "missing_field" in proc.stdout
    assert "Traceback" not in proc.stderr


def test_syntax_error_exits_nonzero(tmp_path) -> None:
    artifact = tmp_path / "broken.json"
    artifact.write_text("{not json", encoding="utf-8")
    proc = _run_cli(
        ["validate", "--input", str(artifact)], tmp_path, tmp_path / "cache"
    )
    assert proc.returncode == 2
    assert "json_syntax_error" in proc.stdout
    assert "Traceback" not in proc.stderr


def test_missing_input_is_a_safe_nonzero_error(tmp_path) -> None:
    proc = _run_cli(
        ["validate", "--input", str(tmp_path / "missing.json")],
        tmp_path,
        tmp_path / "cache",
    )
    assert proc.returncode == 2
    assert "input_unreadable" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_output_report_written_once_and_never_overwritten(tmp_path) -> None:
    artifact = tmp_path / "valid.json"
    artifact.write_bytes(helpers.payload_bytes(helpers.artifact()))
    output = tmp_path / "reports" / "report.json"
    first = _run_cli(
        ["validate", "--input", str(artifact), "--output", str(output)],
        tmp_path,
        tmp_path / "cache",
    )
    assert first.returncode == 0, first.stderr
    assert output.is_file()
    content = output.read_bytes()
    report = json.loads(content.decode("utf-8"))
    assert report["artifact_validated"] is True
    second = _run_cli(
        ["validate", "--input", str(artifact), "--output", str(output)],
        tmp_path,
        tmp_path / "cache",
    )
    assert second.returncode == 2
    assert "output_file_exists" in second.stderr
    assert output.read_bytes() == content


def test_valid_artifact_with_failed_record_exits_zero_flagged(tmp_path) -> None:
    document = helpers.artifact(
        [
            helpers.success_record(task_index=0, execution_order=0),
            helpers.failed_record(task_index=1, execution_order=1),
        ]
    )
    artifact = tmp_path / "mixed.json"
    artifact.write_bytes(helpers.payload_bytes(document))
    proc = _run_cli(
        ["validate", "--input", str(artifact)], tmp_path, tmp_path / "cache"
    )
    assert proc.returncode == 0, proc.stderr
    assert "artifact_validated=true" in proc.stdout
    assert "all_predictions_succeeded=false" in proc.stdout
    assert "failed=1" in proc.stdout


def test_usage_error_is_nonzero(tmp_path) -> None:
    proc = _run_cli([], tmp_path, tmp_path / "cache")
    assert proc.returncode == 2
    assert "usage" in proc.stderr.lower()


def test_json_stdout_never_contains_review_text(tmp_path) -> None:
    proc = _run_cli(
        ["validate", "--check", "--json"], tmp_path, tmp_path / "cache"
    )
    assert proc.returncode == 0
    assert "fabricated" not in proc.stdout
    assert "ug_" not in proc.stdout
