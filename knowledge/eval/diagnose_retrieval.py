#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
检索失败诊断：失败的题到底是「没召回」还是「召回了但排不进 Top-3」。

为什么需要这一步：
  优化命中率之前必须先知道缺口在哪。两种失败的修法完全不同——
  「没召回」要靠跨语言/查询改写，而「召回但排位靠后」要靠重排或邻域扩展。
  只报一个 11/14 这样的总命中率，无法判断该动哪一层。

做法（不另写检索实现，全部读既有产物）：
  1) BM25 名次：读 knowledge/eval/runs/bm25-full.json（由 bm25-full.mjs 导出，复用 lib/graph.mjs）
  2) 向量名次：语料向量走缓存，只实时求 14 条查询向量（与 vector_retrieval.py 同一 TEI）
  3) 融合名次：与 hybrid_experiment.py 同一套 minmax 加权口径，α 可扫
  4) 邻域信号：参考段在原文里的 charRange 是连续的，据此统计「参考答案的前后邻居
     是否进入了某一路的候选池」——这决定邻域扩展有没有用

用法：
  python knowledge/eval/diagnose_retrieval.py
  python knowledge/eval/diagnose_retrieval.py --alpha 0.35
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


def minmax(d):
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-12:
        return {k: 0.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def rank_of(ranking, cid):
    try:
        return ranking.index(cid) + 1
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", type=float, default=0.35, help="BM25 权重")
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--pool", type=int, default=50, help="判定「候选池」的深度")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    by_id = {c["chunkId"]: c for c in chunks}
    ids = [c["chunkId"] for c in chunks]

    # 同来源按 charRange 排序 → 得到原文相邻关系（分块是连续切片，所以首尾相接即为相邻）
    by_source = {}
    for c in chunks:
        by_source.setdefault(c["sourceId"], []).append(c)
    for src in by_source:
        by_source[src].sort(key=lambda c: c["charRange"][0])
    neighbor_of = {}  # chunkId -> {prev: id, next: id}
    for src, rows in by_source.items():
        for i, c in enumerate(rows):
            neighbor_of[c["chunkId"]] = {
                "prev": rows[i - 1]["chunkId"] if i > 0 else None,
                "next": rows[i + 1]["chunkId"] if i + 1 < len(rows) else None,
            }

    qs = json.load(open(QUESTIONS, encoding="utf-8"))["questions"]
    qs = [q for q in qs if q.get("answerable", True)]

    bm = json.load(open(BM25_FULL, encoding="utf-8"))
    b_rank = {x["questionId"]: [r["chunkId"] for r in x["ranking"]] for x in bm["perQuestion"]}
    b_sc = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]}
            for x in bm["perQuestion"]}

    vecs = json.load(open(os.path.join(
        CACHE_DIR, f"chunk-vectors-{MODEL.replace('/', '_')}.json"), encoding="utf-8"))["vectors"]
    t0 = time.time()
    qvecs = embed([q["question"] for q in qs])
    v_rank, v_sc = {}, {}
    for q, qv in zip(qs, qvecs):
        sims = sorted(((sum(a * b for a, b in zip(qv, vecs[cid])), cid) for cid in ids),
                      reverse=True)
        v_rank[q["questionId"]] = [c for _, c in sims]
        v_sc[q["questionId"]] = {c: s for s, c in sims}
    print(f"向量完整排序完成（{time.time()-t0:.1f}s）\n")

    rows = []
    for q in qs:
        qid = q["questionId"]
        refs = list(q.get("referenceChunks") or [])
        b = minmax(b_sc[qid])
        v = minmax(v_sc[qid])
        fused = {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * v.get(c, 0.0)
                 for c in set(b) | set(v)}
        h_rank = sorted(fused, key=lambda c: -fused[c])

        per_ref = []
        for cid in refs:
            per_ref.append({
                "chunkId": cid,
                "bm25Rank": rank_of(b_rank[qid], cid),
                "vectorRank": rank_of(v_rank[qid], cid),
                "hybridRank": rank_of(h_rank, cid),
                "sourceId": by_id.get(cid, {}).get("sourceId"),
            })
        best = min([r["hybridRank"] for r in per_ref if r["hybridRank"] is not None], default=None)

        # 邻域信号：参考答案的原文前后邻居，是否进了任一通道的前 pool
        pool_ids = set(b_rank[qid][:args.pool]) | set(v_rank[qid][:args.pool])
        neigh_hits = []
        for cid in refs:
            nb = neighbor_of.get(cid, {})
            for side in ("prev", "next"):
                nid = nb.get(side)
                if nid and nid in pool_ids:
                    neigh_hits.append({"ref": cid, "side": side, "neighbor": nid,
                                       "bm25Rank": rank_of(b_rank[qid], nid),
                                       "vectorRank": rank_of(v_rank[qid], nid)})

        if best is None or best > args.pool:
            cls = "未召回"
        elif best > 10:
            cls = "召回但排位靠后"
        elif best > args.topk:
            cls = "召回但差一点"
        else:
            cls = "命中"
        rows.append({
            "questionId": qid, "category": q.get("category"), "question": q["question"],
            "referenceChunks": refs, "perRef": per_ref,
            "bestHybridRank": best, "class": cls,
            "hybridTopK": h_rank[:args.topk],
            "neighborInPool": neigh_hits,
        })

    # 打印
    print(f"=== 逐题诊断（融合 α_bm25={args.alpha}，候选池 Top-{args.pool}）===")
    print(f"{'题':5s} {'类别':10s} {'BM25名次':>18s} {'向量名次':>18s} {'最优融合名次':>12s}  判定")
    print("-" * 92)
    for r in rows:
        b = ",".join(str(x["bm25Rank"]) for x in r["perRef"])
        v = ",".join(str(x["vectorRank"]) for x in r["perRef"])
        print(f"{r['questionId']:5s} {str(r['category'])[:8]:10s} {b:>18s} {v:>18s}"
              f" {str(r['bestHybridRank']):>12s}  {r['class']}")

    n = len(rows)
    hit = sum(1 for r in rows if r["class"] == "命中")
    print(f"\n命中 {hit}/{n}；未命中的题：")
    for r in rows:
        if r["class"] == "命中":
            continue
        print(f"  {r['questionId']} [{r['class']}] 参考段 " +
              ", ".join(f"{x['chunkId']}(b={x['bm25Rank']},v={x['vectorRank']},h={x['hybridRank']})"
                        for x in r["perRef"]))
        if r["neighborInPool"]:
            for nh in r["neighborInPool"]:
                print(f"      ↳ 邻域在候选池内：{nh['neighbor']} 是 {nh['ref']} 的"
                      f"{'前' if nh['side'] == 'prev' else '后'}邻 (b={nh['bm25Rank']}, v={nh['vectorRank']})")
        else:
            print("      ↳ 无邻域块进入候选池")

    n_no_recall = sum(1 for r in rows if r["class"] == "未召回")
    n_near = sum(1 for r in rows if r["class"] == "召回但差一点")
    n_far = sum(1 for r in rows if r["class"] == "召回但排位靠后")
    print(f"\n分布：命中 {hit} / 差一点 {n_near} / 排位靠后 {n_far} / 完全未召回 {n_no_recall}")

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "diagnosis.json")
    json.dump({"schema": "career-graph-retrieval-diagnosis/v1",
               "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "alpha_bm25": args.alpha, "topk": args.topk, "pool": args.pool,
               "counts": {"total": n, "hit": hit, "near": n_near, "far": n_far,
                          "noRecall": n_no_recall},
               "perQuestion": rows}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n产物：{os.path.relpath(out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
