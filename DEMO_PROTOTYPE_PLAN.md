# fuwuwaibao：8 案例原型与真实知识库交付方案

> 状态：本文件为收缩范围后的实施建议。
>
> **2026-09-15 状态更新**（下列为机器验证结论，命令与证据见各节）：
> - 前端知识图谱聚焦动画（`components/work-map/knowledge-graph.tsx`）、图谱纯函数模块（`lib/client/graph-view.ts`）与配套样式已写入代码，并已通过单测：`npm test` 5/5 通过（`tests/career-graph-export.test.ts`），`npx tsc --noEmit` 无新增错误（仅剩 `vite.config.ts` 两处历史 TS2307）。
> - **MCP server 已实现并已实际跑通**：位于 `frontend/frotent/frontend1/mcp/`，`npm run mcp:verify` 退出码 0，`tools/list` + `tools/call × 3` 均有原始输出存档于 `frontend/frotent/frontend1/evidence/mcp-verify.json`（详见第 7 节）。
> - **MCP server 的 HTTP 传输入口已实现，并已验证公网可达**：`mcp/http.ts` 暴露 Streamable HTTP 的 `POST /mcp` 与探活 `GET /health`，`npm run mcp:verify-http` 退出码 0（25/25 断言），证据 `frontend/frotent/frontend1/evidence/mcp-http-verify.json`；同一套断言再经第三方公网隧道、用公开 HTTPS 地址跑一遍（`MCP_HTTP_VERIFY_TARGET=https://<隧道域名> npm run mcp:verify-http`）同样退出码 0，证据 `frontend/frotent/frontend1/evidence/mcp-public-verify.json`（详见第 7 节）。**注意：这些请求的发起端仍是本机**，只证明「服务在公网上可达」，不等于平台侧真的连上了。
> - **真实知识库与三层数据已完成**：L0 证据层 26 份登记来源 / 1757 chunk（845,250 字符，其中 25 份有 raw 快照），L1 图谱导出 `knowledge/exports/career-graph.json`（kbVersion `2026.09.15`、graphVersion `0.1.0`、status `pipeline-export`，63 节点 / 226 边 / 1757 chunk），L2 Wiki 38 页（reviewed 29 / llm-draft 9）；契约样例 `knowledge/exports/career-graph.sample.json` 仍保留作对照。检索与评测结果属**原型模拟**水平（消融命中率约 3/18），尚需改进。
> - 仍未实现 / 未确认：**百宝箱平台侧的实际连入**（自部署 MCP 表单尚未提交，平台至今未发起过任何调用）、多端发布验证。百宝箱的知识库导入权限与检索中间态（命中 chunk id / 分值 / Top-K）仍未能核验，必须覆盖的发布端也仍需落实。

## 0. 1–2 天时间约束下的取舍

- **两天目标**：8 个案例、最多 4 个职业方向、一条完整业务闭环、一套小型真实知识库及实际评测、能接通的平台证据、约 4 分钟视频。
- **一天保底**：6 个案例、优先 2 个有资料的职业方向；沿用同一任务模板，不恢复地图/图谱，不做市场趋势页扩展。知识库先完成约 10 份来源资料和 12 道独立评测问题，再按余量扩充。
- **两种版本都要做**：案例隔离、刷新保存、画像到任务的联动、任务记录与确认、明确的知识来源、原型模拟范围说明。
- **立即核实**：企业版账号、可用 API/工具接入权限、小程序发布或开发预览条件。截图没有说明哪些端可以豁免，因此不能自行把手机浏览器预览当作全部多端材料。

建议安排：第一天先恢复启动、建立统一案例状态、跑通 1 个完整案例，再填入剩余案例；同时登记和清洗知识来源。第二天完成真实检索/评测、平台工具连接、实际适配验证和视频录制。以完成标准推进，不承诺上述所有外部平台权限或发布审核可在两天内取得。

## 1. 交付目标

先完成“能交互、能联动、能复现”的 Web 原型，预置 8 个虚构人物案例，复用当前页面；知识库则实际构建、检索和评测，并为平台适配保留一条真实调用链。

交付截图中的“系统原型或完整演示视频（3–5 分钟）”并没有取代企业专项材料。专项材料另外列出了百宝箱企业版配置与操作截图、知识库构建与效果评估报告、MCP 对接方案与实现说明、多端发布验证截图。因此，前端原型可以明确使用模拟数据，平台配置和评测材料需要对应实际操作。

建议同时保留可访问原型和约 4 分钟演示视频：原型用于交互检查，视频用于稳定展示主流程。这里不承诺仅 Web 或仅视频即可满足所有评审要求。

## 2. 三部分工作范围

| 部分 | 最小实现 | 交付时如何说明 |
|---|---|---|
| 前端业务原型 | 8 个案例、画像编辑、推荐选择、路径安排、任务提交、候选确认、成长记录 | 使用虚构人物和模拟业务数据；页面操作确实改变状态 |
| 真实知识库 | 有来源的职业/技能资料，结构化数据，分块检索，引用，独立评测记录 | 展示已收集规模、真实查询、实际指标和失败案例 |
| 平台验证 | 百宝箱配置、知识库或工具接入、最小 MCP 调用、实际端访问证据 | 明确已验证的账号、版本、入口和端；待接通的内容不能写成已完成 |

暂缓正式注册登录、短信邮件、通用 OCR、多用户权限、云端档案、实时招聘抓取、大规模知识图数据库，以及所有职业的自动规划。这些可在报告中作为后续开发项。

## 3. 一条主流程覆盖各 Page

```text
选择演示人物 → 建立/修改画像 → 确认画像 → 推荐岗位
→ 查看知识来源 → 选择目标岗位 → 生成学习计划
→ 开始一项职场任务 → 提交成果 → 查看反馈
→ 确认一条能力观察 → 成长档案 → 返回更新后的画像
```

聊天作为全局助手穿插其中，不再做第二套独立聊天页。

| Page | 原型中必须能操作的功能 | 数据和联动 | 当前最该改的入口 |
|---|---|---|---|
| `/` | 转到演示入口 | 已选择案例时可继续该案例 | `app/page.tsx` |
| `/auth` | 选择 8 个案例之一；进入/继续演示；重置当前案例 | 设置 `activeScenarioId`，进入 onboarding；不要求输入真实密码 | `app/(entry)/auth/page.tsx`、`lib/client/profile-api.ts` |
| `/onboarding` | 加载示例简历、手填编辑、预览、确认 | 只对内置样例加载预制解析结果；普通上传明确尚不支持解析；确认保存到统一状态 | `app/(entry)/onboarding/page.tsx`、`components/entry/profile-form.tsx`、`profile-review-card.tsx` |
| `/growth` | 展示对应人物画像、2–3 个推荐岗位、推荐原因、已具备技能和缺口 | 读取当前案例与确认记录；选岗位后传目标 ID 给 path；能打开知识来源 | `app/(product)/growth/page.tsx`、`lib/client/career-recommendation-api.ts` |
| `/chat` / 聊天抽屉 | 4 类常见提问：推荐原因、技能差距、本周安排、任务反馈；可编辑/确认候选信息 | 演示模式为情境脚本/规则，未覆盖问题明确提示范围；真实知识问答通过已配置的服务返回来源 | `components/chat/chat-conversation.tsx`、`chat-provider.tsx`、`lib/client/chat-api.ts` |
| `/work-map` | 第一版继续跳 growth；时间允许时复用职业图谱作为详情区 | 不为拍视频额外恢复一套复杂主导航 | `app/(product)/work-map/page.tsx`、保留的 `WorkMapExplorer` |
| `/catalog` | 可选作为知识库浏览入口，复用已有 `CatalogBrowser` | 读取同一份真实知识库结构化数据，显示实际数量和来源，绝不用占位统计作成果 | `app/(product)/catalog/page.tsx`、`components/work-map/catalog-browser.tsx` |
| `/path` | 根据人物和目标岗位显示 3 阶段计划；调整每周时长；安排一个任务 | 目标不再固定 AI001，技能不再固定 SK215=3；规则计算差距、先修顺序和预计周数 | `app/(product)/path/page.tsx`、`types/domain/career-path.ts` |
| `/actions` | 展示任务、步骤、提交要求；填写答案/加载示例答案；提交后给反馈 | 一个案例至少有一项可做任务；保存提交记录，产生待确认观察 | `app/(product)/actions/page.tsx`、`lib/client/training-api.ts` |
| `/growth-records` | 查看时间线、前后变化、证据、关联任务；筛选；返回画像 | 和 growth、chat、actions 读取同一份状态；刷新仍保留 | `app/(product)/growth-records/page.tsx`、`components/profile/profile-provider.tsx`、`lib/client/profile-state.ts` |

以上代码路径均相对项目的 `frontend/frotent/frontend1/`。按页面维护计划与说明，运行时仍使用一套应用和共享状态。

## 4. 8 个案例覆盖 4 个职业方向

不把 8 个案例扩成 8 套独立系统。建议先覆盖嵌入式开发、机器视觉、边缘 AI、测试自动化四个方向，各方向共享技能、知识和任务模板。正式名称和 ID 随实际资料整理确认。

| 案例 | 虚构用户起点 | 演示目标 | 展示差异 | 可交互任务示例 |
|---|---|---|---|---|
| S01 | 自动化大三，做过 STM32 传感器项目 | 嵌入式开发 | 有硬件经验、缺工程调试记录 | 按约束排查传感器读数异常 |
| S02 | 同样的专业与项目，了解 Python | 机器视觉 | 相近背景因兴趣不同得到不同推荐 | 判断检测方案并列出验证步骤 |
| S03 | 计算机应届生，有 OpenCV 项目 | 机器视觉 | 已有技能可压缩入门阶段 | 分析光照变化造成的误检 |
| S04 | 嵌入式新人，了解设备资源限制 | 边缘 AI | 利用可迁移能力补齐模型部署 | 权衡延迟、内存与准确率 |
| S05 | 测试新人，有手工测试经历 | 测试自动化 | 从已有测试思维补代码能力 | 写出边界条件与自动化用例 |
| S06 | 有 Python 基础的转型者 | 测试自动化 | 复用编程能力、补工程验证 | 分析失败日志与重现条件 |
| S07 | 与 S04 相近背景，每周仅 3 小时 | 边缘 AI | 时间预算改变计划周数与任务拆分 | 把大任务分成可完成的小步骤 |
| S08 | 大二探索者，技能信息很少 | 暂在嵌入式/视觉间探索 | 不做高置信度推荐，先追问并安排体验 | 比较两个小任务后的兴趣和感受 |

每个案例预置：人物信息、初始技能、兴趣、每周时间、示例简历、2–3 个候选岗位、主要推荐理由、3 阶段路径模板、至少 1 项任务、反馈规则、常见问题脚本。

案例人物、简历、任务答案为虚构；职业知识来源是真实资料。案例数量与知识库文档数量分别统计。

## 5. Mock 必须共用数据和状态

建议使用一个统一的演示服务层，页面不直接从各自文件里取不相关的假数据。

```text
lib/demo/scenarios/       8 个案例的初始数据
lib/demo/store.ts         状态修改、读取、本地持久化
lib/demo/service.ts       沿用现有 API 契约的模拟接口
lib/demo/rules.ts         技能差距、路径时长、任务反馈规则
lib/client/              页面调用入口；显式区分 demo / connected
knowledge/exports/       从真实资料整理出的职业、技能、任务引用数据
```

共同标识：`scenarioId`、`profileVersion`、`occupationId`、`pathId`、`taskId`、`runId`、`candidateId`、`evidenceId`。

状态至少包括：

```text
schemaVersion / activeScenarioId
每个案例的 profile、selectedOccupationId、weeklyHours、path
taskRuns、candidates、confirmedRecords、evidence、events
messages、conversationId、数据模式
```

实现原则：

1. 使用浏览器本地存储保存小体积演示状态，按案例隔离；图片/简历不塞进 localStorage。
2. 初始化时先恢复旧状态，再开始自动保存，避免空状态覆盖；只在客户端访问存储。
3. 每个案例可单独重置；录制时能“一键回到开场”，刷新或换页面不会把别人的记录带过来。
4. 页面共享修改结果：确认候选后 growth 和 growth-records 都更新，不能只更新通知计数。
5. 任务提交由用户触发；确认操作幂等。同一次提交重复点击不能重复生成成长事件。
6. “用户确认一次观察”只新增证据，不自动变成“技能已掌握”；评级变化必须有可解释规则。
7. 模拟对话标注为演示回复。真实服务不可用时显示错误或让用户明确切到演示模式，不能把脚本当真实模型回复。
8. 未提供真实市场统计时，删除当前装饰性趋势图或保留明确的模拟标记；优先展示有来源的岗位能力要求。

## 6. 真实知识库做小，但完整

### 建议的首版规模

- 4 个职业方向，共享约 20–40 个技能条目。
- 先人工筛选约 15–25 份可引用的来源资料；实际数量以收集结果为准。
- 按主题分块，记录实际块数；不为了报告数字复制填充。
- 一套约 24 个问题的小型独立评测集，与录制时使用的演示提问分开。

这些是建议建设范围，不是现有成果或指标承诺。

### 三层知识架构（L0 证据 / L1 图谱 / L2 Wiki）

完整设计见 `knowledge/KNOWLEDGE_BASE_DESIGN.md`（**设计方案**）。三层分工与当前落地状态：

| 层 | 职责 | 产物 | 当前状态 |
|---|---|---|---|
| L0 证据层 | 可引用的来源与检索分块（原文位置、摘要、版本） | 来源登记 + 分块 | 已实现：26 份登记来源（25 份有 raw 快照）、1757 个 chunk（845,250 字符），见 `knowledge/sources/`、`knowledge/chunks/chunks.jsonl` |
| L1 图谱层 | 职业 / 技能 / 知识单元 / 任务 / 工具 / 趋势 / 资质及其之间的边，每条边带 `sourceRefs` 与标注方式 | `knowledge/exports/career-graph.json` | 已实现：63 节点 / 226 边 / 1757 chunk / 26 来源（`kbVersion 2026.09.15`，第 09 步硬约束 13/13 通过）；`career-graph.sample.json` 保留为契约样例 |
| L2 Wiki 页 | 按模板离线编译、人工分级审核后进入生产检索的百科式页面 | `wikiPages` 登记与 review 状态 | 已实现：38 页（reviewed 29 / llm-draft 9），见 `knowledge/wiki/index.json` |

两条铁律：

1. **L2 不得产生新事实**：Wiki 页只能投影 L1 已有的边与 L0 原文，不允许引入图谱中不存在的结论。
2. **一份数据、两端消费**：网页与 MCP 读取同一份版本化 JSON、同一套 TypeScript 纯函数，避免两套实现漂移。

前端对照实现（**实际实现，尚未机器验证**）：`lib/client/graph-view.ts` 提供 `buildAdjacency` / `hopDistances`(BFS) / `graphViewport` / `buildGraphView` / `describeGraphView` 等纯函数；`components/work-map/knowledge-graph.tsx` 消费它渲染聚焦动画，并保持 `graph` / `selected` / `onSelect` 三个 props 不变，调用方 `work-map-explorer.tsx` 无需改动。

### 知识层与用户层分开

| 数据 | 最少字段 | 用在哪里 |
|---|---|---|
| 来源清单 | sourceId、标题、机构、URL、发布日期、采集时间、使用范围 | 报告与引用追溯 |
| 职业 | occupationId、名称、典型任务、相关技能、sourceRefs | 推荐、目录、路径 |
| 技能 | skillId、名称、描述、aliases、prerequisites、sourceRefs | 差距分析和先修顺序 |
| 职业–技能关系 | occupationId、skillId、建议等级、重要度、标注依据 | 推荐与路径；等级/权重为团队标注时明确说明 |
| 学习/训练任务 | taskId、目标技能、步骤、预计时长、交付物、评估点、sourceRefs | path 与 actions；自行设计的任务明确标注 |
| 检索分块 | chunkId、sourceId、主题、原文位置、摘要/摘录、标签、版本 | 聊天证据与检索评测 |

虚构人物和行为记录属于演示用户状态，不计入公共知识库，也不混在职业权威资料中。

### 最小构建过程

来源登记 → 内容筛选与去重 → 按主题分块 → 术语统一 → 职业/技能关系标注 → 图谱契约导出 → Wiki 页离线编译 → 人工分级审核 → 建立索引 → 实际检索 → 引用展示与评测修订（共 11 步，与设计文档一致）。

先用版本化 JSON 保存结构化关系，不必先装图数据库。若企业版提供当前账号可用的知识检索能力，优先上传这些资料并实际验证；本地可建立关键词检索基线用于比较。只有实际实现向量检索后，报告中才写“已实现向量检索”。

知识库必须被产品实际使用：推荐理由能打开来源、技能缺口能定位要求、任务能指出训练目标、知识问答能引用命中分块。

### 效果评测怎么做

准备 24 个独立标注的问题，例如职业要求 6 个、技能差异/先修关系 6 个、情境建议 6 个、超范围/信息不足 6 个。为可回答问题标注参考 chunk/source 和评分依据。

- **检索命中**：可回答问题中，Top-3 是否含相关分块；报告分子与分母。
- **Wiki 审核覆盖率**：审核通过页数 / 编译总页数，按实体热度分层给出分子分母；未通过审核的页不得进入生产检索。
- **引用支持度**：逐条检查回答中的关键结论是否被引用内容支持。
- **信息不足处理**：无依据问题是否明确说资料不足并追问，而非编造答案。
- **实际耗时**：记录本次调用耗时；没有实际模型调用就只报检索耗时。
- **失败与迭代**：保留错误查询、原结果、修改方式和复测结果，不能只放成功截图。

如使用语言模型，固定模型与知识库版本、提问集和参数，保存原始输出。对比实验只能使用真实执行的结果，小样本不作普遍准确率承诺。

## 7. 平台材料用一条真实技术链支撑

### 百宝箱与 MCP

建议只做两个只读工具：

- `search_career_knowledge(query, limit?, kinds?)`：返回真实命中片段、来源和不足提示。
- `get_skill_gap(target, ownedSkills?, maxHop?)`：读取同一份技能关系，用明确规则计算差距。

实现语言已定 **TypeScript**：MCP server 直接复用 `frontend/frotent/frontend1/lib/client/graph-view.ts` 的同一套纯函数与同一份版本化 JSON，与网页同源、零翻译损耗。`@modelcontextprotocol/sdk@^1.30.0` 与 `zod` 已进依赖（`tbox-nodejs-sdk` 是调用智能体做流式对话用的，不提供 MCP 服务端能力）。

> **2026-09-15 更新：已实现并已跑通验证。** server 在 `frontend/frotent/frontend1/mcp/`（`career-graph-store.ts` / `career-graph-tools.ts` / `career-graph-server.ts` / `stdio.ts`），用 stdio 传输真实完成了「客户端发现工具 → 调用 → 拿到结果」：
>
> ```bash
> cd frontend/frotent/frontend1 && npm run mcp:verify    # 退出码 0
> ```
>
> `tools/list` 返回 3 个工具（`search_career_knowledge` / `get_skill_gap` / `get_career_graph_view`），3 次 `tools/call` 全部成功且带 `structuredContent`；原始 JSON-RPC 结果留在 `frontend/frotent/frontend1/evidence/mcp-verify.json`。**仍未做的是连入百宝箱平台**（见下方账号权限）。
>
> 相较本节最初的两个工具，多了一个 `get_career_graph_view`：它返回前端组件真正渲染的那份视图模型，用来证明「网页节点状态」与「MCP 返回值」是同一份，而不是两套各自算一遍。
>
> **2026-09-15 补：HTTP 传输入口。** 原因是百宝箱「自部署 MCP」只接受 `sse` / `streamableHttp`，没有 stdio（见下方账号权限实测）。因此新增 `mcp/http.ts`（Streamable HTTP：`POST /mcp`，探活 `GET /health`，默认 `127.0.0.1:8787`），复用同一个 `createCareerGraphServer(getDefaultStore())`，**工具 schema 一行未改**。
>
> ```bash
> cd frontend/frotent/frontend1 && npm run mcp:http          # 起 HTTP 服务
> cd frontend/frotent/frontend1 && npm run mcp:verify-http   # 退出码 0，25/25 断言（打本机 127.0.0.1:8799）
> # 同一套断言改打公网隧道，验证「经公网之后」接口行为一致：
> cd frontend/frotent/frontend1 && MCP_HTTP_VERIFY_TARGET=https://<隧道域名> npm run mcp:verify-http
> ```
>
> 证据两份：`frontend/frotent/frontend1/evidence/mcp-http-verify.json`（本机）与 `frontend/frotent/frontend1/evidence/mcp-public-verify.json`（经公网），都含每次交互的原始请求与响应。脚本刻意用裸 `fetch` 而不是 SDK 的客户端，因为百宝箱接的是「URL + Header」这一层；裸 HTTP 的通过点更接近平台真实行为，也能用 curl 直接复现。其中一条断言是「stdio 与 HTTP 两条链路的 `tools/list` 逐字相同」，用来钉住「换传输不等于换实现」。
>
> **公网地址是临时隧道，会变。** 匿名隧道只够用来证明「这条链路在公网上通」，不足以支撑提交/评审期间持续可调用；要长期在线需要固定域名的隧道或把服务部署到一台公网主机上。

先让 MCP 客户端成功发现并调用工具，再根据企业版实际支持的接入方式连入平台；保留工具配置、请求参数、调用结果和检索来源。不要仅创建普通 HTTP 接口就称为 MCP。MCP 的工具发现和调用对应 `tools/list`、`tools/call`，见 [MCP 官方服务端说明](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/learn/server-concepts.mdx)。

MCP 与网页都消费相同知识数据，减少重复开发；如百宝箱采用自带知识库检索，可让 MCP 提供技能差距这一补充能力。选定一种主检索流程后再实装，避免重复搭建两个完整 RAG 系统。

企业版账号权限**已登录核验其中一部分**（2026-09-15，证据：登录态浏览器里的「创建 MCP 服务 → 自部署 MCP」页面，逐字段记录见 `frontend/frotent/frontend1/evidence/tbox-mcp-transport.md`）：接入方式只有 **`sse` 与 `streamableHttp` 两个单选项，没有 stdio**；表单字段就是「MCP URL + Header 键值列表 + 鉴权方式」，可选「不需要授权」。这条实测结论直接决定了本节的技术选择 —— 云端平台里跑的智能体不可能 spawn 本机的 node 子进程，所以 stdio 在架构上就不可能被云端接入，必须提供 HTTP 入口，即 `mcp/http.ts`。

仍**未能核验**的是：该账号下知识库的导入权限与配额、检索中间态（能否拿到命中 chunk id / 分值 / Top-K），以及必须覆盖的多端发布入口与限制。因此平台的「知识库对照实验」暂时做不了，本项目效果评测以自建检索为主（见 `knowledge/KNOWLEDGE_BASE_DESIGN.md` §10 的边界说明）。平台侧设置以实际账号与平台文档为准。

> **待提交表单草稿（2026-09-15，仅备齐，未提交）。** 平台入口：`https://b.tbox.cn/plugin`（登录 `https://b.tbox.cn/login?from=INC-ABOUT`；企业版 `https://tbox.alipay.com/`）。走「创建 MCP 服务 → 自部署 MCP」，按下列值填：
>
> | 字段 | 填值 |
> |---|---|
> | 插件名称 | ≤30 字符（如「生涯导航知识图谱」） |
> | 插件描述 | ≤600 字符（取本文件开头的一句话简介） |
> | MCP 创建方式 | `自部署 MCP` |
> | 接入方式 | `streamableHttp` |
> | MCP URL | `https://careergraph-fuwuwaibao.loca.lt/mcp` |
> | Header | 无 |
> | 鉴权方式 | 不需要授权 |
> | 工具 | `search_career_knowledge` / `get_skill_gap` / `get_career_graph_view`（`tools/list` 实测 3 个） |
> | 数据版本 | kbVersion `2026.09.15` / graphVersion `0.1.0`（63 节点 / 226 边 / 1757 chunk / 26 来源） |
>
> **URL 现状：`https://careergraph-fuwuwaibao.loca.lt/mcp`**（localtunnel 固定子域名，2026-09-15 换掉原先的随机域名 `legal-cloths-sink.loca.lt`）。地址本身固定，但**仍是隧道**：承载它的 `npx localtunnel --port 8787 --subdomain careergraph-fuwuwaibao` 进程一停，地址就失效，平台侧调用随即全部失败。因此它是「可提交的过渡地址」，不是终态 —— 终态应是把 `mcp/http.ts` 部署到公网主机或自有域名。
>
> 公网端到端证据：`frontend/frotent/frontend1/evidence/mcp-public-verify.json`（2026-09-15T12:34:25Z，`MCP_HTTP_VERIFY_TARGET=https://careergraph-fuwuwaibao.loca.lt`，25/25 通过，3 个工具均真实调用，failures 为空）。
>
> **状态：未提交。** 平台侧至今未发起过任何调用；提交前需先确认稳定 URL 与账号下的发布端，免得提交后 URL 失效。

### 多端验证

- Web：真实打开同一原型，完成核心流程并截图。
- 手机浏览器：检查布局和输入交互，作为 Web 响应式适配证据。
- 支付宝/微信小程序：按实际账号和赛事要求接入，在对应开发工具/真机留下证据；截图注明开发预览或已发布。
- 手机尺寸浏览器截图不能标成小程序发布截图。企业版发布的聊天应用与自建 Web 页面若覆盖范围不同，需要分别说明。
- 先确认必须覆盖的端数与可用账号，避免临近提交时才发现权限或审核阻塞；不默认任何一个小程序平台可以自动一键完成另一平台发布。

## 8. 开工次序与完成标准

| 顺序 | 工作包 | 完成标准 |
|---|---|---|
| A | 修复前端启动基线 | 现有配置引用的缺失 hosting/build/worker 依赖得到处理；源码在浏览器可访问，记录实际启动命令和版本 |
| B | 统一演示状态与 1 个主案例 | 切页和刷新保持画像，修改内容能传到后续页；所有模拟请求不依赖缺失后端 |
| C | 打通 path → actions → growth-records → growth | 真正点击提交后出现记录，确认后画像新增证据，能返回原任务 |
| D | 扩到 8 个案例与录制控制 | 相同界面下出现不同推荐/计划，案例隔离、一键重置、样例答案可演示 |
| E | 真实知识库、检索、平台最小连接 | 页面能看到来源，实际检索和 MCP 调用有日志，评测结果可复算 |
| F | 视频与交付材料 | 3–5 分钟视频、平台证据、知识库报告、架构/伦理说明、真实试用反馈与修订记录 |

账号准备、来源整理与 A–C 同时推进。先跑完 1 个案例再扩到 8 个；知识库真实设计与数据整理应早开始，不能等页面全部做完。

## 9. 约 4 分钟演示脚本

| 时间 | 操作 | 要证明的事情 |
|---|---|---|
| 0:00–0:20 | 选 S01；展示案例切换 | 原型支持不同背景，当前是虚构案例模式 |
| 0:20–0:50 | 示例简历 → 修改画像 → 确认 | 用户能控制个人信息 |
| 0:50–1:25 | 看推荐、技能差距，打开一条知识来源 | 推荐理由可解释且有知识依据 |
| 1:25–1:55 | 选择职业、调整每周时间、安排任务 | 目标与时间影响学习计划 |
| 1:55–2:40 | 做一项任务、提交、反馈、确认能力观察 | 有行动与反馈，不只是展示卡片 |
| 2:40–3:05 | 成长记录 → 更新后的画像；切到对照案例 | 状态连通、可区分不同用户条件 |
| 3:05–3:35 | 实际知识检索/平台工具调用与来源 | 真实知识库和平台连接证据 |
| 3:35–4:00 | 实际可用端切换与适配证据 | 说明验证范围，结束演示 |

不把 8 个案例全部逐一录完。完整展示 1 个，再用相近背景不同方向或不同周时长的案例快速对照即可。

## 10. 报告与证据目录建议

```text
knowledge/
  sources/             来源清单与采集材料
  structured/          职业、技能、先修关系、训练任务
  exports/             前端/平台使用的版本化导出
  evaluations/         问题集、参考证据、原始结果、指标
delivery/
  prototype/           原型启动/访问说明与模拟范围
  platform/            配置、MCP 调用、多端实际操作截图
  video/               最终视频与录制脚本
  reports/             知识库、架构/伦理、项目概要与 PPT
  user-feedback/       真实试用反馈、日期、问题与修复记录
```

用户试用记录来自真实试用，虚构的 8 个案例不等于 8 名受测用户。报告分别标注“设计方案、原型模拟、实际实现、实际验证”，保证范围与证据一致。
