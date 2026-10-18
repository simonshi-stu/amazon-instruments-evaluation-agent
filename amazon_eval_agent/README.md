# amazon_eval_agent（阶段 1：结果 artifact 校验与安全诊断）

本包是评测诊断项目（B）的阶段 1 独立入口：只做**结果契约校验与安全诊断**，
不生成用户评论、不审核商品事实、不计算准确率。完整评测编排、指标、LLM 解释与
人工确认导出属于后续阶段；当前不要把本工具称为"完整评测 Agent"。

## 独立运行（不需要 A 目录、数据或凭证）

```powershell
# 校验一个 artifact 文件（成功=0；契约无效=2，只打印安全 code，无 traceback）
py -m amazon_eval_agent validate --input <artifact.json>

# 内置手写合成样例（内存中校验，不读文件；证明独立可运行）
py -m amazon_eval_agent validate --check

# 只打印校验计划（不读文件、不写文件）
py -m amazon_eval_agent validate --dry-run

# 可选：安全诊断写到新文件（拒绝覆盖）；--json 输出机器可读报告
py -m amazon_eval_agent validate --input <artifact.json> --output results\report.json --json
```

运行时无第三方依赖（仅标准库，Python >= 3.10）；跑测试需要 pytest：
`python -m pytest -q`。

## 输出诊断语义（必须分开读）

- `artifact_validated`：**文件契约**是否合法（形状/资源门/状态矩阵）；
- `all_predictions_succeeded` / `prediction_success_count`：**记录结果**是否全部为
  success；`degraded`/`refused`/`failed` 单独计数、绝不当作成功；合法 artifact
  可以包含 failed 记录（CLI 仍返回 0，但报告明示未全成功）；
- `declared_source_kind` / `declared_model_source` / 三个指纹：**调用方声明**，
  `fingerprint_verification=declared_only_not_independently_verified`；
- `internal_consistency_note`：诊断不变量只证明**记录自洽**（attempt/repair
  语义、memory 漏斗、引用唯一），不代表外部模型调用或指纹真实；
- `real_measurement_status=not_verified_or_not_run`：即使声明
  `real_model_declared`，也不代表真实模型实测已完成；
- `accuracy_claim=none`、`scoring_available=false`：本阶段不计算 RMSE/MAE 等任何
  猜测指标；契约无效立即失败关闭，不会隐式进入打分。

## 契约摘要（SimulationRunV1，canonical）

- 固定字段：`schema_version`、`source`（producer/kind/model_source + 3 个 64hex
  指纹）、`simulated=true`、`record_count`、`records`；
- record：`task_index` / `execution_order` / `split` / `context_sha256` / 伪名
  `user_group`（确定性配对标签；不声称不可逆匿名）、四态 `status` + 安全
  `reason`（P1 固定枚举）、`prediction_origin` 与状态一致、`stars` / `review` /
  `evidence_refs`、有界诊断 `llm_attempts` / `repair` / `prompt_bytes` /
  `memory` 计数；
- 状态矩阵：success = 整数 1..5 + strip 后非空 review + 1..16 个不重复
  `ev_...` 引用；degraded = 统计基线连续评分、review 为空、
  origin=statistical_baseline；refused / failed = 无评分无评论；
- 诊断自洽不变量（内部一致性，不是外部真实证明）：首轮 success 恰好 1 次
  attempt 且 repair=not_requested；repaired success 恰好 2 次且
  repair=attempted；refused 0 次；degraded 走统计回退语义（1 次+reserve_failed
  或 2 次+attempted）；memory 漏斗
  candidates >= time_eligible >= selected >= prompt_included 且
  filtered_injection + prompt_included <= selected；引用唯一；
- 资源门先于 JSON 解析：字符串/字节/bytearray 先做长度早门（不整块复制）-> <=1 MiB
  字节预检 -> 严格解析（重复键/NaN/Infinity/解析期递归都映射到安全 code）
  -> 迭代深度 <=10 -> 超过 500 records 在任何逐记录校验之前直接拒绝；未知字段、
  布尔星、巨大数值（先范围比较再转 float）、空白 review、重复 task/order、状态或
  诊断不一致全部安全拒绝；错误只回安全 code，不回显字段值，validate_payload
  对恶意输入不抛异常；
- 字段层面禁止：契约没有 actual_stars / groundtruth / 目标文本 / raw prompt /
  API key / 任意文件路径字段；`review` 是生成自由文本、可能含任意词，不做
  内容级脱敏或“完全匿名”声明。

## 与 A 的接口流程（阶段 1）

```text
A: user_simulator_project（离线，fake callable）
   --synthetic / --prepared-dir（全门禁） -> results/simulation_run.v1.json
       (source.kind = fake_on_synthetic / fake_on_real_context)
         -> B: py -m amazon_eval_agent validate --input <该文件>
              -> 安全诊断：artifact_validated / 四态计数 / declared 来源 /
                 real_measurement_status=not_verified_or_not_run
                 （不计算指标；不声称真实成绩）
```

## 来源与版本

- canonical 契约：`amazon_eval_agent/contracts/simulation_run_v1.py`；
  A 侧镜像 `user_simulator_project/simulation_run_v1.py`，双方 `schema_hash()`
  由跨仓库测试钉住一致；
- B 生产代码不 import A、不依赖 A 目录；测试中的跨仓库 subprocess 校验仅用于
  本地两仓库一致性验证；
- 已实现（阶段 1）：严格契约 reader + 独立 validate CLI + 测试（字节/深度/重复键
  预检、parser spy、状态矩阵、无覆盖输出、dry-run 零写入、隐私不回显）；
- 待后续：完整评测编排与指标（需另行确认口径与数据授权）、LLM 解释、人工确认
  导出、真实模型运行验证；本阶段不触碰。
