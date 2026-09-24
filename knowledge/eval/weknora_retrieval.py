#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从 WeKnora 的真实检索接口取上下文，产出与本地检索同构的结果文件。

为什么需要它：`deepeval_rag_eval.py` 的检索档位目前来自本地对
`knowledge/chunks/chunks.jsonl` 的 BM25 / 向量检索。一旦语料改为导入 WeKnora
（中国官方语料审核通过后就是这种情况），检索上下文就必须从 WeKnora 取——
否则评的是本地索引，不是线上系统。

产出格式与 `knowledge/eval/runs/vector-topk.json` 一致，因此可以直接作为
deepeval 的一个档位参与对比。

附带一个语料导出模式（--export-corpus）：把 KB 里的 chunk 列出来，供人工撰写
问题与标注参考答案使用——**没有它就无法为新语料出题**。

用法：
  # 取检索结果
  python knowledge/eval/weknora_retrieval.py --kb <kb-id> \
      --questions knowledge/evaluations/heldout-questions.json \
      --out knowledge/eval/runs/weknora-top3.json

  # 导出语料（供出题）
  python knowledge/eval/weknora_retrieval.py --kb <kb-id> --export-corpus corpus.jsonl

鉴权：默认用账号密码登录换 JWT（与 weknora_lite_check.py 相同），也支持 --api-key。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_QUESTIONS = os.path.join(ROOT, "knowledge", "evaluations", "questions.json")


def req(method, url, body=None, token=None, api_key=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    if token:
        r.add_header("Authorization", "Bearer " + token)
    if api_key:
        r.add_header("X-API-Key", api_key)
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


def login(base, email, password):
    s, r = req("POST", base + "/api/v1/auth/login", {"email": email, "password": password})
    tok = dig(r, ("access_token", "token"))
    if not tok:
        print(f"登录失败 HTTP {s}: {str(r)[:200]}", file=sys.stderr)
        sys.exit(1)
    return tok


def list_kbs(base, token):
    s, r = req("GET", base + "/api/v1/knowledge-bases", token=token)
    out = []
    for kb in unwrap_list(r):
        if isinstance(kb, dict):
            out.append({"id": kb.get("id"), "name": kb.get("name"),
                        "embedding_model_id": kb.get("embedding_model_id")})
    return out


def list_knowledge(base, token, kb, page_size=100):
    """列出知识库下的知识条目。"""
    items, page = [], 1
    while True:
        s, r = req("GET", f"{base}/api/v1/knowledge-bases/{kb}/knowledge"
                          f"?page={page}&page_size={page_size}", token=token)
        batch = unwrap_list(r)
        items += [x for x in batch if isinstance(x, dict)]
        if len(batch) < page_size:
            break
        page += 1
        if page > 500:
            break
    return items


def list_chunks(base, token, kb):
    """拉取某个知识库下的全部 chunk（用于导出语料，供人工出题）。

    注意端点是 GET /chunks/:knowledge_id —— 按**知识**取，不是按知识库取。
    先前误用 /knowledge-bases/:id/chunks 会静默返回 0 条（不报错但拿不到数据）。
    """
    out = []
    for k in list_knowledge(base, token, kb):
        kid = k.get("id")
        if not kid:
            continue
        page = 1
        while True:
            s, r = req("GET", f"{base}/api/v1/chunks/{kid}?page={page}&page_size=200",
                       token=token)
            batch = unwrap_list(r)
            out += [c for c in batch if isinstance(c, dict)]
            if len(batch) < 200:
                break
            page += 1
            if page > 500:
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("WEKNORA_URL", "http://127.0.0.1:8080"))
    ap.add_argument("--kb", required=True, help="知识库 ID")
    ap.add_argument("--questions", default=DEFAULT_QUESTIONS)
    ap.add_argument("--out", default=os.path.join(ROOT, "knowledge", "eval", "runs", "weknora-topk.json"))
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--email", default="agent.verify@local.test")
    ap.add_argument("--password", default="verify12345")
    ap.add_argument("--api-key")
    ap.add_argument("--export-corpus", help="只导出语料到该 jsonl 文件，不做检索")
    ap.add_argument("--list-kb", action="store_true", help="列出知识库后退出")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    token = args.api_key and None or login(base, args.email, args.password)

    if args.list_kb:
        print("可用知识库：")
        for kb in list_kbs(base, token):
            print(f"  {kb['id']}  {kb['name']}  model={kb['embedding_model_id'] or '(空)'}")
        return 0

    if args.export_corpus:
        chunks = list_chunks(base, token, args.kb)
        with open(args.export_corpus, "w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        print(f"导出 {len(chunks)} 个 chunk 到 {os.path.relpath(args.export_corpus, ROOT)}")
        if chunks:
            print("字段示例:", list(chunks[0].keys())[:12])
        return 0

    with open(args.questions, encoding="utf-8") as f:
        doc = json.load(f)
    qs = doc.get("questions", doc) if isinstance(doc, dict) else doc
    main_qs = [q for q in qs if q.get("answerable", True)]
    print(f"题集 {args.questions}：{len(qs)} 题（可回答 {len(main_qs)}）")

    per_question = []
    t0 = time.time()
    for i, q in enumerate(main_qs, 1):
        # 实测：hybrid-search 的查询字段是 query_text（传 query 会 400 query_text is required）
        s, r = req("POST", f"{base}/api/v1/knowledge-bases/{args.kb}/hybrid-search",
                   {"query_text": q["question"], "top_k": args.top_k}, token=token)
        items = unwrap_list(r)
        if s != 200:
            s, r = req("POST", base + "/api/v1/knowledge-search",
                       {"query": q["question"], "knowledge_base_id": args.kb}, token=token)
            items = unwrap_list(r)
        top = []
        for it in items[:args.top_k]:
            if not isinstance(it, dict):
                continue
            top.append({
                "chunkId": it.get("id") or it.get("chunk_id"),
                "score": it.get("score", it.get("similarity")),
                "text": (it.get("content") or "")[:2000],
                "knowledge_id": it.get("knowledge_id"),
            })
        refs = set(q.get("referenceChunks") or [])
        hits = [c["chunkId"] for c in top if c["chunkId"] in refs]
        per_question.append({
            "questionId": q["questionId"], "set": q.get("category", "main"),
            "question": q["question"], "referenceChunks": sorted(refs),
            "topk": top, "hit": bool(hits), "hits": hits,
        })
        print(f"  [{i}/{len(main_qs)}] {q['questionId']} 命中 {len(top)} 段 "
              f"{'✅' if hits else '❌'} HTTP {s}", flush=True)

    el = time.time() - t0
    n = len(per_question)
    hit = sum(1 for r in per_question if r["hit"])
    prec = sum(len(r["hits"]) / args.top_k for r in per_question) / max(n, 1)
    result = {
        "schema": "career-graph-weknora-retrieval/v1",
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "retriever": {"type": "weknora-hybrid-search", "service": base,
                      "kb": args.kb, "topk": args.top_k},
        "metrics": {"main": {"hitRate": f"{hit}/{n}",
                             "hitRateValue": round(hit / max(n, 1), 4),
                             "meanPrecisionAtK": round(prec, 4)}},
        "timing": {"perQueryMs": round(el / max(n, 1) * 1000, 1)},
        "perQuestion": per_question,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n命中 {hit}/{n}，平均精确率@{args.top_k} {prec:.4f}，"
          f"{result['timing']['perQueryMs']} ms/题")
    print(f"产物：{os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
