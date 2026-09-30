# 知识库（`knowledge/`）

三层结构 + 一份版本化导出。**前端、后端、MCP 三端读的是同一份导出**，不各建一套检索。

## 现役规模（都能复算）

| 项 | 值 | 复算方式 |
|---|---|---|
| 来源 | **26**（其中 25 份有 raw 抓取快照，S26 走 `import/`） | `knowledge/sources/sources.json` |
| 文本分块 | **1757 段 / 845,250 字符** | `knowledge/chunks/chunks.jsonl` |
| 图谱 | **63 节点 / 226 边 / 9 类关系** | `knowledge/graph/graph.json` |
| Wiki | **38 页**（人工复核 29 / llm-draft 9） | 导出里的 `wikiPages` |
| 版本 | kbVersion `2026.09.15` / graphVersion `0.1.0` | `knowledge/exports/career-graph.json` |
| 构建闸门 | **14 项校验，硬约束 13/13** | `node knowledge/pipeline/09-integrity-check.mjs` |

> 语料在 **2026-09-27** 由 494 段中文化重组为 1757 段（来源 22 → 26）。这次变更前写的
> 旧数字散落在若干文档里，2026-09-29 已逐处核对修正；**遇到「494 段 / 22 来源」即表示
> 该处记的是重组前的口径**（历史实验报告按当时口径保留，见文末）。

## 目录

| 路径 | 是什么 |
|---|---|
| `sources/` | 来源登记表（sha256 / 许可 / 采集时间 / 方向） |
| `raw/` | 抓下来的原始快照（`.html` / `.txt` / `.blocks.json` + `manifest.json`） |
| `chunks/` | 1757 段原文，每段带 `charRange` 字符区间 |
| `extract/` | 抽取候选与人工裁定结果 |
| `wiki/` | 38 页 Wiki（内链与图谱边双向对应） |
| `graph/` | 图谱本体 `graph.json` + 检索索引 `search-index.json` |
| `exports/` | **对外唯一交付物** `career-graph.json` + 契约样例 |
| `pipeline/` | 12 步离线管线（零依赖 Node，01–12） |
| `evaluations/` | 题集、评测报告、实验记录 |
| `eval/` | 评测脚本与跑批产物（Python 标准库） |
| `import/` | 外部职业库（O*NET / 职业分类大典 / 国家职业技能标准） |
| `evidence/` | 完整性报告与流水线日志 |
| `SOURCE_LICENSES.md` | **26 份来源的许可矩阵与再分发口径**（含 3 份 AGPL-3.0 单独说明） |

## 跑管线（在**仓库根目录**执行）

零三方依赖，`node` 直接跑：

```bash
node knowledge/pipeline/09-integrity-check.mjs   # 构建闸门：14 项校验，不过不产出
node knowledge/pipeline/10-layout-index.mjs      # 确定性坐标 x/y + 检索索引
node knowledge/pipeline/11-export-eval.mjs       # 导出 + 图谱原生 24 题五档消融
```

**图谱类产物是确定性的**（已实测 + 已由 `npm run e2e` 阶段 B 钉住）：重跑 09–11 后，
`exports/career-graph.json`、`graph.json`、`search-index.json`、`evidence/integrity-report.json`
的差异**只有 `generatedAt` 时间戳**。

⚠ 但「产物完全确定性」只对图谱类产物成立：第 11 步自己的评测产物
`evaluations/results.json`（`elapsedMs`）、`evaluations/report.md`（耗时列）与
`evidence/pipeline-log.json`（`startedAt` / `durationMs` / `runCount`）**每跑一次都会变**。
端到端脚本因此按「剥掉时间戳与耗时后的语义投影」比对，语义字段仍必须逐字不变。

01–08 步是**一次性构建**（抓取 / 分块 / 抽取 / 裁定 / 编译 wiki），需要网络或 LLM，
不需要每次重跑；12 步（国标重组入库）同理。产物已在仓库里。

## 哪些命令任何机器都能跑（已验证）

```bash
node knowledge/pipeline/09-integrity-check.mjs                    # 14 项校验，硬约束 13/13
node knowledge/pipeline/10-layout-index.mjs                       # 布局与索引
node knowledge/pipeline/11-export-eval.mjs                        # 导出 + 消融
node knowledge/pipeline/bm25-run.mjs                              # BM25 单路：命中 10/14
node knowledge/pipeline/probe-heldout.mjs                         # 留出集跑分 + 反泄漏校验
node knowledge/pipeline/probe-chunking.mjs                        # 分块 A/B
python knowledge/eval/validate_questionset.py \
  --set knowledge/evaluations/questions-holdout.json --min-gap 3  # 题集硬规则（单集）
cd frontend/frotent/frontend1 && npm run e2e                      # ★ 端到端全链（169 条断言，约 18 s）
cd frontend/frotent/frontend1 && npm run e2e:llm                  # 再验模型接通性（调真端点，约 30 s）
cd frontend/frotent/frontend1 && python -m pytest backend/tests -q # 后端 130 项（19 记忆 + 20 LLM/触发器 + 17 图谱契约 + …）
cd frontend/frotent/frontend1 && npm test                          # 前端 12 项
node knowledge/pipeline/import/bridge-coverage.mjs                 # 桥接覆盖率 + 「机械对齐」实测（M2-6 证据，离线）
node knowledge/pipeline/bm25-full.mjs --questions knowledge/evaluations/questions-dev.json \
  --out knowledge/eval/runs/af-dev-bm25-full.json                  # 先出 BM25 完整排序
node knowledge/pipeline/weknora/candidate-pool.mjs --questions knowledge/evaluations/questions-dev.json \
  --bm25 knowledge/eval/runs/af-dev-bm25-full.json --tag dev       # 双路候选池互补性（M2-4，离线）
```

`npm run e2e` 是本仓库**唯一一条覆盖整条链**的命令（脚本 `frontend/frotent/frontend1/scripts/e2e-knowledge.mjs`），
阶段划分见该脚本头部注释；它同时是下面这些口径的**执行者**：raw 快照逐字节可复现 20/25、
重跑 09–11 语义投影不变、dev 题集 BM25 命中 ≥ 25/34、三端 `generatedAt` 与 counts 同一份字节、
记忆库「候选→确认→召回→推荐增强→删除即遗忘」全生命周期。

`npm run e2e:llm` 会真调一次模型生成触发器（约 30 s，因此**不在默认链里**）：
断言 `generatedBy=llm`、模型名可溯源、字段都在约束内，并且**用它自己生成的问句式去提问能召回该记忆**；
模型不可用时它断言的是「降级标记 + 错误原因都在」，而不是假装成功。

**题集现状**（详见 `evaluations/README.md`）：

| 文件 | 规模 | 作用 |
|---|---|---|
| `questions-dev.json` | 49 题（可答 34：zh 20 / xl 14；拒答 15） | 调参、试方案 |
| `questions-test.json` | 47 题（可答 32；拒答 15） | **已消费三次**（24/32 → 26/32 → 27/32），只作历史 |
| `questions-holdout.json` | 50 题（可答 35：zh 14 / xl 21；拒答 15） | **验收载体，已消费两次**（首次基线 30/35；M2-5 融合对照在 `语言自适应融合.md`） |
| `questions-refusal.json` | 30 题 | 拒答阈值标定 |

## 哪些需要外部服务（跑不了会明确报错，不会静默给错数）

| 档位 | 依赖 |
|---|---|
| 向量档 | 本机嵌入服务 `TEI_EMBED_URL`（缺省 `http://127.0.0.1:8090/v1/embeddings`）。现役用 `knowledge/eval/local_embed_server.py`（fastembed/ONNX，**不需要 Docker**），模型 `Qwen3-Embedding-0.6B(int8,onnx)`、1024 维；向量缓存按模型名分文件（`eval/cache/chunk-vectors-<model>.json`），**换模型必须重算**，文件名对不上会直接报错 |
| 重排档 / 裁判档 / 查询扩展 / 出题 / 术语表 | **同一个** OpenAI 兼容 LLM 端点，配置在 `knowledge/eval/.env`（`DEEPEVAL_*`）。**`.env` 未入库**，模板见 `knowledge/eval/.env.example`（那里记了本机实测的模型清单与 `max_tokens` 实测值） |
| 产品侧：记忆写时触发器（模型版） | 同上这一个端点。统一入口 `frontend/frotent/frontend1/backend/llm.py`，评测侧经 `knowledge/eval/_shared_llm.py` 复用同一份实现 —— **改一处配置，产品与评测一起变**。配不上时触发器自动降级为规则版并在 `generated_by` 里标出来，`GET /health` 的 `llm` 块也会说明 |

> 「推理模型会把额度吃在思维链上」这条踩坑记录现在只有**一份**（`backend/llm.py` 的 `MIN_MAX_TOKENS`
> 与自动翻倍逻辑）。此前 9 个评测脚本各自手写了一遍 `llm()`、各自填 max_tokens（200–4000 不等），
> 改一处纪律改不到另一处；2026-09-29 已全部改为 `from _shared_llm import chat`。

历史备选：`knowledge-v1/scripts/start_tei.sh`（TEI 官方镜像路线，**需要 Docker**）。

## 曾经的断链（2026-09-29 复核：已修复）

文档此前记着「`probe-heldout.mjs` 与 `weknora/build-stack.mjs` 读已删除的
`heldout-questions.json` → 直接抛错」。**现在两个脚本都不再读它**：`probe-heldout.mjs`
默认读现役 `evaluations/questions-test.json`，也可 `--questions <path>` 覆盖；
`weknora/build-stack.mjs` 也已换掉该默认输入。两条命令实测可跑。

## 数据边界（对外陈述前必读）

**真实性**（`knowledge/raw/manifest.json`，2026-09-29 复算）：26 份**登记**来源中
**25 份**有 raw 抓取快照（S26 是国标重组稿，走 `import/osta-standards`，不经 02 抓取）；
0 抓取失败、**25 个互不相同的 sha256**、0 内容重复、快照合计 **4,671,482 字节**、
**25/25 HTTP 200**。

⚠ **sha256 实测只有 20/25 与登记值一致**，5 份不一致：`S15`（差 230 字节）、`S18`（1085）、
`S19`（10）、`S20`（2400）、`S22`（1）。落在 git 里的字节与 `manifest.json` 记录的字节不同
（`.gitattributes` 已对 `raw/**` 关掉行尾转换，但 5 份的差异量与其行数不成比例，成因未定，
疑似抓取后二次编辑或入库前重新编码）。**对外只能声称「20/25 逐字节可复现」，不能声称全部一致。**

**中文占比：全语料 46.4%**（845,250 字符里 391,973 个中文字符；口径＝中文字符 ÷ 全部字符，
与 494 段时代那条 12.8% 同一算法）。按来源分：

| 类别 | 份数 | 例子（中文字符占比） |
|---|---|---|
| 真中文（占比 ≥0.20） | **14** | 国标职业标准 S26 0.85、osta 职业标准 0.77、工信部 0.65、阿里云 0.66、与非网 0.60、TesterHome 0.54、国标全文 0.45、FreeRTOS 0.38、Selenium 中文 0.38、飞桨 0.24–0.26 |
| 半中半英（0.10–0.20） | **6** | ESP-IDF SPI 0.15、MindSpore Lite 0.16、Ultralytics 0.18–0.19、pytest 中文镜像 0.13–0.20 |
| 基本英文（<0.10） | **6** | pytest 官方 0.00、**O*NET 两份 0.00**、ESP-IDF 内存 0.06 / GPIO 0.04、Selenium 等待策略 0.03 |

> `taxonomy.json` 的变更注记里另有「中文**词项**占比 43.4% → 82.7%」一数：那是按**分词后的
> 词项**算的（`tokenize()` 口径），与上表按字符算的是两个指标，**不要混用**。

**`publishedAt` 全缺（0/26）**：字段在 schema 里，但 26 条**一条都没填**（不是导出时丢的，
源头就是空的）→ 任何"时效性"结论目前都没有依据，只能说"什么时候抓的"（`collectedAt` 全是 2026-09）。

**许可**：Apache-2.0 ×8、AGPL-3.0 ×3、public-policy ×3、CC-BY-4.0 ×2、CC-BY-SA-4.0 / MIT 各 1，
另有 **7 份非标准标签**（`community-mirror` ×4、`community-forum` / `public-standard` /
`vendor-media` / `vendor-docs` 各 1）——再分发前需要逐份确认。

**什么在仓库里、什么不在**：`raw/`（11.5MB 原始快照）在；`import/raw/`（111MB 外部职业库
原始下载）与 `knowledge-v1/data/`（403MB 已停用语料）**不在 git 里**，要重建需自行下载。

## 三端怎么消费它

| 端 | 读什么 | 验证命令 |
|---|---|---|
| 后端 | `backend/knowledge.py` 读导出 → 职业/技能目录、推荐算式 | `python -m pytest backend/tests -q`（**130 项**） |
| MCP | `mcp/career-graph-store.ts` 读同一份导出 + 复用前端纯函数 | `npm run mcp:verify`、`npm run mcp:verify-http` |
| 前端 | 经后端接口取数据；图谱视图用 `lib/client/graph-view.ts` 纯函数 | `npm test`（12 项） |

三端同源由断言钉住：MCP 返回里出现的是前端 `RELATION` 的中文标签，且「stdio 与 HTTP
两条链路的 `tools/list` 逐字相同」。

## 历史文档的口径

`evaluations/` 下这些文件记的是**中文化重组之前**（494 段 / 22 来源）的实测数据，
数字按其当时口径保留，不要当成现役规模：
`知识库构建方法与效果评估报告.md`、`系统知识库构建方法与效果评估报告.md`、
`检索命中率优化实验.md`、`WeKnora对照评测.md`、`EVAL_SET_DESIGN.md`（v1）、`report.md`。
现役口径一律以本文件与 `evaluations/实验总表.md` 的 C/D/E 组为准。

现役（1757 段语料）的**检索实验**另见 `evaluations/语言自适应融合.md`（M2-5：单路向量 vs 融合 vs 查询自适应门控，dev 选型 / 新 holdout 报数，零 LLM 可复算）。
