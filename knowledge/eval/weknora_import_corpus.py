#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把本地语料（knowledge/chunks/chunks.jsonl）导入 WeKnora，用于**同题集同判据**的对照评测。

为什么按「一段一个文档」导入：
  本对照要回答的是「同一个向量模型下，腾讯那套检索栈比我们自建的强多少」。
  若导入原始 22 篇文档，WeKnora 会自己重新分块 → 变量变成"分块方式"而不是"检索栈"。
  按我们已有的 494 段导入，检索单元一致，差异才归因到检索/排序这一层。
  （代价要如实标注：这等于绕过了 WeKnora 自带的分块器。）

幂等：按标题去重，已导入的段会跳过，可反复跑。

用法：
  python knowledge/eval/weknora_import_corpus.py                 # 导入全部
  python knowledge/eval/weknora_import_corpus.py --limit 10      # 先试导 10 段
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
STATE = os.path.join(K, "eval", "runs", "weknora-import-state.json")
DEFAULT_KB_NAME = "职业导航语料-494段"


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("WEKNORA_URL", "http://127.0.0.1:8080"))
    ap.add_argument("--email", default="agent.verify@local.test")
    ap.add_argument("--password", default="verify12345")
    ap.add_argument("--kb-name", default=DEFAULT_KB_NAME)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    s, r = req("POST", base + "/api/v1/auth/login",
               {"email": args.email, "password": args.password})
    if s != 200:
        print(f"登录失败 HTTP {s}: {str(r)[:200]}", file=sys.stderr)
        return 2
    token = (r.get("data") or {}).get("access_token") if isinstance(r.get("data"), dict) else None
    token = token or dig(r, ("access_token", "token"))
    if not token:
        print("拿不到 token", file=sys.stderr)
        return 2

    # 取嵌入模型（与本项目自建栈同一个 Qwen3-Embedding-0.6B）
    s, r = req("GET", base + "/api/v1/models", token=token)
    model_id = None
    for m in unwrap_list(r):
        if isinstance(m, dict) and str(m.get("type", "")).lower().startswith("embed"):
            model_id = m.get("id")
            print(f"嵌入模型：{model_id} ({m.get('name')})")
            break
    if not model_id:
        print("找不到 embedding 模型", file=sys.stderr)
        return 2

    # 建库或复用（embedding_model_id 只能在创建时绑定）
    s, r = req("GET", base + "/api/v1/knowledge-bases", token=token)
    kb_id = None
    for kb in unwrap_list(r):
        if isinstance(kb, dict) and kb.get("name") == args.kb_name:
            kb_id = kb.get("id")
            print(f"复用知识库：{kb_id}（{args.kb_name}）")
            break
    if not kb_id:
        s, r = req("POST", base + "/api/v1/knowledge-bases",
                   {"name": args.kb_name, "description": "职业导航语料 494 段（评测对照用）",
                    "embedding_model_id": model_id}, token=token)
        kb_id = dig(r, ("id",))
        print(f"新建知识库：{kb_id}（HTTP {s}）")
        if not kb_id:
            print(f"建库失败：{str(r)[:300]}", file=sys.stderr)
            return 2

    # 已导入的标题（幂等）
    existing = set()
    page = 1
    while True:
        s, r = req("GET", f"{base}/api/v1/knowledge-bases/{kb_id}/knowledge"
                          f"?page={page}&page_size=100", token=token)
        batch = [x for x in unwrap_list(r) if isinstance(x, dict)]
        existing.update(x.get("title") for x in batch)
        if len(batch) < 100 or page > 60:
            break
        page += 1
    print(f"已存在文档 {len(existing)} 条")

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    todo = [c for c in chunks if f"{c['chunkId']} · {c.get('sectionPath') or c['sourceId']}" not in existing]
    if args.limit:
        todo = todo[:args.limit]
    print(f"待导入 {len(todo)} / 共 {len(chunks)} 段")

    t0, ok, fail = time.time(), 0, 0
    state = {"kbId": kb_id, "kbName": args.kb_name, "modelId": model_id, "imported": []}
    for i, c in enumerate(todo, 1):
        title = f"{c['chunkId']} · {c.get('sectionPath') or c['sourceId']}"
        s, r = req("POST", f"{base}/api/v1/knowledge-bases/{kb_id}/knowledge/manual",
                   {"title": title, "content": c["text"], "status": "publish",
                    "channel": "eval-corpus"}, token=token)
        if s in (200, 201):
            ok += 1
            state["imported"].append({"chunkId": c["chunkId"], "title": title})
        else:
            fail += 1
            print(f"  ✗ {c['chunkId']} HTTP {s} {str(r)[:120]}")
        if i % 25 == 0 or i == len(todo):
            el = time.time() - t0
            print(f"  {i}/{len(todo)} 成功 {ok} 失败 {fail}  {el:.0f}s "
                  f"({i/max(el,1e-9):.1f} 条/秒)", flush=True)
            json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(args.sleep)

    state["finishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    state["ok"] = ok
    state["fail"] = fail
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n完成：成功 {ok} 失败 {fail}，用时 {time.time()-t0:.0f}s")
    print(f"知识库 {kb_id}；状态文件 {os.path.relpath(STATE, ROOT)}")
    print("注意：文档解析与向量化是异步的，稍等片刻再做检索；用 --export-corpus 可核对入库 chunk 数。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
