# 第一批：可核验评测配置、任务/来源/时间协议与确认指纹

日期：2026-10-26。状态：**代码入口已实现并测试；真实模型实验未运行；真实本地数据的只读审计被来源/覆盖条件阻塞，不能当作新成绩。**

本文回答五件事：比较对象、任务清单、来源、时间切分、契约切口。所有公开 fixture 都是合成的；真实数据/报告只留在 E 盘 ignored 目录，不进入仓库。

## 0. 本轮实际完成了什么

- `evaluation_config.v1`：闭集、带版本、资源只写逻辑 ID 与期望哈希，不写路径、标签、评论或用户 ID。
- `task_manifest.v1`：B 拥有的任务清单；任务身份、执行顺序、split、cohort 伪名、目标日期、context 指纹。
- A schema 2 manifest 投影 adapter 与 A prepared 目录导入 adapter；不 import A 应用。
- 独立 `b_prepared_context_v1` verifier：重算 A manifest/runtime context、可见历史/参考行选择、严格时序、目标副本排除与运行 context；导入时 fail-closed。
- 独立来源核验：CLI 从本地文件重新计算大小与 SHA-256，声明哈希不等于核验哈希。
- cohort/split/时间协议检查：chronological split、同日期/同 timestamp 组不跨 split、cut 边界、标签覆盖、用户重叠显式报告。
- A `simulation_run.v1` 接入：严格合同校验、任务/split/order/cohort 对齐、dataset/manifest 指纹与本地文件绑定。
- 人工确认 `evaluation_confirmation.v1`：绑定配置、清单、标签、来源、运行结果、baseline 与计划指纹；任一变化验证失败。
- `score`：第一批之后新增的点估计阶段；只算 RMSE/MAE/±1/bias，无区间、无显著性、无 LLM 解释、无批准导出。
- 测试：**240 passed, 3 skipped**（2026-10-26，本机设置恢复 A 环境变量；未设置时为 236 passed, 7 skipped）。3 个固定 skip 仍是 sibling `llm-user-simulation-agent-dual` 为空导致的旧跨仓检查；另用环境变量显式核验了恢复 A 镜像与真实 prepared，见 §7。

## 1. 比较对象：先分清可执行与待批准

### 1.1 首轮实际可执行（合成，不是真实成绩）

- 候选：`examples/batch1_synthetic/run_candidate.json`，`source.kind=fake_on_synthetic`，12 个手写合成任务，其中 test 3 个（2 success、1 degraded）。
- 固定基线：`examples/batch1_synthetic/baseline_predictions.jsonl`，A P0 预测表格式，四模式 `global/user/item/combined`；示例选择 `global=3.0`。
- 可执行内容：`preflight` → `confirm` → `score` 的治理链、分母区分、共同任务比较、排除原因与报告指纹。证明逻辑，不证明模型质量，也不与真实模型排名混在一起。

### 1.2 真实本地只读审计（已接上输入，但被覆盖条件阻塞）

- 候选 artifact：恢复 A 的 `fake_on_real_context` 3 任务 artifact（仅 train，`task_index=0..2`）。
- 基线：A P0 的 `baseline_review_20261008/predictions.jsonl`，四统计基线、400 任务。
- 任务/标签：同目录 prepared 数据，B 导入 400 任务清单和 400 个本地标签。
- 预检结果：来源哈希、400 任务、cut、split、用户重叠、manifest/runtime context、run/user 哈希绑定均通过；但候选没有任何 test 任务，故 **blocked**，不能确认、不能评分、不能宣称真实比较结果。这是正确的拒绝，不是失败后补造。
- 另用**显式本地 fake 测试工具**在真实 prepared 的 80 个 test 任务上生成 `fake_on_real_context` 预测，贯通 preflight/confirm/score；报告标记 fake，不声称真实模型质量。
- 独立重算基线（不是新实验）：B 用 A 的预测表 + B 本地标签，手算复核了四基线的 test/all MAE、RMSE、bias，与 A 旧报告逐位一致；没有重算 bootstrap 区间，也没有把旧报告当成本轮新成绩。

### 1.3 未来待批准的真实比较

- 候选：A 真实 `real_model_declared` 运行（同一 400 任务、同一 test 80 任务）。
- 基线：A P0 `global/user/item/combined` 四统计基线。
- 需另行批准：真实模型调用与预算、数据使用/许可、真实 test-covering artifact、人工评审与报告导出。当前不能运行，也不能排名。
- 当前 400 任务协议 **不是 user-disjoint**：train∩test 21、validation∩test 11、train∩validation 35 个用户（从 manifest 重算）。所以不能把它改名为“新用户泛化”或 unseen-user 成绩；同用户跨 split 不等于时间泄漏。

## 2. 任务清单：字段、构建、合成/真实、任务数

### 2.1 B 清单字段（`task_manifest.v1`）

顶层闭集：`schema_version`、`manifest_id`、`origin`、`protocol`、`cohort_group_binding`、`split_counts`、`tasks`。

任务字段：

| 字段 | 含义 | 来源/所有者 |
| --- | --- | --- |
| `task_index` | 本地任务身份，join 的主键 | A prepared 任务文件 / 合成 fixture |
| `execution_order` | 连续执行顺序，0..n-1 | A manifest schema 2 |
| `split` | `train`/`validation`/`test` | A manifest |
| `cohort_group` | A 清单伪名 `U0001...` 或合成 `ug_...` | 清单；不是原始用户 ID |
| `target_date` | `YYYY-MM-DD`，目标事件日期 | A tasks/groundtruth（B 导入时校验一致） |
| `target_timestamp_sha256` | A 清单的目标时间哈希 | A manifest；不能反推日期 |
| `manifest_context_sha256` | A 清单的 context 摘要 | A manifest |
| `run_context_sha256` | B verifier 从 A manifest+runtime 行重算的运行时 context 期望 | B prepared verifier；A 导入后写入 |
| `history_max_date` | 该任务可见历史的最大日期；无历史为 null | B prepared verifier；严格时序检查 |

清单协议还记录：`history_cut_date`、`timestamp_semantics`、`split_policy`（`train_ratio`/`validation_ratio`/`ties=indivisible_group`）、`history_visibility`、`target_region=on_or_after_cut_date`、`user_holdout_policy`。

### 2.2 构建/使用命令

```powershell
# 从 A prepared 目录只读导入 B manifest + local labels（新文件，拒绝覆盖）
python -m amazon_eval_agent import-a-prepared `
  --prepared-dir <本地 prepared> `
  --manifest-output <新 manifest.json> `
  --labels-output <新 labels.json> `
  --manifest-id amazon_mi_2014_prepared_20261008

# 合成独立自检（不读外部文件）
python -m amazon_eval_agent preflight --check

# 真实/本地预检（所有 logical resource 必须绑定）
python -m amazon_eval_agent preflight --config <config.json> `
  --bind dataset_raw=<...> --bind dataset_normalized=<...> `
  --bind task_manifest=<...> --bind a_manifest_source=<...> `
  --bind labels=<...> --bind run_candidate=<...> `
  --bind baseline_predictions=<...>
```

### 2.3 合成与真实任务数/ID

- 合成 fixture：12 个任务，`task_index=0..11`，split 6/3/3；标签是手写的，不是真实 Amazon 标签。
- 真实 400 任务：`task_index=0..399`，来自 A prepared 的本地任务配对；split 238/82/80；200 个用户；test 56 个用户 cluster。B 只能提供 `task_index` 与来源哈希；**公开 A manifest 不提供可公开的原始任务 ID**，B 不编造字符串 ID。`source_task_index_sha256` 只出现在 A 清单投影中，不导出。
- 真实任务数 400 是从本地 A manifest 重算的；不是从旧报告抄写。导入 summary 只输出计数和哈希，不输出真实标签/用户/文本/路径。

## 3. 来源：具体文件、哈希、schema、许可状态

### 3.1 观察事实（本地只读核对，2026-10-26）

| 项 | 观察值 | 说明 |
| --- | --- | --- |
| A 公开旧 checkout | commit `ea708dce8be83abbc910365ecd48091d4a069b92`（2026-10-18） | sparse，仅公开旧代码；不能只据此设计新 A |
| A 恢复工作快照 | 本地目录，无 git | 只读参考；包含 `amazon_p0.py`、`rating_baselines.py`、`reliable_agent.py`、`user_simulator_project/`、完整 tests |
| A 合同镜像 | `user_simulator_project/simulation_run_v1.py`，SHA-256 `145404891fa5d9fc0d0c8af6fe7768e4fac4ed89e67d35054b0da3be439fdc59` | `schema_hash()=70256fa957a3103e2892f3e3a22e67b23b2a85c1a9ac66c05b18a7418f0da85c`，与 B canonical 一致 |
| A 清单实现 | `memory_audit.py`，SHA-256 `f5904ef582eea06eeda0ca01659cb840152ce0ae0031443cf7486b98701567d2` | schema 2，`manifest_type=strict_temporal_no_groundtruth_context` |
| A 时间 provider | `temporal_context.py`，SHA-256 `2db07ca9b8cc6f8616e97d0e2352ee0c659019b63a83c43f1d5c4379235d9162` | runtime context digest 与 manifest context digest 不同域 |
| A P0 适配器/基线 | `amazon_p0.py` `87ebccfe9cc9877827570cd3446451a67dafa1ec84fddc8c2f4ef6cacae484d2`；`rating_baselines.py` `528cbe21926c1b796d5ea65ad383b83db2efb3448a33f7a9156c367ca8783ec5` | 四基线公式与 P0 报告格式 |
| A export | `user_simulator_project/export.py` SHA-256 `1a3f403ac73c25881cfb427bf6d7e83266645953152e1b7251ad08e81078c8f4` | dataset=normalized_reviews 文件哈希，manifest=manifest.json 文件哈希，user_group 为确定性 hash |
| 原始数据 | `reviews_Musical_Instruments_5.json.gz` SHA-256 `03f9cc27ae13a454cb4ea6cb620fb10546cc1194ef921441d1c5f34addff2748`，2,460,495 bytes | 仅本地只读；未复制进仓库 |
| 规范化源 | `normalized_reviews.jsonl` SHA-256 `abbe175b7988a1826c6d609d403388ee88a431b31c28c6369011a510d61a58a3`，6,691,611 bytes | A export 的 dataset fingerprint |
| A 运行源 | `runtime_reviews.jsonl` SHA-256 `c735c323ae338cb15d72798cf422e31c24cc29fa75be80a15da6cfc787ceb3e8`，4,602,614 bytes | 6157 历史 + 选中目标行 |
| A manifest 文件 | `manifest.json` SHA-256 `77344602d98196b564bc8f0806db535c37282e25ff20ab9126a94b57a1c88584`，463,444 bytes | A export 的 manifest fingerprint |
| A 基线预测表 | `predictions.jsonl` SHA-256 `cb0d8234ddd95dfa5105ecbd4e2f72c7a8cf65b69a99829a6f5ae29d5f794a32`，207,425 bytes | 400 task × 4 mode，无标签/文本 |
| A 基线旧报告 | `report.json` SHA-256 `e6837a61c0686a90b6ab94c5d5f1cf63b6039a396142b04543e95d423982c248`，13,949 bytes | 仅作旧结果对照，不作为本轮新实验 |
| 许可 | `license_status=not_explicitly_verified` | A 代码 MIT 不覆盖原始评论；公开下载可用不等于可再分发/商用 |

B 本轮独立重算的是：文件 SHA-256、400 个 `task_index` 的清单导入、split 238/82/80、cut 日期/目标日期协议、用户重叠 35/21/11。`10,261 rows / 1,429 users / 900 items` 来自 A `p0_policy.json` 与旧审计声明，B 本轮**没有重新解析 raw gzip 逐行计数**；`6,157 history / 4,104 target` 来自 A policy/report，B 也没有重建选择过程。A 旧报告与本地 policy 一致，但旧报告数值不作为新实验。

### 3.2 建议/边界

- 在许可明确前只做本地研究审计，不再分发原始评论、目标标签、用户 ID 或私有路径；公开仓库只保留合成 fixture/配置说明。
- 不把 A 代码 MIT 当作评论数据许可；不把“文件可下载”当作“可外发”。
- 不把旧报告 400 任务的数值写成新一轮真实模型成绩。
- 真实外部模型调用、人工评审、数据外发、付费服务仍需单独确认范围/预算/权限。

## 4. 时间切分：切点、边界相等规则、dev/holdout

### 4.1 冻结切点与区域

- 切点：`2013-08-28`（UTC 日边界）。
- 历史：严格早于目标事件时间；P0 冻结 prefix 6,157 行，不随目标区滚动。
- 目标区：cut 当天及之后（`target_date >= cut_day`），4,104 行；选中 400 个重复用户目标，每个选中用户至少两个不同目标日期。
- 同一 UTC 日/同一 timestamp 组不可分：同组任务不跨 train/validation/test；目标文本/目标行/同日目标从所有上下文排除。B 对导入清单校验同一 `target_date` 与同一 `target_timestamp_sha256` 不跨 split。
- 相等规则：目标时间等于 cut 属于目标区；历史必须 `history_max_date < target_date`，相等即泄漏并拒绝。

### 4.2 开发与独立 holdout

- 开发/调参：`train + validation`（238 + 82 = 320 任务）。
- 独立评测：`test`（80 任务，56 用户 cluster）。
- 不得用 test 反馈调 prompt/model 后仍把同一 test 当独立 holdout；B 不根据旧结果反推新成绩。
- 当前协议允许同用户跨 split；这是时间协议，不是 user-disjoint。需要“新用户泛化”时另建未见用户 holdout，不能把当前 400 manifest 改名。
- B 第二批评分只做 test 上的点估计；不计算 bootstrap 区间/p 值；旧基线区间跨零的结论保持原样，不能改写成“已证明更优”。

### 4.1 后续实验预注册边界（本轮不执行）

- **H1 同用户未来行为**：沿用现有冻结时序目标；目标是同一批用户在 cut 之后的未来行为。train/test 允许用户重叠，必须保留现有 split/时间协议，不能把重叠误报成 user-disjoint。
- **H2 独立用户泛化**：另建用户级不重叠的开发/测试集；测试集锁定后不得因看测试结果改 Prompt、检索或模型配置。新用户“少历史”和“零历史”分开报告；少历史的条数、取样 seed、cut 在真实实验授权前冻结，本轮不编造已定参数。
- **Memory 对照**：有/无 Memory 必须使用相同任务、执行顺序、历史/参考、模型与预算；记录 Memory 是否实际进入 prompt 及用户覆盖。区间按整用户配对 bootstrap；当前 `score` 不提供区间，不能据此推断 Memory 收益。
- H1/H2 不得混报；总数、失败、回退、拒绝、缺失和冷启动均不能静默剔除。本轮只写边界，不运行真实模型、不生成新实验成绩。

## 5. 契约切口：输入/输出、字段拥有者、GT、缺失镜像、确认

### 5.1 输入/输出

```text
A prepared (local, read-only)
  tasks/ groundtruth/ manifest.json p0_policy.json
        │  import-a-prepared (explicit adapter)
        ▼
B task_manifest.v1 + local_labels.v1 (separate files)
        │
A run artifact simulation_run.v1 ──► B preflight:
  validate contract                    source hashes, task/split/order join,
  project safe fields                  cohort/time protocol, baseline coverage
        │                              no GT, no metrics
        ▼
B confirmation v1 (config/manifest/labels/sources/run/plan fingerprints)
        │
        ▼
B score v1: label values used here for errors (governance may parse labels for coverage/joins), success-only vs system incl degraded,
            full cohort coverage + common-task comparison, point estimates only
```

### 5.2 字段拥有者

- A 拥有运行 artifact 的 `source`、`records`、`user_group`、`context_sha256`、`status`、`prediction_origin`、`stars`、`review`、diagnostics。B 只读、不改写、不补造。
- B 拥有 task manifest、local labels、source registry/config、preflight report、confirmation、score report。
- `stars` 在运行 artifact 中是**预测**，不是 groundtruth；`local_labels.v1` 是 B 本地隔离标签；评分器在预测固定后才读取。
- 运行 artifact 合同闭集，不含 actual/groundtruth/target text/raw prompt/key/path 字段；B 不把 `review` 自由文本当标签。
- `user_group` 是配对伪名，不是不可逆匿名保证；报告不输出该值，只输出 cluster 计数。

### 5.3 A 运行接入与指纹

- 来源绑定：A export 对 prepared 运行使用 `sha256(normalized_reviews.jsonl)` 作为 dataset fingerprint、`sha256(manifest.json)` 作为 manifest fingerprint。B 在 preflight 中重新哈希这两个本地文件并比较运行声明；这是**声明与文件字节的绑定**，不是“A 自报即真实”。
- `config_fingerprint_sha256` 仍是 A 声明，B 不重算其内部配置语义；报告写 `caller_declaration_not_independently_verified`。
- A manifest 的 `source_review_sha256` 是 canonical parsed records 指纹，不是 raw gzip 哈希；B 不把它冒充 raw 哈希。raw gzip 的 SHA-256 单独由 source registry 核验。
- `user_group`：A manifest 用 `U0001`；B import 验证同一 user_id 只对应一个 group、同一 group 只对应一个 user、按 execution order 首次出现分配 U0001... 的顺序一致；A export 用确定性 `sha256(b"simulation_run.v1-usergroup\0"+raw)[:16]` 映射为 `ug_<16hex>`，B adapter 重实现该小映射并核对，不 import A。真实 recovered artifact 的 3 个标签已验证匹配。
- `context_sha256`：运行记录是 A 的 **runtime context digest**；A manifest 的 `context_sha256` 是 **manifest context digest**，两者不同域。B 的版本化 `b_prepared_context_v1.reader_v1` 从 `runtime_reviews.jsonl` 重算：目标行身份、可见历史/参考行选择、严格时序、目标副本排除、manifest context 和 runtime context。A 导入清单现在写入每任务 `history_max_date` 与 `run_context_sha256`；preflight 对运行记录实际存在的任务核对 runtime context。真实 400 任务 manifest context/history 全部重算通过，恢复 A 的 3 个运行记录 context 全部匹配；合成 9 任务全链 9/9。未覆盖的运行任务仍不会被假装已核对。
- 目标时刻规则：当前 verifier 只支持 Amazon UTC-day resolved-target 布局；runtime 目标行必须与 manifest 目标时刻**完全相等**（UTC 午夜），不是只比较日期。同日 00:00→12:00 等小时级错配 fail closed（`target_review_time_mismatch`）。
- 本地读取全部经过 stat gate + 有界 read；A manifest/B manifest/任务/GT/policy/labels/run/runtime 分别使用 manifest/config/labels/contract/source 上限；JSONL 每文件与合计字节、总行数有上限，runtime 行数 ≤100,000。
- 任务 join 主键：`task_index`；同时核对 `execution_order`、`split`、cohort group 映射。错 task/split/order/group 都 blocked。

### 5.4 缺失镜像与 skip

- A 公开旧 checkout 不含新版镜像；恢复工作快照在本地，不是“从 B 复制过去”的镜像。B 仓库没有复制/生成 A 的 `user_simulator_project/`。
- 原 3 个跨仓测试仍指向空的 `llm-user-simulation-agent-dual`，因此保持 skip；它们没有被伪造通过。
- 另有环境变量显式开启的恢复镜像测试：`AMAZON_A_RECOVERED_DIR`、`AMAZON_A_PREPARED_DIR`；本机运行通过，未设置时 skip。skip 仍表示“没有原件”，不是通过。
- 未来 A 合同版本变化：新增版本化 adapter；不修改 v1 schema hash；不支持版本显式 `unsupported_run_contract`；不静默强制转换。

### 5.5 人工确认（`evaluation_confirmation.v1`）

确认文件字段：`schema_version`、`confirmation_id`、`status`、`scope=preflight_only_no_metrics`、`confirmed_at`（UTC）、`confirmed_by=local_operator`、`plan_fingerprint`、`bindings`、`contract_schema_hash`、`real_measurement_status=not_verified_or_not_run`、`accuracy_claim=none`、`note`。

`bindings` 绑定：`config_sha256`、`task_manifest_sha256`、`labels_sha256`、`baseline_predictions_sha256`、`run_artifact_sha256`、所有 source 的实际 SHA-256、`task_manifest_origin_kind`、`task_manifest_adapter`。

规则：

- 只有 `ready_for_scoring` 的 preflight 才能确认；blocked 直接拒绝。
- 任一份绑定文件变化、配置变化、清单/标签/来源/运行结果变化，`verify-confirmation` 报 `*_binding_mismatch`/`plan_fingerprint_mismatch`，确认失效。
- `score` 内部重新 parse + verify 当前 confirmation/result，不信任调用方传来的 `confirmation_valid=True`；stale/other-plan/篡改 scope/status/schema/id/time/label binding 全部拒绝。调用方布尔结果只能额外否决，不能授权。
- `PreflightResult` 的标签、baseline、manifest mapping、run memory、report 和 source bindings 均递归冻结，score 不读取可变的旧摘要作为授权。
- 确认是本机完整性绑定，不是密码学签名、身份证明、数据许可批准或真实模型证明；`confirmed_by=local_operator` 不声称实名。
- 确认只覆盖 preflight，不覆盖 score；score 报告另有 `score_fingerprint`，不生成 approved export。

### 5.6 CLI 顺序

```powershell
python -m amazon_eval_agent preflight --check
python -m amazon_eval_agent preflight --config <config> --bind <id=path>... --output <new report.json>
python -m amazon_eval_agent confirm --config <config> --bind ... --confirmed-at 2026-10-26T00:00:00Z --output <new confirmation.json>
python -m amazon_eval_agent verify-confirmation --confirmation <confirmation.json> --config <config> --bind ...
python -m amazon_eval_agent score --config <config> --bind ... --confirmation <confirmation.json> --output <new score.json>
```

退出码：`0` 表示该步通过；`2` 表示 blocked/无效/用法错误，错误只打印稳定 code，无 traceback、无路径或正文。

## 6. 当前阻塞与不能声称的内容

- 真实模型：未调用，无真实 LLM 准确率、风格、成本或延迟；`real_measurement_status=not_verified_or_not_run`。
- 真实比较：恢复 A 的 3 任务 artifact 只有 train，没有 test 覆盖，preflight blocked；不能确认/评分/排名。
- 许可：`not_explicitly_verified`；本地审计的 `publication_status=blocked_license_unverified`，不可公开或再分发。
- 用户泛化：当前 400 任务非 user-disjoint；不能声称新用户泛化。
- 标签来源：B local labels 从 A groundtruth 导入，只有 `task_index+stars`；没有人工双盲评审。
- 统计：B score 只做点估计；无 bootstrap/区间/p 值；无多重比较；不声称显著。
- 诊断：当前合同没有真实 latency/usage/first-format trace；报告写 `unknown_not_in_contract`，不推测。

## 7. 测试与复现

```powershell
# Fresh-clone baseline: B runtime is stdlib-only; pytest is the test dependency.
python -m pip install pytest==8.3.5

# Optional local cache; choose your own writable path.
$CacheRoot = 'E:\your-local-cache'
$env:TEMP = $CacheRoot
$env:TMP = $CacheRoot

# Optional only when the recovered A snapshot / prepared data are present.
$env:AMAZON_A_RECOVERED_DIR = '<recovered A snapshot>'
$env:AMAZON_A_PREPARED_DIR = '<prepared review dir>'
$env:AMAZON_A_BASELINE_DIR = '<baseline review dir>'

python -m pytest -q -rs -o cache_dir="$CacheRoot\amazon-pytest"
```

不设置三个 `AMAZON_A_*` 变量时，恢复 A 相关测试会明确 skip；这不是恢复原件，也不是通过。

本机结果：`240 passed, 3 skipped`（设置恢复 A 镜像/真实 prepared 环境变量）。3 个固定 skip 是 sibling dual 为空的旧跨仓测试；恢复镜像/真实 prepared 的额外测试在设置环境变量后通过；不设置时为 `236 passed, 7 skipped`。真实 prepared 导入/预检/基线重算为本地命令，报告留在 ignored E 盘目录；没有把真实数据复制进仓库。

独立 prepared 验证与本地 fake 测试：

```powershell
python -m amazon_eval_agent verify-a-prepared `
  --prepared-dir <prepared> --manifest <B manifest> --labels <B labels> `
  --run <A artifact>
```

本机真实 prepared 结果：400 任务 history/reference selection、400 manifest context、400 target-copy absence 全部 verified；运行 artifact 的 3 个任务 runtime context 3/3 verified。另用显式 `fake_on_real_context` 本地测试工具在 80 个真实 test 任务上生成合成预测并贯通 `preflight → confirm → score`；该报告明确是 fake 测试，不是模型成绩。
