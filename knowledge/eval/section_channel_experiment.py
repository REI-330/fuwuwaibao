#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
章节标题通道实验：稠密通道「看不见」章节标题，补一个字段通道值不值。

发现的真实口径不一致（不是调参问题）：
  knowledge/pipeline/bm25-full.mjs 的文档文本 = heading + sectionPath + tags + text
  knowledge/eval/vector_retrieval.py 只嵌 c["text"]
同一份语料，两路检索看到的字段不一样。后果在 N12 上最明显：
  问题问「主要任务和工作活动」，答案章节标题就叫 Tasks / Work Activities，
  但正文里没有「章节」这个信息，稠密通道于是被同文档的
  「Transferable Skills」「Professional Associations」等章节压过去。

本脚本先用一个便宜的代理验证该信号是否真的有区分度：
  不重嵌 494 段正文，只嵌「章节标题路径」（去重后条数少），
  然后按 score = 融合 + β·章节标题相似度 混入，扫 β。
若有效，再去重嵌「章节路径 + 正文」的完整字段版（那需要约 20 分钟 CPU）。

用法：
  python knowledge/eval/section_channel_experiment.py
"""
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
RUNS = os.path.join(K, "eval", "runs")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")
SECTION_CACHE = os.path.join(CACHE_DIR, f"section-vectors-{MODEL.replace('/', '_')}.json")


def embed(texts, timeout=600, batch=16):
    out = []
    for i in range(0, len(texts), batch):
        body = json.dumps({"input": texts[i:i + batch], "model": MODEL}).encode("utf-8")
        req = urllib.request.Request(TEI, data=body,
                                     headers={"Content-Type": "application/json"})
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


def main():
    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    ids = [c["chunkId"] for c in chunks]
    by_id = {c["chunkId"]: c for c in chunks}
    qs = [q for q in json.load(open(QUESTIONS, encoding="utf-8"))["questions"]
          if q.get("answerable", True)]
    refs_of = {q["questionId"]: set(q["referenceChunks"]) for q in qs}

    # 章节标题文本：heading 与 sectionPath 可能互为前缀，合并去重
    def section_text(c):
        parts = []
        for p in (c.get("heading"), c.get("sectionPath")):
            if p and p not in parts:
                parts.append(p)
        return " > ".join(parts)

    texts = {cid: section_text(by_id[cid]) for cid in ids}
    uniq = sorted({t for t in texts.values() if t})
    print(f"章节标题：{len(uniq)} 条去重（{len(ids)} 段中 {sum(1 for t in texts.values() if not t)} 段无标题）")
    if os.path.exists(SECTION_CACHE):
        sec_vec = json.load(open(SECTION_CACHE, encoding="utf-8"))["vectors"]
        print("复用章节标题向量缓存")
    else:
        t0 = time.time()
        vecs = embed(uniq)
        sec_vec = dict(zip(uniq, vecs))
        json.dump({"model": MODEL, "count": len(sec_vec), "vectors": sec_vec},
                  open(SECTION_CACHE, "w", encoding="utf-8"))
        print(f"章节标题向量化完成 {len(sec_vec)} 条，用时 {time.time()-t0:.1f}s")

    body_vec = json.load(open(os.path.join(
        CACHE_DIR, f"chunk-vectors-{MODEL.replace('/', '_')}.json"), encoding="utf-8"))["vectors"]
    bm = json.load(open(BM25_FULL, encoding="utf-8"))
    b_sc = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]}
            for x in bm["perQuestion"]}

    print("嵌入查询…")
    qvecs = embed([q["question"] for q in qs], batch=8)
    v_body, v_sec = {}, {}
    for q, qv in zip(qs, qvecs):
        v_body[q["questionId"]] = {cid: sum(a * b for a, b in zip(qv, body_vec[cid])) for cid in ids}
        v_sec[q["questionId"]] = {cid: (sum(a * b for a, b in zip(qv, sec_vec[texts[cid]]))
                                        if texts[cid] else -1.0) for cid in ids}

    alpha = 0.35

    # 原文相邻关系（分块是连续切片，charRange 首尾相接即为相邻）
    by_source = {}
    for c in chunks:
        by_source.setdefault(c["sourceId"], []).append(c)
    prev_of, next_of = {}, {}
    for src, rows in by_source.items():
        rows.sort(key=lambda c: c["charRange"][0])
        for i, c in enumerate(rows):
            prev_of[c["chunkId"]] = rows[i - 1]["chunkId"] if i > 0 else None
            next_of[c["chunkId"]] = rows[i + 1]["chunkId"] if i + 1 < len(rows) else None

    def evaluate(beta, lam_pull=0.0):
        hit = top1 = 0
        prec = 0.0
        fails, detail = [], {}
        for q in qs:
            qid = q["questionId"]
            refs = refs_of[qid]
            b = minmax(b_sc[qid])
            vb = minmax(v_body[qid])
            vs = minmax(v_sec[qid]) if beta else {}
            f = {}
            for cid in ids:
                v = (1 - beta) * vb.get(cid, 0.0) + beta * vs.get(cid, 0.0) if beta else vb.get(cid, 0.0)
                f[cid] = alpha * b.get(cid, 0.0) + (1 - alpha) * v
            if lam_pull:
                # 与 locality_variants.py 的 pull 口径一致：只往上拉、不超过邻块
                s = {}
                for cid in f:
                    best = 0.0
                    for nb in (prev_of.get(cid), next_of.get(cid)):
                        if nb and nb in f:
                            best = max(best, f[nb])
                    s[cid] = f[cid] + lam_pull * max(0.0, best - f[cid])
                f = s
            ranked = sorted(f, key=lambda c: -f[c])
            top = ranked[:3]
            hits = [c for c in top if c in refs]
            detail[qid] = {"top": top, "hits": hits,
                           "bestRefRank": min([ranked.index(r) + 1 for r in refs if r in ranked],
                                              default=None)}
            if hits:
                hit += 1
            else:
                fails.append(qid)
            if top and top[0] in refs:
                top1 += 1
            prec += len(hits) / 3
        n = len(qs)
        return {"beta": beta, "lam_pull": lam_pull, "hit": hit, "n": n,
                "hitRate": round(hit / n, 4), "hitAt1": round(top1 / n, 4),
                "prec": round(prec / n, 4), "fails": fails, "detail": detail}

    print("\n=== 章节标题通道 β 扫描（α_bm25=0.35 固定）===")
    results = []
    for beta in (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5):
        r = evaluate(beta)
        results.append(r)
        print(f"  β={beta:.2f}  命中 {r['hit']}/{r['n']}  命中@1 {r['hitAt1']:.4f}"
              f"  精确率@3 {r['prec']:.4f}  未命中 {r['fails'] or '-'}")

    best = max(results, key=lambda r: (r["hitRate"], r["prec"], r["hitAt1"]))
    print(f"\n最优 β={best['beta']}  命中 {best['hit']}/{best['n']}")

    print("\n=== 叠加上唯一没有退化的邻域信号（pull λ=0.5）===")
    with_pull = []
    for beta in (0.0, best["beta"]):
        r = evaluate(beta, lam_pull=0.5)
        with_pull.append(r)
        print(f"  β={beta:.2f} + pull0.5  命中 {r['hit']}/{r['n']}  命中@1 {r['hitAt1']:.4f}"
              f"  精确率@3 {r['prec']:.4f}  未命中 {r['fails'] or '-'}")

    print("\nN12 参考答案名次（基线 β=0 → 各档）：")
    for r in results + with_pull:
        d = r["detail"]["N12"]
        if d["bestRefRank"] is not None:
            print(f"  β={r['beta']:.2f} pull={r['lam_pull']}  N12 最优参考段名次 {d['bestRefRank']}"
                  f"  top3={d['top']}")

    allr = results + with_pull
    win = max(allr, key=lambda r: (r["hitRate"], r["prec"], r["hitAt1"]))
    base = results[0]
    delta = sorted({q for q in refs_of
                    if bool(win["detail"][q]["hits"]) != bool(base["detail"][q]["hits"])})
    print(f"\n整体最优：β={win['beta']} pull={win['lam_pull']}  "
          f"命中 {win['hit']}/{win['n']}  精确率@3 {win['prec']}")
    print(f"与 β=0 基线相比翻转的题：{delta or '无'}")

    json.dump({"schema": "career-graph-section-channel/v1",
               "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "alpha_bm25": alpha, "uniqueSectionTitles": len(uniq),
               "curve": [{"beta": r["beta"], "lamPull": r["lam_pull"], "hit": f"{r['hit']}/{r['n']}",
                          "hitAt1": r["hitAt1"], "precisionAtK": r["prec"], "fails": r["fails"]}
                         for r in allr]},
              open(os.path.join(RUNS, "section-channel.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"产物：knowledge/eval/runs/section-channel.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
