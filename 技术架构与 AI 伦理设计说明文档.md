# 技术架构与 AI 伦理设计说明文档

> 面向评审与工程交接：把「这个系统怎么搭起来的」「它在伦理上到底做了什么约束」讲清楚，
> 且**每一条可核验的断言都给出证据**：要么是现役后端真实跑出来的请求-响应，要么是源码的 `文件:行号`，
> 要么是仓库内两份规范文档（`README.md`、`项目架构与技术文档.md`）的章节/数字。
>
> 本文档**不给没有证据的结论**。凡是「数据源本身不存在」或「机械规则定不出来」的东西，
> 一律在正文如实标注，并集中列在 §6，与「还没做的工作」严格区分开。

---

## 0. 本文档的证据口径与复现基线

证据分三类，正文逐条标注：

| 标记 | 含义 | 举例 |
|---|---|---|
| 【实测】 | 本轮（2026-10-08）对现役后端（`python backend/run.py`，`127.0.0.1:8000`，DB=`backend/career.db`）发出的真实请求与响应 | `GET /health`、`POST /api/growth-records` 的返回体 |
| 【文件】 | 源码路径 + 行号或函数名，可直接定位 | `backend/tasks.py:445` |
| 【文档】 | 两份规范文档里的章节或数字 | `项目架构与技术文档.md` §5.5 |

**复现基线（本轮实测）**

- 后端单测：`export PYTHONIOENCODING=utf-8 && python -m pytest backend/tests -q` → **204 passed**。
- 知识库端到端：`npm run e2e:all`（`--with-suites`，含前端 SSR 与既有套件）→ **A–H 八阶段 237/237、0 失败**，
  落盘于 `evidence/knowledge-e2e.json`（`generatedAt=2026-10-02`，耗时 23.99 s，`flags={withFrontend:true, withSuites:true, withLlm:false}`）。
- 取证方式：以访客会话调用 `/api/auth/guest` 后，按「画像草稿→确认→推荐/路径/任务→提交→评估→覆盖层→召回」顺序逐请求走一遍，
  把每一步的 HTTP 状态与响应体原样留存（本节所有【实测】数字即出自这一轮）。

路径说明：源码根目录是 `frontend/frotent/frontend1/`（目录名 `frotent` 为仓库原样拼写，非笔误），下文省略该前缀。

---

## 1. 系统全景与技术架构

### 1.1 三层结构

```
        ┌──────────────────────┐   同读一份图谱   ┌────────────────────────────┐
        │  Next.js 前端页面      │ ───────────────► │ knowledge/exports/         │
        │  (app/(product)/...)   │                  │ career-graph.json          │
        └─────────┬────────────┘                   └───────────▲────────────────┘
                  │ HTTP (career_session cookie)                │
                  ▼                                             │
        ┌──────────────────────┐   GraphStore 只读加载           │
        │ Python 标准库后端      │ ───────────────────────────────┘
        │ http.server 零第三方依赖│
        └─────────┬────────────┘
                  │ stdio / Streamable HTTP
                  ▼
        ┌──────────────────────┐
        │   MCP 工具（3 个）     │  读取同一份图谱导出
        └──────────────────────┘
```

### 1.2 关键架构决策

- **后端零第三方 Web 框架**：`Python 标准库 http.server`，三个类分工 `CareerApi / GraphStore / ProfileStore`，
  路由表集中在 `backend/server.py`。【文件】`backend/server.py`；【文档】`项目架构与技术文档.md` §4
  这样的选择让「部署」退化成「有 Python 就能跑」，代价是路由、鉴权、响应包装都得自己写（见 §5）。
- **三端共读同一份图谱**：前端页面、后端 API、MCP 工具都读 `knowledge/exports/career-graph.json`，
  从根上避免「页面显示的岗位」和「后端算的岗位」出现两份副本漂移。
  现役图谱：sources 26、chunks 1757、nodes 63、edges 226、wikiPages 38、evaluationQuestions 24；
  `kbVersion=2026.09.15`、`graphVersion=0.1.0`、`exportStatus=pipeline-export`。【实测】`GET /health`
- **图谱为唯一事实源**：任务、技能、岗位关系都取自图谱的边，而不是另建一张业务表（见 §5.3）。

> 这个「单份图谱、三端共读」是结构性决策：它保证三端显示必然一致，代价是图谱一改就要整体重导出
> （`exportStatus=pipeline-export`，见 §3.1）。

---

## 2. 记忆库（Memory Base）

### 2.1 数据模型

9 张 SQLite 表：【文件】`backend/memories.py:125-252`

`memory_items`(125)、`memory_triggers`(146)、`growth_records`(164)、`profiles`(185)、`profile_evidence`(195)、
`growth_events`(211)、`profile_snapshots`(227)、`career_match_runs`(242)、`career_match_targets`(252)；
迁移幂等为 `_MIGRATIONS`(263)。

状态模型只有两态：`status ∈ {candidate, confirmed}`；persona 常驻只取 4 类
（`career_target/goal/background/preference`），`PERSONA_LIMIT=3`、`RECALL_LIMIT=8`。【文件】`memories.py:56-71`

### 2.2 伦理核心：候选绝不自动「落为已确认」

这是本项目最重要的一条设计约束：**任何一次用户提交都只写「成长记录 + 待确认候选」，绝不直接写 `confirmed`；
评估接口只回反馈、不落库。**

同一次真实会话里的实测链条：

| 步骤 | 实测结果 |
|---|---|
| 全流程开始前取上下文 | `count=0`、`persona=[]`、`memoryHash=""` 【实测】B |
| 画像草稿已同步、候选已生成，但**尚未确认**时再取上下文 | **仍 `count=0`**（候选进不来） 【实测】G |
| 用户在记忆面板确认后 | `persona` 才出现已确认项 【实测】I |
| 任务评估返回体自带的声明 | `disclaimer`:「单次表现不构成已掌握能力；候选观察需你在记忆面板确认后才会进入对话与推荐。」【实测】W |

→ 即：**候选永远进不了对话与推荐，直到人确认**。这不是注释里的承诺，而是 B/G/I 三个响应体直接测出来的。

### 2.3 幂等：每个写操作都可安全重放

后端对所有写操作都采用「先查再写」，重复提交返回「已存在」而不是再写一遍：

| 场景 | 重复提交的实测返回 |
|---|---|
| 成长记录（同 `recordId`） | `{"created": false, "note": {"reason": "record_exists", "message": "同一 recordId 已存在：按幂等处理，不重复写记录与候选"}}`【实测】M2 |
| 任务运行（同 `requestId`） | `{"created": false, "candidateNote": {"reason": "duplicate_request", "message": "同一 requestId 已提交过：按幂等处理，没有重复写记录与候选"}}`【实测】V2 |
| 候选确认（重放） | `confirmedMemoryIds: []`、`alreadyConfirmedMemoryIds: [...]`【实测】N2 |

实现锚点：【文件】`memories.py:1395` `get_growth_record`（先查）、`memories.py:1412` 幂等 guard、`memories.py:1427` 写入。

### 2.4 任务视图覆盖层（task_overrides）：只改"你的视图"

`PATCH /api/tasks/<taskId>` 只允许改**个人视图**（`note` / `hidden` / `position` / `updatedAt`），
任务本身仍是图谱派生的那一份：

- 响应显式回 `taskStillFromGraph: ["difficulty", "estimatedHours"]`
  与说明 `"只改了你的视图：任务内容仍是图谱派生的那一份"`。【实测】X
- 覆盖后 `GET /api/tasks?occupation=AI003&includeHidden=1` 的 `hiddenCount=0`（视图操作不隐藏图谱事实）。【实测】X2

即：用户能做的只有"给自己加备注/排序/隐藏"，**不能改任务内容、不能"造"一条任务**。

### 2.5 候选派生：前缀白名单 + 图谱名词奖励

成长记录 → 候选记忆的派生，用**前缀白名单**控制：

| 前缀常量 | 值 | 【文件】 |
|---|---|---|
| `SKILL_PREFIX` | `具备或正在学习：` | `growth.py:32` |
| `PLAN_PREFIX` | `计划学习：` | `growth.py:33` |
| `OCCUPATION_PREFIX` | `目标职业：` | `growth.py:34` |
| `ACTION_PREFIX` | `已完成行动：` | `growth.py:35` |

- 前两个前缀必须同时出现在 `memories.SKILL_PREFIXES` 白名单里，否则抽出来的"技能名"会是整句
  （如「计划学习：模型量化与部署」）。【文件】`growth.py:31` 注释 + `memories.py:80-93`
- 重要度按记录类别给（`IMPORTANCE_BY_KIND`，类别替代分数，不假装是同一个量），
  命中图谱名词额外 `GRAPH_BONUS=10`。【文件】`growth.py:40-45`
- 实测：成长记录派生出候选 `"具备或正在学习：模型量化与部署"`，`status=candidate`、`triggersPending=true`。【实测】M

### 2.6 授权 / 召回 / 消费

- **召回打分**（轻量加权，替代多路 RRF，因为这里没有多路检索器）：
  `score = 0.4·importance + 0.4·recall + 0.2·recency`；词面召回门限 `WORD_HIT_FLOOR=0.25`。【文件】`memories.py:72-77`
- **召回实测**：查询 `FreeRTOS` → 「本次想起」出现 `具备或正在学习：FreeRTOS（相关度 1.0，word）`；
  上下文按「【常驻】/【本次想起】」分区。【实测】Y
- **无关查询不硬凑**：查询 `今天天气怎么样` → `count=3`、`recalled=[]`，只回常驻 persona。【实测】I2
- **消费（注入）实测**：
  - 对话注入：`injected.count=4` + `memoryHash=292bf06d…`，回答基于注入的记忆与图谱缺口生成。【实测】K
  - 推荐注入：`memory_hash=292bf06d…` + `confirmed_memory[{memoryId, category, content, usedFor:"interests"}]`。【实测】L
- **`memoryHash` 随已确认集合变化**：确认成长记录前 K/L 的 hash 是 `292bf06d…`；
  确认后 N/N2/Y 变为 `2c2dabce…`。→ 该 hash 可作为"这一刻用的到底是哪版已确认记忆"的指纹。【实测】K/L/N/N2/Y

### 2.7 触发器（记忆如何「被想起」）

规则版触发器由记忆原文**确定性派生**，不依赖模型：

- 结构：`triggerId / level / concept / bridge / activationPatterns[]`。【文件】`memories.py` 触发器链
- 实测：对"想往边缘AI方向做嵌入式开发"派生出 concept=`嵌入式开发`、
  bridge=`当前目标 → 嵌入式开发（由记忆原文确定性派生）`、3 条 activationPatterns。【实测】J

---

## 3. 知识库（Knowledge Base）

### 3.1 从语料到单份图谱

- 采集/归一/切块/实体关系抽取/导出由 `knowledge/pipeline/` 的 `01`–`13` 脚本串起来（无 `00`），
  终产物是 `career-graph.json`。【文件】`knowledge/pipeline/*.mjs`；【文档】`README.md`
- 三端共读这一份产物（见 §1.2），因此"页面/后端/MCP 三处一致"是结构保证，不是约定。

### 3.2 完整性核对

| 指标 | 值 | 来源 |
|---|---|---|
| 完整性检查项 | 14 项（其中 13 条硬约束 0 失败） | 【文档】`项目架构与技术文档.md` §5 |
| 内容**逐字符可追溯** | 1699 / 1757 = 96.7% | 【文档】同上 |
| 原始快照 sha256 | 25 份对 25 份，**其中 5 份与 manifest 不一致**（S15/S18/S19/S20/S22） | 【文档】同上 |
| 端到端 | A–H 八阶段 **237/237（0 失败）** | 【实测】`evidence/knowledge-e2e.json` |

> 对"96.7% 可追溯"和"5/25 快照不一致"这两条，本文档按实测数字如实写，不四舍五入成"全部通过"。

### 3.3 检索栈（为什么这么设计）

中文 BM25（`Intl.Segmenter` 分词）+ 向量检索（TEI `Qwen3-Embedding-0.6B`，1024 维）+ LLM 重排：

| 参数 / 结果 | 值 | 来源 |
|---|---|---|
| 融合权重 `α_bm25` | 0.35 | 【文档】§5.5 |
| 拒答阈值 | 0.595 | 【文档】§5.5 |
| 切块参数 | chunkSize 512、overlap 80、parent 4096 | 【文档】§5.5 |
| 开发集采纳率 | 32/34 = 94.1%；P@3 0.5392、R@3 0.8088 | 【文档】§5.5 |
| 冻结留出集（融合+重排） | 24/32 = 75.0%；命中@1 0.6875 | 【文档】§5.5 |
| 抬升信号（分层） | 峰值 < 1.5 时退单向量：dev 29→31/34、holdout 26→27/35 | 【文档】§5.5 |

**负结果也记录**（不粉饰）：图谱证据扩展在留出集上是**单调为负**的（13→12→11→10→9/14）；
增益实际来自"多路召回 + 重排"，不是来自图谱证据扩展。切块 512 的 A/B（941→512：6/18→10/18）也一并留档。【文档】§5.5

### 3.4 拒答分离（防幻觉的边界）

"该答的必须答、不该答的必须拒"被拆成两个独立指标：**可答题零误拒** + 拒答集 dev 15/15、test 14/15，
balanced accuracy 0.9667。【文档】§5.5

---

## 4. AI 伦理设计（重点）

本系统的伦理主张不靠口号，而靠**四条可复现的机制**：不编造、模型不覆盖规则、候选不落已确认、缺数据就承认缺数据。

### 4.1 不编造：两道硬门（gate）

模型评估结果要进入最终结论，必须先过两道门（`_merge_model_evaluation`，`tasks.py:428-478`）：

1. **能力名必须逐字属于该任务的 `requiredSkills`**，否则判 rejected：
   `{kind:"skill", why:"模型提到它，但它不是这条任务要求的能力，未采纳"}`。【文件】`tasks.py:445`
2. **证据必须逐字存在于用户原文**，否则判 rejected（evidence 为空或不在 source_text 里）。【文件】`tasks.py:398-416`

评估 prompt 里写死的规则（原文）：**只能依据用户提交内容判断、不得补造；`observedAbilities[].name` 必须逐字取自 `requiredSkills`；
`evidence` 必须 quote 逐字存在原文；找不到证据的放进 `needsVerification`；不给「已掌握」结论；只返回 JSON。**【文件】`tasks.py:409-416`

**实测证明它真的会"宁可不给结论、也不编造"**：故意提交一段与 `requiredSkills`
（`浏览器自动化 WebDriver`）**完全不匹配**的硬件联调内容后，模型返回
`observedAbilities: []`、`coverage: 0.0`，并把所有观察降到 `needsVerification`，而不是硬凑一个能力。【实测】W

### 4.2 模型不覆盖规则：去重合并而非替换

最终结论 = 采纳的模型观察 + 规则观察，**按 `name` 去重合并，模型不会替换规则观察**；
`coverage` 由合并后的结果重算。【文件】`tasks.py:462-478`
→ 即模型的角色是"补充"，不是"覆盖"，避免单次模型波动抹掉确定性的规则结论。

### 4.3 候选不落已确认：人机回环（human-in-the-loop）

见 §2.2。任何"能力"都必须由人在记忆面板确认为 `confirmed`，才会进入对话与推荐；
评估接口只回反馈、不写 `confirmed`。【实测】B/G/I/W

### 4.4 缺数据就承认缺数据（可追溯 + null）

- 内容**逐字符可追溯** 96.7%。【文档】§5
- 图谱未标注的字段**返回 `null` 并列进 `unavailableFields`**：
  任务 `difficulty`、`estimatedHours`（图谱没有任务级难度/学时）。【实测】U；【文件】`tasks.py:255`
- 并且**不冒名**：`estimatedHours` 是"阶段级"投入（技能等级差 × 24 小时），**不是这一条任务的耗时**，
  这一点在任务响应里明确写出，不冒充为同一个量。【实测】U
- 检索侧同理：拒答分离、可答题零误拒（§3.4）。

### 4.5 降级可观测：不把"退回规则版"伪装成"模型成功"

`generated_by` 有三个取值：`rule-based`（本来就是规则版）、`rule-based-fallback`（模型失败才退回）、`llm`。
代码注释写明：这样区分是为了**事后能统计模型成功率**，否则分不清"本来就规则"和"模型挂了"。【文件】`memories.py:43-50`
→ 这是"诚实标注失败模式"的机制，而不是把降级藏起来。

### 4.6 不黑箱：对"AI 黑箱知识生产"的正面回应

本设计明确反对"黑箱式的知识生产"，把"知识从哪来"全程留痕：

| 做法 | 对应机制 |
|---|---|
| 结论可回到来源 | 图谱节点/边可回溯到来源与原始快照 sha256（§3.2） |
| 判断可解释 | 匹配/路径给的是"画像命中 X 项、还差 Y 项"的具体缺口，而非一个黑箱分数（§5） |
| 模型只有建议权 | 模型不能落库，观察需人确认（§2.2 / §4.3） |
| 失败有标记 | `rule-based-fallback` 让降级可见（§4.5） |

这四条都不是宣传语，各自对应上文一个可复现的实现。

---

## 5. 系统联动（System Integration）

一次真实会话从头到尾打通了"前端 → 后端 → 图谱 → MCP"：

- **访客会话**：`POST /api/auth/guest` → `201`，`Set-Cookie: career_session=…; HttpOnly; SameSite=Lax`。【实测】A
- **后端路由表**：`backend/server.py` 单文件承载全部路由（health / 记忆 / 成长记录 / 画像 / 图谱查询 / 任务 / 附件 / 简历 / 认证 …），
  统一响应包装 `{requestId, data, error}`。【文件】`server.py`；【文档】§4
- **任务来自图谱的 `task→skill` 边**：`GET /api/tasks?occupation=AI003` → `count=4`，
  `counts={available:2, planned:2, completed:0}`，响应 `notes` 说明"任务与交付要求来自图谱的 task→skill 边"。【实测】U；【文件】`tasks.py:192 derive_tasks`
- **推荐是真算出来的**：`match_score=17`、理由"画像技能命中 1 项岗位要求技能；仍需补齐 6 项"、
  `skill_gaps` 6 项。【实测】L
- **职业匹配**：`matchScore=36`、分项 `{skills:17, entryFeasibility:21, interest:100, experience:0}`、
  还带 `needsValidation` 追问（"「微控制器外设驱动」你目前到什么程度？"）。【实测】S
- **学习路径**：`path_valid=true`、`overall_score=72`、`grade=C`、`match_type=profile_coverage`、
  `weekly_hours=8`、缺口 `{total:7, satisfied:0, learning:3, priority_learning:4}`。【实测】R
- **对话与图谱联动**：注入 `count=4` + `memoryHash`，回答里明确写"边缘AI具体先学啥，图谱没给"，只补图谱缺口、不越权编学科顺序。【实测】K
- **MCP**：3 个工具，`stdio` 与 `Streamable HTTP` 两种传输，读的是同一份图谱导出（`npm run mcp` / `npm run mcp:http`）。【文件】`mcp/`；【文档】`README.md`

---

## 6. 结构性不可做 / 刻意不做 / 已知限制

> 本节与"还没做的工作"分开列。这里的每一项都是**数据源不存在、机械规则定不出来、或已明确 descope**，
> 强行补上只会造假。

### 6.1 结构化缺口：数据源里就没有

- 任务级 `difficulty`、`estimatedHours`：图谱未标注 → 返回 `null` 并进 `unavailableFields`。【实测】U；【文件】`tasks.py:255`
- 岗位薪资 `salary_range`：`"暂未提供"`。【实测】L

### 6.2 机械映射定不出来（只能人工裁定）

- 把外部职业大典 / O*NET 接回自建图谱：定不出**机械**的映射规则，只能人工裁定，属门禁而非"待办"。

### 6.3 来源标注不完整（口径分歧已如实留档）

- `publishedAt` 只有 **3/26** 个来源有值（S08–S10），其余缺。
- 仓库内不同文档对这个数字的口径存在分歧，本文档以"3/26"为准并保留该分歧记录，不强行统一成"全部齐备"。

### 6.4 第三方外部依赖已 descope

- 百宝箱接入、多端发布：明确不做（用户拍板）。
- 猎聘岗位数据：做不了——`liepin-cli` 是"招聘者端"工具，取不到面向求职者的真实岗位流。

### 6.5 工程基线遗留（与运行无关）

- 全仓 `npm run lint` **不是全绿**：`components/entry/{image-editor,profile-form}.tsx` 上有 9 个
  `no-explicit-any` / react-hooks 报错，属基线遗留，**与运行无关**，且 lint 不在 e2e 门禁里；
  改动过的文件单独跑 eslint 无报错。【文档】`README.md:197-214`

### 6.6 已知限制（本轮实测发现，**待修复**）

成长记录的幂等是"按用户先查再写"：`get_growth_record` 用
`WHERE user_id = ? AND record_id = ?`【文件】`memories.py:1395`，guard 在【文件】`memories.py:1412`；
但表定义里 `growth_records.record_id` 是 **`TEXT PRIMARY KEY`（全局唯一）**【文件】`memories.py:165`。

后果：**跨用户**复用同一个 `recordId` 会绕过那道用户级 guard，直接在 `INSERT` 触发
`sqlite3.IntegrityError: UNIQUE constraint failed: growth_records.record_id`
（【文件】`memories.py:1427`），且该异常未被 handler 捕获 → **连接被直接断开**
（客户端看到 `RemoteDisconnected`），而不是返回一个规整的错误码。

- 证据：【实测】后端日志 traceback — `server.py:604` → `memories.py:1427`（线程内抛错，进程本身继续存活）。
- 影响面：只在"不同用户恰好撞同一个 `recordId`"时触发；**同一用户**重复提交已被 guard 正确挡下（见 §2.3 M2），
  写入失败时的回滚保证仍成立【文件】`memories.py:1467-1469`。
- 本节如实记录，**不宣称已修复**。

---

## 7. 如何复现本文档的实测

```bash
# 1) 起后端（Python 标准库，无第三方依赖）
cd frontend/frotent/frontend1
python backend/run.py            # 127.0.0.1:8000，DB=backend/career.db（可用 CAREER_MEMORY_DB 指到别处）

# 2) 后端单测
export PYTHONIOENCODING=utf-8
python -m pytest backend/tests -q      # → 204 passed

# 3) 知识库端到端
npm run e2e:all                        # → A–H 237/237（0 失败），evidence/knowledge-e2e.json
```

手动取证顺序（即本文档【实测】各编号的来源）：

1. `POST /api/auth/guest` 拿 `career_session` cookie；
2. `PUT /api/profile`（草稿）→ `POST /api/memories/sync` → 此时取 `GET /api/memories/context` 应为空（对应 B/G）；
3. `POST /api/profile/confirm` → 再取 context，persona 出现（对应 I）；
4. `GET /api/career/recommendations`、`POST /api/v1/career-path/generate`、`POST /api/career-matches/generate`（对应 L/R/S）；
5. `GET /api/tasks?occupation=AI003` → `POST /api/tasks/<id>/runs`（同 `requestId` 再发一次，对应 V/V2）；
6. `POST /api/task-runs/<id>/evaluate`（对应 W/W2）；
7. `PATCH /api/tasks/<id>` 改视图 → `GET /api/tasks?...&includeHidden=1`（对应 X/X2）；
8. `GET /api/memories/context?query=FreeRTOS`（对应 Y）。

> 说明：Windows + git-bash 下 `curl` 传非 ASCII 负载会被服务端当非法 JSON 拒掉（GBK 编码），
> 带中文的写请求请改用 Python `urllib` + `json.dumps`。

---

## 8. 证据索引（速查）

| 编号 | 断言 | 证据 |
|---|---|---|
| B / G / I | 候选在确认前进不了上下文；确认后才进 | 【实测】context 响应 |
| M / M2 | 成长记录派候选 + 同 recordId 幂等 | 【实测】`POST /api/growth-records` |
| N / N2 | 确认链路 + 确认重放幂等 | 【实测】`POST /api/growth-records/confirm` |
| V / V2 | 任务提交 + 同 requestId 幂等 | 【实测】`POST /api/tasks/<id>/runs` |
| W / W2 | 评估两道门 + 评估重放 | 【实测】`POST /api/task-runs/<id>/evaluate` |
| X / X2 | 任务视图覆盖层只改个人视图 | 【实测】`PATCH /api/tasks/<id>` |
| J | 规则触发器确定性派生 | 【实测】`POST /api/memories/<id>/triggers` |
| K | 对话注入已确认记忆 + 图谱缺口 | 【实测】`POST /api/chat` |
| L / S / R | 推荐 / 匹配 / 路径均为计算所得 | 【实测】对应接口 |
| U | 任务来自 task→skill 边；缺字段返 null | 【实测】`GET /api/tasks` |
| Y | 召回 FreeRTOS 相关度 1.0 | 【实测】`GET /api/memories/context` |
| — | 后端单测基线 | 【实测】pytest `204 passed` |
| — | 知识库 e2e | 【实测】`evidence/knowledge-e2e.json` `237/237` |
| — | 密码学级完整性 / 检索指标 | 【文档】`项目架构与技术文档.md` §5、§5.5 |
