"""Command line interface for contract validation and batch-1 governance.

Subcommands:

- ``validate``: stage-1 artifact validation (unchanged semantics);
- ``preflight``: typed config + source hashes + manifest/time/cohort + run join;
- ``confirm``: create a local confirmation bound to preflight fingerprints;
- ``verify-confirmation``: recompute the plan and detect any drift;
- ``import-a-prepared``: explicit A prepared-directory adapter -> B manifest/labels;
- ``verify-a-prepared``: independent A history/context/run-context verifier;
- ``score``: point estimates on an explicitly confirmed plan.

No command calls a model or downloads data. `score` computes point estimates
only; it is not an LLM explanation or approved-report export. Exit code 0
means the requested operation succeeded; a blocked preflight, invalid
confirmation or malformed input exits 2 with a stable safe code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from .adapters.a_prepared_v1 import (
    PreparedImportError,
    import_a_prepared,
    verify_a_prepared,
)
from .adapters.a_p0_predictions_v1 import BaselinePredictionsError
from .adapters.a_temporal_manifest_v2 import AManifestError
from .config_v1 import ConfigError, parse_config_bytes
from .confirmation_v1 import (
    ConfirmationError,
    create_confirmation,
    parse_confirmation_bytes,
    serialize_confirmation,
    verify_confirmation,
)
from .labels_v1 import LabelError
from .preflight_v1 import PreflightError, run_preflight
from .scoring_v1 import ScoreError, score_confirmed_plan
from .safety import (
    MAX_CONFIG_BYTES,
    MAX_SOURCE_BYTES,
    SafetyError,
    deep_thaw,
    is_safe_id,
    read_path_bytes,
)
from .samples import synthetic_sample_payload
from .samples_batch1 import synthetic_bindings, synthetic_files
from .task_manifest_v1 import TaskManifestError
from .validate import (
    STAGE_NAME,
    TOOL_NAME,
    ValidationError,
    validate_bytes,
    validate_file,
)

_DRY_RUN_LINES = (
    f"{TOOL_NAME} validate dry run: no artifact is read and no file is written",
    "plan: byte-size gate (<=1048576 bytes) -> strict JSON parse (duplicate "
    "keys / NaN / Infinity rejected) -> depth gate (<=10) -> closed-schema and "
    "status-matrix validation (<=500 records)",
    "outputs: safe diagnostic counts/enums/digests only; no review text, no paths",
    "real_measurement_status=not_verified_or_not_run; accuracy_claim=none; "
    "scoring_available=false",
    "stage 1 note: full evaluation orchestration, metrics, LLM explanation and "
    "human-confirmed export are later stages",
)


class CliError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Standalone evaluation contract, batch-1 governance and point-score "
            "tooling. No model calls and no downloads."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="Validate a SimulationRunV1 artifact or the built-in sample.",
    )
    source = validate.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input", metavar="PATH", help="Path to a simulation_run.v1 artifact file."
    )
    source.add_argument(
        "--check",
        action="store_true",
        help="Validate the built-in handwritten synthetic sample in memory.",
    )
    source.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the validation plan without reading or writing files.",
    )
    validate.add_argument(
        "--json", action="store_true", help="Print the safe report as JSON."
    )
    validate.add_argument(
        "--output",
        metavar="PATH",
        help="Write the safe diagnostic report to a new file; refuses overwrite.",
    )

    preflight = subparsers.add_parser(
        "preflight",
        help="Check config, sources, manifest, time/cohort protocol and run join.",
    )
    preflight_source = preflight.add_mutually_exclusive_group(required=True)
    preflight_source.add_argument(
        "--config", metavar="PATH", help="Path to an evaluation_config.v1 file."
    )
    preflight_source.add_argument(
        "--check",
        action="store_true",
        help="Use the built-in synthetic bundle; reads no external file.",
    )
    preflight.add_argument(
        "--bind",
        action="append",
        default=[],
        metavar="RESOURCE_ID=PATH",
        help="Bind a logical resource id to local bytes (repeatable).",
    )
    preflight.add_argument("--json", action="store_true", help="Print JSON.")
    preflight.add_argument(
        "--output",
        metavar="PATH",
        help="Write the preflight report to a new file; refuses overwrite.",
    )

    confirm = subparsers.add_parser(
        "confirm",
        help="Create a local confirmation bound to a ready preflight plan.",
    )
    confirm.add_argument("--config", metavar="PATH", required=True)
    confirm.add_argument("--bind", action="append", default=[], metavar="RESOURCE_ID=PATH")
    confirm.add_argument(
        "--confirmed-at",
        required=True,
        metavar="UTC_ISO8601",
        help="Explicit UTC timestamp, for example 2026-10-26T00:00:00Z.",
    )
    confirm.add_argument("--output", metavar="PATH", required=True)
    confirm.add_argument("--json", action="store_true")

    verify = subparsers.add_parser(
        "verify-confirmation",
        help="Recompute the plan and report any fingerprint drift.",
    )
    verify.add_argument("--confirmation", metavar="PATH", required=True)
    verify.add_argument("--config", metavar="PATH", required=True)
    verify.add_argument("--bind", action="append", default=[], metavar="RESOURCE_ID=PATH")
    verify.add_argument("--json", action="store_true")

    importer = subparsers.add_parser(
        "import-a-prepared",
        help="Read a verified A prepared directory and write B manifest + labels.",
    )
    importer.add_argument("--prepared-dir", metavar="PATH", required=True)
    importer.add_argument("--manifest-output", metavar="PATH", required=True)
    importer.add_argument("--labels-output", metavar="PATH", required=True)
    importer.add_argument("--manifest-id", required=True)
    importer.add_argument("--json", action="store_true")

    verifier = subparsers.add_parser(
        "verify-a-prepared",
        help="Recompute A prepared contexts and verify a B manifest/run.",
    )
    verifier.add_argument("--prepared-dir", metavar="PATH", required=True)
    verifier.add_argument("--manifest", metavar="PATH", required=True)
    verifier.add_argument("--labels", metavar="PATH")
    verifier.add_argument("--run", metavar="PATH")
    verifier.add_argument("--json", action="store_true")
    verifier.add_argument("--output", metavar="PATH")

    score = subparsers.add_parser(
        "score",
        help="Compute point metrics for a confirmed preflight plan.",
    )
    score.add_argument("--config", metavar="PATH", required=True)
    score.add_argument("--bind", action="append", default=[], metavar="RESOURCE_ID=PATH")
    score.add_argument("--confirmation", metavar="PATH", required=True)
    score.add_argument("--json", action="store_true")
    score.add_argument(
        "--output",
        metavar="PATH",
        help="Write the point-score report to a new file; refuses overwrite.",
    )
    return parser


def _print_validate_human(report: Mapping[str, Any]) -> None:
    if report["artifact_validated"]:
        counts = report["status_counts"]
        print(
            "artifact_validated=true "
            f"stage={STAGE_NAME} records={report['record_count']} "
            f"success={counts['success']} degraded={counts['degraded']} "
            f"refused={counts['refused']} failed={counts['failed']} "
            f"source_kind={report['declared_source_kind']} "
            f"model_source={report['declared_model_source']} simulated=true "
            f"all_predictions_succeeded={str(report['all_predictions_succeeded']).lower()} "
            "real_measurement_status=not_verified_or_not_run "
            "fingerprints_verified=false accuracy_claim=none"
        )
    else:
        print(
            "artifact_validated=false errors="
            + ",".join(report["validation_errors"])
        )


def _write_report_output(report: Mapping[str, Any], target: str) -> None:
    path = Path(target)
    if path.exists():
        raise CliError("output_file_exists")
    try:
        parent = path.parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(
                    deep_thaw(report),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
    except FileExistsError as exc:
        raise CliError("output_file_exists") from exc
    except OSError as exc:
        raise CliError("output_unavailable") from exc


def _run_validate(args: argparse.Namespace) -> int:
    if args.dry_run:
        for line in _DRY_RUN_LINES:
            print(line)
        return 0
    if args.check:
        report = validate_bytes(synthetic_sample_payload())
    else:
        try:
            report = validate_file(args.input)
        except ValidationError as exc:
            print(f"{TOOL_NAME} validate failed: {exc.code}", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        _print_validate_human(report)
    if args.output:
        _write_report_output(report, args.output)
    return 0 if report["artifact_validated"] else 2


def _parse_bindings(values: list[str]) -> dict[str, bytes]:
    bindings: dict[str, bytes] = {}
    for item in values:
        if not isinstance(item, str) or "=" not in item:
            raise CliError("resource_binding_invalid")
        resource_id, path = item.split("=", 1)
        if not is_safe_id(resource_id) or not path:
            raise CliError("resource_binding_invalid")
        if resource_id in bindings:
            raise CliError("resource_binding_duplicate")
        try:
            bindings[resource_id] = read_path_bytes(path, max_bytes=MAX_SOURCE_BYTES)
        except SafetyError as exc:
            raise CliError(exc.code) from exc
    return bindings


def _load_config_and_bindings(
    args: argparse.Namespace,
) -> tuple[Any, dict[str, bytes]]:
    if getattr(args, "check", False):
        files = synthetic_files()
        config = parse_config_bytes(files["evaluation_config"])
        return config, {
            key: value for key, value in synthetic_bindings().items()
        }
    try:
        payload = read_path_bytes(args.config, max_bytes=MAX_CONFIG_BYTES)
    except SafetyError as exc:
        raise CliError(exc.code) from exc
    config = parse_config_bytes(payload)
    bindings = _parse_bindings(args.bind)
    return config, bindings


def _print_preflight_human(report: Mapping[str, Any]) -> None:
    comparison = report.get("comparison") or {}
    if report["status"] == "ready_for_scoring":
        print(
            "preflight status=ready_for_scoring "
            f"config={report['evaluation_config']['config_id']} "
            f"tasks={report['task_manifest'].get('task_count')} "
            f"holdout={comparison.get('holdout_tasks')} "
            f"common_model={comparison.get('common_model_holdout_tasks')} "
            f"common_system={comparison.get('common_system_holdout_tasks')} "
            f"temporal={report['temporal']['status']} "
            f"publication={report['publication_status']} "
            f"plan_fingerprint={report['confirmation']['plan_fingerprint'][:16]} "
            "accuracy_claim=none real_measurement_status=not_verified_or_not_run"
        )
    else:
        print(
            "preflight status=blocked blockers="
            + ",".join(report["blockers"])
            + " accuracy_claim=none real_measurement_status=not_verified_or_not_run"
        )


def _run_preflight(args: argparse.Namespace) -> int:
    config, bindings = _load_config_and_bindings(args)
    result = run_preflight(config, bindings)
    report = result.report
    if args.json:
        print(
            json.dumps(deep_thaw(report), ensure_ascii=False, sort_keys=True)
        )
    else:
        _print_preflight_human(report)
    if args.output:
        _write_report_output(report, args.output)
    return 0 if result.ready else 2


def _run_confirm(args: argparse.Namespace) -> int:
    config, bindings = _load_config_and_bindings(args)
    result = run_preflight(config, bindings)
    confirmation = create_confirmation(result, confirmed_at=args.confirmed_at)
    payload = serialize_confirmation(confirmation)
    target = Path(args.output)
    if target.exists():
        raise CliError("output_file_exists")
    try:
        parent = target.parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise CliError("output_file_exists") from exc
    except OSError as exc:
        raise CliError("output_unavailable") from exc
    if args.json:
        print(json.dumps(confirmation, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "confirmation status=confirmed scope=preflight_only_no_metrics "
            f"plan_fingerprint={confirmation['plan_fingerprint'][:16]} "
            "accuracy_claim=none real_measurement_status=not_verified_or_not_run"
        )
    return 0


def _run_verify_confirmation(args: argparse.Namespace) -> int:
    try:
        confirmation_payload = read_path_bytes(
            args.confirmation, max_bytes=512 * 1024
        )
    except SafetyError as exc:
        raise CliError(exc.code) from exc
    confirmation = parse_confirmation_bytes(confirmation_payload)
    config, bindings = _load_config_and_bindings(args)
    result = run_preflight(config, bindings)
    verification = verify_confirmation(confirmation, result)
    if args.json:
        print(json.dumps(verification, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"confirmation status={verification['status']} "
            f"plan_fingerprint_match={str(verification['plan_fingerprint_match']).lower()} "
            "scope=preflight_only_no_metrics accuracy_claim=none "
            "real_measurement_status=not_verified_or_not_run"
        )
        if verification["mismatches"]:
            print("mismatches=" + ",".join(verification["mismatches"]))
    return 0 if verification["confirmation_valid"] else 2


def _run_score(args: argparse.Namespace) -> int:
    config, bindings = _load_config_and_bindings(args)
    try:
        confirmation_payload = read_path_bytes(
            args.confirmation, max_bytes=512 * 1024
        )
    except SafetyError as exc:
        raise CliError(exc.code) from exc
    confirmation = parse_confirmation_bytes(confirmation_payload)
    result = run_preflight(config, bindings)
    verification = verify_confirmation(confirmation, result)
    report = score_confirmed_plan(result, confirmation, verification)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        full = report["full_cohort"]
        model = full["model_success_only"]
        system = full["system_including_degraded"]
        comparison = report.get("comparison") or {}
        print(
            f"score status={report['status']} "
            f"model_n={model['eligible']} model_coverage={model['coverage']} "
            f"system_n={system['eligible']} system_coverage={system['coverage']} "
            f"baseline={comparison.get('baseline_mode')} "
            f"common_model={comparison.get('common_model_tasks')} "
            f"common_system={comparison.get('common_system_tasks')} "
            "intervals=not_computed significance=not_computed "
            "accuracy_claim=declared_artifact_point_estimates_only "
            "real_measurement_status=not_verified_or_not_run"
        )
        if report["blockers"]:
            print("blockers=" + ",".join(report["blockers"]))
    if args.output:
        _write_report_output(report, args.output)
    return 0 if report["status"] == "computed" else 2


def _run_verify_prepared(args: argparse.Namespace) -> int:
    report = verify_a_prepared(
        args.prepared_dir, args.manifest, args.run, args.labels
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        context = report["context"]
        print(
            "prepared_verification status=verified "
            f"tasks={context['task_count']} "
            f"history={context['history_selection_verified']} "
            f"references={context['reference_selection_verified']} "
            f"manifest_contexts={context['manifest_context_verified']} "
            f"target_copies_absent={context['target_copy_absence_verified']} "
            f"run_context_checked={context['runtime_context_checked_tasks']} "
            f"run_context_verified={context['runtime_context_verified']} "
            f"labels_verified={report['labels_verified']} "
            "accuracy_claim=none real_measurement_status=not_verified_or_not_run"
        )
    if args.output:
        _write_report_output(report, args.output)
    return 0


def _run_import(args: argparse.Namespace) -> int:
    result = import_a_prepared(
        args.prepared_dir,
        args.manifest_id,
        args.manifest_output,
        args.labels_output,
    )
    summary = result.safe_summary()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        context = summary["context_verification"]
        print(
            "import status=ok "
            f"tasks={summary['task_count']} labels={summary['label_count']} "
            f"splits={json.dumps(summary['split_counts'], sort_keys=True)} "
            f"cut={summary['history_cut_date']} "
            f"contexts={context['manifest_context_verified']} "
            f"history={context['history_selection_verified']} "
            f"references={context['reference_selection_verified']} "
            f"manifest_sha256={summary['task_manifest_sha256']} "
            f"labels_sha256={summary['labels_sha256']} "
            "accuracy_claim=none real_measurement_status=not_verified_or_not_run"
        )
    return 0


def _run(args: argparse.Namespace) -> int:
    if args.command == "validate":
        return _run_validate(args)
    if args.command == "preflight":
        return _run_preflight(args)
    if args.command == "confirm":
        return _run_confirm(args)
    if args.command == "verify-confirmation":
        return _run_verify_confirmation(args)
    if args.command == "import-a-prepared":
        return _run_import(args)
    if args.command == "verify-a-prepared":
        return _run_verify_prepared(args)
    if args.command == "score":
        return _run_score(args)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (
        CliError,
        ConfigError,
        TaskManifestError,
        LabelError,
        PreflightError,
        ConfirmationError,
        PreparedImportError,
        AManifestError,
        BaselinePredictionsError,
        ScoreError,
        SafetyError,
    ) as exc:
        code = getattr(exc, "code", "operation_failed")
        print(f"{TOOL_NAME} {args.command} failed: {code}", file=sys.stderr)
        return 2
    except ValidationError as exc:
        print(f"{TOOL_NAME} {args.command} failed: {exc.code}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - safe category only, no traceback
        print(f"{TOOL_NAME} {args.command} failed: internal_error", file=sys.stderr)
        return 2
