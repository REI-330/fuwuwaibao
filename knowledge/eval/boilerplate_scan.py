#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
语料侧「泛化段」筛查：哪些段几乎对任何查询都往 Top-10 里挤。

为什么需要：判废实验中发现，一个纯导航目录段（S14#s278）会被检索进某题的 Top-3，
占掉一个槽位。机械指标分不开「正文」与「导航/列表页」（散文度、短行占比都失效，见
题集扩容记录 §4），但**检索侧的信号是有效的**：真正的正文段只会被少数相关查询命中，
而导航/目录/新闻摘要这类泛化段会被大量互不相关的查询一起命中。

指标：对同一批题，统计每个段在融合排序 Top-K 里出现的**不同查询数**（覆盖率）。
覆盖率高的段即为候选泛化段，附「被检索到时它是否命中参考答案」以区分
「有用的高频段」与「纯粹占位段」。

用法：
  python knowledge/eval/boilerplate_scan.py \
    --questions knowledge/evaluations/questions-dev.json \
    --bm25 knowledge/eval/runs/dev2-bm25-full.json --alpha 0.35 --topk 10
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
    ap.add_argument("--topk", type=int, default=10)
    ap.add_argument("--min-cover", type=int, default=3,
                    help="被至少这么多个不同查询命中才算可疑")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    ids = [c["chunkId"] for c in chunks]
    by_id = {c["chunkId"]: c for c in chunks}
    vecs = json.load(open(os.path.join(CACHE_DIR, f"chunk-vectors-{MODEL}.json"),
                          encoding="utf-8"))["vectors"]
    qs = [q for q in json.load(open(args.questions, encoding="utf-8"))["questions"]
          if q.get("answerable", True)]
    bm = json.load(open(args.bm25, encoding="utf-8"))
    bs = {q["questionId"]: {r["chunkId"]: r["score"] for r in q["ranking"]}
          for q in bm["perQuestion"]}

    qv = embed([q["question"] for q in qs])
    cover, useful = {}, {}
    for q, v in zip(qs, qv):
        vs = {cid: sum(a * b for a, b in zip(v, vecs[cid])) for cid in ids}
        b = minmax(bs[q["questionId"]])
        vm = minmax(vs)
        s = {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * vm.get(c, 0.0)
             for c in set(b) | set(vm)}
        top = sorted(s, key=lambda c: -s[c])[:args.topk]
        refs = set(q.get("referenceChunks") or [])
        for cid in top:
            cover[cid] = cover.get(cid, 0) + 1
            useful.setdefault(cid, 0)
            if cid in refs:
                useful[cid] = useful.get(cid, 0) + 1

    n = len(qs)
    print(f"题集 {os.path.basename(args.questions)} | {n} 题 | 融合 α={args.alpha} | Top-{args.topk}\n")
    rows = sorted(cover.items(), key=lambda x: -x[1])
    hot = [(c, k) for c, k in rows if k >= args.min_cover]
    print(f"被 >= {args.min_cover} 个不同查询命中的段：{len(hot)} 个"
          f"（占语料 {len(hot) / len(chunks):.1%}）\n")
    print(f"  {'段':16s} {'覆盖率':>7s} {'其中命中答案':>12s} {'中文占比':>8s} {'字符':>6s}  开头")
    import re
    CJK = re.compile(r"[\u4e00-\u9fff]")
    for cid, k in hot[:20]:
        t = by_id[cid]["text"]
        s = re.sub(r"\s", "", t)
        ratio = len(CJK.findall(s)) / len(s) if s else 0
        head = re.sub(r"\s+", " ", t)[:44]
        print(f"  {cid:16s} {k:>3}/{n:<3} {useful.get(cid, 0):>12d} {ratio:>8.2f} {len(t):>6d}  {head}")

    waste = sum(k for c, k in hot if useful.get(c, 0) == 0)
    print(f"\n上述高频段里，从未命中参考答案的「纯占位」段共占 {waste} 个检索槽位"
          f"（Top-{args.topk} 总槽位 {n * args.topk} 的 {waste / (n * args.topk):.1%}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
