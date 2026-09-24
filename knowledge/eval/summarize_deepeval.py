#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
汇总 DeepEval 评测结果 + 检索命中率，产出一张可直接写进报告的马可仕表。

数据来源（按优先级）：
  - knowledge/eval/results/deepeval-*.json  最终产物（脚本正常跑完时生成）
  - knowledge/eval/results/partial-*.json   增量产物（未跑完时也有数据）
  - knowledge/eval/runs/vector-topk.json    向量检索命中率（vector_retrieval.py 产出）
  - knowledge/evaluations/results.json      BM25 各档命中率（既有产物）

用法：
  python knowledge/eval/summarize_deepeval.py
  python knowledge/eval/summarize_deepeval.py --out knowledge/eval/results/summary.md
"""
import argparse
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
RES = os.path.join(K, "eval", "results")
VECTOR = os.path.join(K, "eval", "runs", "vector-topk.json")
BM25_RESULTS = os.path.join(K, "evaluations", "results.json")

METRICS = ["contextual_precision", "contextual_recall", "contextual_relevancy",
           "faithfulness", "answer_relevancy"]
SHORT = {"contextual_precision": "ContextualPrecision",
         "contextual_recall": "ContextualRecall",
         "contextual_relevancy": "ContextualRelevancy",
         "faithfulness": "Faithfulness",
         "answer_relevancy": "AnswerRelevancy"}


def recompute(rows):
    """从逐题行重算均值。

    不能信任产物里的 sums/counts——那只统计「当次启用的指标」。续跑复用的旧行里
    可能带着本次未启用的指标（如 Faithfulness），用 sums 汇总会把它们显示成「—」，
    等于丢掉已经花过钱的测量结果。
    """
    sums, counts = {}, {}
    for r in rows:
        for m, v in (r.get("scores") or {}).items():
            if isinstance(v, (int, float)):
                sums[m] = sums.get(m, 0.0) + float(v)
                counts[m] = counts.get(m, 0) + 1
    return {k: (round(sums[k] / counts[k], 4) if counts.get(k) else None, counts.get(k, 0))
            for k in METRICS}


def load_levels():
    """返回 {level: {...}}。最终产物与增量产物一起按修改时间排序，最新的覆盖旧的。"""
    levels = {}
    files = [p for p in (glob.glob(os.path.join(RES, "deepeval-*.json")) +
                         glob.glob(os.path.join(RES, "partial-*.json")))
             if not p.endswith(".bak")]
    for p in sorted(files, key=os.path.getmtime):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if "levels" in d:
            for lv, v in d["levels"].items():
                rows = v.get("perQuestion", [])
                levels[lv] = {
                    "summary": recompute(rows),
                    "perQuestion": rows,
                    "done": v.get("n") or len(rows), "total": v.get("n") or len(rows),
                    "source": os.path.basename(p),
                }
        else:
            lv = d.get("level")
            if not lv:
                continue
            rows = d.get("perQuestion", [])
            levels[lv] = {
                "summary": recompute(rows),
                "perQuestion": rows,
                "done": d.get("done"), "total": d.get("total"),
                "source": os.path.basename(p),
            }
    return levels


def retrieval_hitrate():
    """各档检索命中率（Top-k 是否含标注 chunk），用于与 deepeval 指标并排看。"""
    out = {}
    if os.path.exists(BM25_RESULTS):
        with open(BM25_RESULTS, encoding="utf-8") as f:
            d = json.load(f)
        for run in d.get("runs", []):
            m = run.get("metrics", {})
            out[run["id"]] = {"main": m.get("hitRate"), "heldout": "-",
                              "meanPrecisionAtK": m.get("meanPrecisionAt3")}
    if os.path.exists(VECTOR):
        with open(VECTOR, encoding="utf-8") as f:
            d = json.load(f)
        out["VECTOR"] = {"main": d["metrics"]["main"]["hitRate"],
                         "heldout": d["metrics"]["heldout"]["hitRate"],
                         "meanPrecisionAtK": d["metrics"]["main"]["meanPrecisionAtK"]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(RES, "summary.md"))
    args = ap.parse_args()

    levels = load_levels()
    hits = retrieval_hitrate()
    if not levels:
        print("还没有任何 deepeval 结果（partial-*.json 未生成）")
        return 1

    lines = ["# DeepEval 评测汇总", "",
             "指标口径：deepeval 4.2.3 内置指标，裁判模型见各产物 JSON 的 `judge` 字段。",
             "命中率口径：Top-3 是否含标注 chunk（与 deepeval 指标口径不同，仅作并排参照）。", ""]

    lines += ["## 一、DeepEval 指标均值", "",
              "| 检索档位 | " + " | ".join(SHORT[k] for k in METRICS) + " | 完成 |",
              "|---|" + "---|" * (len(METRICS) + 1)]
    for lv in ("A-bm25-only", "VECTOR", "E-full", "B-graph-1hop"):
        if lv not in levels:
            continue
        s = levels[lv]["summary"]
        cells = []
        for k in METRICS:
            v = s.get(k)
            cells.append("—" if not v else (f"{v[0]:.4f}" if v[1] else "—"))
        lines.append(f"| {lv} | " + " | ".join(cells) +
                     f" | {levels[lv].get('done')}/{levels[lv].get('total')} |")

    lines += ["", "## 二、检索命中率（非 deepeval 口径，作参照）", "",
              "| 档位 | 主集命中 | 留出集命中 | 平均精确率@3 |", "|---|---|---|---|"]
    for lv, v in hits.items():
        lines.append(f"| {lv} | {v.get('main')} | {v.get('heldout')} | "
                     f"{v.get('meanPrecisionAtK')} |")

    lines += ["", "## 三、逐题明细（已完成的题）", ""]
    for lv in ("A-bm25-only", "VECTOR"):
        if lv not in levels:
            continue
        lines.append(f"### {lv}（来源 {levels[lv]['source']}）")
        lines.append("")
        lines.append("| 题 | 集合 | " + " | ".join(SHORT[k] for k in METRICS) + " | 耗时 |")
        lines.append("|---|---|" + "---|" * (len(METRICS) + 1))
        for r in sorted(levels[lv]["perQuestion"], key=lambda x: x["questionId"]):
            sc = r.get("scores", {})
            cells = [("—" if sc.get(k) is None else f"{sc[k]}") for k in METRICS]
            lines.append(f"| {r['questionId']} | {r.get('set','-')} | " +
                         " | ".join(cells) + f" | {r.get('elapsedSec','-')}s |")
        lines.append("")

    lines += ["## 四、局限", "",
              "- 裁判为 LLM，temperature=0 仍有 run-to-run 波动：同一题两次运行的分值可能差 0.1 量级，"
              "因此**只有量级差异可作结论**，小数位差异不可。",
              "- Faithfulness 在当前配置下恒为 1.0，缺乏区分度。",
              "- 主集参考答案 100% 落在图谱引用集合内（同源偏差），主集上的增益会被高估。",
              "- 主集 18 题 / 留出集 8 题属小样本，不作普适准确率承诺。", ""]

    text = "\n".join(lines)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"\n已写入 {os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
