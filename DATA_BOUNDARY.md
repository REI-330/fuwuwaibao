# 数据边界：仓库里有什么、什么要重下、什么根本没拿到

**给接手的人一张清单**：克隆本仓库后，哪些东西开箱可用、哪些必须重新下载、哪些**无论怎么下都拿不到**。
所有数字都是实测（命令写在 §5），不是估计。

## 1. 入库的（克隆即可复算）

| 路径 | 体积 | 是什么 | 能否复算 |
|---|---|---|---|
| `knowledge/sources/sources.json` | — | 26 份来源登记表（许可 / sha256 / 字符数 / 方向） | ✅ 全量 |
| `knowledge/raw/` | 12 MB | **26 份 × 3 文件**原始快照（`.html` / `.txt` / `.blocks.json`）+ `manifest.json`（79 个文件） | ✅ sha256 可核（实测 25/25 与登记一致；5 份历史不一致） |
| `knowledge/chunks/chunks.jsonl` | 2.5 MB | **1757 段**原文，每段带 `charRange` | ✅ |
| `knowledge/extract/` | — | 抽取候选 + 别名 + 人工裁定（`adjudication.json`） | ✅ 可重跑 04–08 |
| `knowledge/wiki/` | 208 KB | **38 页** Wiki（reviewed 29 / llm-draft 9） | ✅ 可重跑 07–08 |
| `knowledge/graph/` | — | `graph.json` + `search-index.json`（63 节点 / 226 边 / 161 条别名） | ✅ 可重跑 09–10 |
| `knowledge/exports/career-graph.json` | 2.9 MB | **对外唯一交付物**（三端读同一份字节） | ✅ 可重跑 11 |
| `knowledge/eval/runs/` | 14 MB | 150 个评测产物（含冻结留出集结果） | ✅ 有缓存，可复算 |
| `knowledge/SOURCE_LICENSES.md` | — | 26 份来源的许可矩阵与再分发口径 | ✅ |

## 2. 不入库的（`.gitignore` 明确排除，要自己下）

| 路径 | 不下的体积 | 为什么不入库 | 怎么补 |
|---|---|---|---|
| `knowledge/import/raw/` | **116 MB** | 外部职业库的原始下载（O*NET / 大典 / 702 份国家职业技能标准） | 跑 `knowledge/pipeline/import/` 下的抓取脚本（`fetch-osta-dadian.mjs` / `import-onet.mjs` / `fetch-osta-standards.py`） |
| `knowledge-v1/data/` | **404 MB** | 上一版（中文语料重组前）的旧数据层 | 只在需要复现历史结论时下；现役链路不依赖 |
| `knowledge-cn/data/sources/` | **38 MB** | 中国官方语料**采集**区（原始快照 + 1519 条候选） | 采集脚本在 `knowledge-cn/acquisition/`；候选已由业主豁免闸门并导入 WeKnora（见下） |
| `frontend/frotent/frontend1/node_modules/` | **776 MB** | 依赖 | `npm ci` |
| `knowledge/eval/.env` | — | **含 API key** | 从 `knowledge/eval/.env.example` 复制后填自己的 key |

> 补数据后跑 `npm run e2e`：阶段 A 会重新核对 26 份快照的 sha256 与登记值，
> 阶段 B 重跑 09–11 验证「语义投影不变」。**这两道闸门就是判断「数据补对了没有」的方式。**

## 3. 根本没拿到的（不要再花时间找）

| 想拿的东西 | 状态 | 说明 |
|---|---|---|
| **中国官方语料（入库）** | **1519 / 1519 已入库**（2026-10-01） | 审核由**业主豁免**（`WAIVED`，**不是人工逐条审核**）：数据里逐条带 `review_waiver{by,at,reason,policy}`。复算 `python knowledge-cn/waive_review.py --waived-by <谁>`。已导入 WeKnora Lite 并核验（2167 chunk、记录 ID 0 缺失），核验脚本 `python knowledge-cn/verify_import.py`。**这批语料的检索质量评测已于 2026-10-01 首版跑完**：33 题（可答 26 / 超范围 7），混合检索命中@1 61.5%、@5 80.8%、@10 100%、MRR 0.700；但这是**作者自出题 + 机器复核 gold**，不等于业主人工判定。另：7 道超范围题的 top-1 分全部 ≥ 可答题 p25，**检索层无法拒答**。见 `knowledge-cn/evaluations/检索质量评测-20261001.md` |
| **WeKnora 运行态** | 可跑（**不需要 Docker**） | 向量后端用本机 `127.0.0.1:8090` 的 `Qwen3-Embedding-0.6B`（ONNX int8，1024 维，OpenAI 兼容），`config/builtin_models.yaml` 已指向它；起 Lite 用 `set -a; source .env.lite; ./WeKnora-lite.exe` |
| **O*NET 30.3** | 未获得 | 仓库里的 O*NET 是 **29.1**（1,254 节点）与 OnLine 页面（26 份来源之一）。概要说「30.3」与仓库不符，见 `项目架构与技术文档.md` §12 第 8 条 |
| **O*NET 独立外部层的完整图谱** | 部分 | `extern` 层有 21,987 节点 / 108,662 边（`import/build/*.jsonl`），但 `knowledge/import/raw/` 不入库，重下需外网 |
| **WeKnora 标准版镜像** | 拉不动 | 5 个镜像共 2.72 GB，本机 Docker 代理对大镜像层近乎失效。**不影响功能**：对照实验已用 Lite 版完成并有结论 |
| **百宝箱企业版账号** | 无 | 需固定公网地址 + 账号，接入未提交 |
| **平台发布账号与审核** | 无 | 多端发布未做 |
| **26 份来源的 `publishedAt`** | **3 / 26 有值**（S08/S09/S10，取自页面 `schema.org Article.datePublished`）；另 **5 份有「最后更新」**（S08–S12 的 `dateModified`）；其余 21 份两者皆无 | 取值字段逐条记在 `publishedAtSource` / `sourceUpdatedAtSource` 里。**不拿采集时间或更新时间为发布时间顶数**；S08–S10 三份共享同一 `datePublished`，疑似站点模板常量，已在数据里标注待人工复核 |

## 4. 技术栈上的边界（不是数据，但同样影响「能不能跑」）

- **后端与记忆系统零三方依赖**：`backend/` 只用 Python 标准库（含 `sqlite3`、`zipfile`、`xml.etree`）。
  不装 FastAPI / SQLAlchemy / python-docx —— 所以**简历直接支持文本、DOCX 与 PDF**（PDF 用本机已装的
   pypdf / PyMuPDF / pdfminer，运行时探测，都不是硬依赖）；图片与 `.doc` **明确 415**，无 OCR。
- **知识库管线零三方依赖**：`knowledge/pipeline/01–12` 只用 Node 标准库。
- **评测侧需要一个 OpenAI 兼容端点**（`knowledge/eval/.env`）；没配也能跑，只是模型版触发器与
  重排/裁判档不可用，会**自动降级为规则版并标明原因**，不报错。
- **向量检索需要一个本机 TEI 兼容服务**（`127.0.0.1:8090`）；没起就只有 BM25 档。

## 5. 复算命令

```bash
cd <仓库根>

# 入库数据能不能复算
npm --prefix frontend/frotent/frontend1 run e2e        # 阶段 A/B 就是数据自洽闸门

# 各类数据的体积现状
du -sh knowledge/raw knowledge/import/raw knowledge-v1/data knowledge-cn/data/sources

# 来源与许可
python -c "import json;d=json.load(open('knowledge/sources/sources.json',encoding='utf-8'));print(len(d['sources']),'sources')"

# 中国语料的过审门禁现状（预期：0 条获批）
python knowledge/eval/run_after_review.py
```
