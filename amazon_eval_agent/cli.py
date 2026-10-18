"""Command line interface: ``python -m amazon_eval_agent validate ...``.

Stage 1 exposes exactly one subcommand:

- ``validate --input PATH``: validate a ``simulation_run.v1`` artifact file;
- ``validate --check``: validate the built-in handwritten synthetic sample in
  memory (standalone proof that needs no producer context, data or keys);
- ``validate --dry-run``: print the validation plan and exit; no artifact is
  read and no file is written.

Exit codes: 0 = artifact contract valid (records may still contain
degraded/refused/failed outcomes, which are reported separately), 2 = invalid
artifact / usage / I/O error, printed as a stable safe code with no traceback.
``--output`` writes the safe diagnostic report and refuses to overwrite an
existing file. No scoring is available or triggered at this stage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from .samples import synthetic_sample_payload
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Standalone simulation-result artifact validation (stage 1).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser(
        "validate",
        help="Validate a SimulationRunV1 artifact or the built-in sample.",
    )
    source = validate.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input",
        metavar="PATH",
        help="Path to a simulation_run.v1 artifact file.",
    )
    source.add_argument(
        "--check",
        action="store_true",
        help="Validate the built-in handwritten synthetic sample in memory "
        "(no file access).",
    )
    source.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the validation plan without reading or writing files.",
    )
    validate.add_argument(
        "--json",
        action="store_true",
        help="Print the safe diagnostic report as JSON.",
    )
    validate.add_argument(
        "--output",
        metavar="PATH",
        help="Write the safe diagnostic report to a new file; refuses to "
        "overwrite.",
    )
    return parser


def _print_human(report: Mapping[str, Any]) -> None:
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
        raise ValidationError("output_file_exists")
    try:
        parent = path.parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            )
    except FileExistsError as exc:
        raise ValidationError("output_file_exists") from exc
    except OSError as exc:
        raise ValidationError("output_unavailable") from exc


def _run(args: argparse.Namespace) -> int:
    if args.command != "validate":
        return 2
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
        _print_human(report)
    if args.output:
        _write_report_output(report, args.output)
    return 0 if report["artifact_validated"] else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except ValidationError as exc:
        print(f"{TOOL_NAME} validate failed: {exc.code}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - safe category only, no traceback
        print(f"{TOOL_NAME} validate failed: internal_error", file=sys.stderr)
        return 2
