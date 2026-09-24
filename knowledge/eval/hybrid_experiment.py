#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
检索命中率优化实验：BM25 与稠密向量如何融合。

背景（实测）：在新题集上 BM25 与向量各自命中 11/14，但**失败题不同**——
并集覆盖 13/14。这说明两路互补，融合有空间。

本脚本在同一套题、同一份语料上比较多种融合策略，并输出可交给 deepeval 的
结果文件。检索本身沿用既有实现：BM25 来自 lib/graph.mjs（bm25-full.json），
向量来自 TEI（缓存的语料向量 + 实时查询向量），不另写算法以免口径漂移。

用法：
  python knowledge/eval/hybrid_experiment.py
  python knowledge/eval/hybrid_experiment.py --topk 3 --save-best
"""
import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
QUESTIONS = os.path.join(K, "evaluations", "questions-v2.json")
BM25_FULL = os.path.join(K, "eval", "runs", "bm25-full.json")
CACHE_DIR = os.path.join(K, "eval", "cache")
OUT_DIR = os.path.join(K, "eval", "runs")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")


def embed(texts, timeout=600):
    body = json.dumps({"input": texts, "model": MODEL}).encode("utf-8")
    req = urllib.request.Request(TEI, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return [it["embedding"] for it in sorted(d["data"], key=lambda x: x["index"])]


def load_chunk_vectors():
    p = os.path.join(CACHE_DIR, f"chunk-vectors-{MODEL.replace('/', '_')}.json")
    if not os.path.exists(p):
        print(f"缺少语料向量缓存 {p}，请先跑 vector_retrieval.py", file=sys.stderr)
        sys.exit(1)
    with open(p, encoding="utf-8") as f:
        return json.load(f)["vectors"]


def rrf(rank_lists, k=60, weights=None):
    """Reciprocal Rank Fusion：score = Σ w_i / (k + rank_i)。"""
    scores = {}
    for idx, ranks in enumerate(rank_lists):
        w = 1.0 if not weights else weights[idx]
        for rank, cid in enumerate(ranks, 1):
            scores[cid] = scores.get(cid, 0.0) + w / (k + rank)
    return scores


def minmax(d):
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-12:
        return {k: 0.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def evaluate(name, ranking_ids, questions, topk):
    """ranking_ids: {qid: [chunkId,...]} 按名次从高到低"""
    hit = 0
    prec = 0.0
    detail = []
    for q in questions:
        refs = set(q.get("referenceChunks") or [])
        top = ranking_ids[q["questionId"]][:topk]
        h = [c for c in top if c in refs]
        if h:
            hit += 1
        prec += len(h) / topk
        detail.append({"questionId": q["questionId"], "hit": bool(h), "hits": h, "topk": top})
    n = len(questions)
    return {"name": name, "hit": f"{hit}/{n}", "hitRate": round(hit / n, 4),
            "precisionAtK": round(prec / n, 4), "detail": detail}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--save-best", action="store_true", help="把最优策略存成检索结果文件")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    by_id = {c["chunkId"]: c for c in chunks}
    qdoc = json.load(open(QUESTIONS, encoding="utf-8"))
    questions = (qdoc.get("questions", qdoc) if isinstance(qdoc, dict) else qdoc)
    questions = [q for q in questions if q.get("answerable", True)]
    ids = [c["chunkId"] for c in chunks]
    vectors = load_chunk_vectors()

    bm = json.load(open(BM25_FULL, encoding="utf-8"))
    bm25_rank = {q["questionId"]: [r["chunkId"] for r in q["ranking"]] for q in bm["perQuestion"]}
    bm25_score = {q["questionId"]: {r["chunkId"]: r["score"] for r in q["ranking"]}
                  for q in bm["perQuestion"]}

    print(f"语料 {len(chunks)} 段 | 题集 {len(questions)} 题 | top-{args.topk}")
    print("计算向量完整排序（查询向量实时求，语料向量走缓存）…")
    t0 = time.time()
    qvecs = embed([q["question"] for q in questions])
    vec_rank, vec_score = {}, {}
    for q, qv in zip(questions, qvecs):
        sims = [(sum(a * b for a, b in zip(qv, vectors[cid])), cid) for cid in ids]
        sims.sort(reverse=True)
        vec_rank[q["questionId"]] = [cid for _, cid in sims]
        vec_score[q["questionId"]] = {cid: s for s, cid in sims}
    print(f"  完成，用时 {time.time()-t0:.1f}s")

    results = []

    # 单路基线
    results.append(evaluate("BM25", bm25_rank, questions, args.topk))
    results.append(evaluate("VECTOR", vec_rank, questions, args.topk))

    # RRF：不同 k 值
    for k in (10, 60):
        rank = {}
        for q in questions:
            qid = q["questionId"]
            s = rrf([bm25_rank[qid], vec_rank[qid]], k=k)
            rank[qid] = sorted(s, key=lambda c: -s[c])
        results.append(evaluate(f"RRF(k={k})", rank, questions, args.topk))

    # 加权归一化分数融合
    for alpha in (0.3, 0.5, 0.7):
        rank = {}
        for q in questions:
            qid = q["questionId"]
            b = minmax({c: s for c, s in bm25_score[qid].items()})
            v = minmax(vec_score[qid])
            s = {c: alpha * b.get(c, 0.0) + (1 - alpha) * v.get(c, 0.0) for c in set(b) | set(v)}
            rank[qid] = sorted(s, key=lambda c: -s[c])
        results.append(evaluate(f"加权(α_bm25={alpha})", rank, questions, args.topk))

    print()
    print(f"{'策略':22s} {'命中':>8s} {'命中率':>8s} {'精确率@'+str(args.topk):>10s}")
    print("-" * 54)
    for r in results:
        print(f"{r['name']:22s} {r['hit']:>8s} {r['hitRate']:>8.4f} {r['precisionAtK']:>10.4f}")

    best = max(results, key=lambda r: (r["hitRate"], r["precisionAtK"]))
    print(f"\n最优：{best['name']}  命中 {best['hit']}  精确率 {best['precisionAtK']}")

    # 与单路基线比，哪些题被救回来
    base_hit = {d["questionId"] for d in results[0]["detail"] if d["hit"]}
    best_hit = {d["questionId"] for d in best["detail"] if d["hit"]}
    print(f"  融合后新增命中: {sorted(best_hit - base_hit) or '无'}")
    print(f"  融合后丢失命中: {sorted(base_hit - best_hit) or '无'}")

    if args.save_best:
        rank_map = {r["name"]: r for r in results}
        detail = {d["questionId"]: d for d in best["detail"]}
        per_q = []
        for q in questions:
            qid = q["questionId"]
            top = [{"chunkId": cid, "score": None,
                    "text": (by_id.get(cid, {}).get("text") or "")[:2000]}
                   for cid in detail[qid]["topk"]]
            per_q.append({"questionId": qid, "set": q.get("category", "main"),
                          "question": q["question"],
                          "referenceChunks": sorted(q.get("referenceChunks") or []),
                          "topk": top, "hit": detail[qid]["hit"], "hits": detail[qid]["hits"]})
        out = {
            "schema": "career-graph-hybrid-retrieval/v1",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "retriever": {"type": "hybrid", "strategy": best["name"], "topk": args.topk,
                          "components": ["bm25 (lib/graph.mjs)", "dense (TEI Qwen3-Embedding-0.6B)"]},
            "metrics": {"main": {"hitRate": best["hit"], "hitRateValue": best["hitRate"],
                                 "meanPrecisionAtK": best["precisionAtK"]}},
            "perQuestion": per_q,
        }
        path = os.path.join(OUT_DIR, "hybrid-topk.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\n最优策略已存为 {os.path.relpath(path, ROOT)}（可作为 deepeval 的 HYBRID 档位）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
