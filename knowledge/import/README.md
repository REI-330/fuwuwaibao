# 外部职业库导入层（O*NET / 中国职业分类大典 / 国家职业技能标准 / ESCO）

这一层把公开的职业分类标准导进知识库，规模从自建的 4 个职业扩到万级。

**当前已接入三个来源：**

| 来源 | 规模 | 提供什么 |
|---|---|---|
| O*NET 29.1 | 1,016 个职业 | 职业 → 技能要素 / 知识域 / Hot Technology |
| 中国职业分类大典 | 1,676 个职业 | 职业名称、编号、四级分类路径 |
| 国家职业技能标准 | **702 份标准 / 43,642 行工作要求表格** | 471 个职业 → 14,184 条工作内容（技能）+ 3,118 条职业功能（归类）+ 1,755 个基础知识域 |

合并后外部层共 **21,987 个节点 / 108,662 条边**（另有 9 条桥边接回自建层）。

ESCO 的 API 已实测可用（约 3,000 个职业），按当前范围决定暂不接入，脚本没写。

## 为什么单独一层，而不是直接并进 `exports/career-graph.json`

自建层（步骤 04–11 的 63 个节点 / 226 条边）有一条对所有节点都成立的性质：

> 每个节点、每条边的 `sourceRefs` 都能指回我们自己的 `knowledge/chunks/chunks.jsonl` 里的原文。

这条性质是步骤 04 的子串级引文硬校验在守的，也是 24 题评测、Wiki 内链 ↔ 图谱边双向对应（09-08）
能成立的前提。外部职业库做不到这一点 —— O*NET 的条目本身就是出处，它没有「我们抓的那段原文」。
把两千多个这样的节点塞进同一张图，等于让那条性质变成「对 63 个节点成立、对其余不成立」，
检查就废了。

第二个原因是画布：步骤 10 按设计文档 §3.1 把节点铺进 1050×820 的单张画布，行距已经压到比胶囊高度
少 4px。千级节点在这张画布上是铺不下的，硬铺只会得到一张糊成一片的图。

所以分成两层，各自有各自的产物、各自的检查、各自的消费者：

| | 自建层 | 外部层（本目录） |
|---|---|---|
| 产物 | `knowledge/graph/graph.json`、`knowledge/exports/career-graph.json` | `knowledge/import/build/*.jsonl`、`knowledge/exports/external-career-library.json` |
| 节点数 | 63 | 2,930（1,016 O*NET 职业 + 1,676 大典职业 + 35 技能 + 33 知识域 + 170 工具） |
| 出处 | `sourceRefs` → 自己的 chunk | `standardRef` → 标准条目编号 / SOC 号 |
| 标注 | 有 `annotatedBy`（human / llm-reviewed / llm-draft） | 无（整条按官方数据换算，没有「谁标注的」） |
| 消费者 | 前端画布、MCP 图检索、24 题评测 | 职业检索、自建职业对回公开标准（`aligned_with`） |

两层共用**同一份词表** `knowledge/pipeline/taxonomy.json`：node kind 与 edge type 的字面量不许各写一套。
`frontend/frotent/frontend1/tests/external-library.test.ts` 的第 ① 条断言就是在守这件事。

## 目录与重跑顺序

```
knowledge/import/
  raw/onet-29.1/        O*NET 29.1 官方文本库（原样保存，不改一个字节）
  raw/osta-dadian/      职业分类大典的接口原始返回（tree / careers / summary）
  raw/osta-standards/   702 份标准的文本与表格（PDF 不留；sha256 记在 records/ 里）
  mappings/             人工写的跨库对齐表（occupation-alignments.json）
  build/                导入产物（JSONL + 摘要），可重跑、可 diff
```

```bash
python knowledge/pipeline/import/fetch-osta-standards.py   # 702 份 PDF → 文本 + 表格（约 30 分钟，可续跑）
node knowledge/pipeline/import/fetch-osta-dadian.mjs       # 大典分类树 + 1,676 职业（450 次请求）
node knowledge/pipeline/import/import-onet.mjs             # raw → build/onet.jsonl
node knowledge/pipeline/import/import-dadian.mjs           # raw → build/dadian.jsonl
node knowledge/pipeline/import/import-dadian-skills.mjs    # raw/osta-standards → build/dadian-skills.jsonl
node knowledge/pipeline/import/build-library.mjs           # 合并 + 校验 + 桥边 → exports/external-career-library.json
```

四个 `import*/build*` 都是**确定性**的：同一份 raw 跑两次，输出逐字节相同（排序与字段顺序都固定）。

## 数据来源与版本

- **O*NET 29.1 数据库**（美国劳工部 O*NET Resource Center），`db_29_1_text.zip`，13,159,492 字节。
  <https://www.onetcenter.org/dl_files/database/db_29_1_text.zip>
  本次只取其中 4 张表：`Occupation Data`（1,016 个职业）、`Skills`（35 项技能要素）、
  `Knowledge`（33 个知识领域）、`Technology Skills`（含 Hot Technology 标记）。
  技能边 `specifies` 的 `importance = IM / 5`（归一到 0–1，与自建层 `requires.importance` 同口径），
  `level = LV` 原值（0–7）。`Recommend Suppress = Y` 与 `Not Relevant = Y` 的评分行会被跳过，
  宁可少一条边也不写一个统计上不可靠的数字。
- **《中华人民共和国职业分类大典》**（人力资源和社会保障部 · 技能人才评价工作网 · 职业分类大典系统）
  <https://www.osta.org.cn/career>。接口：`/api/client/get/tree`（分类树）+
  `/api/client/subordinate/data?careerCode=X&versionId=2`（下级职业）。实测拿到
  **8 大类 / 79 中类 / 450 小类 / 1,676 个职业 / 3,053 个工种**。
  注意线上是 1,676，而 2022 版纸质大典的官方口径是 1,639 —— 线上在持续收新职业，比书新。
- **《国家职业技能标准》**（同上网站 · 国家职业标准查询系统 <https://www.osta.org.cn/skillStandard>），
  共 **702 份**，每份是一个职业的完整标准 PDF：职业定义、职业技能等级、基本要求（职业道德 + 基础知识）、
  工作要求（按等级分列 职业功能 / 工作内容 / 技能要求 / 相关知识要求）、权重表。
  接口 `GET /api/public/skillStandardList?pageSize=100&pageNum=N`，
  PDF 走 `GET /api/sys/downloadFile/decrypt?fileName=<standardInfo>`。
  每份约 1.1 MB，702 份 ≈ 750 MB，**PDF 不留**，只留抽好的文本与表格；原件可重下，sha256 记在 manifest。
- **ESCO**（欧盟委员会），公开 API <https://ec.europa.eu/esco/api>，约 3,000 个职业、1.4 万项技能。
  API 实测可用（`/api/search?type=occupation` 返回 200），**本次未接入**。

## 已知取舍（写在明面上，不靠读者自己发现）

1. **语言**：O*NET 的标签是英文，大典的是中文，自建层是中文。外部层的 label 保持原文，
   不做机器翻译 —— 机器翻译的职业名会污染检索关键词，且无法回溯。
2. **技能节点取「工作内容」，不取「技能要求」的每一条**：702 份标准里「技能要求」是句级描述
   （「能检查、识别并确认作业环境和工作场所」），合计五万多条，且几乎每条都绑死在具体工种上
   （「能检查高空作业机械油位」）。逐条建节点得到的是 5 万个只出现一次的节点，不是技能表。
   「工作内容」是它上一层的能力条目（「作业环境识别和安全防护」「设备运行检查」），
   再加它上一层的「职业功能」（「设备维护」「质量检验」）作归类，形成可浏览的三层。
   逐条原文不丢，存在技能节点的 `sampleRequirements` 里。
   **但要如实说：这套标准的跨职业复用很低**（702 份标准跑完：工作内容 836/14,184 = 5.9%、
   职业功能 252/3,118 = 8.1%）。
   它逐个职业描述工作活动，不是一份技能分类法；行数看着多，是因为要求表按五级/四级/三级分别列了一遍。
   想要真正的共享技能表，得回到 O*NET 那类要素模型，或者靠人做同义合并 —— 都不是这个数据源能给的。
3. **不做同义词合并**：「设备维护」和「设备保养」是两个条目。合并它们需要人来做，
   机器猜出来的合并比不合并没有价值。合并率就摆在 `dadian-skills.summary.json` 里，不藏。
4. **两类 `domain` 分得清**：一类是「基本要求 · 基础知识」的小节（机械基础知识、电工与电子基础知识…），
   只由职业直接引用；另一类是「工作要求」表的「职业功能」列（设备维护、质量检验…），
   通过 `belongs_to` 连到技能上。两者字段 `domainKind` 分别是 `knowledge-area` / `occupational-function`，
   不混成一堆。标准里没写「哪项工作内容属于哪个基础知识小节」，那层关系不编。
5. **边上的 `level` 是「首次要求该能力的等级」**：五级/初级工 = 1 … 一级/高级技师 = 5，
   同一能力在多个等级出现时取最小值，含义是「从这个等级起就得会」。不做加权、不编重要度。
6. **两套编号对不齐是常态**：国家职业技能标准按颁布批次追加，大典分类树取的是某个 `versionId`
   快照，会出现「标准里有这个职业编号、大典树里还没有」。这类边被挡在交付物之外，
   并在 `validation.unmatchedStandards` 里计数 —— 不是静默丢弃，数量异常增长时报告里看得见。
3. **分类层级不做成节点**：职业编号 `1-01-00-01` 本身就把大类/中类/小类全编码进去了，
   为复述编号再加 537 个节点和一种新边类型不划算。层级写成 `categoryPath` 字段。
4. **工种（3,053 个）只记数量**：它们是同一职业下的具体分工（如「灌区管理工（灌区供水工）」），
   属于别名而不是另一个职业。
5. **大典名称末尾的 `S` / `L` 已剥离**：那是官方标注符号（通常解释为数字职业 / 绿色职业），
   剥下来存进 `dadianMarkers`。一个名字可以同时带两个（`L/S`），所以两项计数会重叠
   （实测：只标 L 的 115 个、只标 S 的 84 个、两个都标的 24 个）。本层只原样记录，
   不据此在图上做任何判断。
6. **任务节点未导入**：O*NET 有 18,797 条任务陈述，逐条建节点会让节点数翻十几倍，
   而任务本身对「职业 → 需要什么技能」这条主链路的价值低于技能要素。留作后续。
7. **工具节点只收 Hot Technology**：`Technology Skills` 里 8,768 个技术名，O*NET 自己只把其中
   170 个标为 Hot Technology（正在快速增长的技术）。全收会把工具层淹没。
8. **不做跨库合并**：O*NET 的 `15-1252.00 Software Developers` 与 O*NET 的
   `2-02-10-03 计算机软件工程技术人员` 是同一个职业吗？本层**不猜**。两库各自成节点，
   需要合并时靠 `aligned_with` 边显式声明，且必须由人写下 `alignment`（full / partial / related）
   与理由。目前 9 条桥边覆盖自建的 4 个职业，其中 `AI002 机器视觉工程师` 在大典里
   **没有直接对应的职业**，只能挂两条 `related`（工业视觉系统运维员、智能制造工程技术人员）——
   这是事实，不是没找。

   **这条纪律的实测代价（2026-09-30 补，M2-6）**：覆盖率现在 **4/1676 = 0.239%**，
   想提高**只能靠人**。证据脚本 `node knowledge/pipeline/import/bridge-coverage.mjs`
   （只读、可复算）把「按名字机械对齐」走到头：自建层 127 个词条（label + aliases）
   对 1,676 个大典职业 **命中 0**、对 18,552 个职业功能/技能 **命中 1**、对 O*NET 1,253 条
   **命中 1**（两份分别是 `skill:SK111 数据标注`、`tool:selenium`）。
   两套词表本质不相交（「SPI 总线事务设计」vs「(二)孵化操作」），**没有可机械判定的子集**。
   人力上限：472 个职业编号有国标数据、其中 461 个能对上号 → 人写 461 条 `alignment`
   可把覆盖率提到 27.5%。机器替不了这一笔。

## 抓取与解析踩过的坑（都修了，写下来免得重犯）

这一层是整条链路里最脏的一段，值得单独记：

1. **PDF 文本层把条目编号排在了词中间**。原文是「1. 1 监 控 / 运行」，按坐标取文本时编号会插进
   字符串里变成「监控1.1运行」，实测 43,642 行里 13,295 行（61%）中招。
   → `cleanCn()` 去掉任何位置的 `x.y(.z)` 编号并收拢重复标点，清理后残留编号 0 个。
   标点仍可能因位移而语序错乱 —— 这一层只保证「编号不混进内容」，不保证逐字还原。
2. **有一批 PDF 的字体编码是坏的**，取出来只剩 Latin/ASCII（`!`、`?@ABC)`）。留着会在图上多出
   一堆符号节点。→ 要求条目名至少含一个汉字，丢掉的行计数并留样例（实测 258 行工作内容、4 行职业功能）。
3. **「职业功能」是纵向合并单元格**，只有合并块第一行带值。不向下填充，下游会拿到一堆没归类的行。
4. **权重表里也有「技能要求」字样**，不按章切段会把权重表当成能力项收进来。
5. **线程池在这儿完全无效**：pdfplumber 是纯 Python，表格分析全受 GIL 限制，4 线程 → 12 线程反而更慢。
   换进程池之后每份从 ~15s 降到 <1s。
6. **过滤条件必须与建节点时完全一致**，差一条就会产出「指向不存在节点」的边 ——
   实测漏了一条，出现 19 条 `to` 为 `undefined` 的 `specifies` 边。现在组装边时查不到目标直接报错。
7. **`ManagedProcess` 的 stop 杀不掉 Python 子进程**，旧任务会在后台继续抢带宽和 CPU，
   让新任务慢一倍。停任务后要显式确认进程真的没了。

## 下一步

- 把外部层的技能接进检索与推荐：现在 6 万条 `specifies` 边只是数据，MCP 的
  `search_career_knowledge` / `get_skill_gap` 还只读自建层的 63 个节点。要让千级职业真正可用，
  得让索引与工具也读外部层 —— 这是接入之后的下一个工作量。
- 若之后要接 ESCO：同一套 `lib/external.mjs` 的 `slug` / `assertUnique` / `toJsonl` 可以直接复用。
