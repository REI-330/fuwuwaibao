# 中国职业导航知识库

以用户提供的《职业路径导航 Agent 数据来源与获取设计文档 V1.0》为需求依据。
当前处于重建阶段，旧 O*NET/ESCO 文件不在来源白名单内。

## 已取得官方快照

- 教育部 2026 本科专业目录：538,898 bytes，SHA-256 `51026248004546171620678895e991a6f0ada1ebf0de6498fe8c563873b43f11`。
- 国家统计局 2025 工资发布：490,106 bytes，SHA-256 `4ce7e6bf8fe69d52babb53f26ae55359b100f47c977c523ccf9ec630e41cf20a`。
- 职业分类大典转载附件（旧快照）：29,078,833 bytes，SHA-256 `43b86f85a730627ad0941354ccf4e32cca6140a343c1bdb5dc0b4629386ebf8d`，已降级为待核验附件。
- 职业分类大典社会公示稿：9,450,957 bytes，SHA-256 `4b3af986925b9abc2fe048d50c56b3fc7b0781e6de3fbb21f8a6b3feffebd3ce`，570 页；仅作 `DRAFT_STANDARD` 候选，不计入正式职业事实。

所有快照均通过 Clash `http://127.0.0.1:7897` 获取并记录 `health.json`、`latest.json`、内容哈希和获取时间。

## 当前可执行流程

```powershell
python -m acquisition.collect --source moe-majors-2026 --proxy http://127.0.0.1:7897
python -m acquisition.collect --source nbs-wages-2025 --proxy http://127.0.0.1:7897
python -m acquisition.collect --source occupation-2022-replacement --proxy http://127.0.0.1:7897
```

工作目录为本目录。依赖见 requirements.txt。代理仅对当前采集命令生效，不修改系统设置。教育部 PDF 的同域 HTTP 重定向会被记录为 `transport_warning`。

WeKnora 模型配置与验收顺序见 [docs/WEKNORA_MODEL_SETUP.md](docs/WEKNORA_MODEL_SETUP.md)。

## 数据边界

- 来源登记不等于已接入。`discovery_required` 来源不能启动采集。
- 企业公示系统仅作按需验证；实时岗位页面须先核验访问规则。
- 原始文件保存在 `data/sources/<id>/<sha256>.*`，元数据和健康状态单独保存。
- 原始快照、抽取候选、已审核事实和检索块分别计数，关系边不计作独立知识文档。
- FACT、STATISTIC、JOB_POSTING、MARKET_SIGNAL、REPORT_CLAIM 必须保持类型，统计口径不能混用。
- 薪资保存年度、地区、单位、样本范围；不将统计平均工资视作具体招聘薪资。
- 公开可访问不代表可以公开再分发，当前快照仅作内部研究证据。

## 已验收：导入与检索（2026-10-01）

1519 条已审核候选（业主豁免闸门 `WAIVED`）**已导入 WeKnora Lite 并通过核验**：

```powershell
# 1) 起 WeKnora Lite（必须显式加载 .env.lite，否则 DB_DRIVER 为空会 panic）
cd knowledge-v1/weknora-src
set -a; source .env.lite; set +a; ./WeKnora-lite.exe     # 监听 0.0.0.0:8080

# 2) 门禁（自带登录，不需要预先给 token）
python knowledge-cn/acquisition/check_weknora.py

# 3) 导入：**上传真实 .md 文件**，不要用 knowledge/manual
#    结构化目录要带切块覆盖，否则 850 条记录会被挤成 3 条/chunk
python knowledge-cn/acquisition/import_weknora.py --input data/reviewed/moe-majors-2026-structured.jsonl \
    --kb-id <kb-id> --strategy heading --chunk-size 200
# 或者一次性照表重建全部 5 份（可重复跑，同名会先删再传）
python knowledge-cn/acquisition/rebuild_weknora.py --kb-id <kb-id>

# 4) 核验：逐条比对记录 ID / 向量完整性 / 活检索探针
python knowledge-cn/verify_import.py --db ../knowledge-v1/weknora-src/data/weknora-cn.db --kb <kb-id>
```

核验结果（`evidence/import-verify.json`）：**5 份文档 / 2641 chunk** / 记录 ID **1519 全命中、0 缺失** /
向量 2641 条（1024 维）/ 活检索有命中。

三个**踩过的坑**，照抄可省一轮：

- **不要用 `knowledge/manual`**：它建的 knowledge `file_type=manual`，不在 docparser 的
  `simpleFormats`（md/txt/csv/json）里 → 被路由到 Python **docreader**；本机 docreader 没跑
  （50051 无监听），解析会停在 processing。上传真实 `.md` 才走 Go 原生解析。
- **`/api/v1/models` 要带 token**：它挂在 `Viewer()` 后面，**无 token 返回 200 + 空数组**，
  看起来像「没配模型」。门禁脚本必须真登录。
- **删除是软删除**：`DELETE /api/v1/knowledge/:id` 只把 `parse_status` 置成 `deleting` +
  写 `deleted_at`，`chunks` 表里的旧行不会立刻消失。核验/统计必须
  `JOIN knowledges ON deleted_at IS NULL`，否则重建一次数字就虚高。

## 已验收：检索质量评测（2026-10-01，两版）

题集 `evaluations/questions-cn-v1.json`（33 题：可答 26 / 超范围 7，gold 组 35 个），对真实
`hybrid-search` 实测（`match_count=10`、`skip_context_enrichment=true`）：

| 版本 | 通道 | 命中@1 | 命中@5 | 命中@10 | MRR |
|---|---|---|---|---|---|
| 第一版（改造前，6 文档/2167 chunk） | 混合 | 16/26 = 61.5% | 21/26 = 80.8% | 26/26 = 100% | 0.700 |
| | 纯关键词 | 17/26 = 65.4% | 23/26 = 88.5% | 25/26 = 96.2% | 0.759 |
| | 纯向量 | 12/26 = 46.2% | 18/26 = 69.2% | 23/26 = 88.5% | 0.563 |
| **第二版（改造后，5 文档/2641 chunk）** | 混合 | 13/26 = 50.0% | 21/26 = 80.8% | **26/26 = 100%** | 0.632 |
| | 纯关键词 | 15/26 = 57.7% | 23/26 = 88.5% | 25/26 = 96.2% | 0.710 |
| | 纯向量 | 8/26 = 30.8% | 18/26 = 69.2% | 22/26 = 84.6% | 0.475 |

- 报告：[evaluations/检索质量评测-20261001.md](evaluations/检索质量评测-20261001.md)；
  证据：`evidence/retrieval-quality-20261001{,-v2}.json`、`evidence/questions-verified.json`、
  `evidence/boilerplate-experiment.json`。
- 「低空经济与管理 检索到了经济工程」已查清为**预览口径的错觉**，且已按新口径重建：
  结构化目录 287 chunk（3 条/块）→ **862 chunk（一块一条记录）**，记录在块首的比例
  33.8% → **100%**，该查询的预览现在直接显示目标记录；重复文档 nbs-wages-2025.md 已合并为 1 份。
  详见 [evaluations/召回排查-低空经济与管理-20261001.md](evaluations/召回排查-低空经济与管理-20261001.md)。
- **改造没有让位次变好**：全题均秩 2.77 → 2.85（未换 gold 的 23 题为 2.87 → 2.83），
  @1 有升有降（升 6 降 4，另 3 题因标注多组化而变严）；@10 覆盖率两版都是 26/26。
  「58% 出处样板稀释向量」的假说**已实测推翻**（剥掉样板后平均两两余弦只从 0.490 降到 0.470，
  名次 2 升 3 降），因此没有再做精简重建。
- 两版一致的两条：**关键词通道单独跑优于混合**（本题集偏字面事实，不可外推）；**超范围题
  在检索层完全不可分**（7 道题 top-1 分数全部是满分，按阈值判拒答 7/7 误判）。
- 与旧 24 题（面向自建职业图谱）**语料不重叠，数字不可互相引用**；也不对标
  `docs/EVALUATION.md` 里「人工 100 题 Top-5 ≥ 80%」的门槛（本次是作者自出 26 题）。

## 尚未验收

招聘源接入、去重合并、岗位失效策略、**业主人工出题/人工判定相关性**、偏语义改写题集
（用来判断 RRF 0.7/0.3 权重是否合适）、大典（1665 chunk）的切块参数对照均需后续验证。
不得引用旧版万条计数或单关键词测试作为本版本验收结果。
