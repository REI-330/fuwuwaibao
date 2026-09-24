#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
划分稳定性检验：量化「在同一批题上选型」带来的乐观偏差。

背景（见 EVAL_SET_DESIGN.md §0）：这套链路零训练，真正被"学习"的只有超参，
而 α 是在这 14 题上选的、又用这 14 题报成绩。新 test 集还没出题，
所以先用现有 14 题做一次**对半劈开**的模拟：

  对每一个 7/7 切分（C(14,7)=3432 种）：
    A 半当"开发集"用来选 α；B 半当"测试集"用来验收。
  统计三件事：
    1) 在 A 上选出的 α 分布 —— 如果 α 稳，那选型风险就小
    2) B 上拿到的成绩 vs「固定用 α=0.35」vs「B 上的事后最优（oracle）」
    3) 乐观偏差 = A 上的成绩 − 同一配置在 B 上的成绩

同时检验「重排要不要上」这个二元决定在两个半集上是否一致（重排结果读缓存，不重复调模型）。

局限先说清：每半只有 7 题，命中率的最小刻度是 1/7 ≈ 0.143，单次切分极粗；
所以只看**聚合统计**，不看单次切分的结果。

用法：python knowledge/eval/split_half_check.py
"""
import itertools
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
QUESTIONS = os.path.join(K, "evaluations", "questions-v2.json")
RUNS = os.path.join(K, "eval", "runs")
BM25_FULL = os.path.join(RUNS, "bm25-full.json")
RERANK_CACHE = os.path.join(RUNS, "rerank-cache-10.json")
CACHE_DIR = os.path.join(K, "eval", "cache")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")
ALPHAS = [round(0.05 * i, 2) for i in range(1, 20)]
TOPK = 3


def embed(texts, timeout=600, batch=8):
    out = []
    for i in range(0, len(texts), batch):
        body = json.dumps({"input": texts[i:i + batch], "model": MODEL}).encode("utf-8")
        req = urllib.request.Request(TEI, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        out += [it["embedding"] for it in sorted(d["data"], key=lambda x: x["index"])]
    return out


def minmax(d):
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-12:
        return {k: 0.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def main():
    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    ids = [c["chunkId"] for c in chunks]
    qdoc = json.load(open(QUESTIONS, encoding="utf-8"))
    qs = [q for q in qdoc["questions"] if q.get("answerable", True)]
    refs = {q["questionId"]: set(q["referenceChunks"]) for q in qs}

    bm = json.load(open(BM25_FULL, encoding="utf-8"))
    b_sc = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]}
            for x in bm["perQuestion"]}
    vecs = json.load(open(os.path.join(
        CACHE_DIR, f"chunk-vectors-{MODEL.replace('/', '_')}.json"), encoding="utf-8"))["vectors"]
    qvecs = embed([q["question"] for q in qs])

    # 每个 (α, 题) 的命中与精确率
    hit = {a: {} for a in ALPHAS}
    prec = {a: {} for a in ALPHAS}
    for q, qv in zip(qs, qvecs):
        qid = q["questionId"]
        vs = {cid: sum(x * y for x, y in zip(qv, vecs[cid])) for cid in ids}
        b, v = minmax(b_sc[qid]), minmax(vs)
        for a in ALPHAS:
            f = {c: a * b.get(c, 0.0) + (1 - a) * v.get(c, 0.0) for c in set(b) | set(v)}
            top = sorted(f, key=lambda c: -f[c])[:TOPK]
            h = [c for c in top if c in refs[qid]]
            hit[a][qid] = 1.0 if h else 0.0
            prec[a][qid] = len(h) / TOPK

    # 重排（读缓存）
    rr = {}
    if os.path.exists(RERANK_CACHE):
        picks = json.load(open(RERANK_CACHE, encoding="utf-8"))["picks"]
        for q in qs:
            qid = q["questionId"]
            p = list((picks.get(qid) or {}).get("picked") or [])[:TOPK]
            rr[qid] = 1.0 if any(c in refs[qid] for c in p) else 0.0

    qids = [q["questionId"] for q in qs]
    n = len(qids)
    half = n // 2

    def hit_of(d, subset):
        return mean([hit[d][q] for q in subset])

    def prec_of(d, subset):
        return mean([prec[d][q] for q in subset])

    picks_alpha, gaps, held, held_fixed, oracle, prec_held, prec_fixed = [], [], [], [], [], [], []
    rr_consistent = rr_winA = rr_loseA = 0
    trend = []
    for combo in itertools.combinations(range(n), half):
        A = [qids[i] for i in combo]
        B = [qids[i] for i in range(n) if i not in combo]
        # 在 A 上按 (命中率, 精确率) 选 α
        a_star = max(ALPHAS, key=lambda a: (hit_of(a, A), prec_of(a, A)))
        picks_alpha.append(a_star)
        gaps.append(hit_of(a_star, A) - hit_of(a_star, B))
        held.append(hit_of(a_star, B))
        held_fixed.append(hit_of(0.35, B))
        oracle.append(max(hit_of(a, B) for a in ALPHAS))
        prec_held.append(prec_of(a_star, B))
        prec_fixed.append(prec_of(0.35, B))
        if rr and all(q in rr for q in qids):
            # 重排 14/14、融合 13/14 属包含关系，半集上常常打平 —— 平局必须单独算，
            # 否则「两边都不更差」会被误报成「决定不可靠」。
            rrA = mean([rr[q] for q in A]); fA = mean([hit[0.35][q] for q in A])
            rrB = mean([rr[q] for q in B]); fB = mean([hit[0.35][q] for q in B])
            if rrA >= fA and rrB >= fB:
                rr_consistent += 1
            elif rrA > fA and rrB < fB:
                rr_winA += 1
            elif rrA < fA and rrB > fB:
                rr_loseA += 1

    # dev 规模敏感性：乐观偏差随开发集变大怎么变（用于判断 +7pp 能不能外推到 dev=14）
    for dev_size in (5, 7, 9, 11, 13):
        g, h, o = [], [], []
        for combo in itertools.combinations(range(n), dev_size):
            A = [qids[i] for i in combo]
            B = [qids[i] for i in range(n) if i not in combo]
            a_star = max(ALPHAS, key=lambda a: (hit_of(a, A), prec_of(a, A)))
            g.append(hit_of(a_star, A) - hit_of(a_star, B))
            h.append(hit_of(a_star, B))
            o.append(hit_of(0.35, B))
        trend.append({"devSize": dev_size, "testSize": n - dev_size, "splits": len(g),
                      "optimismGap": round(mean(g), 4), "heldOut": round(mean(h), 4),
                      "fixed035HeldOut": round(mean(o), 4)})

    from collections import Counter
    dist = Counter(picks_alpha)
    total = len(picks_alpha)
    print(f"=== 对半劈开稳定性检验（{total} 种 7/7 切分，每半 {half} 题）===\n")
    print("1) 在 A 半上选出的 α 分布（若某一档占绝对多数，说明 α 稳）")
    for a, c in sorted(dist.items(), key=lambda x: -x[1])[:8]:
        print(f"   α={a:<5} {c:5d} 次  {100*c/total:5.1f}%")
    print(f"   不同取值的个数：{len(dist)} / {len(ALPHAS)}")
    print(f"   选到已采纳 α=0.35 的比例：{100*dist.get(0.35,0)/total:.1f}%")
    in_band = sum(c for a, c in dist.items() if 0.30 <= a <= 0.40)
    print(f"   落在 α∈[0.30,0.40] 的比例：{100*in_band/total:.1f}%\n")

    print("2) 把 A 上选的 α 拿到 B 半（留出）验收")
    print(f"   命中率  留出 {mean(held):.4f}   固定 α=0.35 时留出 {mean(held_fixed):.4f}"
          f"   B 上事后最优(oracle) {mean(oracle):.4f}")
    print(f"   精确率@3 留出 {mean(prec_held):.4f}   固定 α=0.35 时留出 {mean(prec_fixed):.4f}\n")

    print("3) 乐观偏差 = A 半成绩 − 同一配置在 B 半的成绩")
    print(f"   平均 {mean(gaps):+.4f}（每半仅 {half} 题，命中率刻度 1/{half}≈{1/half:.3f}）")
    print(f"   → 换算成「题」：平均高估约 {mean(gaps)*half:+.2f} 题 / {half} 题\n")

    if rr:
        print("4) 「重排要不要上」这个决定的跨半一致性（A 半上的结论能否在 B 半复现）")
        print(f"   两边都「重排不更差」：{rr_consistent}/{total}  {100*rr_consistent/total:.1f}%")
        print(f"   A 说更好、B 说更差（**决定不可靠**）：{rr_winA}/{total}  {100*rr_winA/total:.1f}%")
        print(f"   A 说更差、B 说更好：{rr_loseA}/{total}  {100*rr_loseA/total:.1f}%")

    print("\n5) dev 规模敏感性：乐观偏差随开发集变大而衰减")
    print(f"   {'dev':>4s} {'test':>5s} {'切分数':>7s} {'乐观偏差':>9s} {'留出命中率':>11s} {'固定α=0.35':>11s}")
    for t in trend:
        print(f"   {t['devSize']:>4d} {t['testSize']:>5d} {t['splits']:>7d}"
              f" {t['optimismGap']:>+9.4f} {t['heldOut']:>11.4f} {t['fixed035HeldOut']:>11.4f}")

    print(f"\n口径提示：每半 {half} 题，最小刻度 1/{half}≈{1/half:.3f}，单次切分毫无分辨力；上面是聚合统计。"
          "\n真正的验收必须等 questions-test.json（40 题）出题冻结后再做，本检验只是过渡。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
