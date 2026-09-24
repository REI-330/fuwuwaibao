#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
融合策略对照实验：BM25 与稠密向量怎么合。

为什么要新写一个：`hybrid_experiment.py` 写死了 questions-v2.json 与 bm25-full.json
（旧题集与旧产物），且只报整体命中率，看不到「跨语言层」与「中文层」的差别，
也不报 R@10（候选池深度）——而重排只能在给定候选里选，候选池浅是真正的瓶颈。

被测策略（都在同一套题、同一份语料、同一份 BM25/向量排序上算）：
  - 单路：BM25 / VECTOR
  - 分数融合（minmax 归一化）：α_bm25 细扫
  - RRF（Reciprocal Rank Fusion，无需标定）：k = 10/20/60
  - 加权 RRF：w_bm25 = 0.3/0.5/0.7
  - **分层的 oracle 上界**：按层各取该层最优 α（需要知道答案语言，不可部署，
    只用来量「按层定权」到底有多少空间）

指标：命中@3、命中@1、精确率@3、R@3、R@10，并按 layer（zh/xl）拆开。

用法：
  python knowledge/eval/fusion_variants.py \
    --questions knowledge/evaluations/questions-dev.json \
    --bm25 knowledge/eval/runs/dev2-bm25-full.json
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
CACHE_DIR = os.path.join(K, "eval", "cache")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")


def embed(texts, timeout=600, batch=8):
    """分批调用：一次塞太多会被 TEI 判 413 Payload Too Large（实测 34 条即触发）。"""
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        body = json.dumps({"input": chunk, "model": MODEL}).encode("utf-8")
        req = urllib.request.Request(TEI, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        out.extend(it["embedding"] for it in sorted(d["data"], key=lambda x: x["index"]))
    return out


def load_chunk_vectors():
    p = os.path.join(CACHE_DIR, f"chunk-vectors-{MODEL.replace('/', '_')}.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)["vectors"]


def minmax(d):
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-12:
        return {k: 0.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def rrf(rank_lists, k=60, weights=None):
    scores = {}
    for i, ranks in enumerate(rank_lists):
        w = 1.0 if not weights else weights[i]
        for rank, cid in enumerate(ranks, 1):
            scores[cid] = scores.get(cid, 0.0) + w / (k + rank)
    return sorted(scores, key=lambda c: -scores[c])


def score_fusion(bm, vec, alpha):
    b = minmax(bm)
    v = minmax(vec)
    s = {c: alpha * b.get(c, 0.0) + (1 - alpha) * v.get(c, 0.0) for c in set(b) | set(v)}
    return sorted(s, key=lambda c: -s[c])


def evaluate(rank_by_q, questions, topk, gold_k=10):
    """返回整体与分层的指标。投毒口径：命中 = Top-k 里至少 1 段参考答案。"""
    def blank():
        return {"n": 0, "hit": 0, "hit1": 0, "p": 0.0, "r3": 0, "r10": 0, "refs": 0}
    agg = {"ALL": blank()}
    for q in questions:
        qid = q["questionId"]
        layer = q.get("layer", "?")
        refs = set(q.get("referenceChunks") or [])
        ranking = rank_by_q[qid]
        top = ranking[:topk]
        top10 = ranking[:gold_k]
        h = len(refs.intersection(top))
        h10 = len(refs.intersection(top10))
        for key in ("ALL", layer):
            s = agg.setdefault(key, blank())
            s["n"] += 1
            s["hit"] += 1 if h else 0
            s["hit1"] += 1 if (top and top[0] in refs) else 0
            s["p"] += h / topk
            s["r3"] += h
            s["r10"] += h10
            s["refs"] += len(refs)
    for s in agg.values():
        n = max(1, s["n"])
        s["hitRate"] = s["hit"] / n
        s["hitAt1"] = s["hit1"] / n
        s["pAtK"] = s["p"] / n
        s["rAt3"] = s["r3"] / max(1, s["refs"])
        s["rAt10"] = s["r10"] / max(1, s["refs"])
    return agg


def fmt(name, agg, keys=("ALL", "zh", "xl")):
    out = [f"{name:22s}"]
    for k in keys:
        s = agg[k]
        out.append(f"| {k}: {s['hit']:>2}/{s['n']:<2} @1={s['hitAt1']:.2f} "
                   f"P3={s['pAtK']:.3f} R10={s['rAt10']:.3f}")
    return " ".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--bm25", required=True)
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--json-out")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    ids = [c["chunkId"] for c in chunks]
    questions = [q for q in json.load(open(args.questions, encoding="utf-8"))["questions"]
                 if q.get("answerable", True)]
    vectors = load_chunk_vectors()

    bm = json.load(open(args.bm25, encoding="utf-8"))
    bm_rank, bm_score = {}, {}
    for q in bm["perQuestion"]:
        bm_rank[q["questionId"]] = [r["chunkId"] for r in q["ranking"]]
        bm_score[q["questionId"]] = {r["chunkId"]: r["score"] for r in q["ranking"]}

    print(f"语料 {len(chunks)} 段 | 题集 {len(questions)} 题 | top-{args.topk}")
    layers = {}
    for q in questions:
        layers[q.get("layer", "?")] = layers.get(q.get("layer", "?"), 0) + 1
    print(f"分层：{layers}\n计算向量完整排序…")
    t0 = time.time()
    qvecs = embed([q["question"] for q in questions])
    vec_rank, vec_score = {}, {}
    for q, qv in zip(questions, qvecs):
        sims = sorted(((sum(a * b for a, b in zip(qv, vectors[cid])), cid) for cid in ids),
                      reverse=True)
        vec_rank[q["questionId"]] = [cid for _, cid in sims]
        vec_score[q["questionId"]] = {cid: s for s, cid in sims}
    print(f"  用时 {time.time() - t0:.1f}s\n")

    results = {}

    def add(name, fn):
        results[name] = evaluate({q["questionId"]: fn(q["questionId"]) for q in questions},
                                 questions, args.topk)

    add("BM25 单路", lambda qid: bm_rank[qid])
    add("VECTOR 单路", lambda qid: vec_rank[qid])
    for k in (10, 20, 60):
        add(f"RRF k={k}", lambda qid, k=k: rrf([bm_rank[qid], vec_rank[qid]], k=k))
    for w in (0.3, 0.5, 0.7):
        add(f"加权RRF k=60 w_bm25={w}",
            lambda qid, w=w: rrf([bm_rank[qid], vec_rank[qid]], k=60, weights=[w, 1 - w]))
    for a in [x / 20 for x in range(0, 21)]:
        add(f"分数融合 α={a:.2f}",
            lambda qid, a=a: score_fusion(bm_score[qid], vec_score[qid], a))

    # 分层的 oracle 上界：按层各取该层最优 α（不可部署，只看空间）
    best_alpha_per_layer = {}
    for layer in ("zh", "xl"):
        cand = []
        for a in [x / 20 for x in range(0, 21)]:
            agg = results[f"分数融合 α={a:.2f}"]
            s = agg[layer]
            cand.append((s["hitRate"], s["pAtK"], a))
        best_alpha_per_layer[layer] = max(cand)[2]
    print(f"分层 oracle 的最优 α：{best_alpha_per_layer}")

    print(f"\n{'策略':22s}  分层指标")
    print("-" * 120)
    order = ["BM25 单路", "VECTOR 单路"] + \
            [f"RRF k={k}" for k in (10, 20, 60)] + \
            [f"加权RRF k=60 w_bm25={w}" for w in (0.3, 0.5, 0.7)] + \
            [f"分数融合 α={x / 20:.2f}" for x in range(0, 21)]
    for name in order:
        print(fmt(name, results[name]))

    print(f"\n{'策略':22s} {'整体命中':>8s} {'命中@1':>7s} {'P@3':>7s} {'R@3':>7s} {'R@10':>7s}")
    print("-" * 62)
    for name in order:
        s = results[name]["ALL"]
        print(f"{name:22s} {s['hit']:>5}/{s['n']:<3} {s['hitAt1']:>7.3f} "
              f"{s['pAtK']:>7.3f} {s['rAt3']:>7.3f} {s['rAt10']:>7.3f}")

    if args.json_out:
        slim = {n: {k: {kk: vv for kk, vv in v.items() if kk != "refs"}
                   for k, v in agg.items()} for n, agg in results.items()}
        json.dump({"questions": args.questions, "topk": args.topk,
                   "oracleAlphaPerLayer": best_alpha_per_layer, "results": slim},
                  open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n机读结果 → {os.path.relpath(args.json_out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
