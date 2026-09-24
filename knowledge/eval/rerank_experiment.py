#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
二阶段 LLM 重排实验：把融合 Top-N 交给裁判模型重排，只留 3 段。

为什么最后才试它：它是针对「召回了但排不进前三」的标准解法（Cohere Rerank / bge-reranker
一类的做法，只是这里用通用 LLM 代替专用重排模型）。代价明确：每题多一次 LLM 调用、
检索非确定、延迟上升。所以只要确定性手段还有余地就不该先上它——
实测确定性手段（α 精扫、邻域加权、图证据扩展、章节标题通道）都没能解决 N12，
才轮到它。

N12 为什么是它的典型场景：
  S16 整页都在讲「软件质量保证分析师与测试员」，参考答案在 (Tasks) / (Work Activities)
  两节，但 (Transferable Skills)、(Professional Associations) 等节与问题的向量相似度更高。
  这是「文档整体相关、只有其中几节是答案」的判别问题，不是字面匹配问题。

判据与前面所有实验一致：Top-3 是否含标注 chunk。
额外如实报告：LLM 调用次数、每题耗时、命中是否只靠单题翻转。

用法：
  python knowledge/eval/rerank_experiment.py
  python knowledge/eval/rerank_experiment.py --candidates 20 --save-best
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
QUESTIONS = os.path.join(K, "evaluations", "questions-v2.json")
BM25_FULL = os.path.join(K, "eval", "runs", "bm25-full.json")
CACHE_DIR = os.path.join(K, "eval", "cache")
RUNS = os.path.join(K, "eval", "runs")
ENV_FILE = os.path.join(K, "eval", ".env")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")

RERANK_PROMPT = """下面是从职业知识库里检索出的候选片段，请选出最有助于回答该问题的 3 段。

要求：
- 只依据片段内容判断，不要用你自己的先验知识补全；
- 若多个片段来自同一章节且内容重复，只保留信息量最大的一段；
- 按相关度从高到低输出，每行一个片段 id，格式为 `id: <片段id>`，不要解释、不要输出片段正文。

问题：{question}

候选片段：
{candidates}
"""


def load_env():
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def llm(prompt, timeout=180):
    base = os.environ["DEEPEVAL_BASE_URL"].rstrip("/")
    body = json.dumps({"model": os.environ["DEEPEVAL_MODEL"], "temperature": 0,
                       "max_tokens": 300,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + os.environ["DEEPEVAL_API_KEY"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return (d["choices"][0]["message"]["content"] or "").strip()


def embed(texts, timeout=600, batch=8):
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


def parse_ids(text, allowed, k=3):
    """从模型输出里抽出候选 id；只在候选集合内取，避免模型编造 id 被算进来。"""
    found = []
    for m in re.finditer(r"([A-Za-z0-9][A-Za-z0-9#~._-]*)", text):
        tok = m.group(1)
        if tok in allowed and tok not in found:
            found.append(tok)
    return found[:k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", type=float, default=0.35)
    ap.add_argument("--candidates", type=int, default=10, help="送进重排的候选数")
    ap.add_argument("--text-chars", type=int, default=280, help="每候选给模型看多少字符")
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--questions", default=QUESTIONS, help="题集文件（默认 questions-v2.json）")
    ap.add_argument("--bm25", default=BM25_FULL, help="BM25 完整排序产物（换了题集必须同步换）")
    ap.add_argument("--cache-tag", default="",
                    help="重排缓存前缀标签，如 dev2；避免不同题集共用同一份缓存")
    ap.add_argument("--exclude", default="",
                    help="逗号分隔的 chunkId：检索期剔除（导航/列表段等，见 boilerplate_scan.py）")
    ap.add_argument("--retries", type=int, default=4, help="单题重排失败的重试次数")
    ap.add_argument("--retry-wait", type=float, default=3.0, help="重试基础退避秒数（线性递增）")
    ap.add_argument("--save-best", action="store_true")
    args = ap.parse_args()
    load_env()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    ids = [c["chunkId"] for c in chunks]
    by_id = {c["chunkId"]: c for c in chunks}
    qs = [q for q in json.load(open(args.questions, encoding="utf-8"))["questions"]
          if q.get("answerable", True)]
    refs_of = {q["questionId"]: set(q["referenceChunks"]) for q in qs}

    body_vec = json.load(open(os.path.join(
        CACHE_DIR, f"chunk-vectors-{MODEL.replace('/', '_')}.json"), encoding="utf-8"))["vectors"]
    bm = json.load(open(args.bm25, encoding="utf-8"))
    b_sc = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]}
            for x in bm["perQuestion"]}

    qvecs = embed([q["question"] for q in qs])
    excluded = {x.strip() for x in args.exclude.split(",") if x.strip()}
    if excluded:
        print(f"检索期剔除 {len(excluded)} 段：{sorted(excluded)}")
    fused = {}
    for q, qv in zip(qs, qvecs):
        vs = {cid: sum(a * b for a, b in zip(qv, body_vec[cid])) for cid in ids}
        b = minmax(b_sc[q["questionId"]])
        v = minmax(vs)
        fused[q["questionId"]] = {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * v.get(c, 0.0)
                                  for c in set(b) | set(v) if c not in excluded}

    def block(cid):
        c = by_id[cid]
        sec = c.get("sectionPath") or c.get("heading") or ""
        return f"id: {cid}\n章节: {sec}\n正文: {c['text'][:args.text_chars]}\n"

    cache_path = os.path.join(RUNS, f"rerank-cache{args.cache_tag}-{args.candidates}.json")
    cache = {}
    if os.path.exists(cache_path):
        cache = json.load(open(cache_path, encoding="utf-8"))["picks"]
        print(f"复用重排缓存：{os.path.relpath(cache_path, ROOT)}")

    def base_eval():
        hit = top1 = 0
        prec = 0.0
        fails, detail = [], {}
        for q in qs:
            qid = q["questionId"]
            f = fused[qid]
            top = sorted(f, key=lambda c: -f[c])[:args.topk]
            hits = [c for c in top if c in refs_of[qid]]
            detail[qid] = {"top": top, "hits": hits}
            if hits:
                hit += 1
            else:
                fails.append(qid)
            if top[0] in refs_of[qid]:
                top1 += 1
            prec += len(hits) / args.topk
        n = len(qs)
        return {"tag": f"基线 融合α={args.alpha}", "hit": hit, "n": n,
                "hitAt1": round(top1 / n, 4), "prec": round(prec / n, 4),
                "fails": fails, "detail": detail}

    base = base_eval()
    print(f"\n基线：命中 {base['hit']}/{base['n']}  命中@1 {base['hitAt1']}"
          f"  精确率@3 {base['prec']}  未命中 {base['fails']}")

    calls, cost, failed = 0, 0.0, []
    for q in qs:
        qid = q["questionId"]
        if qid in cache and cache[qid].get("picked"):
            continue
        f = fused[qid]
        cand = sorted(f, key=lambda c: -f[c])[:args.candidates]
        prompt = RERANK_PROMPT.format(question=q["question"],
                                      candidates="\n".join(block(c) for c in cand))
        t0 = time.time()
        out, picked, err = None, [], None
        # 端点会间歇 503。旧版在这里直接把失败写成 picked=[] 落进缓存：
        # 既污染缓存（重跑复用失败），又让成绩静默退化成基线、看起来只像是"没增益"。
        for attempt in range(1, args.retries + 1):
            try:
                out = llm(prompt)
                picked = parse_ids(out, set(cand), args.topk)
                if picked:
                    err = None
                    break
                err = "模型没给出可用编号"
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:160]
                except Exception:
                    pass
                err = f"HTTP {e.code} {e.reason} {body}"
            except Exception as e:
                err = f"{type(e).__name__} {e}"
            if attempt < args.retries:
                time.sleep(args.retry_wait * attempt)
        cost += time.time() - t0
        calls += 1
        if not picked:
            failed.append(qid)
            print(f"   {qid} 重排失败（{args.retries} 次重试后）：{err}")
            continue
        cache[qid] = {"candidates": cand, "raw": out[:1200], "picked": picked}
        print(f"   {qid} 重排 {time.time()-t0:5.1f}s  选中 {picked}")

    # 有题没重排成功就**中止**，不要照报指标：那等于把重排档静默退化成基线，
    # 而报告里看起来只像"没有增益"。缓存只存成功项，失败项下次会重试。
    if failed:
        json.dump({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "model": os.environ.get("DEEPEVAL_MODEL"),
                   "candidatesPerQuestion": args.candidates,
                   "perQuestionSec": round(cost / max(calls, 1), 2),
                   "picks": cache},
                  open(cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n✗ {len(failed)}/{len(qs)} 题重排未成功：{failed}")
        print(f"  已把成功部分写入 {os.path.relpath(cache_path, ROOT)}，**不输出指标**。")
        print(f"  直接重跑本命令即可续跑（失败项不在缓存里，会被重试）。")
        return 2

    json.dump({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "model": os.environ.get("DEEPEVAL_MODEL"),
               "candidatesPerQuestion": args.candidates,
               "perQuestionSec": round(cost / max(calls, 1), 2),
               "picks": cache},
              open(cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 重排档：模型选出的 3 段；不足 3 段时用原融合排序补齐（如实计入）
    hit = top1 = 0
    prec = 0.0
    fails, detail = [], {}
    for q in qs:
        qid = q["questionId"]
        f = fused[qid]
        rec = cache.get(qid, {})
        picked = list(rec.get("picked") or [])
        for cid in sorted(f, key=lambda c: -f[c]):
            if len(picked) >= args.topk:
                break
            if cid not in picked:
                picked.append(cid)
        top = picked[:args.topk]
        hits = [c for c in top if c in refs_of[qid]]
        detail[qid] = {"top": top, "hits": hits}
        if hits:
            hit += 1
        else:
            fails.append(qid)
        if top and top[0] in refs_of[qid]:
            top1 += 1
        prec += len(hits) / args.topk
    n = len(qs)
    rr = {"tag": f"LLM 重排（候选 {args.candidates}）", "hit": hit, "n": n,
          "hitAt1": round(top1 / n, 4), "prec": round(prec / n, 4),
          "fails": fails, "detail": detail}

    print(f"\n=== 结果对照（Top-{args.topk}，判据：是否含标注 chunk）===")
    for r in (base, rr):
        print(f"  {r['tag']:26s} 命中 {r['hit']:>2d}/{r['n']:<2d}  命中@1 {r['hitAt1']:.4f}"
              f"  精确率@3 {r['prec']:.4f}  未命中 {r['fails'] or '-'}")
    print(f"\n开销：{calls} 次 LLM 调用，{cost/max(calls,1):.1f}s/题；"
          f"temperature=0 但跨次运行仍有波动，本脚本只报单次结果")

    gained = sorted({q for q in refs_of
                     if rr["detail"][q]["hits"] and not base["detail"][q]["hits"]})
    lost = sorted({q for q in refs_of
                   if base["detail"][q]["hits"] and not rr["detail"][q]["hits"]})
    print(f"重排新增命中：{gained or '无'}    重排丢失命中：{lost or '无'}")
    for qid in sorted(set(gained) | set(lost)):
        print(f"  {qid}: 基线 {base['detail'][qid]['top']} → 重排 {rr['detail'][qid]['top']}")

    ok_picks = sum(1 for q in qs if cache.get(q["questionId"], {}).get("picked"))
    better = (rr["hit"], rr["prec"]) > (base["hit"], base["prec"])
    if args.save_best and ok_picks == len(qs) and better:
        per_q = []
        for q in qs:
            qid = q["questionId"]
            d = rr["detail"][qid]
            per_q.append({
                "questionId": qid, "set": q.get("category", "main"),
                "question": q["question"],
                "referenceChunks": sorted(refs_of[qid]),
                "topk": [{"chunkId": c, "score": None,
                          "text": (by_id.get(c, {}).get("text") or "")[:2000]}
                         for c in d["top"]],
                "hit": bool(d["hits"]), "hits": d["hits"],
            })
        out = {"schema": "career-graph-reranked-retrieval/v1",
               "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "retriever": {"type": "hybrid+llm-rerank", "strategy": rr["tag"],
                             "alpha_bm25": args.alpha, "candidates": args.candidates,
                             "topk": args.topk},
               "metrics": {"main": {"hitRate": f"{rr['hit']}/{rr['n']}",
                                    "hitRateValue": round(rr["hit"] / rr["n"], 4),
                                    "hitAt1": rr["hitAt1"],
                                    "meanPrecisionAtK": rr["prec"]}},
               "ablation": [{"tag": base["tag"], "hit": f"{base['hit']}/{base['n']}",
                             "hitAt1": base["hitAt1"], "precisionAtK": base["prec"],
                             "fails": base["fails"]},
                            {"tag": rr["tag"], "hit": f"{rr['hit']}/{rr['n']}",
                             "hitAt1": rr["hitAt1"], "precisionAtK": rr["prec"],
                             "fails": rr["fails"]}],
               "cost": {"llmCalls": calls, "secPerQuestion": round(cost / max(calls, 1), 2),
                        "candidatesPerQuestion": args.candidates},
               "perQuestion": per_q}
        path = os.path.join(RUNS, "reranked-topk.json")
        json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n已存为 {os.path.relpath(path, ROOT)}")
    elif args.save_best:
        print(f"\n不保存产物：成功重排 {ok_picks}/{len(qs)} 题、"
              f"且（命中, 精确率）=({rr['hit']}, {rr['prec']}) 未优于基线"
              f"({base['hit']}, {base['prec']})——避免把回退结果写成「最优档」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
