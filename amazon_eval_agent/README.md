# amazon_eval_agent（阶段 1 + 第一批治理 + 点估计）

本包是评测诊断项目（B）的独立入口。当前包含：

- `validate`：严格结果契约校验与安全诊断；
- `preflight` / `confirm` / `verify-confirmation`：版本化评测配置、独立来源
  哈希、任务/split/时间/cohort 检查、A 运行接入与人工确认指纹；
- `import-a-prepared`：显式 A prepared 只读 adapter，严格 join task/GT/manifest，
  生成 B 清单与本地标签；
- `verify-a-prepared`：B 独立重算 A manifest/runtime context、可见历史/参考行
  选择、目标副本排除与运行 context；
- `score`：已确认计划上的点估计（RMSE/MAE/±1/bias、分母与覆盖），内部重验
  确认，不信任调用者布尔结论；不生成区间、不做 LLM 解释、不导出批准报告。

它不生成用户评论、不审核商品事实、不声称真实模型已运行。完整区间、解释、
结论校验与批准导出属于后续阶段。

## 独立运行（不需要 A 目录、数据或凭证）

```powershell
# 校验一个 artifact 文件（成功=0；契约无效=2，只打印安全 code，无 traceback）
py -m amazon_eval_agent validate --input <artifact.json>

# 内置手写合成样例（内存中校验，不读文件；证明独立可运行）
py -m amazon_eval_agent validate --check

# 只打印校验计划（不读文件、不写文件）
py -m amazon_eval_agent validate --dry-run

# 第一批：纯合成 preflight（不读外部文件、不调用模型）
py -m amazon_eval_agent preflight --check

# 文件模式：每个 logical resource 绑定本地字节；报告不输出路径/正文/ID
py -m amazon_eval_agent preflight --config <config.json> --bind <id=path> --output results\preflight.json
py -m amazon_eval_agent confirm --config <config.json> --bind <id=path> `
  --confirmed-at 2026-10-26T00:00:00Z --output results\confirmation.json
py -m amazon_eval_agent verify-confirmation --confirmation results\confirmation.json `
  --config <config.json> --bind <id=path>
py -m amazon_eval_agent score --config <config.json> --bind <id=path> `
  --confirmation results\confirmation.json --output results\score.json

# A prepared 只读导入：只写 B 清单 + task_index/stars 标签
py -m amazon_eval_agent import-a-prepared --prepared-dir <prepared> `
  --manifest-output results\manifest.json --labels-output results\labels.json `
  --manifest-id amazon_mi_2014_prepared_20261008
py -m amazon_eval_agent verify-a-prepared --prepared-dir <prepared> `
  --manifest results\manifest.json --labels results\labels.json --run <A artifact.json>
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
- `accuracy_claim=none`、`scoring_available=false`：`validate`/`preflight` 不计算
  RMSE/MAE 等指标；契约无效立即失败关闭，不会隐式进入打分。

## 第一批与点估计语义（必须分开）

- `preflight` 只回答“这个比较能不能执行”：配置/清单/标签/来源实际哈希、A 运行
  任务/split/order/cohort join、时间协议、baseline 覆盖、共同 test 任务与用户
  cluster 数。它不计算误差，也不把 A 自报指纹当独立核验。
- `A dataset/manifest fingerprint` 只有在配置绑定到本地文件时才比较；B 用实际
  文件 SHA-256，而不是运行结果里的自报值。`config_fingerprint` 仍是声明。
- A 运行记录的 `context_sha256` 是 runtime digest；A 清单的 `context_sha256` 是
  manifest digest，两者不同域。B 的 `b_prepared_context_v1` 从 runtime 行重算
  manifest/runtime context、可见历史/参考行和目标副本排除；A 导入清单写入
  `history_max_date`/`run_context_sha256`，preflight 对实际出现的运行记录核对
  context。未覆盖的任务不会假装已核对。
- A manifest 的 `user_group`（如 `U0001`）与 A export 的 `ug_<16hex>` 通过一个
  明确版本的确定性 hash adapter 核对；这不是原始用户 ID。
- `confirm` 只对 `ready_for_scoring` 计划生效，绑定 config/manifest/labels/
  baseline/run/source 实际哈希；任一文件或配置变化都使确认失效。确认不是签名、
  实名或数据许可批准。
- `score` 只输出点估计。模型 `success` 与系统 `success+degraded` 使用不同分母；
  `refused`/`failed`/`missing` 不按零误差填；同时报告全 test cohort 覆盖和共同
  有效任务比较。标签可被 preflight/import 解析做覆盖/一致性检查，但标签值只在
  scorer 用于误差。没有 bootstrap、区间、p 值、LLM 解释或批准导出。
- 当前真实 recovered A fake artifact 只覆盖 3 个 train 任务，没有 test 覆盖；
  预检会 blocked，不能评分或与真实模型排名混在一起。

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
- 已实现：严格契约 reader + 独立 validate CLI；`evaluation_config.v1` /
  `task_manifest.v1` / `local_labels.v1`；A schema 2 manifest 投影与 prepared
  导入；独立 source 哈希；preflight/确认/score；点估计与安全反例测试。
- 待后续：真实 test-covering 模型运行（需授权与预算）、区间/显著性、LLM 解释、
  结论校验、批准导出、真实人评；本阶段不伪造。
