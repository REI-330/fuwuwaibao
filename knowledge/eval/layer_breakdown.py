#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
按语言分层（zh / xl）拆开报检索指标。

为什么要拆：这份语料里没有纯中文文档（中文占比 >0.9 的段是 0 段），
「中文问→中文答」与「中文问→英文原文答」是两种不同的检索任务，
混在一起算会互相掩盖——实测融合权重 α 在中英混合的旧开发集上最优（0.30–0.35），
而在跨语言题占多数的旧留出集上反而有害（8/21，低于任一单路）。

用法：
  python knowledge/eval/layer_breakdown.py \
    --questions knowledge/evaluations/questions-dev.json \
    --runs dev2-bm25-topk.json,dev2-vector-topk.json,dev2-fusion-a035-topk.json,dev2-adopted-a035-topk.json

  # 只看某一层
  python knowledge/eval/layer_breakdown.py --questions ... --runs ... --layer zh
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
EV = os.path.join(K, "evaluations")
RUNS = os.path.join(K, "eval", "runs")


def load_questions(path):
    doc = json.load(open(path, encoding="utf-8"))
    return {q["questionId"]: q for q in doc["questions"] if q.get("answerable", True)}


def load_run(name):
    path = name if os.path.isabs(name) else os.path.join(RUNS, name)
    if not os.path.exists(path):
        return None
    return json.load(open(path, encoding="utf-8"))


def evaluate(run, questions, topk):
    """返回 {layer: {n, hit, hitAt1, pAt3, refHits}}"""
    stats = {}
    for pq in run.get("perQuestion", []):
        q = questions.get(pq["questionId"])
        if not q:
            continue
        layer = q.get("layer", "(未标)")
        refs = set(q.get("referenceChunks") or [])
        got = [t["chunkId"] for t in (pq.get("topk") or [])][:topk]
        hit = len(refs.intersection(got))
        s = stats.setdefault(layer, {"n": 0, "hit": 0, "hitAt1": 0, "pAt3": 0.0, "refHits": 0})
        s["n"] += 1
        s["hit"] += 1 if hit else 0
        s["hitAt1"] += 1 if (got and got[0] in refs) else 0
        s["pAt3"] += hit / max(1, topk)
        s["refHits"] += hit
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--runs", required=True, help="逗号分隔的产物文件名（相对 knowledge/eval/runs）")
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--layer", help="只报这一层（zh / xl）")
    args = ap.parse_args()

    questions = load_questions(args.questions)
    layers = sorted({q.get("layer", "(未标)") for q in questions.values()})
    if args.layer:
        layers = [args.layer]

    print(f"题集 {os.path.relpath(args.questions, ROOT)}：{len(questions)} 可回答")
    for l in sorted({q.get('layer', '(未标)') for q in questions.values()}):
        print(f"  {l}: {sum(1 for q in questions.values() if q.get('layer') == l)} 题")
    print(f"（Top-{args.topk} 口径）\n")

    for name in [x.strip() for x in args.runs.split(",") if x.strip()]:
        run = load_run(name)
        if run is None:
            print(f"=== {name} ===（缺产物，跳过）\n")
            continue
        stats = evaluate(run, questions, args.topk)
        print(f"=== {name} ===")
        print(f"  {'层':6s} {'n':>4s} {'命中':>10s} {'命中@1':>8s} {'精确率@3':>9s} {'答案段':>9s}")
        for l in layers:
            s = stats.get(l)
            if not s:
                continue
            print(f"  {l:6s} {s['n']:>4d} {s['hit']:>4d}/{s['n']:<5d} "
                  f"{s['hitAt1'] / s['n']:>8.4f} {s['pAt3'] / s['n']:>9.4f} "
                  f"{s['refHits']:>4d}/{s['n'] * args.topk:<4d}")
        tot = {k: sum(stats[l][k] for l in layers if l in stats)
               for k in ("n", "hit", "hitAt1", "pAt3", "refHits")}
        print(f"  {'合计':6s} {tot['n']:>4d} {tot['hit']:>4d}/{tot['n']:<5d} "
              f"{tot['hitAt1'] / tot['n']:>8.4f} {tot['pAt3'] / tot['n']:>9.4f} "
              f"{tot['refHits']:>4d}/{tot['n'] * args.topk:<4d}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
