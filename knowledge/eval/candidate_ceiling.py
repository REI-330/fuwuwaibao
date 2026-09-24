#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
候选池天花板分析：在给定融合排序下，每题两段参考答案分别落在 Top-K 的什么位置。

用途：定位真正的瓶颈。重排只能在给定候选里选，所以「Top-K 里一段答案都没有」的题，
任何重排都救不回来——这个比值就是各档位的命中率上界。

用法：
  python knowledge/eval/candidate_ceiling.py \
    --questions knowledge/evaluations/questions-dev.json \
    --bm25 knowledge/eval/runs/dev2-bm25-full.json --alpha 0.35
"""
import argparse
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
CACHE_DIR = os.path.join(K, "eval", "cache")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")


def embed(texts, batch=8):
    out = []
    for i in range(0, len(texts), batch):
        body = json.dumps({"input": texts[i:i + batch], "model": MODEL}).encode("utf-8")
        req = urllib.request.Request(TEI, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.load(r)
        out.extend(it["embedding"] for it in sorted(d["data"], key=lambda x: x["index"]))
    return out


def minmax(dd):
    lo, hi = min(dd.values()), max(dd.values())
    if hi - lo < 1e-12:
        return {k: 0.0 for k in dd}
    return {k: (v - lo) / (hi - lo) for k, v in dd.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--bm25", required=True)
    ap.add_argument("--alpha", type=float, default=0.35)
    ap.add_argument("--ks", default="3,10,20,50")
    ap.add_argument("--exclude", default="",
                    help="逗号分隔的 chunkId：检索期剔除（用于测试「剔掉导航/列表段」的效果）")
    args = ap.parse_args()
    excluded = {x.strip() for x in args.exclude.split(",") if x.strip()}

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    ids = [c["chunkId"] for c in chunks]
    vecs = json.load(open(os.path.join(CACHE_DIR, f"chunk-vectors-{MODEL}.json"),
                          encoding="utf-8"))["vectors"]
    qs = [q for q in json.load(open(args.questions, encoding="utf-8"))["questions"]
          if q.get("answerable", True)]
    bm = json.load(open(args.bm25, encoding="utf-8"))
    bs = {q["questionId"]: {r["chunkId"]: r["score"] for r in q["ranking"]}
          for q in bm["perQuestion"]}

    qv = embed([q["question"] for q in qs])
    rank = {}
    for q, v in zip(qs, qv):
        vs = {cid: sum(a * b for a, b in zip(v, vecs[cid])) for cid in ids}
        b = minmax(bs[q["questionId"]])
        vm = minmax(vs)
        s = {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * vm.get(c, 0.0)
             for c in set(b) | set(vm) if c not in excluded}
        rank[q["questionId"]] = sorted(s, key=lambda c: -s[c])

    def both(q, k):
        return len(set(q["referenceChunks"]).intersection(rank[q["questionId"]][:k]))

    print(f"题集 {os.path.basename(args.questions)} | 融合 α={args.alpha} | {len(qs)} 题")
    print("\n=== 候选池天花板（该档位命中率的上界）===")
    print(f"  {'档位':>6s} {'两段都在':>8s} {'只有一段':>8s} {'一段都无':>8s} {'上界':>10s}")
    for k in [int(x) for x in args.ks.split(",")]:
        full = sum(1 for q in qs if both(q, k) == 2)
        part = sum(1 for q in qs if both(q, k) == 1)
        none = sum(1 for q in qs if both(q, k) == 0)
        print(f"  Top-{k:<2d} {full:>8d} {part:>8d} {none:>8d} "
              f"{full + part:>4d}/{len(qs):<5d}")

    hit3 = sum(1 for q in qs if both(q, 3) >= 1)
    hit1 = sum(1 for q in qs
               if rank[q["questionId"]][:1]
               and rank[q["questionId"]][0] in set(q["referenceChunks"]))
    p3 = sum(both(q, 3) / 3 for q in qs) / len(qs)
    print(f"\n=== 该排序自身的 Top-3 表现 ===")
    print(f"  命中 {hit3}/{len(qs)}  命中@1 {hit1 / len(qs):.4f}  精确率@3 {p3:.4f}")
    if excluded:
        print(f"  （已剔除 {len(excluded)} 段：{sorted(excluded)}）")

    print("\n=== 逐层：两段都在的比例 ===")
    for L in sorted({q.get("layer") for q in qs}):
        sub = [q for q in qs if q.get("layer") == L]
        row = []
        for k in [int(x) for x in args.ks.split(",")]:
            n = sum(1 for q in sub if both(q, k) == 2)
            row.append(f"Top-{k}: {n}/{len(sub)}")
        print(f"  {L}: " + "  ".join(row))

    print("\n=== 具体缺口 ===")
    for k in (10, 50):
        miss = [q["questionId"] for q in qs if both(q, k) == 0]
        print(f"  Top-{k} 里一段答案都没有的题（任何重排都救不回）：{miss or '无'}")
    one = [q["questionId"] for q in qs if both(q, 50) == 1]
    print(f"  Top-50 里只找到一段的题（另一半在全语料里已丢）：{one or '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
