# 评测 Agent 可用数据集与许可边界

核对日期：2026-10-14。本轮只核对发布说明，未下载新数据、接受条款或发送评论给外部模型。

| 数据集 | 适合做的评测 | 不适合的推断 | 状态与权限 |
| --- | --- | --- | --- |
| 手写合成 fixture | 数学手算、结果合同、异常/预算/记忆隔离测试 | 真实模型水平或 Memory 收益 | 离线使用；必须标 fake/synthetic |
| Amazon 2014 乐器 5-core | 评分、风格代理、时间协议、四基线 | 商品事实真假、真实购买经历 | A 本地核验 10,261 行/400 目标；本项目复用待接入；许可未明确核实 |
| Amazon 2018 Software | 第二领域评分与评论复现 | 仅凭“5-core”就认定时间与重复用户机会合格 | 官方列 12,805 行；adapter/许可待确认 |
| MovieLens 100K | 偏好评分、冷启动、历史事件检索 | 没有正文，不能衡量评论风格 | 100,000 评分；研究/引用要求，商业和再分发需额外许可 |
| Goodreads Book Graph | 阅读评分、长文本风格候选 | 评论等同客观书籍事实 | 官方仅学术使用，禁止再分发和商业用途；需小子集 adapter |
| Yelp Open Dataset | 商家评论领域候选 | 旧课程资产自动代表新版可用 | 官方入口本次 403；数据集协议未核验 |
| Amazon Reviews 2023 乐器 | 新版领域、细粒度时间与元数据扩展 | 抓取时平均评分/元数据等同历史可见事实 | 约 300 万乐器评论；许可、采样与扩容待做 |
| UCI Online Retail | 购买/取消行为和记忆检索诊断 | 无评论/评分，不可直接算评论任务的 RMSE | 541,909 交易记录；CC BY 4.0；需独立事件 adapter |

## 选择原则

先把现有 Amazon 2014 乐器的 A→B 互通与评测完成，再选择第二领域。两个项目可共享获准本地数据，**不共享目标标签的模型可见视图**：A 只见安全历史，B 末端评分器读取标签。

公开下载、当前代码支持、可以发给第三方，是三个不同问题。第三方平台的代码 MIT/镜像 CC0 标记不能自动证明原始评论使用权。

大文件不能通过调高配置取消既有 64 MiB/10 万行资源门槛。MovieLens 随机划分、2023 毫秒时间与 parent_asin、累积 helpful/vote/average_rating，都需要新协议核对，不能直接套用旧字段映射。

## 官方来源与核对结论

1. [Amazon 2014](https://cseweb.ucsd.edu/~jmcauley/datasets/amazon/links.html)：小文件和字段已核对；明确开放许可未核实。
2. [Amazon 2018](https://cseweb.ucsd.edu/~jmcauley/datasets/amazon_v2/)：官方 Software 小文件统计可见；尚不代表已接入。
3. [Amazon 2023](https://amazon-reviews-2023.github.io/) / [数据卡](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023)：类别与字段可见；许可不作开放授权推定。
4. [MovieLens 100K](https://grouplens.org/datasets/movielens/100k/) / [README 许可](https://files.grouplens.org/datasets/movielens/ml-100k-README.txt)：研究用途、需引用，不得无额外许可再分发或商业使用。
5. [Goodreads Book Graph](https://cseweb.ucsd.edu/~jmcauley/datasets/goodreads.html)：academic use only，不得再分发或商业使用。
6. [Yelp](https://www.yelp.com/dataset)：403，协议待人工核查；不以通用网站服务条款代替数据集协议。
7. [UCI Online Retail](https://archive.ics.uci.edu/dataset/352/online+retail)：明确 CC BY 4.0，按要求署名；不代表个人信息可任意公开。

原始文件、评论、标签和实验产物只放获准 E 盘目录；仓库只保留工具、配置示例、合成 fixture 与来源说明。任何模型调用或数据外发仍先征得确认。
