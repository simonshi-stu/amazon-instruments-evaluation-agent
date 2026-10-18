"""Deterministic, synthetic-only demonstration. Never loads private run files."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

VERSION = "synthetic-evaluation-demo.v2"
STATES = ("success", "degraded", "failed", "refused")

SCENARIOS = {
    "balanced": "公平比较：同一批任务上的 A / B",
    "survivor": "低误差的假象：大量失败被排除",
    "insufficient": "无法比较：没有共同成功任务",
}


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


def _percent(value: float | None) -> str:
    return "无法判断" if value is None else f"{value:.0%}"


def _number(value: float | None) -> str:
    return "无法判断" if value is None else f"{value:.3f}"


def _scenario_copy(scenario: str, schemes: list[dict], pair: dict) -> tuple[dict, list[dict], dict]:
    a = schemes[0]["success_only"]
    b = schemes[1]["success_only"]
    common_n = pair["common_n"]
    a_mae = _number(pair["a"]["mae"])
    b_mae = _number(pair["b"]["mae"])
    delta = pair["mae_b_minus_a"]

    objectives = {
        "balanced": "比较两种方案在同一批任务上的误差，并说明为什么只能用“共同成功任务”比较。",
        "survivor": "演示一个看起来误差很低的方案，如何因为大量失败没有进入分母而制造假象。",
        "insufficient": "演示两边没有共同成功任务时，为什么必须说“无法比较”，而不是硬选一个赢家。",
    }
    if scenario == "balanced":
        conclusion = (
            f"在 {common_n} 个共同成功任务上，B 的 MAE 比 A 低 {abs(delta):.3f}。"
            "但这只是 12 个合成任务，没有置信区间，不能宣称 B 整体更好或效果显著。"
        )
    elif scenario == "survivor":
        conclusion = (
            f"B 只有 {b['counts']['success']} / {b['requested']} 个成功请求（覆盖率 {_percent(b['coverage'])}）。"
            "它在少数简单任务上误差低，但大量失败被排除；整体不能判定 B 更好。"
        )
    else:
        conclusion = (
            "A 和 B 没有任何共同成功任务，误差没有可比基础。"
            "正确结论是“无法比较”，不能因为某个数字更低就选赢家。"
        )
    analysis = [
        {
            "title": "先看样本和覆盖",
            "text": (
                f"A 成功 {a['counts']['success']} / {a['requested']}，覆盖率 {_percent(a['coverage'])}；"
                f"B 成功 {b['counts']['success']} / {b['requested']}，覆盖率 {_percent(b['coverage'])}。"
                "回退、失败和拒绝分别计数，不混进成功样本。"
            ),
        },
        {
            "title": "再看共同任务",
            "text": (
                "A 和 B 没有任何任务同时成功，因此共同任务为 0；没有共同任务，就没有可比的误差基础。"
                if common_n == 0
                else (
                    f"只保留 A、B 都成功的 {common_n} 个任务；另外 {pair['excluded_n']} 个任务被排除在误差比较之外。"
                    "共同任务越少，结论越弱。"
                )
            ),
        },
        {
            "title": "最后看误差",
            "text": (
                "没有共同成功任务，A/B 的 MAE 和差值都无法计算。页面不会用 0 或猜测值填空，也不宣称显著改善。"
                if common_n == 0
                else (
                    f"共同任务上 A 的 MAE = {a_mae}，B = {b_mae}，差值（B − A）= "
                    f"{'无法计算' if delta is None else f'{delta:+.3f}'}。"
                    "页面没有计算置信区间，因此不宣称显著改善。"
                )
            ),
        },
    ]
    overview = {
        "objective": objectives[scenario],
        "data": "12 个手写合成任务、4 个合成用户；没有真实 Amazon 评论、目标标签或模型调用。",
        "reading": "阅读顺序：共同成功任务数 → 覆盖率与失败 → MAE、RMSE、±1 星和偏差。",
    }
    summary = {
        "accuracy": {
            "status": "真实模型准确率：未测量",
            "detail": (
                "本页面没有调用 LLM，也没有读取真实 Amazon 运行结果。MAE、RMSE 等数字只用于验证评测口径，"
                "不能当成某个模型的准确率。"
            ),
            "checked": (
                "已用手算合成样例检查：失败不算零误差、回退不算成功、只在共同成功任务上比较、"
                "没有共同任务时拒绝比较。这证明计算和方法边界，不证明模型能力。"
            ),
        },
        "conclusion": conclusion,
        "improvements": [
            "接入真实运行结果：读取 A 的 simulation_run.v1，计算真实任务的误差、失败和覆盖。",
            "补上不确定性：按用户聚类做 bootstrap，报告区间；没有区间就不说显著。",
            "加入受证据约束的解释：模型只读脱敏指标，代码校验引用、数值、方向和分母。",
            "人工审批与导出：报告绑定配置、证据和报告哈希，内容变化后审批自动失效。",
            "做真实对照实验：小规模比较 LLM、Prompt、检索和 Memory；老用户未来行为和新用户泛化分开报告。",
            "最后再评估微调：只有错误分析表明 Prompt 和检索仍解决不了时，才考虑 QLoRA。",
        ],
    }
    return overview, analysis, summary


def build_report(scenario: str) -> dict:
    if scenario not in SCENARIOS:
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
    overview, analysis, summary = _scenario_copy(scenario, schemes, pair)
    evidence = [{"id": "paired-mae", "label": "共同成功任务的 MAE 差（B − A）", "value": pair["mae_b_minus_a"], "denominator": pair["common_n"]},
                {"id": "coverage-a", "label": "A 成功覆盖率", "value": schemes[0]["success_only"]["coverage"], "denominator": len(left)},
                {"id": "coverage-b", "label": "B 成功覆盖率", "value": schemes[1]["success_only"]["coverage"], "denominator": len(right)}]
    claims = [
        {"evidence": ["paired-mae"], "text": "没有共同成功任务，无法比较误差。" if not pair["common_n"] else "只在共同成功任务上比较误差；负差值表示 B 在这一子集上的 MAE 更低，不代表全体表现更好。"},
        {"evidence": ["coverage-a", "coverage-b"], "text": "同时查看覆盖率和失败情况，不能把失败样本当成零误差，也不能把回退算作模型成功。"},
    ]
    payload = {"version": VERSION, "scenario": scenario, "title": SCENARIOS[scenario], "source": "handwritten_synthetic_only", "real_llm": False,
               "overview": overview, "analysis": analysis, "summary": summary,
               "schemes": schemes, "comparison": pair, "evidence": evidence, "claims": claims,
               "limits": ["全部输入为手写合成数据，不是 Amazon 真实成绩。", "没有 LLM 调用、人工评审、Memory 收益或新用户泛化结果。", "没有置信区间或 p 值，不能宣称显著改善。", "页面展示预计算报告；点击不会实时调用 Python 或模型。", "解释是确定性模板，不是 LLM 生成报告；完整 Agent 编排仍在开发。"],
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
    (output / "reports.json").write_text(json.dumps([build_report(s) for s in SCENARIOS], ensure_ascii=False, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build synthetic-only Pages demonstration; no model calls.")
    parser.add_argument("--output", type=Path, required=True)
    build_site(parser.parse_args().output)
