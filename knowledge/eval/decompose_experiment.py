#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
子查询分解检索实验：多方位问题被「一份文档的多个近义块」挤掉答案时该怎么救。

诊断（diagnose_retrieval.py / locality_variants.py）：
  N12「软件质量保证分析师与测试员这个岗位，主要任务和工作活动包括哪些内容？」
  参考答案 S16#s291~2 / S16#s292 在融合排序里排第 9 / 22，而排在前面的
  S16#s283、S16#s300、S16#s310、S16#s297 全部来自同一份 O*NET 文档的其它章节。
  也就是说：文档整体与问题高度相关，但只有 (Tasks) 这一节是答案；
  单一查询向量把「整篇都在讲这个职业」的块都打成了高分，3 个槽位被近义块占满。

  邻域信号（前邻 S16#s291 排第 5）只能把它从第 9 推到第 4，推不进前 3，
  因为前面挡着的是语义近邻而不是同一切片——打分微调解决不了。

本脚本试「方面分解」（RAG-Fusion 的标准做法）：
  1) LLM 把问题拆成 2–3 个覆盖不同方面的子查询（中文原词 + 英文对应词）
  2) 每个子查询各自跑 BM25 + 向量并融合（同一套 α，不另立口径）
  3) 合并策略对照：轮转取各子查询 Top-1 / RRF 融合 / 直接拼接后重排
并同时给出「基线」，注明额外开销（LLM 次数、耗时、非确定性）。

分解结果缓存到 runs/decomposed-queries.json，重跑不再调模型。

用法：
  python knowledge/eval/decompose_experiment.py
  python knowledge/eval/decompose_experiment.py --merge rr --save-best
"""
import argparse
import json
import os
import subprocess
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
ENV_FILE = os.path.join(K, "eval", ".env")
DECOMP_CACHE = os.path.join(RUNS, "decomposed-queries.json")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")

DECOMP_PROMPT = """你是检索查询规划器。把下面的问题拆成 2 到 3 个子查询，每个子查询只覆盖问题的一个方面，
彼此不要重复。因为语料里既有中文资料也有英文资料，每个子查询都要写成「中文关键词 + 英文对应词」的一行。
只输出子查询，每行一个，不要编号、不要解释、不要引号。

问题：{q}
子查询："""


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
                       "max_tokens": 300,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + os.environ["DEEPEVAL_API_KEY"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    text = (d["choices"][0]["message"]["content"] or "")
    subs = [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip()]
    return [s for s in subs if len(s) > 3][:3]


def embed(texts, timeout=600, batch=16):
    """分批送：TEI 侧 max_client_batch_size=32，42 条子查询一次送会 413 Payload Too Large。"""
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--alpha", type=float, default=0.35)
    ap.add_argument("--merge", default=None, help="只跑某一种合并策略：rr / rrf / concat")
    ap.add_argument("--save-best", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="忽略分解缓存，重新调模型")
    args = ap.parse_args()
    load_env()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    ids = [c["chunkId"] for c in chunks]
    by_id = {c["chunkId"]: c for c in chunks}
    qs = [q for q in json.load(open(QUESTIONS, encoding="utf-8"))["questions"]
          if q.get("answerable", True)]
    refs_of = {q["questionId"]: set(q["referenceChunks"]) for q in qs}

    vecs = json.load(open(os.path.join(
        CACHE_DIR, f"chunk-vectors-{MODEL.replace('/', '_')}.json"), encoding="utf-8"))["vectors"]

    # ---- 1) 分解（带缓存）----
    decomps, llm_time = {}, 0.0
    if os.path.exists(DECOMP_CACHE) and not args.refresh:
        decomps = json.load(open(DECOMP_CACHE, encoding="utf-8"))["subqueries"]
        print(f"复用分解缓存（{len(decomps)} 题）：{os.path.relpath(DECOMP_CACHE, ROOT)}")
    else:
        print("== 1) LLM 分解（本题集一次性） ==")
        t0 = time.time()
        for q in qs:
            try:
                decomps[q["questionId"]] = llm(DECOMP_PROMPT.format(q=q["question"]))
            except Exception as e:
                print(f"   {q['questionId']} 失败 {type(e).__name__}，回退整句")
                decomps[q["questionId"]] = [q["question"]]
        llm_time = time.time() - t0
        json.dump({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "model": os.environ.get("DEEPEVAL_MODEL"),
                   "perQuestionSec": round(llm_time / len(qs), 2),
                   "subqueries": decomps},
                  open(DECOMP_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"   {len(decomps)} 题，用时 {llm_time:.1f}s（{llm_time/len(qs):.1f}s/题）")
    for q in qs[:3]:
        print(f"   {q['questionId']}: {decomps[q['questionId']]}")
    n_sub = sum(len(v) for v in decomps.values())
    print(f"   子查询总数 {n_sub}（平均 {n_sub/len(qs):.1f} 条/题）")

    # ---- 2) 子查询上的 BM25（复用 lib/graph.mjs）----
    flat = []
    for q in qs:
        for i, s in enumerate(decomps[q["questionId"]], 1):
            flat.append({"questionId": f"{q['questionId']}#{i}", "question": s, "answerable": True})
    tmp_q = os.path.join(RUNS, "_decomposed_questions.json")
    json.dump({"questions": flat}, open(tmp_q, "w", encoding="utf-8"), ensure_ascii=False)
    sub_bm25 = os.path.join(RUNS, "bm25-full-decomposed.json")
    print("\n== 2) 子查询上跑 BM25 ==")
    rc = subprocess.call(["node", "knowledge/pipeline/bm25-full.mjs",
                          "--questions", tmp_q, "--out", sub_bm25], cwd=ROOT)
    if rc != 0:
        print("node 退出码非 0，中止", file=sys.stderr)
        return 1

    # ---- 3) 子查询上的向量 ----
    print("\n== 3) 子查询上跑向量 ==")
    t0 = time.time()
    sub_v = {}
    qvecs = embed([s["question"] for s in flat])
    for item, qv in zip(flat, qvecs):
        sub_v[item["questionId"]] = {cid: sum(a * b for a, b in zip(qv, vecs[cid])) for cid in ids}
    print(f"   {len(flat)} 条子查询，用时 {time.time()-t0:.1f}s")

    bm = json.load(open(sub_bm25, encoding="utf-8"))
    sub_b = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]}
             for x in bm["perQuestion"]}

    def fused(qid_sub):
        b = minmax(sub_b.get(qid_sub, {}))
        v = minmax(sub_v.get(qid_sub, {}))
        return {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * v.get(c, 0.0)
                for c in set(b) | set(v)}

    # 基线（整句）
    base_bm = json.load(open(BM25_FULL, encoding="utf-8"))
    base_b = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]}
              for x in base_bm["perQuestion"]}
    base_v = {}
    for q, qv in zip(qs, embed([q["question"] for q in qs])):
        base_v[q["questionId"]] = {cid: sum(a * b for a, b in zip(qv, vecs[cid])) for cid in ids}

    def base_fused(qid):
        b = minmax(base_b[qid]); v = minmax(base_v[qid])
        return {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * v.get(c, 0.0)
                for c in set(b) | set(v)}

    # ---- 4) 合并策略 ----
    def merge_rr(qid, k):
        """轮转：每个子查询的 Top-1 轮流进槽，保证方面覆盖。"""
        lists = []
        for i in range(1, len(decomps[qid]) + 1):
            f = fused(f"{qid}#{i}")
            lists.append(sorted(f, key=lambda c: -f[c])[:k])
        out, seen = [], set()
        for depth in range(k):
            for lst in lists:
                if depth < len(lst) and lst[depth] not in seen:
                    out.append(lst[depth]); seen.add(lst[depth])
                if len(out) >= args.topk:
                    return out
        return out

    def merge_rrf(qid, k=60):
        s = {}
        for i in range(1, len(decomps[qid]) + 1):
            f = fused(f"{qid}#{i}")
            for rank, cid in enumerate(sorted(f, key=lambda c: -f[c]), 1):
                s[cid] = s.get(cid, 0.0) + 1.0 / (k + rank)
        return sorted(s, key=lambda c: -s[c])[:args.topk]

    def merge_concat(qid):
        """直接拼接各子查询的完整排序名次后重排（等价于对各子查询做等权 RRF 的近似）。"""
        s = {}
        for i in range(1, len(decomps[qid]) + 1):
            f = fused(f"{qid}#{i}")
            for cid, sc in f.items():
                s[cid] = max(s.get(cid, 0.0), sc)
        return sorted(s, key=lambda c: -s[c])[:args.topk]

    strategies = {"rr": merge_rr, "rrf": lambda q: merge_rrf(q), "concat": lambda q: merge_concat(q)}

    def evaluate(name, picker, use_base=False):
        hit = top1 = 0
        prec = 0.0
        fails, detail = [], {}
        for q in qs:
            qid = q["questionId"]
            refs = refs_of[qid]
            if use_base:
                f = base_fused(qid)
                top = sorted(f, key=lambda c: -f[c])[:args.topk]
            else:
                top = picker(qid, args.topk) if picker is merge_rr else picker(qid)
            hits = [c for c in top if c in refs]
            detail[qid] = {"top": top, "hits": hits}
            if hits:
                hit += 1
            else:
                fails.append(qid)
            if top and top[0] in refs:
                top1 += 1
            prec += len(hits) / args.topk
        return {"tag": name, "hit": hit, "n": len(qs), "hitRate": round(hit / len(qs), 4),
                "hitAt1": round(top1 / len(qs), 4), "prec": round(prec / len(qs), 4),
                "fails": fails, "detail": detail}

    print("\n== 4) 合并策略对照（同一套题、同一语料、同一 α）==")
    results = [evaluate(f"基线（整句查询）", None, use_base=True)]
    for key, fn in strategies.items():
        if args.merge and key != args.merge:
            continue
        results.append(evaluate(f"分解 + {key}", fn))
    for r in results:
        print(f"  {r['tag']:24s} 命中 {r['hit']:>2d}/{r['n']:<2d}  命中@1 {r['hitAt1']:.4f}"
              f"  精确率@3 {r['prec']:.4f}  未命中 {r['fails'] or '-'}")

    best = max(results, key=lambda r: (r["hitRate"], r["prec"], r["hitAt1"]))
    print(f"\n最优：{best['tag']}  命中 {best['hit']}/{best['n']}"
          f"  精确率@3 {best['prec']}")
    if best["tag"].startswith("分解"):
        base = results[0]
        gained = sorted({q for q in refs_of
                         if best["detail"][q]["hits"] and not base["detail"][q]["hits"]})
        lost = sorted({q for q in refs_of
                       if base["detail"][q]["hits"] and not best["detail"][q]["hits"]})
        print(f"  分解后新增命中：{gained or '无'}    丢失命中：{lost or '无'}")
    print(f"\n额外开销：{n_sub} 条子查询（4 路检索变 {2+2*n_sub/len(qs):.1f} 路），"
          f"LLM 分解 {llm_time/len(qs):.1f}s/题（有缓存则 0）")

    if args.save_best:
        per_q = []
        for q in qs:
            qid = q["questionId"]
            d = best["detail"][qid]
            per_q.append({
                "questionId": qid, "set": q.get("category", "main"), "question": q["question"],
                "subqueries": decomps[qid],
                "referenceChunks": sorted(refs_of[qid]),
                "topk": [{"chunkId": c, "score": None,
                          "text": (by_id.get(c, {}).get("text") or "")[:2000]}
                         for c in d["top"]],
                "hit": bool(d["hits"]), "hits": d["hits"],
            })
        out = {"schema": "career-graph-decomposed-retrieval/v1",
               "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "retriever": {"type": "hybrid+decomposition", "strategy": best["tag"],
                             "alpha_bm25": args.alpha, "topk": args.topk,
                             "components": ["bm25 (lib/graph.mjs)",
                                            "dense (TEI Qwen3-Embedding-0.6B)",
                                            "LLM 方面分解"]},
               "metrics": {"main": {"hitRate": f"{best['hit']}/{best['n']}",
                                    "hitRateValue": best["hitRate"],
                                    "hitAt1": best["hitAt1"],
                                    "meanPrecisionAtK": best["prec"]}},
               "ablation": [{"tag": r["tag"], "hit": f"{r['hit']}/{r['n']}",
                             "hitAt1": r["hitAt1"], "precisionAtK": r["prec"],
                             "fails": r["fails"]} for r in results],
               "perQuestion": per_q}
        path = os.path.join(RUNS, "decomposed-topk.json")
        json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"已存为 {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
