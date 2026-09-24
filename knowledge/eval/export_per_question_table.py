#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把各档位的逐题结果导出成一张**压缩矩阵**（一行一题 × 一列一档），供报告附录使用。

为什么需要它：
  「逐题结果」在题量变大后不能写成每题一段文字（80+ 题会撑爆报告），
  也不能只贴聚合数字（评委无法核对）。压缩矩阵是两者的折中：
  正文放矩阵（✅/❌/—），明细留在 runs/*.json 里，报告注明产物路径与复算命令。

用法：
  python knowledge/eval/export_per_question_table.py \
    --set knowledge/evaluations/questions-test.json \
    --runs knowledge/eval/runs/test-bm25-topk.json,knowledge/eval/runs/test-fusion-topk.json
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def label_of(path):
    base = os.path.basename(path).replace(".json", "")
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, help="题集（questions-*.json）")
    ap.add_argument("--runs", required=True, help="逗号分隔的各档位结果文件")
    ap.add_argument("--out", default=None, help="输出 md 路径（默认打印到 stdout）")
    ap.add_argument("--title", default="逐题结果矩阵")
    args = ap.parse_args()

    qdoc = json.load(open(args.set, encoding="utf-8"))
    questions = qdoc["questions"]
    runs = []
    for p in args.runs.split(","):
        p = p.strip()
        if p and os.path.exists(p):
            runs.append((label_of(p), json.load(open(p, encoding="utf-8"))))
        elif p:
            print(f"  ⚠ 缺文件，跳过：{p}", file=sys.stderr)

    # 组装：qid -> {label: top3}
    table = {}
    for label, doc in runs:
        for pq in doc.get("perQuestion", []):
            top = [t.get("chunkId") for t in pq.get("topk", [])]
            refs = set(pq.get("referenceChunks") or [])
            table.setdefault(pq["questionId"], {})[label] = {
                "top": top, "refs": refs, "hit": bool([c for c in top if c in refs])}

    labels = [l for l, _ in runs]
    lines = [f"# {args.title}", "",
             f"- 题集：`{os.path.relpath(args.set, ROOT)}`（{len(questions)} 题）",
             f"- 档位：{' / '.join(labels)}",
             f"- 判定：Top-3 是否含标注 chunk（✅ 命中 / ❌ 未命中 / — 该档无结果）",
             "", "| 题号 | 类目 | 问题（截断） | " + " | ".join(labels) + " | 参考答案段 |",
             "|---|---|---|" + "---|" * len(labels) + "---|"]

    n_hit = {l: 0 for l in labels}
    n_tot = {l: 0 for l in labels}
    for q in questions:
        qid = q["questionId"]
        if not q.get("answerable", True):
            cells = ["—（拒答，不判命中）" for _ in labels]
            lines.append(f"| {qid} | 超范围 | {q['question'][:38]}… | " + " | ".join(cells)
                         + " | 无（应拒答） |")
            continue
        cells = []
        for l in labels:
            e = table.get(qid, {}).get(l)
            if not e:
                cells.append("—")
                continue
            n_tot[l] += 1
            if e["hit"]:
                n_hit[l] += 1
            cells.append("✅" if e["hit"] else "❌")
        refs = "、".join(q.get("referenceChunks") or [])
        qtext = q["question"].replace("|", "／")[:38]
        lines.append(f"| {qid} | {q.get('category', '')} | {qtext}… | " + " | ".join(cells)
                     + f" | {refs} |")

    lines += ["", "## 合计", "", "| 档位 | 命中 | 命中率 |", "|---|---|---|"]
    for l in labels:
        if n_tot[l]:
            lines.append(f"| {l} | {n_hit[l]}/{n_tot[l]} | {n_hit[l]/n_tot[l]:.4f} |")

    text = "\n".join(lines) + "\n"
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        open(args.out, "w", encoding="utf-8").write(text)
        print(f"已写出：{os.path.relpath(args.out, ROOT)}（{len(questions)} 行）")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
