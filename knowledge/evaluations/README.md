# 评测产物目录说明

## 题集（当前有效）

| 文件 | 说明 |
|---|---|
| `questions-v2.json` | **当前唯一有效的题集**，14 题**全部可回答**（每题恰好 2 段参考答案，共 28 段），**无超范围题** |

设计约束（写在文件自身的 `selectionRule` / 各题 `gradingNotes` 字段里，便于复核）：

- **每题恰 2 段参考答案**。上一版主集均值 7.4 段、留出集恰 1 段，而检索预算只有 3 段——
  期望段数不一致会让两集不可比。本版统一为 2 段，消除该混淆。
- **全部参考答案取自未被图谱 `sourceRefs` 引用的段落**（494 段中的 440 段）。上一版主集
  参考答案 100% 落在引用集合内，会系统性高估任何「用图」策略。本版经脚本校验：
  28 段参考答案**无一落在引用集合内**。
- **已知偏向（2026-09-17 实测补充）**：参考答案是按「每个来源取 2 段连续块」机械选出的，
  实测 **7/14 题的两段参考答案在原文直接相邻，12/14 距离 ≤5**。这会让任何邻域/窗口类方法的
  增益被结构性放大，**不可外推**；要评估这类技术，须先重画一批跨章节、非连续的参考答案。
- 题目与参考答案为同一作者读原文手写，仍属同源；结果只作方向性信号。

> 题集曾是 24 题（20 可回答 + 4 超范围），后按要求缩为当前 14 题全可回答的版本；
> 上表的题数与段数已按 `questions-v2.json` 的 `counts` 字段核对。

## 已被删除的上一版题集

`questions.json`（24 题主集）与 `heldout-questions.json`（8 题留出集）**已按要求删除**。

影响与后果（如实记录）：

- 本目录下的 `results.json` 与 `report.md` 是**上一版题集的产物**，其 `perQuestion` 里的
  `referenceChunks` 指向旧语料的 chunk id。它们作为历史记录保留，但**不可再与当前题集混用**，
  也无法再由 `node knowledge/pipeline/11-export-eval.mjs` 复算（该脚本依赖已删除的题集文件）。
- `knowledge/pipeline/weknora/build-stack.mjs`、`knowledge/pipeline/probe-heldout.mjs` 同样依赖
  已删除的题集文件，现无法直接运行；若要复用其分块 A/B 与反泄漏对照逻辑，需先把题集适配到
  `questions-v2.json` 的字段结构。

## 题集划分（dev / test / 拒答）

`questions-v2.json` 自 2026-09-17 起**只作 dev（开发集）**：α、候选池深度、重排要不要上都是在它上面选的，
所以它上面的成绩带乐观偏差，**不能当最终成绩**。完整方案见 **`EVAL_SET_DESIGN.md`**，
实际落地的规模与语言分层见 **`题集扩容记录-20260917.md`**。摘要：

| 集合 | 题量（2026-09-17 实测落地） | 纪律 |
|---|---|---|
| dev | **34 可回答**（中文层 20 / 跨语言层 14） | 可反复跑，用于调参与试错 |
| test | **32 可回答**（中文层 12 / 跨语言层 20） | **冻结**，只跑一次，不得用于调参 |
| 拒答 | 30（dev 15 / test 15） | 不含参考答案；阈值在 dev 上定 |

**没做到设计文档的 54 / 40**，卡在语料容量：这份语料里中文占比 >0.9 的段是 **0 段**，
要求两段参考答案中文占比都 ≥0.20 时最多只能出 28 题。阈值—容量对照与逐类目核算见扩容记录 §1。

**语言分层是硬要求**：`layer=zh` 表示两段参考答案的中文占比都 ≥0.20，`layer=xl` 为跨语言层
（中文提问、英文原文作答）——两层是不同的检索任务，**必须分开报数**，混在一起会互相掩盖。

校验命令：

```bash
python knowledge/eval/pool_capacity.py --min-gap 3 --lang cjk      # 容量（修正过「已占用」口径）
python knowledge/eval/plan_new_questions.py --help                 # 选段（含人工判废理由）
python knowledge/eval/validate_questionset.py \
  --dev knowledge/evaluations/questions-dev.json \
  --test knowledge/evaluations/questions-test.json --min-gap 3      # 六条硬规则
```

现有 14 题已实测出 **10 题不满足「两段参考答案间隔 ≥ 3」**（7 题直接相邻），已记入该文件的 `knownDefects`。

## 检索命中率优化实验（当前有效结论）

完整记录见 **`检索命中率优化实验.md`**（2026-09-17 实测）。四行摘要：

| 档位 | 命中 | 命中@1 | 精确率@3 | Top-3 槽里的参考答案段数 |
|---|---|---|---|---|
| BM25 | 11/14 | 10/14 | 0.4048 | 17/42 |
| 稠密向量（TEI） | 11/14 | 7/14 | 0.3571 | 15/42 |
| 融合 α_bm25=0.35 | 13/14 | 10/14 | 0.4524 | 19/42 |
| 融合 + LLM 重排（候选 10） | 14/14 | 12/14 | 0.6429 | 27/42 |

已被实测否掉的方案（负结果同样留档，避免重复试）：图证据扩展（单调负收益）、
LLM 查询扩展（12/14）、子查询分解（三种合并 10–12/14）、章节标题独立通道（无增益）、
邻域加权（有自反馈缺陷，且被题集「参考答案常相邻」的性质放大）。

### 已采纳配置的复核（2026-09-17）

`采用配置复核报告.md`：把 α_bm25=0.35 融合 + LLM 重排固化成一条命令重跑（`run_adopted.py`），
并用对半劈开模拟量化「在同一批题上选型」的乐观偏差（`split_half_check.py`）。

| 档位（dev 集 14 题） | 命中 | 命中@1 | 精确率@3 |
|---|---|---|---|
| FUSION α=0.35 | 13/14 | 0.7143 | 0.4524 |
| **FUSION + RERANK（采用配置）** | **14/14** | **0.8571** | **0.6429** |

三条关键结论：① α 的选择**不稳**（3432 种对半切分里只有 35.7% 能选出 0.35，22.2% 选到 0.05）；
② 均衡切分下乐观偏差 **+6.5 ~ +7.5pp**，故 14/14 属 dev 观测值、不可当验收成绩；
③ 「重排要不要上」这个决定 **100% 跨半一致**，不依赖样本量。

> 注意：`questions-test.json`（40 题）尚未出题，因此**当前没有任何 test 集成绩**，本目录所有数字都是 dev 数字。

## 复算

```bash
# 向量检索（TEI，无需 LLM）
python knowledge/eval/vector_retrieval.py

# BM25 基线与完整排序（复用 lib/graph.mjs）
node knowledge/pipeline/bm25-full.mjs --top 50
node knowledge/pipeline/bm25-run.mjs

# 融合与最优档位
python knowledge/eval/hybrid_experiment.py --topk 3 --save-best

# 已采纳配置（α=0.35 融合 + LLM 重排）按划分口径重跑
python knowledge/eval/run_adopted.py
python knowledge/eval/run_adopted.py --questions knowledge/evaluations/questions-test.json

# 选型偏差检验（对半劈开 + dev 规模敏感性）
python knowledge/eval/split_half_check.py

# 诊断 + 优化实验（详见 检索命中率优化实验.md 第七节）
python knowledge/eval/diagnose_retrieval.py --alpha 0.35
python knowledge/eval/optimize_retrieval.py
python knowledge/eval/locality_variants.py
python knowledge/eval/section_channel_experiment.py
python knowledge/eval/decompose_experiment.py
python knowledge/eval/rerank_experiment.py --candidates 10 --save-best

# DeepEval 评测（需 knowledge/eval/.env 的裁判配置；deepeval 装在仓库根 .venv）
./.venv/Scripts/python.exe knowledge/eval/deepeval_rag_eval.py \
  --levels BM25,HYBRID,RERANKED --workers 4 \
  --metrics contextual_precision,contextual_recall,contextual_relevancy

# 汇总
python knowledge/eval/summarize_deepeval.py --out knowledge/eval/results/summary.md
```

### `knowledge/eval/runs/` 产物对照

| 产物 | 由谁产出 | 内容 |
|---|---|---|
| `bm25-full.json` / `bm25-full-expanded.json` | `bm25-full.mjs` | 每题 Top-50 完整排序（融合实验的输入） |
| `bm25-topk.json` / `vector-topk.json` / `hybrid-topk.json` / `reranked-topk.json` | 对应脚本 `--save-best` | DeepEval 的 BM25 / VECTOR / HYBRID / RERANKED 档位 |
| `diagnosis.json` | `diagnose_retrieval.py` | 逐题参考答案名次与失败分类 |
| `rerank-cache-N.json` / `decomposed-queries.json` / `expanded-queries.json` / `section-channel.json` | 各实验脚本 | 模型输出缓存与扫描曲线，重跑不再调模型 |

