# 知识库（`knowledge/`）

三层结构 + 一份版本化导出。**前端、后端、MCP 三端读的是同一份导出**，不各建一套检索。

## 现役规模（都能复算）

| 项 | 值 | 复算方式 |
|---|---|---|
| 来源 | **22** | `knowledge/sources/sources.json` |
| 文本分块 | **494 段 / 433,698 字符** | `knowledge/chunks/chunks.jsonl` |
| 图谱 | **63 节点 / 226 边 / 9 类关系** | `knowledge/graph/graph.json` |
| Wiki | **38 页**（人工复核 29） | 导出里的 `wikiPages` |
| 版本 | kbVersion `2026.09.15` / graphVersion `0.1.0` | `knowledge/exports/career-graph.json` |
| 构建闸门 | **13 项校验，硬约束 12/12** | `node knowledge/pipeline/09-integrity-check.mjs` |

## 目录

| 路径 | 是什么 |
|---|---|
| `sources/` | 来源登记表（sha256 / 许可 / 采集时间 / 方向） |
| `raw/` | 抓下来的原始快照（`.html` / `.txt` / `.blocks.json` + `manifest.json`） |
| `chunks/` | 494 段原文，每段带 `charRange` 字符区间 |
| `extract/` | 抽取候选与人工裁定结果 |
| `wiki/` | 38 页 Wiki（内链与图谱边双向对应） |
| `graph/` | 图谱本体 `graph.json` + 检索索引 `search-index.json` |
| `exports/` | **对外唯一交付物** `career-graph.json` + 契约样例 |
| `pipeline/` | 11 步离线管线（零依赖 Node） |
| `evaluations/` | 题集、评测报告、实验记录 |
| `eval/` | 评测脚本与跑批产物（Python 标准库） |
| `import/` | 外部职业库（O*NET / 职业分类大典 / 国家职业技能标准） |
| `evidence/` | 完整性报告与流水线日志 |

## 跑管线（在**仓库根目录**执行）

零三方依赖，`node` 直接跑：

```bash
node knowledge/pipeline/09-integrity-check.mjs   # 构建闸门：13 项校验，不过不产出
node knowledge/pipeline/10-layout-index.mjs      # 确定性坐标 x/y + 检索索引
node knowledge/pipeline/11-export-eval.mjs       # 导出 + 24 题五档消融
```

**产物是确定性的**（已实测）：重跑 09–11 后，`exports/career-graph.json`、`graph.json`、`search-index.json` 的差异**只有 `generatedAt` 时间戳**，各 1 行。

01–08 步是**一次性构建**（抓取 / 分块 / 抽取 / 裁定 / 编译 wiki），需要网络或 LLM，不需要每次重跑。产物已在仓库里。

## 哪些命令任何机器都能跑（已验证）

```bash
node knowledge/pipeline/09-integrity-check.mjs                    # 13 项校验 12/12
node knowledge/pipeline/10-layout-index.mjs                       # 布局与索引
node knowledge/pipeline/11-export-eval.mjs                        # 导出 + 消融
node knowledge/pipeline/bm25-run.mjs                              # BM25 单路：命中 11/14
node knowledge/pipeline/probe-chunking.mjs                        # 分块 A/B
python knowledge/eval/validate_questionset.py \
  --dev knowledge/evaluations/questions-dev.json \
  --test knowledge/evaluations/questions-test.json --min-gap 3     # 题集六条硬规则
python -m pytest backend/tests -q                                  # 后端 16 项（在 frontend/frotent/frontend1 下）
```

## 哪些需要外部服务（跑不了会明确报错，不会静默给错数）

| 档位 | 依赖 |
|---|---|
| 向量档 | 本机 TEI：`TEI_EMBED_URL`，缺省 `http://127.0.0.1:8090/v1/embeddings`，模型 Qwen3-Embedding-0.6B（1.19GB）。启动脚本在 `knowledge-v1/scripts/start_tei.sh`（**容器，需要 Docker**） |
| 重排档 | OpenAI 兼容 LLM 端点（`run_full_eval.py` 不跳过重排时） |
| 裁判档 | DeepEval + `knowledge/eval/.env` 里的 `DEEPEVAL_*`。**`.env` 未入库**，模板见 `knowledge/eval/.env.example` |

## 已知断链（照文档跑会报错的两处）

`probe-heldout.mjs` 与 `weknora/build-stack.mjs` 读 `knowledge/evaluations/heldout-questions.json`，**该文件已被删除** → 直接抛错退出。修法见 `IMPLEMENTATION_PLAN.md` 的 M2-2（改成读现役 `questions-test.json`）。其余脚本不受影响。

## 数据边界（对外陈述前必读）

**真实性**（`knowledge/raw/manifest.json`）：22 份来源、0 抓取失败、**22 个互不相同的 sha256**、0 内容重复、快照合计 4,553,319 字节；**22/22 快照的实测 sha256 与登记值完全一致**，22/22 HTTP 200。

**中文占比：全语料 12.8%**（433,698 字符里 55,487 个中文字符）。按来源分：

| 类别 | 份数 | 例子 |
|---|---|---|
| 真中文（占比 ≥0.20） | **12** | 人社部 0.77、工信部 0.65、与非网 0.60、TesterHome 0.54、国标 0.45、FreeRTOS 0.38、Selenium 中文 0.38、飞桨 0.24–0.26 |
| 半中半英（0.10–0.20） | 4 | ESP-IDF SPI 0.15、MindSpore Lite 0.16、Ultralytics 0.18–0.19 |
| 基本英文（<0.10） | **6** | pytest 官方 0.00、**O*NET 两份 0.00**、ESP-IDF 内存/GPIO 0.04–0.06、Selenium 等待策略 0.03 |

**`publishedAt` 全缺（0/22）**：字段在 schema 里，但 22 条**一条都没填**（不是导出时丢的，源头就是空的）→ 任何"时效性"结论目前都没有依据，只能说"什么时候抓的"（`collectedAt` 全是 2026-09）。

**许可**：Apache-2.0 ×8、**AGPL-3.0 ×3**（Ultralytics）、CC-BY-4.0 ×2、public-policy ×2、CC-BY-SA-4.0 / MIT 各 1，另有 **5 份非标准标签**（`community-mirror`、`community-forum`、`public-standard`、`vendor-media`、`vendor-docs`）——再分发前需要逐份确认。

**什么在仓库里、什么不在**：`raw/`（6.4MB 原始快照）在；`import/raw/`（116MB 外部职业库原始下载）与 `knowledge-v1/data/`（404MB 已停用语料）**不在 git 里**，要重建需自行下载。

## 三端怎么消费它

| 端 | 读什么 | 验证命令 |
|---|---|---|
| 后端 | `backend/knowledge.py` 读导出 → 职业/技能目录、推荐算式 | `python -m pytest backend/tests -q` |
| MCP | `mcp/career-graph-store.ts` 读同一份导出 + 复用前端纯函数 | `npm run mcp:verify`、`npm run mcp:verify-http` |
| 前端 | 经后端接口取数据；图谱视图用 `lib/client/graph-view.ts` 纯函数 | `npm test` |

三端同源由断言钉住：MCP 返回里出现的是前端 `RELATION` 的中文标签，且「stdio 与 HTTP 两条链路的 `tools/list` 逐字相同」。
