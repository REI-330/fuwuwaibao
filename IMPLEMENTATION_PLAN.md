# 实施计划（2026-09-27 修订版）

> 本文档是**唯一的开发计划入口**。旧版（FastAPI 假设、后端目录规划）已作废重写。
> 产品范围与页面说明见 `PAGE_FUNCTION_MAP.md`；架构与实测事实见 `项目架构与技术文档.md`；知识库方法与结果见 `knowledge/evaluations/系统知识库构建方法与效果评估报告.md`。

## 0. 已拍板决策（不要再动摇）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 交付形态 | **本地跑 + SQLite 单文件**。不上公网主机，不上 Cloudflare Worker/D1 |
| 2 | 学习单元关系 | **新增 `covers` 边（knowledge → skill）**，9 个单元手工标注，不重跑抽取 |
| 3 | 主检索归属 | 走 **A → B**：先修 α 过拟合（A），再做「自建 + WeKnora 双路召回，统一重排」（B）。**不重构段 id 体系** |
| 4 | 演示范围 | 就按现役规模交付：**4 个职业 / 26 来源 / 1757 段**。不追规模数字 |

补充纪律：**冻结留出集已消费过一次，后续任何配置改动不得再用它验收**，需要新建留出集。

## 1. 关键设计约束（不可违反）

1. **AI 观察不得直接变成能力结论**：未经用户确认，只能写入候选（candidate），确认后才落 records/evidence/events。
2. **确认必须事务化且幂等**：同一 candidateId 重复提交返回同一条记录，不产生重复行。
3. **市场薪资与趋势必须带来源和更新时间**；拿不到就明确显示缺失，不许估。
4. **检索必须有引用，没有依据就说没有**；未审核内容（`annotatedBy=llm-draft`）在下游要降权且可辨识。
5. **不新增图谱结论**：后端只做字段映射与既有边的邻域查询；导出里没有的字段如实留空。
6. **未实现的接口保持 501**，不假装可用；前端对 501 要有明确降级界面。

## 2. 里程碑与任务

### M1 · 让主链路能跑通（预计 2–3 天）—— ✅ 已完成（2026-09-30）

| # | 任务 | 验收标准 | 依赖 | 状态 |
|---|---|---|---|---|
| M1-1 | 后端 SQLite 持久化 | 画像/证据/成长事件落库；重建 `CareerApi` 实例后数据仍在；进程重启后 curl 仍读得到 | 决策 1 | ✅ |
| M1-2 | `POST /api/auth/guest` + 会话隔离 | 返回 201 + HttpOnly Cookie；两个会话画像互不可见；无 Cookie 回落 `user_local`（保证现有测试不破） | M1-1 | ✅ |
| M1-3 | `GET /api/growth-records` + `POST /api/growth-records/confirm` | 候选→记录→证据→事件**一个事务**写入；重复 confirm 不产生重复行 | M1-1 | ✅ |
| M1-4 | 路径引擎 `POST /api/v1/career-path/generate` | 4 个职业都能生成；`hard_checks` 全 false；拓扑序满足所有 prerequisite 边；阶段不早于前置；同输入两次输出完全相同；未知职业 404 | 无 | ✅ |
| M1-5 | `/work-map`、`/catalog` 从 redirect 改真页面 | 两个页面能渲染职业/技能真数据；career-matches 的 501 显示「尚未上线」而非白屏 | 无 | ✅ |
| M1-6 | `/path` 读已确认画像 | 去掉 `AI001`/`SK215=3`/`weekly_hours=10` 硬编码；每周小时数可改且影响周数 | M1-4 | ✅ |
| M1-7 | 请求层统一 | onboarding 里 6 处相对 `/api` 裸 fetch 全部走 `apiUrl()` | 无 | ✅ |

**落地要点（2026-09-30）**

- **M1-1**：`MemoryStore` 新增 `profiles` / `profile_evidence` / `growth_events` 三张表（stdlib `sqlite3`，与记忆库同一个 `career.db`）；`ProfileStore` 接受可选 `memories` 参数做读写透传，**不传时保持旧的纯内存行为**（既有单测不必改）。证据与事件的 `id` 由内容指纹决定，重复写入幂等。
- **M1-2**：`POST /api/auth/guest` 下发 `career_session=user_<12hex>` + `HttpOnly; SameSite=Lax`；`CareerApi.handle` 新增 `cookies` / `response` 两个**每请求**参数（不用实例属性——`ThreadingHTTPServer` 每请求一线程，实例属性会被并发覆盖）。`user_id_from()` 校验 Cookie 形状，非法即回落 `user_local`（不报 401）。`register`/`login` 保持 501。
- **M1-3**：`confirm_growth_candidates()` 在一个事务里改 `memory_items.status`、写 `profile_evidence`、写 `growth_events`；越权（传别的记录的 candidateId）返回 400 而不是静默跳过。事件刻意不设外键：记录删掉后事件仍是历史。
- **M1-4**：新增 `backend/career_path.py`（零三方依赖）。阶段由**先修深度**决定（不是目标等级——按目标等级会让一条高等级公共先修把全部后继顶到最高阶段，实测整条路径塌进一个阶段）。`prerequisite_promotion` 保证「阶段不早于前置」。6 指标 / 6 硬校验 / 工作量全部由算出的结构自检得出。假设（`HOURS_PER_LEVEL=24`、各阶段建议最长周数）**写在常量里并在 `warnings` 里明说**，不冒充语料结论。
- **M1-5/6/7**：`/work-map`、`/catalog` 挂真组件；`career-match-panel` 对 501 显示「职业匹配尚未上线」而不是把用户领去重建画像；`/path` 的目标职业按 `?occupation=` → 画像候选 → 目录第一个回落，每周小时数可改；onboarding 六处裸 fetch 统一走 `apiUrl()`。

**M1 完成定义**：`npm test` + `pytest` 全绿；五个入口全部是真页面；画像刷新/重启后仍在。→ **已满足**（pytest 130、前端 12、`e2e` 169/169）。

### M2 · 知识库补齐（预计 3–5 天）—— 部分完成（2026-09-30）

> 状态汇总：**M2-2 / M2-3 / M2-4 / M2-5 / M2-7 ✅**（M2-4 的结论是「已评估、候选池无互补→不投入」）；**M2-1 / M2-6 ⛔ 卡在「只能人来做」的裁定**（同一类门禁）。

| # | 任务 | 验收标准 | 依赖 | 状态 |
|---|---|---|---|---|
| M2-1 | 新增 `covers` 边 | taxonomy 有 covers；9 个学习单元各 ≥1 条、`sourceRefs` 指向真实 chunk；09 步硬约束仍 13/13；导出与前端 `RELATION_LABELS` 同步；相关测试计数更新为实际值 | 人工裁定 | ⛔ **受「不代签」阻塞**（见下） |
| M2-2 | 修两条断链脚本 | `probe-heldout.mjs`、`weknora/build-stack.mjs` 读现役题集、退出码 0；拒答题不计入分母；防泄漏校验保留 | 无 | ✅ 实测双双 exit 0 |
| M2-3 | 补 `publishedAt` | 26 份来源逐份核实发布时间；拿不到的明确标缺失（不猜）；前端据此显示时效 | 需查资料 | ⚠ 部分（核实属人力 → 改为把「缺失」做实，见下） |
| M2-4 | 检索双路召回（B 方案） | 自建 + WeKnora 各出候选，合并后统一重排；用**新建的留出集**报数；id 体系不变 | 本机 WeKnora 产物 | ✅ **已评估：候选池无互补** —— 并集在 dev **+0 题**、holdout 仅 **+1 题**（XC01），落在 ±1 波动内；重排无从增益，故不再投入（见下） |
| M2-5 | α 过拟合修正（A 方案） | 融合权重改为按查询自适应或仅中文层启用；在新建留出集上不低于单路向量 | 同上 | ✅ **已达标 + 可选门控**（见下） |
| M2-6 | 大典职业接进图谱 | 桥接覆盖从 4/1676 提到一个有意义的比例，且每条桥边有 `sourceRefs` 与对齐度 | 映射规则 | ⛔ **定不出机械规则**（见下，0/127 名字重合）；只能人工裁定，与 M2-1 同类门禁 |
| M2-7 | 用现成端点重跑重排档 / 裁判档 | `knowledge/eval/rerank_experiment.py` 等在同一题集上跑通并报数；**只用 dev / 新建留出集**，不动已消费的 holdout | LLM 端点**已可用** | ✅ 重排档 + 拒答档 + **`--judge` 答案表述核查 15/15**（dev） |

> **M2-1 为什么没做（这条留痕比做完更重要）**
>
> `covers` 边（学习单元 → 技能）**已经从词表到代码实现了一遍**：taxonomy 加了 `covers`、
> seed.json 的 9 个学习单元各手写了 1–4 个技能、04 步生成了 20 条边（`mustAppear=['to']` 硬校验、
> `sourceRefs` 取技能自己的依据段）、07 步加了渲染、前端加了 `RELATION_LABELS.covers`、
> 后端 `occupation_detail.stages[].covers` 也接上了 —— 跑通到 **63 节点 / 246 边 / 09 步 13/13**。
>
> 然后卡在第 06 步：
>
> ```
> [kb:error] knowledge/review/adjudication.json 还不能用来裁定（1 项）：
> [kb]   - spotCheck.reviewed=22，必须等于抽样条数 27
> ```
>
> 新增 20 条边把人工裁定的**抽样**从 22 条顶到 27 条，而 `adjudication.json` 开头写得很清楚：
>
> > 「本文件是「人在回路」的实证：reviewer / reviewedAt / 各组 verdict 由人填写，
> >   06-adjudicate.mjs 只负责校验与应用，**不代签**。」
>
> 也就是说：**加图谱内容 = 需要新增人工裁定**。在「不依赖人工审核」的前提下，
> 正确做法不是替人把 5 条 decision 填上（那是伪造审核记录），而是**回退这次改动**、
> 把结论记在这里，等有人的判断可用时再上（代码路径已验证过一次，重做成本很低）。
>
> 现状：图谱仍是 **226 边 / 9 类关系**，`graphVersion 0.1.0`；`knowledge/README.md` 与
> `项目架构与技术文档.md` §11 第 11 条保留为已知缺口。
>
> **顺带得到两条可复算的事实**（对交付有用）：
> 1. **管线 01–08 是确定性的**：同一 seed + 同一语料重跑 04，226 条边**逐条一致**，
>    只有 `updatedAt`/`version` 这类「什么时候算的」字段变成当天（实测 id 顺序、字段值全部相同）。
> 2. **改图谱结构的真实成本是「人工裁定 + 217 处对外计数」**，不是改几行代码 ——
>    所以这类改动要么一次做对，要么别开。

> **M2-3 最后落成了什么（2026-09-30 二度更新）**：先是把「逐份核实发布日期」降级为**把「缺失」做实**；
> 后来发现**「缺」这个判断本身下得太早** —— 快照里其实有日期，只是没人去解析。于是新增
> `knowledge/pipeline/13-annotate-source-dates.mjs`（离线、幂等、**正式解析 JSON-LD 并按 `@type` 收窄**，
> 绝不正则抓裸日期），实测结果：
> * **发布时节能核到 3/26**（S08/S09/S10 的 `schema.org Article.datePublished` = 2023-11-12）；
> * **页面"最后更新"能核到 5/26**（S08–S12 的 `dateModified`）—— 单独存进 `sourceUpdatedAt`，
>   **不拿更新时间冒充发布时间**；
> * 其余 **21 份确实两者皆无**，保持 `null`，仍**不拿 `collectedAt` 顶上**；
> * 每条的取值字段记进 `publishedAtSource` / `sourceUpdatedAtSource`，便于人工复核。
>
> ⚠️ 已如实写进数据的一条观察：**S08/S09/S10 三份共享同一个 `datePublished`**，疑似站点模板常量
> 而非逐页真实发布日 —— 第 13 步会在日志里点名提示，没把这三个数当成「核实无误」。
>
> * MCP 的引用输出带 `sourcePublishedAt` / `sourcePublishedAtSource` / `sourceUpdatedAt` / `sourceTimeNote`：
>   没有发布时间时明确写缺什么，**并区分「有更新但无发布」与「两者都没有」**，
>   调用方拿到的不是一个空字符串，而是一句它必须显示的话；
> * 类型也从 `publishedAt: string` 改成 `string | null` —— 之前那个类型是**在撒谎**。


> **M2-7 进展（2026-09-29）**：在现成端点上把「已采纳配置」四档在 **dev（34 道可答 + 15 道拒答）**上真跑了一遍
> （`python knowledge/eval/run_full_eval.py --questions knowledge/evaluations/questions-dev.json --tag m27-dev-`）：
>
> | 档位 | 命中 | 命中@1 | 精确率@3 | 答案段 |
> |---|---|---|---|---|
> | BM25 | 25/34 | 0.5588 | 0.3529 | 36/102 |
> | VECTOR（int8/ONNX，本机 `127.0.0.1:8090`） | 28/34 | 0.5294 | 0.3725 | 38/102 |
> | FUSION α=0.35 | 29/34 | 0.5588 | 0.4020 | 41/102 |
> | **FUSION+RERANK（采纳配置）** | **30/34** | **0.7647** | **0.4510** | 46/102 |
>
> - 产物：`knowledge/eval/runs/m27-dev-{bm25-full,bm25-topk,vector-topk,fusion-a035-topk,adopted-a035-topk,rerank-cache,summary}.json`；
>   **未动已消费的 holdout / test**（题集只用 `questions-dev.json`）。
> - 重排档结论：命中只 +1（29→30），但**命中@1 从 0.56 提到 0.76**、精确率@3 从 0.402 提到 0.451 ——
>   重排的收益主要在「把本来就召回对的排到第一」，与既有判断「瓶颈在排序不在召回」一致。
> - **端点现实成本要如实记**：单题重排 17–150 s（推理模型 + 10 候选长提示），串行跑完 34 题约 40 min+。
>   本次按**同一配置、同一端点、同一个缓存文件**分片续跑（每题的重排是独立的 listwise 判断，分片只影响墙钟时间、
>   不影响任何一题的判定），最后用**完整缓存的整集一次运行**出数；分片中间产物已清理，只留 canonical 的那一套。
> - 拒答档：`python knowledge/eval/refusal_eval.py --set knowledge/evaluations/questions-dev.json --tune`
>   → 最优阈值 **t=0.58**，拒答正确率 **15/15**、误拒率 **0.0588**（2/34：N13、N12）、平衡准确率 **0.9706**
>   （产物 `knowledge/eval/runs/refusal-eval-dev.json`）。
> - **答案表述核查（`--judge`）已补跑**（2026-09-30）：对 dev 的 15 道拒答题各生成一次回答再交裁判，
>   **15/15 明确说「资料不足」，正确率 1.0000**（写进同一份 `refusal-eval-dev.json` 的 `answerSideCheck`）。
>   M2-7 至此三项（重排档 / 拒答档 / 裁判档）齐全。

> **M2-5 做成了什么（2026-09-30）**：详见 `knowledge/evaluations/语言自适应融合.md`（零 LLM，可随时复算）。
> * **旧 test 的「融合伤 xl」反转不复现**：旧 test（494 段语料）xl 层「单路向量 14/21 > 融合 8/21」；
>   现役 1757 段语料的新 holdout 上 xl 层是「融合 **12/21** > 单路向量 **11/21**」，总分 26/35 > 22/35
>   —— 所以**验收「不低于单路向量」由现役配置本身就已满足**。
> * 仍加了一道**查询自适应门控**（`peak = BM25 榜首/第 10 名`，`< 1.5` 就退单路向量）修掉
>   「minmax 把噪声拉满」这个机制：**dev 29→31/34**（追平拿标注做分流的 oracle）、**holdout 26→27/35**
>   （xl 12→**14**/21）。阈值 1.40–1.60 是平台，对阈值不敏感。
> * **处置**：增益只有 +1～2 题（落在项目自己定义的「±1 波动」边缘），所以**默认不改现役配置**，
>   把它做成 `run_adopted.py --gate-peak 1.5` 的**可选项**；三条被 holdout 筛掉的备选
>   （GATE-CJK 阈值不可迁移、GATE-LATIN 倒扣、绝对幅度归一化零增益）记在文档 §5。
> * 顺带修了一个复算坑：`run_adopted.py` 等脚本的语料缓存名由 `TEI_MODEL` 决定，未设时会取到
>   float32 那份，与现役 int8 服务**不同源**（命中数差 1–3 题）—— 现已改为未设时打印警告。

> **M2-6 为什么没做（2026-09-30：把「需先定映射规则」这条依赖查清了）**
>
> 原以为 M2-6 只是「定一条映射规则 + 写边」，实测发现**定不出机械规则**。证据脚本
> `node knowledge/pipeline/import/bridge-coverage.mjs`（只读、可随时复算）：
>
> | 项 | 实测 |
> |---|---|
> | 现在覆盖率 | 9 条桥边 → **4/1676 = 0.239%**（大典 5 条 / O*NET 4 条；full 2 / partial 5 / related 2） |
> | 自建层参与比对的词条 | **127**（63 个节点的 label + aliases，归一化去重） |
> | 与**大典 · 职业**（1676 条）名字重合 | **0** |
> | 与**大典 · 职业功能/技能**（18,552 条）名字重合 | **1**（`skill:SK111 数据标注`） |
> | 与 **O*NET 全部条目**（1,253 条）名字重合 | **1**（`tool:selenium` ↔ `Selenium`） |
>
> 两套词表**本质上不相交**：自建层是「SPI 总线事务设计」这种细粒度工程技能，外部层是
> 「(二)孵化操作」这种按职业写的工作内容条目。**任何自动生成的桥边都是「猜」**，而
> `knowledge/import/README.md` §8 明确禁止：
>
> > **不做跨库合并** …… 需要合并时靠 `aligned_with` 边显式声明，**且必须由人写下
> > `alignment`（full / partial / related）与理由**。
>
> 所以 M2-6 与 **M2-1 是同一类门禁**：不是「没写代码」，是「这一步只能人来做」。
> 机器能做的只有把候选范围收窄，收不窄 —— 1,676 条里没有可机械判定的子集。
>
> **人力上限（供排期，不是承诺）**：472 个职业编号有国标「工作内容」数据，其中 **461 个**
> 能在 1,676 个职业树里对上号 → 人把这 461 个职业各写 1 条 `alignment`，覆盖率可从
> 0.24% 提到 **27.5%**。这一步没有任何地方可以外包给脚本。

### M3 · 交付与合规 —— ✅ 完成（2026-09-30）

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| M3-1 | 根 `README.md` | 环境要求、启动命令、**唯一构建源提示**、哪些跑得通/跑不通、已知坑 | ✅ 已补「跑得通 / 跑不通」表 + 8 条「已知坑」+ 文档索引加两份新文档 |
| M3-2 | `LICENSE` + 来源许可矩阵 | 26 份来源 × 许可 × 可否再分发；3 份 AGPL-3.0 单独说明 | ✅ 矩阵完成（`knowledge/SOURCE_LICENSES.md`）；**`LICENSE` 故意没建** —— 选哪个许可只有作者能定（见文件 §6） |
| M3-3 | 数据边界说明 | 仓库里有什么、什么要重下、什么根本没拿到（中国官方语料 0 过审） | ✅ `DATA_BOUNDARY.md`（入库/不入库各 5 项、拿不到的 7 项、复算命令） |
| M3-4 | 仓库清理 | 清掉 `knowledge-cn/data/sqlite-amalgamation*`；确认 `.env` 未被跟踪 | ✅ 删掉 `sqlite-amalgamation/`（11 MB）+ `.zip`（2.8 MB）+ `element.md` + `pelican_bike.html`（均已 `git add -u` 记为删除）；`.env` 未跟踪已验证 |
| M3-5 | 演示脚本与视频 | 3–5 分钟，覆盖「画像→推荐→依据→路径→行动→记录」 | ✅ **改为可复算的演示脚本**：`npm run demo` → 9 步、真起后端、真数据、exit 0。理由见 `scripts/demo-walkthrough.mjs` 头部：视频只能证明「当时跑过」，脚本能证明「你现在也能跑出同样的东西」，且随代码被回归覆盖 |


## 3. 外部阻塞（2026-09-30 复核）

**没有卡在外部依赖上的东西**（端点、数据都在盘上）；但有 **两处是「只能人来做」的裁定**，
如实列出 —— 它们不是「没写代码」，`descope` 也替代不了：

| 事项 | 处置 | 依据 |
|---|---|---|
| 重排档 / 裁判档评测 | **已解** | `knowledge/eval/.env` 端点可用，产品/评测统一到 `backend/llm.py`；向量档所需 TEI 兼容服务本机在跑（`127.0.0.1:8090`） |
| WeKnora **标准版**对照 | **不做（不影响功能）** | 对照已用 **Lite 版**完成并有结论（`knowledge/evaluations/WeKnora对照评测.md`：同套重排下 test 17/21 vs 自建 13/21，差距全在候选池）。标准版只多测图谱/GraphRAG、自带 rerank、docreader，属**对照上限**而非产品能力；产品零 WeKnora 依赖 |
| 检索双路召回 / α 自适应（M2-4/M2-5） | **两项都已做（结论：一项采纳为可选、一项无可吃空间）** | M2-5 见 `knowledge/evaluations/语言自适应融合.md`（零 LLM、可复算：dev 29→31/34、新 holdout 26→27/35）。M2-4 见 `实验总表.md` F 组：把 WeKnora 思路的 2,861 子块按区间映回 1,757 段取并集，**dev 并集 +0 题、holdout 仅 +1 题（XC01）**，落在 ±1 波动内 → 候选池无互补，统一重排无从增益 |
| 大典 / O*NET 接进自建图谱（M2-6） | ⛔ **卡在「只能人写」** | 数据早在盘上，但**定不出机械映射规则**：自建 127 个词条 vs 大典 1,676 个职业 **命中 0**、vs 18,552 个职业功能/技能 **命中 1**、vs O*NET 1,253 条 **命中 1**（`node knowledge/pipeline/import/bridge-coverage.mjs`）。`import/README.md` §8 要求 `aligned_with` **必须由人写下 `alignment` 与理由** → 与 M2-1「不代签」同类。人力上限 461 条 → 27.5% |
| 学习单元 `covers` 边（M2-1） | ⛔ **卡在「不代签」** | 见 M2 段注记：代码路径已验证过一次，卡在第 06 步人工裁定抽样（22→27），回退保持 226 边 |
| 26 份 `publishedAt`（M2-3） | **已用足可核验的部分**：3 份有发布时间（S08/S09/S10）、5 份有最后更新时间（S08–S12），其余 21 份保持 `null` | 新增 `knowledge/pipeline/13-annotate-source-dates.mjs`（离线、幂等、**正式解析 JSON-LD 而非正则抓裸日期**）；`publishedAtSource` / `sourceUpdatedAtSource` 逐条记下取值字段供复核 |
| 中国官方语料 | **已入库（2026-10-01）** | 1519 条由业主**豁免闸门**（`WAIVED`，非人工审核；`promote.py`/`waive_review.py` 可复算），已导入 WeKnora Lite 并通过核验（**5 份文档 / 2612 chunk**、记录 ID 0 缺失、向量完整、活检索有命中，`knowledge-cn/verify_import.py`）。**检索质量评测已跑完三版（2026-10-01）**：新题集 33 题（可答 26 / 超范围 7），现役混合检索命中@1 73.1% / @5 92.3% / @10 100% / MRR 0.819（改造前 61.5% / 0.700，只改结构那版 50.0% / 0.632），报告 `knowledge-cn/evaluations/检索质量评测-20261001.md`；现役配置 = 结构化目录一块一条记录 + 工资统计 2400 字一块 + RRF 权重 0.2/0.8。注意是作者自出题、非业主人工判定，且超范围题分数不可分（检索层无法拒答） |
| 百宝箱平台接入 | **不做** | 需固定公网地址 + 账号 |
| 多端发布验证 | **不做** | 需对应平台账号与审核 |

> 口径：外部数据只解决「内容从哪来」，不解决「能力有没有」。M2 的 `covers`（M2-1）与大典桥接（M2-6）
> 属于**同一类**：机器能算范围、能出证据（`06-adjudicate.mjs` 的抽样、`bridge-coverage.mjs` 的命中统计），
> 但**下结论的那一笔只能是人**。在「不依赖人工审核」的前提下这两项就是做不到 —— 如实留白，不替人签。

## 4. 已完成（不要再做一遍）

| 事项 | 证据 |
|---|---|
| 1-12 步离线构建管线 | `knowledge/pipeline/01..12`，09 步 14 项校验 13/13 通过 |
| 版本化图谱导出 | `knowledge/exports/career-graph.json`（26 来源 / 1757 段 / 63 节点 / 226 边 / 38 wiki） |
| MCP 三工具 + 双传输 | `npm run mcp:verify`（stdio 14/14）、`mcp:verify-http`（25/25），证据在 `evidence/` |
| 最小后端 15 组路由 | `backend/server.py`，**未实现接口显式 501**（**2026-09-30 起仅余 `/api/auth/{register,login}`** —— 职业匹配已实现） |
| 测试 | 前端 12 项、后端 **204** 项（图谱/契约 + 记忆库 + LLM/触发器 + 对话 + 成长记录 + 简历 + 画像落库与画像历史快照 + 访客会话 + 成长确认 + 路径引擎 + 职业匹配 + 模拟面试 + 跨岗位训练 + 任务实践/附件），端到端 `npm run e2e` **237** 项断言（`e2e:all` 237 再加既有套件），均通过。**逐文件计数以 `python -m pytest backend/tests --collect-only -q` 为准**，别抄旧表 |
| 画像/证据/事件持久化（M1-1/M1-3） | `backend/memories.py` 新增 `profiles` / `profile_evidence` / `growth_events` 三表；`ProfileStore` 读写透传同一 `career.db`；`POST /api/growth-records/confirm` 一事务确认且幂等 |
| 访客会话（M1-2） | `POST /api/auth/guest` → 201 + `HttpOnly` Cookie + 按 `user_id` 隔离；无/坏 Cookie 回落 `user_local`；`register`/`login` 仍 501 |
| 路径引擎（M1-4） | 新增 `backend/career_path.py`（零三方依赖）：先修深度定阶段 + 拓扑序 + 6 指标 / 6 硬校验 / 工作量自检；同输入两次输出完全相同；未知职业 404 |
| 五个入口全通（M1-5/6/7） | `/work-map`、`/catalog` 挂真组件（200 + SSR 出内容）；`/path` 读已确认画像、每周小时可调；onboarding 六处裸 fetch 走 `apiUrl()` |
| 职业匹配（2026-09-30 新增） | `backend/career_match.py` + `/api/career-matches/{current,generate,select}`：**不再是 501**。四维打分（技能 0.50 / 兴趣 0.25 / 经历 0.15 / 入门可行性 0.10，权重是写在常量里的**产品假设**）+ 逐条依据（`reasons[].source`）+ 图谱缺口（skills 的 `targetLevel`/`importance`）+ 待验证问题。**不新增图谱结论、不臆造英文职业名、没有依据的维度记 0 并明说**；未生成 404 / 过期 409 `CAREER_MATCH_STALE` / 画像太空 409 `INSUFFICIENT_PROFILE`。测试 16 项、e2e 9 项，**全离线、不需要任何外部数据源** |
| 记忆库 | `backend/memories.py`（stdlib + SQLite）+ 6 条 `/api/memories*` 路由 + `/growth` 记忆面板；候选闸门 / 写时触发器 / persona+联想召回 / 删除即遗忘，端到端全部有断言 |
| 对话注入 | `backend/chat.py` + `POST /api/chat`：已确认记忆 + 图谱事实拼成固定可审计前缀；配了模型用模型、否则降级规则版（`provider`/`llm.error` 逐轮回传）；端到端离线档与真模型档各钉一遍 |
| 成长记录 → 候选记忆 | `backend/growth.py` + `/api/growth-records`（写记录同事务派生候选、按 `recordId` 幂等、删记录只清未确认候选）；档案页有「写入记忆候选」入口 |
| 简历解析 | `backend/resume.py` + `POST /api/resumes/extract`（**文本 / DOCX / PDF**；DOCX 走 stdlib 零依赖，PDF 走运行时探测的可选后端；图片与 `.doc` 415 + 可执行建议）；产出画像草稿 + 待确认候选，每条抽取带原文 `charRange`；onboarding 的 760ms 假数据已删除，改走真链路 |
| LLM 接入层 | `backend/llm.py`：产品侧（记忆触发器）与评测侧（重排/裁判/出题/术语表）**共用一份配置与重试纪律**；9 个评测脚本已从「各自手写 HTTP」改为 `from _shared_llm import chat`；实测生成可用触发器（24 s，概念高一级抽象） |
| 评测体系 | dev 34 / test 32 / 拒答 30；冻结结果 24/32；拒答零误拒 |
| 检索自适应门控（M2-5，2026-09-30） | `knowledge/eval/adaptive_fusion.py`（零 LLM）：dev 29→**31/34**、新 holdout 26→**27/35**；`run_adopted.py --gate-peak 1.5` 可选项；详见 `knowledge/evaluations/语言自适应融合.md` |
| 裁判档（M2-7，2026-09-30） | `refusal_eval.py --judge`：dev 15 道拒答题**答案表述核查 15/15 = 1.0000**（写进 `refusal-eval-dev.json` 的 `answerSideCheck`） |
| 大典桥接证据（M2-6，2026-09-30） | `knowledge/pipeline/import/bridge-coverage.mjs`（只读、离线）：覆盖率 4/1676 = 0.24%；「按名字机械对齐」命中 0/1/1 → 结论「只能人工裁定」 |
| 交接文档 | `项目架构与技术文档.md`、`PAGE_FUNCTION_MAP.md`、前后端页面契约 |
| GBK 崩溃修复 | commit `3f674a1` |
| 模拟面试 + 跨岗位沟通训练（2026-10-01 从队友 `career-ai-system` 移植） | `backend/interviews.py`、`backend/cross_role.py`、`backend/resume_store.py`、`backend/data/cross_role_questionnaires.json`（32 职业 / 320 题）；路由 `/api/v1/interview-skills`、`/api/v1/interviews*`、`/api/v1/cross-role/*`、`GET /api/resumes`；前端 `/actions` → `/mock-interview`、`/scenarios/cross-role`。**差异与「没搬的部分」见 `模拟面试与跨岗位训练移植说明.md`** |  |
| 任务实践（2026-10-01，本项目自己的设计） | `backend/tasks.py`（任务由路径引擎从图谱 `task --trains--> skill` 边派生）+ `task_runs` 表；路由 `GET /api/tasks`、`GET /api/tasks/<id>`、`POST /api/tasks/<id>/runs`、`POST /api/task-runs/<id>/evaluate`；前端 `/actions/tasks[/<taskId>]`，`/path` 的任务卡片已接上。**提交只写成长记录 + 待确认候选；评估不落已确认能力**（`test_tasks.py` 11 项钉住） |  |
| 任务附件真上传（2026-10-01 第五轮） | `task_attachments` 表（**字节存 `content`**）+ `TaskStore` 附件读写；路由 `POST /api/tasks/<id>/attachments`（multipart，5MB / 每条任务 20 个）、`GET /api/attachments/<id>`；提交时 `attachmentIds` 必须是**自己在这条任务下**上传过的（否则 422 `UNKNOWN_ATTACHMENT`）。与简历解析刻意不同：**收得下就存**，读不出就说读不出；**附件文字不进评估输入**（不算能力证据）。前端 `/actions/tasks/<taskId>` 加文件上传与勾选引用（`test_tasks.py` 另 6 项钉住） |  |
| 缺口收口（2026-10-01 第六轮） | ① 成长记录**分页 + 落库**：`GET /api/growth-records` 加 `limit`/`cursor` 与 `total`/`nextCursor`/`hasMore`，`ProfileProvider` 改服务端时间线；② 对话**会话落库**：`chat_sessions`/`chat_messages` + `GET|DELETE /api/chat/sessions*`，候选确认写记忆库待确认区；③ 任务**个人视图覆盖层**：`task_overrides` + `PATCH /api/tasks/<id>`、`POST /api/tasks/reorder`；④ 附件**原字节下载与删除**（被引用 409）；⑤ 侧边导航补 `/work-map`/`/catalog`/AI 对话；⑥ 过时文档口径同步 |  |
| 知识库评测补完（2026-10-01 第六轮） | 偏语义改写题集 + RRF 权重外推、引用支持度/回答质量（真模型）、结构化目录切块扩展扫描、超范围题跨语料复核；raw 快照 sha256 成因取证（结论：git 行尾转换）。报告在 `knowledge-cn/evaluations/`，脚本在 `knowledge-cn/eval/` 与 `knowledge/eval/probe_raw_sha.py` |  |
| 画像历史快照（2026-10-01 第七轮） | `profile_snapshots` 表（**只增不改**）+ `ProfileStore` 每次写入/确认留一份；`GET /api/profile/history`（位移分页）与 `GET /api/profile/history/<snapshotId>`；快照 id 由 `(user_id, profileVersion, status)` 派生 → **重复 confirm 幂等**。前端 `/growth-records` 底部「画像历史档案」区块（只读）。补齐 `PAGE_FUNCTION_MAP.md` §10 长期标注的「仍未做：路径快照式历史版本回溯」 | `backend/memories.py`、`backend/server.py`、`backend/tests/test_profile.py`（5 项）、`lib/client/profile-api.ts`、`app/(product)/growth-records/page.tsx` |

## 5. 待清理的卫生问题（低优先，但影响交接）

> 状态（2026-09-30 收口）：**4 条全部处理完**，留痕如下。

| # | 原问题 | 处置 | 状态 |
|---|---|---|---|
| 1 | 顶层有无关遗留文件：`element.md`（牌局笔记）、`pelican_bike.html` | 已删除（提交 `8ee8006`）；同批删掉 `knowledge-cn/data/sqlite-amalgamation/`（11 MB）+ `.zip`（2.8 MB）、以及未入库的队友第三方包副本 `mcp-server(1)/` 与两份重复 zip | ✅ |
| 2 | 规划类文档重复，没标注哪份是现行 | 根 `README.md`「文档索引（哪份是现行）」已用 ★ 标出现行、并把 `DEMO_PROTOTYPE_PLAN.md`/`FRONTEND_MVP_FEATURE_LIST.md` 标为**仅供参考** | ✅ |
| 3 | `knowledge/` 顶层没有 README，11 步跑法散落 | 已有 `knowledge/README.md`：目录结构、11 步跑法、口径（中文占比/快照 sha256）、哪些跑得了 | ✅ |
| 4 | 评测类文档 7 份且有「不可复算」的历史结论 | `knowledge/evaluations/` 已有 `README.md` + `实验总表.md` 做索引，标明哪份可复算；本轮新增 `语言自适应融合.md`（零 LLM 可随时复算） | ✅ |

**同批处理的其它交付卫生问题**：

- `knowledge/eval/runs/` 里 `m27-shard1b-*` 三个分片中间产物已清（实为 canonical 34 题结果的 **4 题真子集** D05–D08，已用脚本核验为子集后才删）。
- 复核 `.env` 未被跟踪（`.gitignore` 用 `.env*` + `!.env.example` 通配，避免 `.env.bak` 之类漏网）。
- 137 项未提交改动按主题拆成 8 个提交（见 `交付验收对照表.md` 末尾清单），每个提交自洽、可单独回滚。
