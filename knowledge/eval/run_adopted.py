#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
按「已采纳的配置」跑一遍检索评测，并按新的题集划分口径（split 字段）分组报数。

已采纳的配置（来自 检索命中率优化实验.md 的结论）：
  1) BM25 与稠密向量归一化后加权融合，α_bm25 = 0.35
  2) 融合 Top-10 交给 LLM 重排，只取 3 段（重排结果读缓存，不重复调模型）

为什么单独写一个脚本：
  之前存盘的 hybrid-topk.json 是 α=0.30（因为 hybrid_experiment.py 的网格只含 0.3/0.5/0.7），
  与已采纳的 0.35 不一致。本脚本把「采纳配置」固化成一条命令，产物与旧档位并存、互不覆盖。

划分口径：题集里带 split 字段（dev / test）时，按 split 分组报数；拒答题（answerable=false）
单独统计，且明确标注「拒答指标是否为空」。本脚本**不**做任何调参——α 是给定值，不是选出来的。

用法：
  python knowledge/eval/run_adopted.py
  python knowledge/eval/run_adopted.py --questions knowledge/evaluations/questions-test.json --alpha 0.35
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

RERANK_PROMPT = """下面是从职业知识库里检索出的候选片段，请选出最有助于回答该问题的 3 段。

要求：
- 只依据片段内容判断，不要用你自己的先验知识补全；
- 若多个片段来自同一章节且内容重复，只保留信息量最大的一段；
- 按相关度从高到低输出，每行一个片段 id，格式为 `id: <片段id>`，不要解释、不要输出片段正文。

问题：{question}

候选片段：
{candidates}
"""

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


ENV_FILE = os.path.join(K, "eval", ".env")


def load_env():
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def llm(prompt, timeout=180, max_tokens=3000, attempts=4):
    """带退避重试：端点对突发调用会直接拒（实测），必须重试。

    max_tokens 默认给 3000 而不是 300：端点现役的 deepseek-v4.1-flash 是推理模型，
    思维链也吃这个额度。实测给 1200 时 34 题里有 3 题返回空 content / 无 content 字段，
    被误判成"模型没给出可用编号"而重试到失败。
    """
    base = os.environ["DEEPEVAL_BASE_URL"].rstrip("/")
    body = json.dumps({"model": os.environ["DEEPEVAL_MODEL"], "temperature": 0,
                       "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                base + "/chat/completions", data=body,
                headers={"Authorization": "Bearer " + os.environ["DEEPEVAL_API_KEY"],
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            return (d["choices"][0]["message"]["content"] or "").strip()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except Exception as e:
            last = f"{type(e).__name__} {e}"
        time.sleep(min(2 ** i, 12))
    raise RuntimeError(last)


def parse_ids(text, allowed, k=3):
    found = []
    for m in re.finditer(r"([A-Za-z0-9][A-Za-z0-9#~._-]*)", text or ""):
        tok = m.group(1)
        if tok in allowed and tok not in found:
            found.append(tok)
    return found[:k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=QUESTIONS)
    ap.add_argument("--alpha", type=float, default=0.35, help="已采纳的 BM25 权重")
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--rerank-cache", default=RERANK_CACHE)
    ap.add_argument("--bm25", default=BM25_FULL, help="该题集对应的 BM25 完整排序产物")
    ap.add_argument("--tag", default="", help="产物文件名前缀（如 dev-/test-），用于区分题集")
    ap.add_argument("--do-rerank", action="store_true",
                    help="对缓存里缺的题真正跑一遍 LLM 重排（否则新题会退回融合顺序）")
    ap.add_argument("--rerank-candidates", type=int, default=10)
    ap.add_argument("--exclude", default="",
                    help="逗号分隔的 chunkId：检索期剔除（导航/列表段等）")
    ap.add_argument("--retries", type=int, default=4, help="单题重排失败的重试次数")
    ap.add_argument("--retry-wait", type=float, default=3.0, help="重试基础退避秒数（线性递增）")
    args = ap.parse_args()
    if args.do_rerank:
        load_env()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    ids = [c["chunkId"] for c in chunks]
    by_id = {c["chunkId"]: c for c in chunks}

    qdoc = json.load(open(args.questions, encoding="utf-8"))
    allq = qdoc.get("questions", qdoc) if isinstance(qdoc, dict) else qdoc
    answerable = [q for q in allq if q.get("answerable", True)]
    refused = [q for q in allq if not q.get("answerable", True)]
    print(f"题集：{os.path.relpath(args.questions, ROOT)}")
    print(f"  可回答 {len(answerable)} 题 / 拒答 {len(refused)} 题"
          f"（split 字段：{sorted({q.get('split', '(未标)') for q in allq})}）")

    bm = json.load(open(args.bm25, encoding="utf-8"))
    b_sc = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]}
            for x in bm["perQuestion"]}
    missing_bm = [q["questionId"] for q in answerable if q["questionId"] not in b_sc]
    if missing_bm:
        print(f"  ⚠ BM25 完整排序里缺 {len(missing_bm)} 题：{missing_bm[:5]}"
              f"\n     需先跑：node knowledge/pipeline/bm25-full.mjs --questions <题集>", file=sys.stderr)

    vecs = json.load(open(os.path.join(
        CACHE_DIR, f"chunk-vectors-{MODEL.replace('/', '_')}.json"), encoding="utf-8"))["vectors"]
    print(f"  计算查询向量（{len(answerable)} 条）…")
    qvecs = embed([q["question"] for q in answerable])

    rerank = {}
    if os.path.exists(args.rerank_cache):
        rerank = json.load(open(args.rerank_cache, encoding="utf-8"))["picks"]

    fused = {}
    excluded = {x.strip() for x in args.exclude.split(",") if x.strip()}
    if excluded:
        print(f"检索期剔除 {len(excluded)} 段：{sorted(excluded)}")
    for q, qv in zip(answerable, qvecs):
        qid = q["questionId"]
        if qid not in b_sc:
            continue
        vs = {cid: sum(a * b for a, b in zip(qv, vecs[cid])) for cid in ids}
        b, v = minmax(b_sc[qid]), minmax(vs)
        fused[qid] = {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * v.get(c, 0.0)
                      for c in set(b) | set(v) if c not in excluded}

    if args.do_rerank:
        todo = [q for q in answerable if q["questionId"] in fused
                and not (rerank.get(q["questionId"]) or {}).get("picked")]
        print(f"\n== LLM 重排（候选 {args.rerank_candidates}）：缓存里缺 {len(todo)} 题 ==")
        t0 = time.time()
        failed = []
        for i, q in enumerate(todo, 1):
            qid = q["questionId"]
            f = fused[qid]
            cand = sorted(f, key=lambda c: -f[c])[:args.rerank_candidates]
            blocks = []
            for cid in cand:
                c = by_id[cid]
                sec = c.get("sectionPath") or c.get("heading") or ""
                blocks.append(f"id: {cid}\n章节: {sec}\n正文: {c['text'][:280]}")
            out, picked, err = None, [], None
            # 端点会间歇 5xx。旧版在这里把失败写成 picked=[] 并落进缓存：
            # 既污染缓存，又让成绩静默退化成融合档、看起来只像"重排没增益"。
            for attempt in range(1, args.retries + 1):
                try:
                    out = llm(RERANK_PROMPT.format(question=q["question"],
                                                   candidates="\n".join(blocks)))
                    picked = parse_ids(out, set(cand), args.topk)
                    if picked:
                        err = None
                        break
                    # 报错必须带上原始返回：只写"没给出可用编号"时，明明是模型答对了、
                    # 只是解析没吃上，也看不出来（D18 就踩过这个）。
                    err = f"模型没给出可用编号（原始返回前 200 字：{(out or '')[:200]!r}）"
                except Exception as e:
                    err = f"{type(e).__name__} {e}"
                if attempt < args.retries:
                    time.sleep(args.retry_wait * attempt)
            if not picked:
                failed.append(qid)
                print(f"  {qid} 重排失败（{args.retries} 次重试后）：{err}")
            else:
                rerank[qid] = {"picked": picked, "candidates": cand}
            if i % 10 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} 用时 {time.time()-t0:.1f}s", flush=True)
        json.dump({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "model": os.environ.get("DEEPEVAL_MODEL"),
                   "candidates": args.rerank_candidates, "picks": rerank},
                  open(args.rerank_cache, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        ok = sum(1 for q in answerable if (rerank.get(q["questionId"]) or {}).get("picked"))
        print(f"  完成：{ok}/{len(answerable)} 题有重排结果 → "
              f"{os.path.relpath(args.rerank_cache, ROOT)}")
        if failed:
            # 不照报指标：那等于把重排档静默退化成融合档。缓存只存成功项，重跑即可续跑。
            print(f"\n✗ {len(failed)}/{len(answerable)} 题重排未成功：{failed}", file=sys.stderr)
            print("  已把成功部分写入缓存，**不输出指标**；直接重跑本命令可续跑。",
                  file=sys.stderr)
            return 2

    fusion_q, rerank_q = [], []
    for q in answerable:
        qid = q["questionId"]
        if qid not in fused:
            continue
        f = fused[qid]
        f_top = sorted(f, key=lambda c: -f[c])[:args.topk]
        picks = list((rerank.get(qid) or {}).get("picked") or [])
        for cid in sorted(f, key=lambda c: -f[c]):
            if len(picks) >= args.topk:
                break
            if cid not in picks:
                picks.append(cid)
        rerank_q.append((q, picks[:args.topk]))
        fusion_q.append((q, f_top))

    def stat(pairs):
        hit = top1 = 0
        refs = 0
        n = len(pairs)
        for q, top in pairs:
            r = set(q.get("referenceChunks") or [])
            h = [c for c in top if c in r]
            refs += len(h)
            if h:
                hit += 1
            if top and top[0] in r:
                top1 += 1
        return {"n": n, "hit": hit, "hitRate": round(hit / n, 4) if n else None,
                "hitAt1": round(top1 / n, 4) if n else None,
                "refsInSlots": refs, "slots": args.topk * n,
                "precisionAtK": round(refs / (args.topk * n), 4) if n else None}

    def grouped(pairs, label):
        print(f"\n=== {label} ===")
        groups = defaultdict(list)
        for q, top in pairs:
            groups[q.get("split", "(未标)")].append((q, top))
        rows = []
        for split in sorted(groups):
            s = stat(groups[split])
            print(f"  split={split:6s} n={s['n']:3d}  命中 {s['hit']}/{s['n']}"
                  f"  命中@1 {s['hitAt1']}  精确率@3 {s['precisionAtK']}"
                  f"  答案段 {s['refsInSlots']}/{s['slots']}")
            rows.append({"split": split, **s})
        s = stat(pairs)
        print(f"  {'合计':10s} n={s['n']:3d}  命中 {s['hit']}/{s['n']}"
              f"  命中@1 {s['hitAt1']}  精确率@3 {s['precisionAtK']}"
              f"  答案段 {s['refsInSlots']}/{s['slots']}")
        rows.append({"split": "ALL", **s})
        return rows

    # 候选数必须从缓存里读：写死 10 会在用 30 的缓存时谎报配置（真跑过一次才发现）。
    cache_cands = sorted({len(v.get("candidates") or []) for v in rerank.values() if v.get("picked")})
    print(f"\n配置：BM25+向量加权融合 α_bm25={args.alpha}；"
          f"重排候选 {cache_cands or '(缓存为空)'}（读缓存 {os.path.basename(args.rerank_cache)}）")
    fusion_rows = grouped(fusion_q, f"档位 FUSION(α={args.alpha})")
    rerank_rows = grouped(rerank_q, "档位 FUSION+RERANK（已采纳配置）")

    if refused:
        print(f"\n=== 拒答档位 ===")
        print(f"  本集有 {len(refused)} 道拒答题；拒答判定依赖「资料不足」阈值，"
              f"该阈值需在 dev 拒答集上确定后才可评估——本次未做，**拒答指标为空**")
    else:
        print(f"\n=== 拒答档位 ===")
        print("  本集**没有**拒答题 → 拒答指标为空，不可报告")

    # 落盘，产物名带题集前缀与 alpha，不覆盖旧档位
    asuffix = str(args.alpha).replace(".", "")
    out_f = os.path.join(RUNS, f"{args.tag}fusion-a{asuffix}-topk.json")
    out_r = os.path.join(RUNS, f"{args.tag}adopted-a{asuffix}-topk.json")

    def dump(path, rows, pairs, retriever):
        per_q = []
        for q, top in pairs:
            r = set(q.get("referenceChunks") or [])
            h = [c for c in top if c in r]
            per_q.append({
                "questionId": q["questionId"], "split": q.get("split"),
                "set": q.get("category", "main"), "question": q["question"],
                "referenceChunks": sorted(r),
                "topk": [{"chunkId": c, "score": None,
                          "text": (by_id.get(c, {}).get("text") or "")[:2000]} for c in top],
                "hit": bool(h), "hits": h,
            })
        json.dump({"schema": "career-graph-adopted-retrieval/v1",
                   "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "questions": os.path.relpath(args.questions, ROOT),
                   "retriever": retriever,
                   "metrics": rows,
                   "refusedQuestions": len(refused),
                   "refusalMetric": "empty（本集无拒答题或阈值未定）",
                   "perQuestion": per_q},
                  open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  产物：{os.path.relpath(path, ROOT)}")

    print()
    dump(out_f, fusion_rows, fusion_q,
         {"type": "fusion", "alpha_bm25": args.alpha, "topk": args.topk,
          "components": ["bm25 (lib/graph.mjs)", "dense (TEI Qwen3-Embedding-0.6B)"]})
    dump(out_r, rerank_rows, rerank_q,
         {"type": "fusion+llm-rerank", "alpha_bm25": args.alpha, "topk": args.topk,
          "rerankCandidates": 10, "rerankCache": os.path.relpath(args.rerank_cache, ROOT),
          "components": ["bm25 (lib/graph.mjs)", "dense (TEI Qwen3-Embedding-0.6B)",
                         "LLM listwise rerank (claude-sonnet-4.6)"]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
