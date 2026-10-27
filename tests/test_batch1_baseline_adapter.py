"""A P0 prediction-table adapter tests (synthetic rows only)."""

from __future__ import annotations

import json

import pytest

from amazon_eval_agent.adapters.a_p0_predictions_v1 import (
    BaselinePredictionsError,
    parse_baseline_predictions_bytes,
)
from amazon_eval_agent.samples_batch1 import synthetic_files


def _rows() -> list[dict]:
    return [
        json.loads(line)
        for line in synthetic_files()["baseline_predictions"].decode("utf-8").splitlines()
    ]


def _payload(rows: list[dict]) -> bytes:
    return ("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n").encode(
        "utf-8"
    )


def test_valid_table_has_four_modes_and_no_labels() -> None:
    table = parse_baseline_predictions_bytes(
        synthetic_files()["baseline_predictions"]
    )
    assert table.task_count == 12
    assert set(table.rows_by_mode) == {"global", "user", "item", "combined"}
    assert table.select("global")[0] == 3.0
    serialized = json.dumps(table.safe_summary(), sort_keys=True)
    assert "stars" not in serialized
    assert "PRIVATE" not in serialized


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda rows: rows[0].update(prediction=float("nan")), "json_nonfinite_number"),
        (lambda rows: rows[0].update(prediction=True), "baseline_prediction_invalid"),
        (lambda rows: rows[0].update(prediction=6), "baseline_prediction_invalid"),
        (lambda rows: rows[0].update(mode="median"), "baseline_mode_invalid"),
        (lambda rows: rows[0].update(split="holdout"), "baseline_split_invalid"),
        (lambda rows: rows[0].update(user_cold_start=1), "baseline_cold_flag_invalid"),
        (lambda rows: rows[0].update(actual_stars=4), "baseline_unknown_field"),
        (lambda rows: rows.append(dict(rows[0])), "baseline_duplicate_prediction"),
    ],
)
def test_baseline_adapter_rejects_bad_rows(mutate, code) -> None:
    rows = _rows()
    mutate(rows)
    with pytest.raises(BaselinePredictionsError) as exc:
        parse_baseline_predictions_bytes(_payload(rows))
    assert code in exc.value.errors or exc.value.code == code


def test_baseline_mode_task_sets_must_match() -> None:
    rows = _rows()
    rows = [row for row in rows if not (row["mode"] == "global" and row["task_index"] == 0)]
    with pytest.raises(BaselinePredictionsError) as exc:
        parse_baseline_predictions_bytes(_payload(rows))
    assert exc.value.code == "baseline_mode_task_set_mismatch"


def test_baseline_empty_and_oversize_rejected() -> None:
    with pytest.raises(BaselinePredictionsError) as exc:
        parse_baseline_predictions_bytes(b"")
    assert exc.value.code == "artifact_too_large" or exc.value.code == "empty_records"
    with pytest.raises(BaselinePredictionsError) as exc:
        parse_baseline_predictions_bytes(b"x" * (8 * 1024 * 1024 + 1))
    assert exc.value.code == "artifact_too_large"
