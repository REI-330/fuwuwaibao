# 职业导航知识库设计：图谱 + Wiki 三层架构

> 状态：**§1–§7 已由离线流水线实现**（真实资料已收集：26 份来源 / 1757 chunk；导出 `knowledge/exports/career-graph.json`，kbVersion `2026.09.15`、graphVersion `0.1.0`、status `pipeline-export`，含 63 节点 / 226 边 / 38 Wiki 页 / 24 道评测题；检索与评测结果属**原型模拟**水平，命中率仍待改进）；**§8 MCP 工具契约已实现并实际验证**（stdio 与 Streamable HTTP 双传输，见 §8.2 与 §12）；**§9 前端图谱视图已实现并实际验证**。本文档描述目标结构与契约，实现进度以 `knowledge/evaluations/` 和各步骤产出为准。
>
> 已确定的两个方向性决定（均取 A 方案）：
> 1. **Wiki 页生成**：离线 LLM 编译 + 人工审核为主，`llm-draft` 兜底。
> 2. **主检索归属**：自建检索与 MCP 服务为主，百宝箱企业版知识库作为对照实验。
>
> 依据：`赛题要求/【A02】...docx` 企业专项材料 ②③④ 与早期范围规划。
>
> 与早期范围规划的关系：首版规模与知识层字段表由早期规划给出，本文档在其基础上补齐**图谱契约、Wiki 契约、检索路由、动画匹配**四项它未展开的内容。两者不冲突，冲突处以本文档为准。

---

## 0. 范围与规模

| 维度 | 首版目标 | 说明 |
|---|---|---|
| 职业方向 | 4 个 | 嵌入式开发、机器视觉、边缘 AI、测试自动化 |
| 技能节点 | 20–40 条 | 共享，不按方向重复 |
| 来源资料 | 15–25 份 | 可引用的真实资料，登记到 `sources` |
| 知识分块 | 按主题分块，记录实际块数 | 不为凑数复制填充 |
| Wiki 页 | 4 方向约 30–40 页 | 分级审核，见 §4 |
| 评测问题 | 24 题，四类各 6 | 与演示提问分开 |
| MCP 工具 | 2–3 个只读工具 | 见 §8 |

**不做**：大规模图数据库、实时招聘抓取、通用 OCR、全职业自动规划。这些在报告中列为后续开发项。

---

## 1. 为什么是三层，而不是一套 RAG

比赛里最常见的做法是"把资料切块塞进向量库"。这个做法在本赛题下有三个说不通的地方：

1. **说不清来源**。赛题要求"引用"与"可追溯"，分块检索的中间态若不落库，报告里写不出引用支持度。
2. **先修关系会靠模型编**。`career-path.ts` 里已有 `prerequisite_ids`、`prerequisite_depth`、`priority_score`、`gap` —— 这些是**算出来的**，不是"找出来最相似的段落"。检索解不了图推理。
3. **评测无从下手**。没有确定性的中间结构，24 题评测只能靠人工看回答像不像。

因此三层分工，各司其职：

| 层 | 存什么 | 谁消费 | 不可变的性质 |
|---|---|---|---|
| **L0 证据层** | 原文、分块 chunk、来源登记 | 引用追溯、评测标注 | 只增不改；改则新增版本 |
| **L1 图谱层** | 实体节点 + 有类型的边 | 推荐理由、技能差距、先修顺序、路径、MCP | 每条边挂 `sourceRefs[]` |
| **L2 Wiki 层** | 每实体一页 Markdown | 聊天问答、节点解释、目录详情 | **只投影 L1 的边与 L0 的原文，不产生新事实** |

**L2 不产生新事实**是整套设计的防幻觉核心。它把 LLM 的错误半径从"事实错误"压缩到"表达错误"，也是报告里"AI 伦理设计"章节最实在的一条机制。

---

## 2. L0 证据层

### 2.1 来源登记

每份资料一条记录，字段见 `exports/career-graph.json` 的 `sources`：

```
sourceId, title, publisher, url, publishedAt, collectedAt, license, scopeZh, sha256, chunkCount
```

- `publishedAt` 与 `collectedAt` 必须分开。市场/趋势类结论若无 `publishedAt`，前端显示"缺少时间信息"，不猜。
- `sha256` 用于去重与版本判定，同一 URL 内容变化视为新版本。
- `scopeZh` 写清"这份资料支持哪些结论"，避免超范围引用。

### 2.2 分块

```
chunkId, sourceId, heading, sectionPath, charRange, text, tags[], version
```

- `sectionPath` 保留原文层级（如 `2.1 > 岗位要求`），是"原文位置"可追溯的最小保证。
- 分块不重写、不摘要替代原文。摘要另存 `summary` 字段，原文 `text` 保留。
- `tags[]` 只放主题词，不放推断结论。

---

## 3. L1 图谱层

### 3.1 节点

8 类 `kind`。**kind 取值必须与前端 `GraphNode.kind` 字面量一致**，因此这里用 `knowledge` 而不是 `knowledge_unit`：

| kind | 示例 id | id 前缀 | 说明 |
|---|---|---|---|
| `occupation` | `occupation:AI001` | `occupation:` | 职业 |
| `skill` | `skill:SK215` | `skill:` | 技能；含 `aliases` 供实体识别 |
| `knowledge` | `knowledge:AI001:stage1` | `knowledge:` | 学习单元 |
| `task` | `task:T0042` | `task:` | 训练任务，映射 `/actions` |
| `tool` | `tool:freertos` | `tool:` | 工具。**与技能是不同节点，不表示已掌握** |
| `trend` | `trend:edge-ai-2026` | `trend:` | 趋势信号 |
| `credential` | `credential:cert-embedded` | `credential:` | 认证 |
| `domain` | `domain:embedded` | `domain:` | 能力域。`belongs_to` 的指向目标，用于目录聚类与图谱分组 |

> **前端限制（需处理）**：`lib/client/career-graph.ts` 的 `GraphNode.kind` 当前只声明 `"occupation" | "skill" | "knowledge" | "tool"` 四种。新增 `task` / `trend` / `credential` / `domain` 时必须同步拓宽该联合类型，否则 `buildCareerGraph` 无法承载。未拓宽前，图谱导出里这四类节点对前端不可见（对 MCP 可见）。

节点公共字段：

```json
{
  "id": "skill:SK215",
  "kind": "skill",
  "label": "模型量化与部署",
  "aliases": ["模型量化", "INT8 量化"],
  "description": "……",
  "x": 510, "y": 300,
  "sourceRefs": ["S03#2.1", "S07#sec-4"]
}
```

`x` / `y` 是**预算好的确定性布局坐标**，由构建流水线第 10 步产出并版本化。理由：录制视频与截图需要稳定，用户也需要靠空间位置建立记忆。**不要每次渲染跑力导向布局。**

### 3.2 边

统一结构：

```json
{
  "id": "e-0007",
  "type": "prerequisite",
  "from": "skill:SK101",
  "to": "skill:SK215",
  "weight": 0.8,
  "sourceRefs": ["S07#sec-4"],
  "annotatedBy": "human | llm-reviewed | llm-draft",
  "updatedAt": "2026-07-28"
}
```

八种边：

| type | from → to | 附加字段 | 消费方 |
|---|---|---|---|
| `requires` | occupation → skill | `targetLevel`(1-5), `importance`(0-1) | `/growth` 推荐理由、技能差距 |
| `prerequisite` | skill → skill（有向无环） | — | `/path` 学习顺序与周数 |
| `belongs_to` | skill → domain | — | `/catalog`、图谱聚类 |
| `trains` | task → skill | `deliverable`, `assessmentPoints[]` | `/path` → `/actions` |
| `uses` | occupation → tool | — | `/work-map`，现有 `buildCareerGraph` 直接可画 |
| `transitions_to` | occupation → occupation | `horizon`(年), `deltaSkills[]` | 3-5 年职业路径（赛题硬指标） |
| `emerging_in` | trend → skill | `year`, `direction` | "未来工作"叙事 |
| `evidenced_by` | 任意 → chunk | — | **全站"查看依据"入口** |

**`evidenced_by` 是全站可追溯的支点。** 任何前端上显示的关系，都要能沿它回到 L0 原文。

> **`evidenced_by` 的 `to` 是 chunk 引用，不是节点 id**，统一写成 `chunk:<chunkId>`（如 `chunk:S03#2.1`）。这类目标**不是图节点**，没有 `x`/`y`，因此：
> - 前端可视化必须过滤掉两端不都是真实节点的边（现有 `knowledge-graph.tsx` 的 `if (!from || !to) return null` 已经天然做到了）。
> - `graph-view.ts` 的邻接表构建也要显式排除 `chunk:` 目标，否则会产生没有位置的幽灵节点并污染 BFS 跳数。

### 3.3 三条硬约束

1. **每条边必须有 `sourceRefs`，且指向真实存在的 `chunkId`。** 构建流水线第 9 步做完整性校验，缺失即构建失败。
2. **`annotatedBy` 必须标注**。人工裁定、LLM 抽取后经人工审核、LLM 直接抽取三者视觉与检索行为都不同（见 §9.4）。
3. **先修关系必须无环。** `career-path.ts` 里已有 `prerequisite_cycle` 硬校验位，图谱导出时做同样的检查，环即失败。

---

## 4. L2 Wiki 层

### 4.1 页面契约

```markdown
---
entityId: occupation:AI001
entityType: occupation
title: 嵌入式开发工程师
aliases: [嵌入式开发, MCU 开发, 固件工程师]
version: 2026-07-28
review: reviewed          # reviewed | llm-draft | runtime
reviewedBy: human
sources: [S03#2.1, S07#sec-4]
---

## 一句话定义
## 典型任务
## 需要的能力
[[skill:SK101]] [[skill:SK215]]        ← 来自 requires 边，不新增
## 先修提示
从 [[skill:SK090]] 起步                  ← 来自 prerequisite 边
## 常见误区
## 相邻职业
[[occupation:AI004]]                     ← 来自 transitions_to 边
## 引用来源
- S03#2.1 …（可点开原文）
```

两条规则：

- **`[[entityId]]` 内链是 L1 边的自然语言投影。** 正文里出现的每个内链，都必须能在图谱里找到对应边；反之，图谱里该实体的边都应能在页面上体现。这既是防幻觉机制，也是前端"图谱动画匹配"能成立的前提（见 §9）。
- **`aliases` 是实体识别的确定性入口。** 查询时先查别名词典做实体归一，不依赖模型猜。

### 4.2 分级审核（A 方案落地）

| 层级 | 范围 | 处理 | review 标记 |
|---|---|---|---|
| 全审 | 会被推荐理由、技能差距、任务直接引用的实体 | 逐页人工过 | `reviewed` |
| 抽审 | 其余技能/工具节点 | LLM 生成，抽查 ≥20% | `llm-draft` |
| 即时 | 未编页实体 | 运行时生成，界面明确标注 | `runtime` |

工作量估算：30–40 页 × 每页 3–5 分钟审核 ≈ **2–3 小时**。这是可接受的成本，不是"几天"。

报告里应同时给出两个数字：**已审核覆盖率** 与 **未审核内容的降权策略**。后者见 §5。

### 4.3 铁律

Wiki 页**只能投影 L1 已有的边与 L0 已有的原文**。不得引入图谱里不存在的关系，不得给出 L0 支持不了的结论。违反此条的页面在审核时应被驳回，驳回记录本身就是材料 ② 的证据。

---

## 5. 检索路由

单路向量检索会让"结构化问题"和"解释性问题"互相污染。改为四路路由：

```
query
 ├─ ① 别名词典实体识别（确定性，不调模型）
 ├─ ② 意图分类（规则优先，未命中再交小模型）
 │
 ├─ 结构化问题（差距 / 顺序 / 要求）→ 图查询，代码算，LLM 只负责措辞
 ├─ 解释性问题（为什么 / 是什么）→ 命中 wiki 页 + 一跳邻居 → LLM + 强制 citations
 ├─ 事实查找（具体数据 / 定义）→ BM25（可选叠加向量）命中 chunk → 抽取式返回 + citations
 └─ 无法归类或无依据 → 明确"资料不足"并追问
```

三个要点：

1. **结构化问题不经过模型推理。** "我缺哪些技能、先学哪个、要几周"由 §3 的边加上 `career-path.ts` 已有规则算出，模型只把结果组织成话。这直接消除了一整类幻觉。
2. **引用是强制的，不是建议的。** 回答里每个关键结论必须挂 `evidenced_by` 指向的 chunk；挂不上的结论不允许输出。
3. **`llm-draft` 页降权。** 检索命中 `llm-draft` 页时权重下调（建议 ×0.6）并在 UI 标注"该内容尚未人工审核"。`runtime` 页不进检索索引，只作实时解释。

版本化：每次检索结果记录 `graphVersion` + `kbVersion` + `modelVersion`，这是"效果评估报告"能复算的前提。

---

## 6. 构建流水线

```
01 来源登记            → sources.json（sourceId、sha256、scopeZh）
02 抓取与去重          → raw/（按 sha256 判重）
03 主题分块            → chunks.jsonl（sectionPath 保留原文层级）
04 LLM 抽取候选实体/关系 → candidates.jsonl（每条带 chunkId，annotatedBy=llm-draft）
05 术语归一与消歧      → aliases.json（同一技能的不同叫法合并）
06 人工裁定            → accepted.jsonl（accept/reject/edit，留操作日志）
07 LLM 编译 wiki 草稿   → wiki/*.md（review=llm-draft）
08 人工审核            → wiki/*.md（review=reviewed，记录驳回理由）
09 完整性校验          → 边无 sourceRefs 即失败；先修环即失败
10 布局与索引          → graph.json（含确定性 x/y）+ 检索索引
11 评测与导出版本化     → exports/graph.v0.1.json、evaluations/
```

每一步的**命令、输入、输出、条数**都记录下来，直接构成材料 ② 的"构建方法"部分。这比贴几张截图说服力高得多。

**注意 06 不可省。** 它是 `llm-draft` → `llm-reviewed` 的唯一通道，也是"人在回路"的实证。

---

## 7. 效果评估

24 题，四类各 6 题：职业要求 / 技能差距与先修 / 情境建议 / 超范围与信息不足。

| 指标 | 定义 | 前提 |
|---|---|---|
| 检索命中率 | 可回答问题中 Top-3 是否含标注的相关 chunk，报分子分母 | **必须能拿到检索中间态** → 见 §10 决定二 |
| 引用支持度 | 逐条人工判：回答的关键结论是否被引用内容支持 | 需 citations 落库 |
| 信息不足处理率 | 无依据问题是否明确说资料不足并追问，而非编造 | 需 `runtime` 页与追问路径 |
| 平均耗时 | 记录实际调用耗时；无模型调用时只报检索耗时 | — |

**消融对比**（自建侧为 A 方案，所以这组实验做得了）：

| 配置 | 目的 |
|---|---|
| BM25 only | 基线 |
| + 图遍历（一跳邻居） | 图结构是否带来增益 |
| + wiki 页（reviewed） | Wiki 层是否带来增益 |
| + wiki 页（含 llm-draft 降权） | 降权策略是否有效 |
| 全量 | 完整链路 |

固定模型、知识库版本、提问集、参数，保存原始输出。小样本不作普遍准确率承诺。**保留失败案例、原结果、修改方式与复测结果**——只放成功截图的报告不可信。

---

## 8. MCP 工具契约

依据项目纪律：**不得把普通 HTTP 接口称为 MCP**，必须以 `tools/list` 与 `tools/call` 真实验证。

> **状态：已实际实现并验证（2026-09-15）。** TypeScript MCP server 落在 `frontend/frotent/frontend1/mcp/`，用 stdio 真实跑通了「客户端发现工具 → 调用 → 拿到结果」：
>
> ```bash
> cd frontend/frotent/frontend1
> npm run mcp:verify        # = node --import tsx scripts/verify-mcp.ts
> ```
>
> 复现结果：`tools/list` 返回 3 个工具；3 次 `tools/call` 全部 `isError !== true`，断言全绿、退出码 0；原始 JSON-RPC 结果落盘在 `frontend/frotent/frontend1/evidence/mcp-verify.json`。server 启动日志确认数据源就是 `knowledge/exports/career-graph.json`（`kbVersion 2026.09.15` / `graphVersion 0.1.0` / `status pipeline-export`），与网页同源。

### 8.1 工具清单

| 工具 | 输入 | 输出 | 与百宝箱知识库的关系 |
|---|---|---|---|
| `search_career_knowledge` | `query`, `limit?`, `kinds?` | 命中节点 + 图邻域关系 + chunk 级引用 + wiki 路径 + `dataVersion` | **功能重叠**，用于对照 |
| `get_skill_gap` | `target`, `ownedSkills[]?`, `maxHop?` | 缺口技能、等级差、权重、先修闭包 `blockedBy`、`neededFor`、**有序** `learningOrder`、每步 `citations` | **平台做不了**，自建独有 |
| `get_career_graph_view` | `focusId?`, `maxHop?`, `animate?` | 前端组件真正渲染的视图模型：`focal/hop1/hop2/context/dimmed` 状态、边状态、跳数表、包围盒与相机 | 平台做不了；保证「网页看到的图」与「模型看到的图」是同一份 |

**与初稿的偏差（已落地，需同步材料 ③）**：初稿列了 `get_prerequisite_chain`，实测后把它并进了 `get_skill_gap` —— 先修链单独成工具时，调用方还必须自己再把结果和缺口做一次拼接，反而更容易错；合并后一次调用直接给出「缺口 + 先修闭包 + 拓扑学习序」。空出来的位置给了 `get_career_graph_view`，因为「网页节点状态」与「MCP 返回值」必须是同一份视图模型，这正是铁律②在图上的体现。

`get_skill_gap` 与 `get_career_graph_view` 是 MCP 的存在理由：**百宝箱知识库负责企业级编排与多端发布，自建服务负责可复算的图推理与引用追溯，MCP 是两者的桥。** 这句话建议直接写进材料 ③。

实测样例（`evidence/mcp-verify.json`，dataVersion `kbVersion 2026.09.15` / `status pipeline-export`）：

- `get_skill_gap(target="occupation:AI004", ownedSkills=[])` → `summaryZh`：「职业「边缘 AI 工程师」要求 8 项技能，已具备 0 项，待学 11 项。推荐顺序：1) C 语言与内存模型；2) 数据集格式与标注；3) 张量与自动微分基础；4) 微控制器外设驱动（先补 C 语言与内存模型）；… 11) 端侧推理性能基准测试（先补 轻量级推理引擎集成）。」—— 11 步顺序是沿 `prerequisite` 边做先修闭包后拓扑排序算出来的，每步带 `blockedBy` / `neededFor` / `citations`（引用具体 chunk，如 `S03#s46`），不是关键词排序。
- `search_career_knowledge(query="模型量化")` → `summaryZh`：「在 2026.09.15 里为「模型量化」命中 5 个图谱节点（最高分「模型量化与部署」）、5 段原文」；命中节点 `skill:SK215` 带回 6 条图邻域（`需要技能 → occupation:AI004`、`前置技能 ← skill:SK101`、`属于 → domain:edge-ai`、`涌现于 ← trend:edge-ai-2026` 等）以及 chunk 级引用与出处 URL。
- `get_career_graph_view(focusId="occupation:AI001")` → 63 个节点、117 条可渲染边（226 条里 109 条 `chunk:` 证据边按设计跳过）、`prerequisiteChain: 18`。

### 8.2 技术前提（已满足）

- **MCP SDK 已安装**：`@modelcontextprotocol/sdk@^1.30.0` 已进 `dependencies`；`zod` 也已显式声明（此前只是 SDK 的传递依赖，靠传递依赖跑不通「可复现」这一条）。`tsx` 提升为显式 devDependency。
- **入口脚本**：`"mcp": "node --import tsx mcp/stdio.ts"`（stdio server）、`"mcp:http": "node --import tsx mcp/http.ts"`（HTTP server）、`"mcp:verify": "node --import tsx scripts/verify-mcp.ts"`、`"mcp:verify-http": "node --import tsx scripts/verify-mcp-http.ts"`。
- **stdio 传输**：stdout 只走 JSON-RPC，所有日志走 stderr —— 否则协议流会被日志污染。
- **HTTP 传输（Streamable HTTP）**：`mcp/http.ts` 用 SDK 的 `StreamableHTTPServerTransport` 提供 `POST /mcp`，另加 `GET /health` 探活。同时覆盖有状态与无状态两条路径（有状态时 `initialize` 返回 `Mcp-Session-Id`，用 `Map<sessionId, transport>` 复用；无状态时每请求新建 transport，因为 SDK 明确规定无 `sessionIdGenerator` 时 transport 不能跨请求复用）。工具逻辑零改动，仍是同一个 `createCareerGraphServer(getDefaultStore())`。`npm run mcp:verify-http` 退出码 0（25/25 断言），证据 `evidence/mcp-http-verify.json`。
- **为什么必须补 HTTP**：百宝箱「创建 MCP 服务 → 自部署 MCP」只有 `sse` / `streamableHttp` 两种接入方式（平台页面实测），云端智能体无法 spawn 本机进程，stdio 在架构上不可能被云端接入。
- **公网可达性**：`MCP_HTTP_VERIFY_TARGET=https://<隧道域名> npm run mcp:verify-http` 把同一套 25/25 断言打到公网地址（第三方隧道回源本机 `127.0.0.1:8787`），同样退出码 0，证据 `evidence/mcp-public-verify.json`。**但发起端仍是本机**，这只证明服务在公网上可达，不等于平台已连入 —— 报告措辞不得越过这条线。隧道地址是临时的、会变，只用于取证。
- 前端与 MCP **消费同一份版本化 JSON 与同一套纯函数**，不重复搭两套 RAG。已由验证脚本断言：MCP 结果里出现 `lib/client/graph-view.ts` 的 `RELATION` 中文标签（`需要技能` / `前置技能` / `使用工具` / `学习单元`），说明英文 `type` 确实经过前端的 `relationFromType` 适配，而不是 MCP 侧另写了一套映射。

#### 8.2.1 仍未处理

- **无 `backend/` 目录**：但 `package.json` 的 `dev:backend`、`backend:install`、`test:backend` 三个脚本已假定存在 Python 后端。MCP 语言已定为 TypeScript（见 §11），所以这三个脚本要么补上 Python 后端，要么删掉——**现状是悬空的死脚本**。

---

## 9. 前端图谱动画匹配

目标：让"这个 wiki 页"和"图谱上的这些节点"看起来是同一份知识，而不是两套系统。

### 9.1 契约：`lib/client/graph-view.ts`

```ts
type NodeState = "focal" | "hop1" | "hop2" | "context" | "dimmed";
type EdgeState = "active" | "context" | "dimmed";
type GraphCamera = { tx: number; ty: number; scale: number };

buildGraphView(graph, focusId, { viewport, depth })
  → { focusId, nodes: Map<id, NodeState>, edges: Map<edgeKey, EdgeState>,
      hop: Map<id, number>, camera: GraphCamera, activeNodeIds, activeEdgeKeys }
```

**先算对状态，动画只是状态的视觉投影。** 这个函数是纯的、可单测的，同时也是报告里可展示的确定性算法。

### 9.2 镜头数学

固定 `viewBox`，动画内层 `<g>` 的 `transform`（**不要动画 viewBox**，CSS transition 无法平滑插值它）：

```
屏幕坐标 = (tx + scale * x, ty + scale * y)
聚焦 (fx, fy) 到视口中心： tx = W/2 - scale * fx,  ty = H/2 - scale * fy
```

CSS 需要 `transform-box: view-box; transform-origin: 0 0;` 才能让上式成立。

### 9.3 动画清单

| 效果 | 实现 | 表达的真实含义 |
|---|---|---|
| 镜头飞行 | 内层 `g` 的 transform transition 620ms | 从全局缩到当前实体邻域 |
| 邻域匹配高亮 | 非邻域节点 opacity 降至 0.18、边降至 0.08 | 只有相关关系留在台上 |
| **先修链逐跳描边** | `pathLength={1}` 归一化 + keyframes 动画 `stroke-dashoffset: 1 → 0`，延迟 = `hop * 110ms` | **动画节奏本身编码了 BFS 深度，即先修层级** |
| 焦点呼吸光圈 | 焦点节点叠加 `rect`，keyframes 动 `stroke-opacity` | 当前实体 |
| wiki ↔ 图谱双向绑定 | 正文 `[[内链]]` hover → 节点高亮；节点点击 → 正文定位 | 两边同一个 `entityId` |
| 路径生长回放 | 按 `prerequisite_depth` 排序依次点亮 | 3-5 年路径 |

### 9.4 三条工程约束

1. **`stroke-dasharray` 冲突（已实现中解决）**。原 `knowledge-graph.tsx` 用 `strokeDasharray="5 5"` 表示"前置技能"，而描边动画需要独占 `dasharray`。当前实现改为**配色区分**（前置关系用紫色 + 紫色箭头 marker），图例同步从"虚线：前置关系"改为配色说明。若希望保留虚线语汇，替代方案是在同一 `<g>` 内叠一条装饰性虚线 path，但两根路径叠加会互相干扰观感，不建议。
2. **`prefers-reduced-motion` 必须降级**：命中时跳过飞行与描边，直接切终态。
3. **不能让动画暗示数据里没有的东西**：`weight` 缺失时不要用线宽表示"重要度"；`llm-draft` / `runtime` 来源的边与人工标注的边必须视觉可区分（虚线或半透明），这同时是伦理章节的实证。

---

## 10. 两个方向性决定的落地

### 决定一：Wiki 页生成 → A 方案

- 主体：离线 LLM 编译 + 人工审核入库（§6 步骤 07-08）。
- 兜底：低流量实体走 `llm-draft`，检索时按 §5 降权。
- 运行时按需生成仅作"未编页实体"的优雅降级，**不进主链路**——因为它的结果不可复现，材料 ② 的"效果评估"在它上面不成立。

### 决定二：主检索归属 → A 方案

- 主链路：自建检索 + 自建 MCP server，Web 前端与百宝箱智能体都调用它。
- 对照：若企业版账号支持导入，同一批资料在百宝箱另建一套，**只用于 §7 的对比评测**，不承担主链路。
- 选 A 的关键理由：**必须能拿到检索中间态**（命中 chunk、分值、Top-K），否则 §7 的检索命中率与引用支持度两个指标算不出真实分子分母，材料 ② 的效果评估部分会塌掉。

**最坏情况**（百宝箱知识库权限拿不到）：仍有完整的知识库报告与 MCP 实现，只少一组对照实验。反过来若依赖平台，权限一卡整块材料都没了。

### 10.1 A 方案下的能力边界（必须诚实标注）

| 能力 | A 方案下的状态 |
|---|---|
| 4 方向职业/技能图谱 | 自建，可复算 |
| 引用追溯 | 自建 `evidenced_by` → L0 原文 |
| 检索指标（Top-3、引用支持度） | 自建侧可算真实分子分母 |
| 跨平台企业级数据 | **不具备**。无实时招聘抓取、无第三方平台真实数据。报告中列为后续项 |
| 百宝箱侧检索指标 | **以平台实际返回为准**；拿不到中间态则该组只报人工观察 |
| 多端发布 | 与知识库解耦，独立验证 |

---

## 11. 待确认事项

| # | 事项 | 影响 | 状态 |
|---|---|---|---|
| 1 | 百宝箱企业版账号的知识库导入权限与检索接口 | 决定对照实验能否做 | **部分核验**：接入方式已实测为只有 `sse` / `streamableHttp`（无 stdio，见 §8.2）；知识库导入权限与配额、检索中间态（命中 chunk id / 分值 / Top-K）**仍未核验**，故对照实验暂不做 |
| 2 | MCP server 用 TypeScript 还是 Python | 决定 `backend/` 目录与三个 npm 脚本的处理方式 | **已定：TypeScript**。已落地在 `frontend/frotent/frontend1/mcp/` 并跑通验证。`backend/` 三脚本随之悬空，待删或补 |
| 3 | `GraphNode.kind` 是否拓宽到 8 类（新增 `task` / `trend` / `credential` / `domain`） | 决定这四类节点能否上图；未拓宽时前端适配层必须显式过滤 | **已拓宽**：8 类已进 `lib/client/career-graph.ts` 的 `GraphNode.kind`，并由前端单测覆盖（`tests/career-graph-export.test.ts` 5/5 通过、`tsc --noEmit` 干净） |
| 4 | 来源资料收集进度 | 是全部下游工作的输入 | **已完成**：26 份登记来源（25 份抓取快照 + S26 国标重组导入），1757 个 chunk / 845,250 字符，见 `knowledge/sources/sources.json` / `knowledge/chunks/chunks.jsonl` |
| 5 | 是否保留前置关系的虚线语汇 | 仅影响视觉，不影响数据 | 已按配色方案实现，可回退 |
| 6 | 学习单元（`knowledge`）与技能/职业之间缺一条关系类型 | §3.2 八种边里没有以 `knowledge` 为 `from` 的边，学习单元只能靠 `evidenced_by` 挂到原文，无法进入学习路径图 | 未定：新增 `covers` / `learned_by`，或复用 `prerequisite` 并放开 from 类型 |

---

## 12. 交付物映射

| 赛题要求 | 本文档对应 | 产出目录 |
|---|---|---|
| ② 知识库构建方法与效果评估报告 | §2-§7、§10 | `knowledge/sources/`、`structured/`、`exports/`、`evaluations/` |
| ③ MCP 协议对接方案与实现说明 | §8 | MCP server + 调用日志 |
| 架构与 AI 伦理设计说明 | §1（L2 不产生新事实）、§4.3、§9.4 | `delivery/reports/` |
| 多端发布验证截图 | 与知识库解耦 | `delivery/platform/` |

报告措辞必须区分四种状态：**设计方案 / 原型模拟 / 实际实现 / 实际验证**。本文档的分段状态：

| 部分 | 状态 | 依据 |
|---|---|---|
| §1–§7 知识库方法 | **实际实现 + 部分实际验证（结果属原型模拟）** | 管线 01→11 全链路退出码 0（第 09 步硬约束 13/13 通过、pending 0、warn 0），导出 `knowledge/exports/career-graph.json`（`kbVersion 2026.09.15` / `status pipeline-export`，26 来源 / 1757 chunk / 63 节点 / 226 边）；24 题评测已跑（`knowledge/evaluations/`），但检索命中率仍低（2026-09-29 复跑：消融 BM25 1/18，含图 +1 跳 / wiki 层均 6/18），属**原型模拟**水平的初步结果 |
| §8 MCP 工具契约 | **实际实现 + 实际验证** | `npm run mcp:verify`（stdio，14/14 断言）与 `npm run mcp:verify-http`（Streamable HTTP，25/25 断言）均退出码 0，证据 `evidence/mcp-verify.json`、`evidence/mcp-http-verify.json`；HTTP 入口经公网隧道再跑同一套断言同样 25/25（`evidence/mcp-public-verify.json`），即**公网可达** |
| §9 前端图谱动画匹配 | **实际实现 + 实际验证** | `npm test` 5/5 通过（`tests/career-graph-export.test.ts`），`tsc --noEmit` 无新增错误 |
| §10 检索归属与边界 | **设计方案**（依赖 §1–§7 落地） | 待真实资料与百宝箱账号 |

**注意**：MCP 的「实际验证」指的是**协议链路与双端同源**已验证 —— 即「客户端能发现工具、能调用、能拿到图推理结果，且结果与网页同源」。它**不**代表知识内容的效果已经达标（底层虽已是自建管线导出的真实数据：26 来源 / 1757 chunk / 63 节点 / 226 边，但 24 题检索命中率仍处原型水平，见上表第 1 行），也**不**代表百宝箱平台已经连入：公开 URL 上的三次调用均由本机发起，平台侧至今未提交过自部署 MCP 配置、未发起过任何调用。只有拿到平台侧的 `tools/list` / `tools/call` 返回，才能把这一项升级为「已实现 MCP 平台接入」。
