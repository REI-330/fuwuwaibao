# 图谱 + LLM-wiki 技术方案调研

- 调研时间：2026-09-15（所有 URL 的可达性均于当日实测）
- 调研人：Agent（离线流水线维护方）
- 目的：为知识库从「4 职业 / 25 技能 / 22 来源」扩到「10 职业 / 100+ 技能 / 40+ 来源」找可落地的外部方法论，避免闭门造车；每条方案都要说清**落到本项目的哪一层**（L0 证据 / L1 图谱 / L2 Wiki / 检索 / MCP）。

## 0. 本机网络约束（决定了调研能取哪些证据）

GitHub、Google、Wikipedia/Wikidata、HuggingFace 在本机网络下**不可达**，因此本次调研不引用 GitHub 仓库页，改用同等效力的可达证据源（arXiv 摘要页 + PyPI 包元数据 + 官方文档站）。

| 目标 | 结果 | 说明 |
|---|---|---|
| `https://api.github.com/rate_limit` | 000 | 连接失败（DNS 服务器为校园网 ns1.zust.edu.cn，GitHub 解析不通） |
| `https://www.google.com` | 000 | 同上 |
| `https://en.wikipedia.org` / `wikidata.org` | 000 | 同上，故 Wikipedia 编辑机制**未核验**、不作为引用 |
| `https://arxiv.org/abs/…` | 200 | 论文摘要可达 |
| `https://pypi.org/pypi/<pkg>/json` | 200 | 包版本号与仓库指向可达 |
| `https://gitee.com/api/v5/search/...` | 200 | 国内镜像检索可达（本次未用作证据） |
| `https://www.onetonline.org/link/summary/…` | 200 | 职业-技能结构化页可达 |
| `https://www.onetcenter.org/dl_files/database/db_29_0_text.zip` | 200 / 9,233,881 B | O*NET 数据库文本包可下载 |
| `https://esco.ec.europa.eu/en` | 200 | 欧盟 ESCO 门户可达 |
| `https://deepwiki.com` | 200 | 自动 wiki 形态可参考 |

复现命令：`curl -s -o /dev/null -w "%{http_code}" --max-time 20 <url>`；页面可解析性统一用 `node knowledge/pipeline/probe-url.mjs <url>` 判定。

## 1. 采纳的方案（6 条）

### 1.1 Microsoft GraphRAG — 社区检测 + 社区摘要（全局检索通道）

- 证据：`From Local to Global: A Graph RAG Approach to Query-Focused Summarization`，arXiv:2404.16130（v2，2025-02-19 修订），https://arxiv.org/abs/2404.16130；实现包 `graphrag 3.1.2`（PyPI 元数据指向 microsoft/graphrag），https://pypi.org/pypi/graphrag/json
- 机制：把语料抽成实体图后做**社区检测**，为每个社区写摘要；回答「整个语料的主题是什么」这类全局问题时，用社区摘要做 map-reduce，而不是去检索相似段落（论文明确指出 RAG 在 global 问题上失效）。
- 落到本项目：**L1** 增加 `community` 划分产物（能力域社区 + 中心度）；**L2** 每个社区一页「域级摘要页」；**MCP** 增加全局检索通道（当前 3 个工具只有局部检索 + 图推理，全局问题只能靠人肉翻目录）。
- 现状差距：知识库已有 4 个 `domain` 节点与 `belongs_to` 边，但没有社区检测、没有域级摘要，`search_career_knowledge` 对「整个知识库覆盖哪些主题」这类问题只能返回零散节点。

### 1.2 LightRAG — 双层检索 + 增量更新

- 证据：`LightRAG: Simple and Fast Retrieval-Augmented Generation`，arXiv:2410.05779（v3，2025-04-28 修订），https://arxiv.org/abs/2410.05779；实现包 `lightrag-hku 1.5.7`，https://pypi.org/pypi/lightrag-hku/json
- 机制：检索分**低层（实体/具体概念关键词）**与**高层（主题/全局关键词）**两条通道，再合并上下文；并强调新数据到来时**增量更新图**而非重建。
- 落到本项目：**MCP 检索打分**改为双层——低层保留现有「节点 label/alias/id/desc + chunk 子串」打分，高层新增「能力域/趋势/标准节点」的主题层打分；**L0→L1** 的增量语义对应「新增来源时只追加 S## 与 chunk，不动既有节点 id」。
- 现状差距：`mcp/career-graph-tools.ts` 的 `scoreNode` 只做低层匹配（2 字滑窗 + 字段权重），没有任何主题层；`type: trend` / `standard` 节点在检索里几乎不出现。

### 1.3 KAG — 数值、时间与规则要显式建模

- 证据：`KAG: Boosting LLMs in Professional Domains via Knowledge Augmented Generation`，arXiv:2409.13731（v3，2024-09-26 修订），https://arxiv.org/abs/2409.13731
- 机制：论文点出「向量相似度 ≠ 知识推理相关性」，专业领域里**数值、时间关系、专家规则**必须显式表达，否则检索解不了。
- 落到本项目：**L1 边字段**继续走数值化（`requires.importance` / `targetLevel`、`emerging_in.year`），新增边一律带结构化字段而不是只写自然语言；**L2** 页面上数值直接展示，不用形容词。
- 现状差距：已有 9 类边中 `prerequisite` / `belongs_to` / `uses` 没有任何字段，无法表达强度或层级深浅。

### 1.4 Zep / Graphiti — 双时间戳与「失效而不删除」

- 证据：`Zep: A Temporal Knowledge Graph Architecture for Agent Memory`，arXiv:2501.13956（2025-01-20），https://arxiv.org/abs/2501.13956；实现包 `graphiti-core 0.30.2`（PyPI 元数据指向 getzep/graphiti），https://pypi.org/pypi/graphiti-core/json
- 机制：每条事实边带**两个时间**——事实在现实中生效的时间（valid time）与被系统写入的时间（ingestion time）；新事实到来时旧边被标记失效而不是物理删除，因此可以回答「某时间点这条边成立吗」。
- 落到本项目：**L1 全部边**可选带 `validFrom` / `validTo` / `ingestedAt`；新增 `supersedes` 边类型表达「新来源更新了旧结论」；**L2** 页面保留修订记录。
- 现状差距：现有边的 `sourceRefs` 只有空间维度（哪段原文），没有时间维度；`trend` 节点的 `year` 是唯一的时间信息。

### 1.5 Graph RAG 综述 — 检索设计空间的自查框架

- 证据：`Graph Retrieval-Augmented Generation: A Survey`，arXiv:2408.08921（v2，2024-09-10 修订），https://arxiv.org/abs/2408.08921；`Retrieval-Augmented Generation with Graphs (GraphRAG)`，arXiv:2501.00309（v2，2025-01-08 修订），https://arxiv.org/abs/2501.00309
- 机制：把 GraphRAG 拆成「图索引 → 图检索器 → 生成器」三段，并给出每段的设计维度（图类型、检索粒度、是否多跳、是否用文本单元作为最终证据）。
- 落到本项目：不改产物，作为 **检索章节的设计自查表**——我们最终仍以 L0 原文 chunk 作为返回证据（不返回模型改写），这一点与综述里「文本单元作为最终证据」的做法一致。

### 1.6 O*NET + ESCO — 职业-技能词表与层级/等价关系

- 证据：O*NET OnLine 职业摘要页（例：Robotics Engineers `17-2199.08`，实测 570 blocks / 34,038 字符正文），https://www.onetonline.org/link/summary/17-2199.08 ；O*NET 数据库文本包 9,233,881 B 可下载，https://www.onetcenter.org/dl_files/database/db_29_0_text.zip ；许可 CC BY 4.0（既有 S16/S17 已按此登记）。ESCO 门户 https://esco.ec.europa.eu/en
- 机制：O*NET 给出职业↔技能/知识/能力的**结构化清单 + Importance(1–5) / Level(1–7) 数值**；ESCO 给出 occupation–skill/competence–qualification 三层词表，并把技能分为 essential / optional，同时给出跨职业的等价技能。
- 落到本项目：**L1** 新增职业的 `requires` 边直接引用 O*NET 的数值作为 `importance` / `targetLevel` 依据（避免自己拍脑袋）；借鉴 ESCO 的分层与等价概念，新增 `part_of`（技能层级）与 `equivalent_to`（跨职业可迁移技能）两类边。
- 现状差距：现有 28 条 `requires` 边的 importance 值来源不可复查；技能之间只有线性的 `prerequisite`，没有层级与跨方向等价关系。

## 2. 参考但未采纳

| 方案 | 证据 | 不采纳的原因 |
|---|---|---|
| Wikipedia / MediaWiki 编辑机制 | en.wikipedia.org、wikidata.org 均 000 | 不可达，无法核验；本项目已有「草稿 / 已复核 / 已裁定」三态，不引入未验证断言 |
| DeepWiki 自动 wiki 形态 | https://deepwiki.com（200） | 形态可参考（每页带来源定位与更新时间），但它是代码库 wiki；知识库的 L2 契约已覆盖该形态，不单独登记为来源 |
| `nano-graphrag 0.0.8.2` | https://pypi.org/pypi/nano-graphrag/json | 轻量实现，机制未超出 1.1；避免堆方案 |
| MLflow / nuScenes / ROS 2 文档站 | probe 结果：0 blocks 或 0 字节（JS 壳、反爬） | 不符合「服务端直出正文」的准入条件，写入 `sources.candidates.json` 的 `probe.rejected` |

## 3. 本次调研直接导出的扩充动作

1. 词表 v2：新增节点类型 `standard`（标准与政策）、`assessment`（考核点）；新增边类型 `part_of`、`equivalent_to`、`assessed_by`、`governed_by`、`supersedes`；所有边可选带 `validFrom` / `validTo` / `ingestedAt`。
2. 来源扩充：按 §1.6 的证据源把职业从 4 个扩到 10 个（新增：大模型应用、AI 平台/MLOps、具身智能、智能驾驶感知、AI 数据与标注质量、AI 安全与合规），每个新职业至少 2 份可达来源。
3. 图谱扩充：技能节点规模上探，每条新增 `requires` 边的数值都要能指回 O*NET 或官方文档原文。
4. 产物新增：能力域社区（GraphRAG 式）+ 技能-来源覆盖报告（哪些技能没有来源支撑，显式标记而不是隐藏）。
