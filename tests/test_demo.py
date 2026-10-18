"""Hand-calculated synthetic metrics and public build boundaries."""
import json
import math
from pathlib import Path

import pytest

from amazon_eval_agent.demo import build_report, build_site, compare, score

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_hand_calculated_scopes_and_failures():
    rows = [
        dict(task="a", user="u", target=1, prediction=2, status="success"),
        dict(task="b", user="u", target=5, prediction=3, status="degraded"),
        dict(task="c", user="v", target=4, prediction=None, status="failed"),
    ]
    assert score(rows)["mae"] == 1
    full = score(rows, ("success", "degraded"))
    assert full["mae"] == 1.5
    assert full["rmse"] == math.sqrt(2.5)
    assert full["bias"] == -.5
    assert full["within_one"] == .5
    assert full["eligible"] == 2 and full["requested"] == 3
    assert score([])["mae"] is None
    with pytest.raises(ValueError):
        score(rows + [rows[0]])
    with pytest.raises(ValueError):
        score([dict(rows[0], prediction=float("nan"))])


def test_alignment_common_denominators_and_unknown():
    a = [dict(task="a", user="u", target=2, prediction=3, status="success")]
    with pytest.raises(ValueError):
        compare(a, [dict(a[0], target=3)])
    with pytest.raises(ValueError):
        compare(a, [dict(a[0], user="other")])
    assert compare(a, [dict(a[0], status="failed", prediction=None)])["mae_b_minus_a"] is None
    r = build_report("survivor")
    assert r["comparison"]["common_n"] == 3
    assert r["schemes"][1]["success_only"]["coverage"] == .25
    assert r["comparison"]["interval"] is None
    assert build_report("insufficient")["comparison"]["common_n"] == 0


def test_demo_copy_has_overview_visualization_analysis_and_summary():
    html = (REPO_ROOT / "demo" / "index.html").read_text(encoding="utf-8")
    for marker in ("概要", "Visualization", "可视化", "分析", "总结", "后续改进", "当前方案的准确性"):
        assert marker in html

    reports = [build_report(name) for name in ("balanced", "survivor", "insufficient")]
    assert len({report["summary"]["conclusion"] for report in reports}) == 3
    assert "没有任何任务同时成功" in reports[2]["analysis"][1]["text"]
    for report in reports:
        assert report["version"] == "synthetic-evaluation-demo.v2"
        assert report["overview"]["objective"]
        assert len(report["analysis"]) == 3
        assert report["summary"]["accuracy"]["status"] == "真实模型准确率：未测量"
        assert "没有调用 LLM" in report["summary"]["accuracy"]["detail"]
        assert len(report["summary"]["improvements"]) >= 5
        assert all(item["title"] and item["text"] for item in report["analysis"])


def test_repeatable_public_whitelist_build(tmp_path):
    assert build_report("balanced") == build_report("balanced")
    build_site(tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {"index.html", "app.js", "style.css", "reports.json"}
    reports = json.loads((tmp_path / "reports.json").read_text(encoding="utf-8"))
    for report in reports:
        assert report["source"] == "handwritten_synthetic_only" and report["real_llm"] is False
        ids = {item["id"] for item in report["evidence"]}
        assert all(set(claim["evidence"]) <= ids for claim in report["claims"])
    with pytest.raises(ValueError):
        build_report("private-data")
