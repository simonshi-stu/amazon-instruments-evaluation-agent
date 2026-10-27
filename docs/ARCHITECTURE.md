# 公共架构与运行说明（v0.2.0）

日期：2026-10-26。对应实现：第一批治理入口 + 点估计评分。此文档只描述
**已实现、可测试**的能力；真实 LLM 实验、区间、解释、批准导出仍未完成。

## 1. 定位与不变量

- 项目 B 独立评测项目 A 生成的模拟评分/评论；B 不生成评论、不冒充 Amazon。
- B 不 import A 应用；跨项目边界只通过版本化 artifact/adapter。
- 没有真实 LLM 调用、没有网络下载、没有付费服务；真实模型实验需单独授权。
- 运行结果自报指纹不是独立证据；B 只把**本地文件实际 SHA-256**当独立核验。
- `real_model_declared` 仍是声明；`real_measurement_status` 保持
  `not_verified_or_not_run`，直到有单独授权的真实测量。
- fake/合成 artifact 的误差即使能算，也只是评测管线诊断，不是模型效果。

## 2. 版本化公共接口

| 接口 | 版本 | 输入 | 输出 | 所有者 |
| --- | --- | --- | --- | --- |
| `simulation_run_v1` | `simulation_run.v1` / contract 1 | A 运行结果 JSON | 严格校验后的只读视图 | 共享合同；B canonical |
| `evaluation_config` | `evaluation_config.v1` | 逻辑资源/条件/时间/cohort 配置 | typed config | B |
| `task_manifest` | `task_manifest.v1` | 合成 fixture 或 A prepared 导入 | 任务清单 | B |
| `local_labels` | `local_labels.v1` | `task_index` + 整数 stars | 本地隔离标签 | B |
| `evaluation_confirmation` | `evaluation_confirmation.v1` | ready preflight | 指纹确认文件 | B |
| `evaluation_preflight` | `evaluation_preflight.v1` | config + 本地资源字节 | 预检报告 | B |
| `evaluation_score` | `evaluation_score.v1` | 已确认 preflight | 点估计报告 | B |
| A temporal manifest adapter | `a_temporal_manifest_v2.reader_v1` | A schema 2 manifest | 安全投影 | B adapter |
| A prepared adapter | `a_prepared_v1.reader_v1` | A prepared 目录 | B manifest + labels | B adapter |
| A P0 predictions adapter | `a_p0_predictions_v1.reader_v1` | A `predictions.jsonl` | 四模式预测表 | B adapter |

版本策略：不静默改 v1 字段/schema hash；新版本新增 adapter/模块；旧 artifact
继续按旧版本读取或显式拒绝。跨仓镜像缺失时测试保持 skip，不用 B 文件伪造 A
原件。

## 3. 数据流

```text
A prepared (local, read-only)
  tasks/ groundtruth/ manifest.json p0_policy.json runtime_reviews.jsonl
        │ import-a-prepared（显式版本 adapter）
        │   + b_prepared_context_v1：重算可见历史/参考行、目标副本排除、
        │     manifest context 与 runtime context
        ▼
B task_manifest.v1 (history_max/run_context 已绑定) + local_labels.v1
        │
A simulation_run.v1 ──► preflight（无 GT、无指标）
        │  合同校验 + source 实际哈希 + task/split/order/cohort join
        │  + 时间/cohort 协议 + baseline 覆盖
        ▼
evaluation_confirmation.v1（绑定 config/manifest/labels/source/run/plan）
        │
        ▼
score（标签值只在此处用于误差；preflight/import 可解析标签做覆盖检查）
  点估计：RMSE/MAE/±1/bias
  分母：model success-only / system success+degraded / 全 test cohort
  排除：missing/refused/failed；共同任务与 baseline 比较
  无区间、无 p 值、无 LLM 解释、无批准导出
```

## 4. 模块边界

| 模块 | 职责 |
| --- | --- |
| `safety.py` | 有界 JSON/JSONL、深度/非有限数/重复键、SHA-256、资源读取 |
| `config_v1.py` | 闭集 typed 配置、资源引用、purpose/license/cohort/temporal 校验 |
| `task_manifest_v1.py` | B 清单解析、split/日期/timestamp 组/计数/用户重叠检查 |
| `resources_v1.py` | 本地声明的实际大小/SHA-256 核验 |
| `labels_v1.py` | 本地标签闭集解析、与清单精确覆盖 |
| `adapters/a_temporal_manifest_v2.py` | A schema 2 投影；不重算 A 源哈希 |
| `adapters/a_prepared_v1.py` | 合成/真实 prepared 只读导入；严格 task/GT/manifest 身份 join |
| `adapters/prepared_context_v1.py` | B 独立重算 A manifest/runtime context、历史/参考行选择与目标副本排除 |
| `adapters/a_p0_predictions_v1.py` | A 四基线预测表格式校验与选择 |
| `adapters/simulation_run_v1.py` | A 运行合同读取、安全投影、user group 映射 |
| `preflight_v1.py` | 来源/任务/时间/cohort/baseline 预检与 plan fingerprint |
| `confirmation_v1.py` | 计划确认、绑定哈希、变更失效验证 |
| `scoring_v1.py` | 点估计、分母/覆盖/排除、固定 baseline 对照、基线审计；内部重验确认 |
| `cli.py` | `validate`/`preflight`/`confirm`/`verify-confirmation`/`import-a-prepared`/`verify-a-prepared`/`score` |

## 5. 运行

```powershell
python -m amazon_eval_agent validate --check
python -m amazon_eval_agent preflight --check

python -m amazon_eval_agent preflight --config <config.json> --bind <id=path>... --output results\preflight.json
python -m amazon_eval_agent confirm --config <config.json> --bind <id=path>... `
  --confirmed-at 2026-10-26T00:00:00Z --output results\confirmation.json
python -m amazon_eval_agent verify-confirmation --confirmation results\confirmation.json `
  --config <config.json> --bind <id=path>...
python -m amazon_eval_agent score --config <config.json> --bind <id=path>... `
  --confirmation results\confirmation.json --output results\score.json
python -m amazon_eval_agent verify-a-prepared --prepared-dir <prepared> `
  --manifest <B manifest> --labels <B labels> --run <A artifact>
```

纯标准库，Python >= 3.10。真实数据/GT/prompt/keys/逐任务结果/实验报告只留在
E 盘 ignored 目录；公开仓库只放合成 fixture 与安全说明。

## 6. 真实结果文件评分 vs 真实 LLM 成绩

- **能评分真实结果文件**：任何符合 `simulation_run.v1` 的 artifact 都能被
  `preflight`/`score` 处理；真实 `real_model_declared` artifact 也会被评分。
- **不等于真实模型成绩**：合同声明不证明模型真实运行；`real_measurement_status`
  仍是 `not_verified_or_not_run`。
- **fake 误差不能当真实效果**：`fake_on_synthetic`/`fake_on_real_context` 的
  RMSE/MAE 只证明管线/分母/排除逻辑，不能排名或宣称模型能力。
- **本地纯工具不需要 LLM 授权**：读取本地 artifact、做哈希/协议/点估计不需要
  模型授权；只有真实 LLM 运行、数据外发或人评才需要另行确认。
- **本机 smoke 区分**：真实 prepared 的 400 任务上下文可独立重算；恢复 A 的
  3 任务 artifact context 3/3 通过。另用显式 `fake_on_real_context` 本地测试
  工具在 80 个 test 任务上跑通评分，但报告标记 fake；这不是模型成绩。

## 7. 公开边界

不得提交或链接：真实评论、GT/标签、用户 ID、prompt、key、逐任务真实结果、
实验报告、私人面试笔记/问答。可以提交：合成 fixtures、schema、CLI、测试、
架构/运行说明、公开来源/许可说明。

## 8. English summary

This is the public architecture note for v0.2.0. B is an independent evaluator:
it never imports Project A, never calls a model or downloads data in these
tools, and never treats a declared fingerprint as independent evidence. The
batch-1 path verifies local source bytes, a B-owned task manifest, labels, A run
joins, time/cohort protocol and fingerprint-bound confirmation. The point-score
path computes RMSE/MAE/±1-star/bias with explicit model/system/full-cohort
denominators and no intervals/significance. Scoring a real artifact is not the
same as a verified real-LLM result; fake or synthetic errors are pipeline
evidence only. Real reviews, labels, identifiers, prompts, keys, per-task real
results and private interview material must not be published.
