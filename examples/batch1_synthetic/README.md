# 第一批合成样例

这里的每一个文件都是**手写合成**的：12 个任务、4 个示例用户、固定 3.0 的
`global` baseline、含 `(synthetic batch1 sample)` 标记的评论。没有真实 Amazon
评论、标签、用户 ID、Prompt 或私有路径。

文件：

| 文件 | 用途 |
| --- | --- |
| `evaluation_config.json` | `evaluation_config.v1`，资源只写逻辑 ID/期望哈希 |
| `task_manifest.json` | B 任务清单；12 任务，train/validation/test=6/3/3 |
| `local_labels.json` | 本地隔离标签；仅 task_index + stars |
| `run_candidate.json` | `fake_on_synthetic` 候选运行结果；测试 2 success + 1 degraded |
| `baseline_predictions.jsonl` | A P0 四模式预测表格式；示例选择 global=3.0 |
| `synthetic_dataset.jsonl` | 仅用于 dataset fingerprint 绑定的合成字节 |

```powershell
python -m amazon_eval_agent preflight --config .\examples\batch1_synthetic\evaluation_config.json `
  --bind dataset_normalized=.\examples\batch1_synthetic\synthetic_dataset.jsonl `
  --bind task_manifest=.\examples\batch1_synthetic\task_manifest.json `
  --bind labels=.\examples\batch1_synthetic\local_labels.json `
  --bind run_candidate=.\examples\batch1_synthetic\run_candidate.json `
  --bind baseline_predictions=.\examples\batch1_synthetic\baseline_predictions.jsonl
```

这不是真实模型成绩；不要把这些数字用于排名或对外宣称。
