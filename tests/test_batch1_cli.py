"""Subprocess CLI tests for preflight, confirmation, import and point scoring."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from amazon_eval_agent.samples_batch1 import synthetic_files
from tests import batch1_helpers

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples" / "batch1_synthetic"
BIND_NAMES = {
    "dataset_normalized": "synthetic_dataset.jsonl",
    "task_manifest": "task_manifest.json",
    "labels": "local_labels.json",
    "run_candidate": "run_candidate.json",
    "baseline_predictions": "baseline_predictions.jsonl",
}


def _env(cache_root: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in ("API", "KEY", "TOKEN", "SECRET"))
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONPYCACHEPREFIX"] = str(cache_root)
    return env


def _run(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "amazon_eval_agent", *args],
        cwd=REPO_ROOT,
        env=_env(tmp_path / "cache"),
        capture_output=True,
        text=True,
    )


def _bind_args(directory: Path = EXAMPLES) -> list[str]:
    args = []
    for resource_id, name in BIND_NAMES.items():
        args.extend(["--bind", f"{resource_id}={directory / name}"])
    return args


def test_committed_examples_match_synthetic_generator() -> None:
    files = synthetic_files()
    for resource_id, name in {
        "evaluation_config": "evaluation_config.json",
        **BIND_NAMES,
    }.items():
        assert (EXAMPLES / name).read_bytes() == files[resource_id]


def test_preflight_check_is_standalone_and_writes_nothing(tmp_path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "amazon_eval_agent", "preflight", "--check", "--json"],
        cwd=work,
        env=_env(tmp_path / "cache"),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["status"] == "ready_for_scoring"
    assert report["scoring_available"] is False
    assert report["comparison"]["metrics_status"] == "not_computed_in_preflight"
    assert list(work.iterdir()) == []


def test_preflight_files_ready_then_output_refuses_overwrite(tmp_path) -> None:
    output = tmp_path / "report.json"
    proc = _run(
        ["preflight", "--config", str(EXAMPLES / "evaluation_config.json"), *_bind_args(), "--output", str(output)],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert output.is_file()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "ready_for_scoring"
    second = _run(
        ["preflight", "--config", str(EXAMPLES / "evaluation_config.json"), *_bind_args(), "--output", str(output)],
        tmp_path,
    )
    assert second.returncode == 2
    assert "output_file_exists" in second.stderr
    assert output.read_bytes()


def test_preflight_hash_mismatch_is_blocked_and_traceback_free(tmp_path) -> None:
    tampered = tmp_path / "run.json"
    tampered.write_bytes((EXAMPLES / "run_candidate.json").read_bytes() + b"\n")
    proc = _run(
        [
            "preflight",
            "--config",
            str(EXAMPLES / "evaluation_config.json"),
            "--bind",
            f"dataset_normalized={EXAMPLES / 'synthetic_dataset.jsonl'}",
            "--bind",
            f"task_manifest={EXAMPLES / 'task_manifest.json'}",
            "--bind",
            f"labels={EXAMPLES / 'local_labels.json'}",
            "--bind",
            f"run_candidate={tampered}",
            "--bind",
            f"baseline_predictions={EXAMPLES / 'baseline_predictions.jsonl'}",
        ],
        tmp_path,
    )
    assert proc.returncode == 2
    assert "preflight status=blocked" in proc.stdout
    assert "resource_size_mismatch" in proc.stdout or "resource_hash_mismatch" in proc.stdout
    assert "Traceback" not in proc.stderr


def test_confirm_verify_score_and_tamper(tmp_path) -> None:
    confirmation = tmp_path / "confirmation.json"
    confirm = _run(
        [
            "confirm",
            "--config",
            str(EXAMPLES / "evaluation_config.json"),
            *_bind_args(),
            "--confirmed-at",
            "2026-10-26T00:00:00Z",
            "--output",
            str(confirmation),
        ],
        tmp_path,
    )
    assert confirm.returncode == 0, confirm.stderr
    assert "confirmation status=confirmed" in confirm.stdout

    verify = _run(
        [
            "verify-confirmation",
            "--confirmation",
            str(confirmation),
            "--config",
            str(EXAMPLES / "evaluation_config.json"),
            *_bind_args(),
            "--json",
        ],
        tmp_path,
    )
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["confirmation_valid"] is True

    score = tmp_path / "score.json"
    score_proc = _run(
        [
            "score",
            "--config",
            str(EXAMPLES / "evaluation_config.json"),
            *_bind_args(),
            "--confirmation",
            str(confirmation),
            "--output",
            str(score),
        ],
        tmp_path,
    )
    assert score_proc.returncode == 0, score_proc.stderr
    report = json.loads(score.read_text(encoding="utf-8"))
    assert report["status"] == "computed"
    assert report["full_cohort"]["model_success_only"]["eligible"] == 2
    assert report["measurement"]["intervals"] == "not_computed"

    changed_config = json.loads((EXAMPLES / "evaluation_config.json").read_text(encoding="utf-8"))
    changed_config["cohort"]["min_common_holdout_tasks"] = 1
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps(changed_config, sort_keys=True), encoding="utf-8")
    invalid = _run(
        [
            "verify-confirmation",
            "--confirmation",
            str(confirmation),
            "--config",
            str(changed_path),
            *_bind_args(),
            "--json",
        ],
        tmp_path,
    )
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)["confirmation_valid"] is False


def test_score_requires_valid_confirmation_and_refuses_overwrite(tmp_path) -> None:
    confirmation = tmp_path / "confirmation.json"
    _run(
        [
            "confirm",
            "--config",
            str(EXAMPLES / "evaluation_config.json"),
            *_bind_args(),
            "--confirmed-at",
            "2026-10-26T00:00:00Z",
            "--output",
            str(confirmation),
        ],
        tmp_path,
    )
    tampered = json.loads(confirmation.read_text(encoding="utf-8"))
    tampered["plan_fingerprint"] = "f" * 64
    confirmation.write_text(json.dumps(tampered), encoding="utf-8")
    output = tmp_path / "score.json"
    proc = _run(
        [
            "score",
            "--config",
            str(EXAMPLES / "evaluation_config.json"),
            *_bind_args(),
            "--confirmation",
            str(confirmation),
            "--output",
            str(output),
        ],
        tmp_path,
    )
    assert proc.returncode == 2
    assert "confirmation_invalid" in proc.stderr
    assert not output.exists()


def test_import_cli_is_safe_and_refuses_overwrite(tmp_path) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    manifest_out = tmp_path / "out" / "manifest.json"
    labels_out = tmp_path / "out" / "labels.json"
    proc = _run(
        [
            "import-a-prepared",
            "--prepared-dir",
            str(prepared),
            "--manifest-output",
            str(manifest_out),
            "--labels-output",
            str(labels_out),
            "--manifest-id",
            "synthetic_cli_import",
            "--json",
        ],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["task_count"] == 9
    assert "synthetic-user" not in proc.stdout
    assert "fabricated" not in proc.stdout
    assert manifest_out.is_file() and labels_out.is_file()
    second = _run(
        [
            "import-a-prepared",
            "--prepared-dir",
            str(prepared),
            "--manifest-output",
            str(manifest_out),
            "--labels-output",
            str(labels_out),
            "--manifest-id",
            "synthetic_cli_import",
        ],
        tmp_path,
    )
    assert second.returncode == 2
    assert "output_file_exists" in second.stderr


def test_verify_a_prepared_cli_recomputes_contexts(tmp_path) -> None:
    prepared = batch1_helpers.synthetic_prepared_dir(tmp_path)
    manifest_out = tmp_path / "manifest.json"
    labels_out = tmp_path / "labels.json"
    imported = _run(
        [
            "import-a-prepared",
            "--prepared-dir",
            str(prepared),
            "--manifest-output",
            str(manifest_out),
            "--labels-output",
            str(labels_out),
            "--manifest-id",
            "synthetic_cli_verify",
        ],
        tmp_path,
    )
    assert imported.returncode == 0, imported.stderr
    verified = _run(
        [
            "verify-a-prepared",
            "--prepared-dir",
            str(prepared),
            "--manifest",
            str(manifest_out),
            "--labels",
            str(labels_out),
            "--json",
        ],
        tmp_path,
    )
    assert verified.returncode == 0, verified.stderr
    report = json.loads(verified.stdout)
    assert report["status"] == "verified"
    assert report["context"]["task_count"] == 9
    assert report["context"]["manifest_context_verified"] == 9
    assert report["labels_verified"] == 9
    assert report["real_measurement_status"] == "not_verified_or_not_run"


def test_missing_config_is_safe_and_no_traceback(tmp_path) -> None:
    proc = _run(
        ["preflight", "--config", str(tmp_path / "missing.json")],
        tmp_path,
    )
    assert proc.returncode == 2
    assert "resource_unreadable" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_usage_error_is_nonzero(tmp_path) -> None:
    proc = _run([], tmp_path)
    assert proc.returncode == 2
    assert "usage" in proc.stderr.lower()
