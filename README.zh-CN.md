# Amazon 乐器评论评测与诊断 Agent

[English](README.md) | [简体中文](README.zh-CN.md)

这是用户模拟项目的**评测侧（项目 B）**。项目 A 负责生成 Amazon 乐器用户的预测评分和评论；这个项目负责检查预测到底好不好、错在哪里，以及现有证据能支持什么结论。

它不是评论生成器，也不是第二个 Amazon 购物 Agent。

## 0. 引用与致谢

这个项目建立在几个彼此独立的工作之上：

- **项目 A：[LLM 用户模拟 Agent](https://github.com/simonshi-stu/llm-user-simulation-agent)。** 同一个研究项目的生成侧。A 产出模拟评分和评论，B 通过版本化结果契约进行独立评测。
- **[AgentSocietyChallenge](https://github.com/tsinghua-fib-lab/AgentSocietyChallenge)**（清华大学 FIB Lab，MIT License）。项目 A 使用其 PyPI 包作为模拟框架；这个评测仓库**没有**复制或导入该框架。框架作者没有审阅或背书本项目。
- **Amazon Product Data（2014）Musical Instruments 5-core 评论**，由加州大学圣地亚哥分校 Julian McAuley 团队维护，数据入口在[这里](https://cseweb.ucsd.edu/~jmcauley/datasets/amazon/links.html)。本仓库不分发原始评论、目标标签或私有实验文件。“网上可以下载”不等于“可以自由再分发或商用”，使用前请核对原始数据页面和适用条款。
- **GitHub Pages** 托管纯静态合成演示，不对外提供数据集或模型服务。

以上个人和机构提供了数据、框架代码或托管能力，但没有参与本项目的评测设计，也不为项目结论背书。

## 1. 这个项目有什么用？为什么需要它？

模拟用户完全可以写出一段听起来很真实的评论，同时把 2 星写成 5 星、大段复制参考评论、跑到一半失败，或者在 Memory 根本没进入 Prompt 时宣称“记忆带来了提升”。

这些是不同的问题。一个准确率数字会把它们全部盖住。

所以这个项目把“生成”和“评测”分开：

- **能算的交给工具算。** 评分误差、±1 星命中、覆盖率、格式失败、重试、回退、Prompt 实际纳入情况、耗时。
- **公平比较。** 两种方法必须在同一批任务、同一批用户上比较，不能各自挑自己做完的那部分。
- **把分母摆出来。** 12 个任务里只成功 3 个的方案，不应该因为那 3 个简单任务误差低就显得更好。
- **解释必须跟着证据走。** 后续可以让模型解释工具结果，但不能自己发明数字、引用或因果结论。
- **证据不足就说不知道。** 没有共同任务、用户 cluster 太少、没有真实运行，就应该老实写“无法判断”，而不是画一张自信的图。

它也不是为了做成“多 Agent 炫技”。第一版是**工具增强的固定工作流**：Python 负责计算，LLM 以后只负责解释，人负责批准。当前阶段不需要 LangChain、LangGraph 或向量数据库。RAG 和持久 Memory 更重要的是生成侧，而且必须先做受控对照，才能说有没有收益。

## 2. 用在什么场景？流程怎么走？

一次典型的评测是这样的：

```text
A 产出一份运行结果
        ↓
B 检查结果契约和声明的来源
        ↓
B 计算指标，把成功 / 回退 / 失败 / 拒绝分开
        ↓
B 只在共同有效任务上比较不同方案
        ↓
B 把数值、分母、覆盖率、限制整理成证据包
        ↓
解释层可以描述证据，但不能重算或编造
        ↓
人工审阅并批准报告
```

几个具体场景：

- **“新 Prompt 有没有用？”** 在相同任务上跑旧版和新版，比较 MAE、RMSE、±1 星、偏差、失败率和覆盖率。只在更少的成功样本上误差更低，不等于整体更好。
- **“Memory 有没有用？”** 先确认记忆真的进了 Prompt，再做同任务的有／无记忆配对比较。模式名称本身不是收益证据。
- **“能不能用在没见过的用户身上？”** 留出一批从未参与 Prompt 调优的用户做独立测试，老用户的未来行为和新用户泛化要分开报告。
- **“报告为什么这么说？”** 每条结论都要能点回具体指标、样本量、分母和限制。

在线 Demo 用三个小型合成场景讲这件事：

- 在共同任务上做公平比较；
- 一个“误差很低”的方案，其实是因为大部分失败被排除在分母外；
- 没有共同成功任务时，正确答案是“无法比较”。

## 3. 项目目前做到哪一步？

**已经能用**

- 严格的 `simulation_run.v1` 结果校验器和独立命令行入口。检查结构、资源上限、状态一致性、诊断不变量和敏感字段，同时不回显评论正文或文件路径。
- GitHub Pages 在线合成 Demo：[打开演示](https://simonshi-stu.github.io/amazon-instruments-evaluation-agent/)。
- 确定性的 Python 示例评分：MAE、RMSE、±1 星、偏差、覆盖率、共同任务比较；没有共同任务时拒绝比较。
- 一份精简的中文面试／决策记录，覆盖方法选择、Prompt 变化、幻觉检查、评测设计、Memory 和泛化：[docs/INTERVIEW_NOTES.zh-CN.md](docs/INTERVIEW_NOTES.zh-CN.md)。
- 本轮验证：**99 项测试通过，3 项跳过**。跳过的是需要项目 A 新版契约镜像的跨仓检查，原件目前缺失。A 的公开 `main` 源码已恢复，但不能拿公开旧版冒充遗失的新版。

**还没有完成**

- 生产评测管线还没有接真实运行结果。现在的评分和比较在合成 Demo 里。
- 模型解释层、结论与证据校验、不确定性区间、人工批准导出都还在后续阶段。
- 本轮没有跑真实 LLM，没有人工风格评审，没有验证 Memory 收益，也没有新用户留出集成绩。
- Demo 里的图证明的是评测逻辑，不是模型质量。页面会明确写这一点，不宣称真实准确率。

一句话：**契约和透明的合成 Demo 已经能用；完整的评测诊断 Agent 还没完成。**

## 4. 怎么运行和部署？

没有插件，也没有后端服务。公开 Demo 是静态网页；本地工具是一个 Python 命令行和标准库包。

### 直接看在线 Demo

打开：[https://simonshi-stu.github.io/amazon-instruments-evaluation-agent/](https://simonshi-stu.github.io/amazon-instruments-evaluation-agent/)

选择场景，查看指标，点开每条结论对应的证据，也可以下载合成演示报告。页面不会上传文件，也不会调用模型。

### 在本地校验结果文件

需要 Python 3.10 或更新版本。校验器本身只用标准库。

```powershell
python -m amazon_eval_agent validate --check
python -m amazon_eval_agent validate --input "<结果文件绝对路径.json>" --json
```

退出码 `0` 表示结果文件**符合契约**，不代表预测准确。合法文件里仍可能有失败、拒绝或回退记录，报告会分别显示。

### 在本地跑测试和 Demo

```powershell
python -m pip install pytest==8.3.5
python -m pytest -q -rs

python -m amazon_eval_agent.demo --output _site
python -m http.server 8193 --bind 127.0.0.1 --directory _site
```

浏览器打开 `http://127.0.0.1:8193/`，按 `Ctrl+C` 停止。Demo 构建只复制三个白名单静态文件和生成的合成报告，不读取私有数据目录。

### 部署自己的副本

仓库里的 `.github/workflows/pages.yml` 会在 `main` 更新时运行测试、构建 `_site` 并部署到 GitHub Pages。Fork 之后的步骤：

1. 把仓库推到 GitHub。
2. 打开 **Settings → Pages**，把 Source 选成 **GitHub Actions**。
3. 推送到 `main`，或者手动运行 **Synthetic evaluation demo** workflow。

不需要 API key、数据库或 LLM 接口。这是有意的：公开 Demo 证明评测链路，不是付费模型的成绩单。

真实评论、目标标签、私有运行结果、Prompt 和实验报告都不进仓库。任何真实模型调用或数据外发，都需要单独确认范围、预算和权限。

## 5. 继续阅读

- [开发计划与评测边界](docs/DEVELOPMENT_PLAN.md)
- [数据集与许可核对](docs/DATASETS.md)
- [中文面试与决策记录](docs/INTERVIEW_NOTES.zh-CN.md)
- [包内契约细节](amazon_eval_agent/README.md)
- [项目 A：生成侧](https://github.com/simonshi-stu/llm-user-simulation-agent)

如果只记住一句话：**这个项目的价值不是做一张更漂亮的排行榜，而是让模拟用户 Agent 的说法可以被检查、被公平比较，并且在证据不足时诚实地说“不知道”。**
