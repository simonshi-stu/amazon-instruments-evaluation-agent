"""Deterministic, synthetic-only demonstration. Never loads private run files."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

VERSION = "synthetic-evaluation-demo.v1"
STATES = ("success", "degraded", "failed", "refused")


def score(rows: list[dict], allowed: tuple[str, ...] = ("success",)) -> dict:
    """Score only explicit eligible predictions; failures never become zero."""
    ids = set()
    errors = []
    counts = dict.fromkeys(STATES, 0)
    for row in rows:
        if row["task"] in ids or row["status"] not in STATES:
            raise ValueError("duplicate task or invalid status")
        ids.add(row["task"])
        counts[row["status"]] += 1
        target, predicted = row["target"], row["prediction"]
        if isinstance(target, bool) or not isinstance(target, (float, int)) or not math.isfinite(target) or not 1 <= target <= 5:
            raise ValueError("invalid target")
        if row["status"] in ("success", "degraded"):
            if isinstance(predicted, bool) or not isinstance(predicted, (float, int)) or not math.isfinite(predicted) or not 1 <= predicted <= 5:
                raise ValueError("invalid prediction")
        elif predicted is not None:
            raise ValueError("failure cannot carry prediction")
        if row["status"] in allowed:
            errors.append(predicted - target)
    n = len(errors)
    return {
        "requested": len(rows), "eligible": n, "excluded": len(rows) - n,
        "counts": counts, "coverage": n / len(rows) if rows else None,
        "mae": sum(abs(x) for x in errors) / n if n else None,
        "rmse": math.sqrt(sum(x * x for x in errors) / n) if n else None,
        "within_one": sum(abs(x) <= 1 for x in errors) / n if n else None,
        "bias": sum(errors) / n if n else None,
    }


def compare(left: list[dict], right: list[dict]) -> dict:
    """Use identical task IDs, users and targets for paired success comparison."""
    score(left)
    score(right)
    a, b = {r["task"]: r for r in left}, {r["task"]: r for r in right}
    if a.keys() != b.keys() or any((a[k]["target"], a[k]["user"]) != (b[k]["target"], b[k]["user"]) for k in a):
        raise ValueError("task/target/user alignment mismatch")
    common = sorted(k for k in a if a[k]["status"] == b[k]["status"] == "success")
    paired_a, paired_b = score([a[k] for k in common]), score([b[k] for k in common])
    return {
        "common_tasks": common, "common_n": len(common),
        "users": len({a[k]["user"] for k in common}),
        "excluded_n": len(a) - len(common), "a": paired_a, "b": paired_b,
        "mae_b_minus_a": paired_b["mae"] - paired_a["mae"] if common else None,
        "interval": None, "significance": "not_computed",
    }


def build_report(scenario: str) -> dict:
    titles = {"balanced": "相同任务上的两种示例方案", "survivor": "低误差可能掩盖大量失败", "insufficient": "没有共同成功任务时怎么办"}
    if scenario not in titles:
        raise ValueError("unknown scenario")
    targets = [1, 2, 3, 4, 5, 3, 2, 4, 1, 5, 3, 4]
    left, right = [], []
    for i, target in enumerate(targets):
        sa = "success" if i < 9 else ("degraded", "failed", "refused")[i - 9]
        sb = "success" if i < 10 else ("degraded", "failed")[i - 10]
        if scenario == "survivor":
            sb = "success" if i < 3 else "failed"
        elif scenario == "insufficient":
            sa, sb = ("success", "failed") if i % 2 else ("failed", "success")
        for rows, status, delta in ((left, sa, 1), (right, sb, 0 if i % 3 else -1)):
            rows.append({"task": f"synthetic-task-{i + 1}", "user": f"synthetic-user-{i % 4}", "target": target,
                         "prediction": max(1, min(5, target + delta)) if status in ("success", "degraded") else None, "status": status})
    pair = compare(left, right)
    schemes = [{"name": name, "success_only": score(rows), "including_fallback": score(rows, ("success", "degraded"))} for name, rows in (("示例方案 A", left), ("示例方案 B", right))]
    evidence = [{"id": "paired-mae", "label": "共同成功任务的 MAE 差（B − A）", "value": pair["mae_b_minus_a"], "denominator": pair["common_n"]},
                {"id": "coverage-a", "label": "A 成功覆盖率", "value": schemes[0]["success_only"]["coverage"], "denominator": len(left)},
                {"id": "coverage-b", "label": "B 成功覆盖率", "value": schemes[1]["success_only"]["coverage"], "denominator": len(right)}]
    claims = [
        {"evidence": ["paired-mae"], "text": "没有共同成功任务，无法比较误差。" if not pair["common_n"] else "只在共同成功任务上比较误差；负差值表示 B 在这一子集上的 MAE 更低，不代表全体表现更好。"},
        {"evidence": ["coverage-a", "coverage-b"], "text": "同时查看覆盖率和失败情况，不能把失败样本当成零误差，也不能把回退算作模型成功。"},
    ]
    payload = {"version": VERSION, "scenario": scenario, "title": titles[scenario], "source": "handwritten_synthetic_only", "real_llm": False,
               "schemes": schemes, "comparison": pair, "evidence": evidence, "claims": claims,
               "limits": ["全部输入为手写合成数据，不是 Amazon 真实成绩。", "无 LLM 调用、无 Memory 收益验证、无人工评审。", "未计算置信区间或 p 值，不宣称显著性、因果改善或新用户泛化。", "本页面不上传文件；不读取原始评论、密钥或真实实验。", "解释是确定性模板，不是模型生成报告；完整 Agent 编排仍在开发。"],
               "input_fingerprint": hashlib.sha256(json.dumps([left, right], sort_keys=True).encode()).hexdigest()}
    payload["report_fingerprint"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


def build_site(output: Path) -> None:
    """Build public assets from an explicit whitelist, never private folders."""
    import shutil

    source = Path(__file__).resolve().parents[1] / "demo"
    output.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "app.js", "style.css"):
        shutil.copyfile(source / name, output / name)
    (output / "reports.json").write_text(json.dumps([build_report(s) for s in ("balanced", "survivor", "insufficient")], ensure_ascii=False, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build synthetic-only Pages demonstration; no model calls.")
    parser.add_argument("--output", type=Path, required=True)
    build_site(parser.parse_args().output)
