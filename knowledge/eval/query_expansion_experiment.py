#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
查询扩展实验：用 LLM 把中文问题扩展成中英双语关键词查询，再测命中率。

动机（实测）：唯一失败题 N12 是「中文问 / 英文 O*NET 语料」——它的参考答案在向量
检索里排第 8 与第 20（能召回、排不进前三），BM25 完全找不到（字面零重叠）。这是
跨语言匹配问题，靠调融合权重解决不了。

做法：对每题生成一条扩展查询（中文原词 + 英文对应词），分别对扩展查询重跑 BM25 与
向量检索，再与基线在同一套题上比。

代价必须如实报：LLM 调用使检索非确定、并增加延迟与外部依赖。因此本脚本同时输出
「基线（无扩展）」与「扩展后」两组数字，供权衡。

用法：
  python knowledge/eval/query_expansion_experiment.py --save-best
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
QUESTIONS = os.path.join(K, "evaluations", "questions-v2.json")
RUNS = os.path.join(K, "eval", "runs")
CACHE_DIR = os.path.join(K, "eval", "cache")
ENV_FILE = os.path.join(K, "eval", ".env")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
TEI_MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")

EXPAND_PROMPT = """你是检索查询改写器。把下面的中文问题改写成一行检索查询，
要求：保留中文关键词，并补上对应的英文术语（因为待检索的语料可能是英文的）。
只输出一行查询串，不要解释、不要编号、不要引号。

问题：{q}
查询："""


def load_env():
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def llm(prompt, timeout=120):
    base = os.environ["DEEPEVAL_BASE_URL"].rstrip("/")
    body = json.dumps({"model": os.environ["DEEPEVAL_MODEL"], "temperature": 0,
                       "max_tokens": 200,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + os.environ["DEEPEVAL_API_KEY"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return (d["choices"][0]["message"]["content"] or "").strip().splitlines()[0].strip()


def embed(texts, timeout=300):
    body = json.dumps({"input": texts, "model": TEI_MODEL}).encode("utf-8")
    req = urllib.request.Request(TEI, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return [it["embedding"] for it in sorted(d["data"], key=lambda x: x["index"])]


def minmax(d):
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    return {k: 0.0 for k in d} if hi - lo < 1e-12 else {k: (v - lo) / (hi - lo) for k, v in d.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--alpha", type=float, default=0.35, help="BM25 权重（基线实验的最优值）")
    ap.add_argument("--save-best", action="store_true")
    args = ap.parse_args()
    load_env()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    by_id = {c["chunkId"]: c for c in chunks}
    ids = [c["chunkId"] for c in chunks]
    qs = json.load(open(QUESTIONS, encoding="utf-8"))["questions"]
    vecs = json.load(open(os.path.join(CACHE_DIR,
        f"chunk-vectors-{TEI_MODEL.replace('/', '_')}.json"), encoding="utf-8"))["vectors"]

    # 1) LLM 查询扩展
    print("== 1) LLM 查询扩展 ==")
    expanded = {}
    t0 = time.time()
    for q in qs:
        try:
            expanded[q["questionId"]] = llm(EXPAND_PROMPT.format(q=q["question"]))
        except Exception as e:
            print(f"   {q['questionId']} 扩展失败 {type(e).__name__}，回退原问题")
            expanded[q["questionId"]] = q["question"]
    exp_time = time.time() - t0
    print(f"   {len(expanded)} 题，用时 {exp_time:.1f}s（{exp_time/len(qs):.1f}s/题）")
    for q in qs[:3]:
        print(f"   {q['questionId']}: {expanded[q['questionId']][:110]}")
    out_exp = os.path.join(RUNS, "expanded-queries.json")
    json.dump({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "model": os.environ["DEEPEVAL_MODEL"], "queries": expanded},
              open(out_exp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 2) 用扩展查询重跑 BM25（复用 lib/graph.mjs，不另写实现）
    print("\n== 2) 扩展查询上的 BM25 ==")
    tmp_q = os.path.join(RUNS, "_expanded_questions.json")
    doc = {"questions": [dict(q, question=expanded[q["questionId"]]) for q in qs]}
    json.dump(doc, open(tmp_q, "w", encoding="utf-8"), ensure_ascii=False)
    bm_out = os.path.join(RUNS, "bm25-full-expanded.json")
    rc = subprocess.call(["node", "knowledge/pipeline/bm25-full.mjs",
                          "--questions", tmp_q, "--out", bm_out], cwd=ROOT)
    print("   node 退出码:", rc)

    # 3) 基线 & 扩展后的完整排序
    def rankings(queries):
        bm = json.load(open(os.path.join(RUNS, "bm25-full.json" if queries is None else bm_out),
                            encoding="utf-8"))
        b_rank = {x["questionId"]: [r["chunkId"] for r in x["ranking"]] for x in bm["perQuestion"]}
        b_sc = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]} for x in bm["perQuestion"]}
        qtexts = [q["question"] if queries is None else queries[q["questionId"]] for q in qs]
        qvecs = embed(qtexts)
        v_rank, v_sc = {}, {}
        for q, qv in zip(qs, qvecs):
            sims = sorted(((sum(a * b for a, b in zip(qv, vecs[cid])), cid) for cid in ids), reverse=True)
            v_rank[q["questionId"]] = [c for _, c in sims]
            v_sc[q["questionId"]] = {c: s for s, c in sims}
        return b_rank, b_sc, v_rank, v_sc

    def score(strategy, b_rank, b_sc, v_rank, v_sc):
        hit = 0; prec = 0.0; fails = []
        for q in qs:
            qid = q["questionId"]; refs = set(q["referenceChunks"])
            if strategy == "bm25":
                top = b_rank[qid][:args.topk]
            elif strategy == "vector":
                top = v_rank[qid][:args.topk]
            else:  # 加权融合
                b = minmax(b_sc[qid]); v = minmax(v_sc[qid])
                s = {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * v.get(c, 0.0)
                     for c in set(b) | set(v)}
                top = sorted(s, key=lambda c: -s[c])[:args.topk]
            h = [c for c in top if c in refs]
            if h: hit += 1
            else: fails.append(qid)
            prec += len(h) / args.topk
        return {"hit": hit, "n": len(qs), "rate": round(hit / len(qs), 4),
                "prec": round(prec / len(qs), 4), "fails": fails, "top": top}

    print("\n== 3) 结果对比 ==")
    base = rankings(None)
    exp = rankings(expanded)
    print(f"{'策略':26s} {'命中':>8s} {'命中率':>8s} {'精确率':>8s}  失败题")
    print("-" * 74)
    table = {}
    for label, pack in (("基线", base), ("扩展后", exp)):
        for strategy in ("bm25", "vector", "hybrid"):
            r = score(strategy, *pack)
            name = f"{label} / {strategy}"
            table[name] = r
            print(f"{name:26s} {r['hit']:>4d}/{r['n']:<3d} {r['rate']:>8.4f} {r['prec']:>8.4f}  {r['fails']}")

    best_name = max(table, key=lambda k: (table[k]["rate"], table[k]["prec"]))
    best = table[best_name]
    print(f"\n最优：{best_name}  命中 {best['hit']}/{best['n']}  精确率 {best['prec']}")
    print(f"查询扩展额外开销：{exp_time/len(qs):.1f}s/题、{len(qs)} 次 LLM 调用")

    if args.save_best:
        # 用最优配置生成结果文件（供 deepeval）
        use_exp = best_name.startswith("扩展后")
        pack = exp if use_exp else base
        b_rank, b_sc, v_rank, v_sc = pack
        per_q = []
        for q in qs:
            qid = q["questionId"]
            b = minmax(b_sc[qid]); v = minmax(v_sc[qid])
            s = {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * v.get(c, 0.0) for c in set(b) | set(v)}
            top_ids = sorted(s, key=lambda c: -s[c])[:args.topk]
            refs = set(q["referenceChunks"])
            hits = [c for c in top_ids if c in refs]
            per_q.append({
                "questionId": qid, "set": q.get("category", "main"),
                "question": q["question"],
                "query_used": expanded[qid] if use_exp else q["question"],
                "referenceChunks": sorted(refs),
                "topk": [{"chunkId": c, "score": round(s[c], 6),
                          "text": (by_id.get(c, {}).get("text") or "")[:2000]} for c in top_ids],
                "hit": bool(hits), "hits": hits,
            })
        out = os.path.join(RUNS, "hybrid-topk.json")
        json.dump({
            "schema": "career-graph-hybrid-retrieval/v1",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "retriever": {"type": "hybrid", "alpha_bm25": args.alpha, "topk": args.topk,
                          "queryExpansion": use_exp,
                          "components": ["bm25 (lib/graph.mjs)", f"dense (TEI {TEI_MODEL})"]},
            "metrics": {"main": {"hitRate": f"{best['hit']}/{best['n']}",
                                 "hitRateValue": best["rate"],
                                 "meanPrecisionAtK": best["prec"]}},
            "perQuestion": per_q,
        }, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"最优配置已存为 {os.path.relpath(out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
