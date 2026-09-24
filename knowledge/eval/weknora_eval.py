#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WeKnora 对照评测：同一套题、同一份语料、同一判据，测腾讯那套检索栈。

与旧版 `weknora_retrieval.py` 的区别（旧版的结果作废）：
  旧版拿 WeKnora 返回的 chunkId 与本地 chunkId 精确比对 —— 两者根本不同源，
  恒为 0 命中，那不是"效果差"而是**判据错**。本脚本改成**按文本对齐**：
  WeKnora 返回的 content 与我们导进去的那一段文本相同（或互为包含），
  就认为命中了同一段语料；对齐不上的记为"未对齐"并单独统计，不计入命中。

语料导入方式：`weknora_import_corpus.py`，把 494 段一段一文档导入。

用法：
  python knowledge/eval/weknora_eval.py --kb <kb-id> --questions knowledge/evaluations/questions-dev.json --tag wk-dev
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
RUNS = os.path.join(K, "eval", "runs")


def req(method, url, body=None, token=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except Exception:
            return e.code, {"raw": raw[:300]}
    except Exception as e:
        return None, {"err": f"{type(e).__name__}: {e}"}


def dig(obj, keys):
    if isinstance(obj, dict):
        for k in keys:
            if isinstance(obj.get(k), str) and obj[k]:
                return obj[k]
        for v in obj.values():
            got = dig(v, keys)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = dig(v, keys)
            if got:
                return got
    return None


def unwrap_list(r):
    d = r.get("data") if isinstance(r, dict) else None
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("items", "list", "results", "chunks", "records"):
            if isinstance(d.get(k), list):
                return d[k]
    for k in ("items", "list", "results"):
        if isinstance(r.get(k), list):
            return r[k]
    return []


def norm(s):
    return re.sub(r"\s+", "", s or "")


def build_index(chunks):
    """用于文本对齐的三张表：规范化全文、前缀、标题里的 chunkId。"""
    exact, by_len = {}, []
    for c in chunks:
        n = norm(c["text"])
        exact.setdefault(n, c["chunkId"])
        by_len.append((len(n), n, c["chunkId"]))
    by_len.sort(reverse=True)
    return exact, by_len


def align(content, exact, by_len, min_ratio=0.6):
    """把返回文本对齐到本地 chunkId。

    顺序：完全相等 → 本地段包含返回文本（取覆盖比最高的）→ 无。
    """
    n = norm(content)
    if not n:
        return None, 0.0
    if n in exact:
        return exact[n], 1.0
    best, best_ratio = None, 0.0
    for ln, cn, cid in by_len:
        if ln < len(n) * 0.5:
            break
        if n in cn:
            ratio = len(n) / ln
            if ratio > best_ratio:
                best, best_ratio = cid, ratio
        elif cn in n:
            ratio = ln / len(n)
            if ratio > best_ratio:
                best, best_ratio = cid, ratio
    if best_ratio >= min_ratio:
        return best, best_ratio
    return None, best_ratio


def align_by_title(it, known_ids):
    """按返回项自带的 knowledge_title 对齐：标题前缀就是导入时写的 chunkId。

    实测 WeKnora 会把我们导入的每个文档再切成更小的 sub-chunk（返回里带
    chunk_index/start_at/end_at），所以**文本**只能包含关系、对齐率常在 0.4–0.7；
    而 knowledge_title 始终是父文档标题，可以直接取回 chunkId。
    「命中」的定义：检索器取回了标注参考答案所在的那一篇（粒度差异在报告里说明）。
    """
    title = str(it.get("knowledge_title") or "")
    if " · " in title:
        cid = title.split(" · ", 1)[0].strip()
        if cid in known_ids:
            return cid, 1.0
    if title.strip() in known_ids:
        return title.strip(), 1.0
    return None, 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("WEKNORA_URL", "http://127.0.0.1:8080"))
    ap.add_argument("--kb", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--tag", required=True, help="产物前缀，如 wk-dev")
    ap.add_argument("--topk", type=int, default=3, help="判据用的 Top-K")
    ap.add_argument("--fetch-k", type=int, default=10, help="一次请求取回多少（用于同时算 R@10）")
    ap.add_argument("--email", default="agent.verify@local.test")
    ap.add_argument("--password", default="verify12345")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    exact, by_len = build_index(chunks)
    known_ids = {c["chunkId"] for c in chunks}

    s, r = req("POST", base + "/api/v1/auth/login",
               {"email": args.email, "password": args.password})
    token = dig(r, ("access_token", "token"))
    if not token:
        print(f"登录失败 HTTP {s}", file=sys.stderr)
        return 2

    qdoc = json.load(open(args.questions, encoding="utf-8"))
    qs = [q for q in qdoc["questions"] if q.get("answerable", True)]
    print(f"题集 {os.path.relpath(args.questions, ROOT)}：可回答 {len(qs)} 题")
    print(f"WeKnora 知识库 {args.kb}；每次取回 {args.fetch_k}，按 Top-{args.topk} 判命中\n")

    per_q, t0 = [], time.time()
    unaligned = 0
    for i, q in enumerate(qs, 1):
        s, r = req("POST", f"{base}/api/v1/knowledge-bases/{args.kb}/hybrid-search",
                   {"query_text": q["question"], "top_k": args.fetch_k}, token=token)
        items = unwrap_list(r)
        top = []
        for it in items[:args.fetch_k]:
            if not isinstance(it, dict):
                continue
            content = it.get("content") or it.get("text") or ""
            cid, ratio = align_by_title(it, known_ids)
            if cid is None:                     # 标题缺失时退回文本对齐
                cid, ratio = align(content, exact, by_len)
            if cid is None:
                unaligned += 1
            top.append({"chunkId": cid, "rawId": it.get("id") or it.get("chunk_id"),
                        "score": it.get("score", it.get("similarity")),
                        "alignRatio": round(ratio, 3),
                        "text": content[:600]})
        refs = set(q.get("referenceChunks") or [])
        # 去重：WeKnora 会把我们导入的每一段再切成多个 sub-chunk，
        # 同一个参考段可能在一份返回里出现多次；不去重会把 P@3/R@K 顶到不可能的值（实测 R@10 > 1）。
        def uniq_refs(items):
            out, seen = [], set()
            for c in items:
                cid = c["chunkId"]
                if cid in refs and cid not in seen:
                    seen.add(cid)
                    out.append(cid)
            return out
        top3 = top[:args.topk]
        hits = uniq_refs(top3)
        hits10 = uniq_refs(top[:10])
        hits_all = uniq_refs(top)
        per_q.append({"questionId": q["questionId"], "set": q.get("category", "main"),
                      "question": q["question"], "referenceChunks": sorted(refs),
                      "topk": top3, "topAll": top, "hit": bool(hits), "hits": hits,
                      "hitsAt10": hits10, "hitsAtFetch": hits_all,
                      "fetchK": args.fetch_k,
                      "returnedCount": len(top)})
        print(f"  [{i}/{len(qs)}] {q['questionId']} {'✅' if hits else '❌'} "
              f"对齐 {sum(1 for c in top if c['chunkId'])}/{len(top)}", flush=True)

    n = len(per_q)
    hit = sum(1 for x in per_q if x["hit"])
    refs_in = sum(len(x["hits"]) for x in per_q)
    refs_in10 = sum(len(x["hitsAt10"]) for x in per_q)
    refs_in_fetch = sum(len(x["hitsAtFetch"]) for x in per_q)
    tot_ref = sum(len(x["referenceChunks"]) for x in per_q)
    out = {
        "schema": "career-graph-weknora-eval/v2",
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "retriever": {"type": "weknora-hybrid-search", "service": base, "kb": args.kb,
                      "topk": args.topk, "fetchK": args.fetch_k,
                      "alignment": "按返回项的 knowledge_title 前缀对齐到本地 chunkId（非 id 精确匹配）"},
        "metrics": {"main": {
            "hitRate": f"{hit}/{n}", "hitRateValue": round(hit / n, 4),
            "precisionAtK": round(refs_in / (args.topk * n), 4),
            "recallAtK": round(refs_in / tot_ref, 4),
            "recallAt10": round(refs_in10 / tot_ref, 4),
            "recallAtFetch": round(refs_in_fetch / tot_ref, 4),
            "unalignedChunks": unaligned}},
        "timing": {"seconds": round(time.time() - t0, 1), "perQuestionMs": round((time.time() - t0) / n * 1000, 1)},
        "perQuestion": per_q,
    }
    path = os.path.join(RUNS, f"{args.tag}-weknora-topk.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    m = out["metrics"]["main"]
    print(f"\n=== WeKnora（{os.path.basename(args.questions)}）===")
    print(f"  命中率@{args.topk}  {m['hitRate']} ({m['hitRateValue']})")
    print(f"  准确率 P@{args.topk} {m['precisionAtK']}")
    print(f"  召回率 R@{args.topk} {m['recallAtK']}   R@10 {m['recallAt10']}   R@{args.fetch_k} {m['recallAtFetch']}")
    print(f"  未对齐的返回块 {unaligned} 个（不计入命中）")
    print(f"  耗时 {out['timing']['perQuestionMs']} ms/题")
    print(f"\n产物：{os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
