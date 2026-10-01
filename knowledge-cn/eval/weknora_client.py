"""与 WeKnora Lite 真实检索接口打交道的公共客户端。

## 为什么单独抽一个模块

`knowledge/eval/weknora_retrieval.py` 里埋了两个**只在这份 WeKnora 上成立**的坑，
如果每个新脚本各自写一遍，就会各自踩一遍：

1. **检索参数不是 `top_k`**。`types.SearchParams` 只有
   `match_count` / `vector_threshold` / `keyword_threshold` /
   `disable_keywords_match` / `disable_vector_match` / `skip_context_enrichment`。
   传 `top_k` 会被 JSON 静默忽略（不报错），服务端按默认
   `DefaultRetrievalTopK=50` 返回——于是「我明明要 top-3，怎么来了 110 条」。
   110 条里还有 `processSearchResults` 补进来的父子/相邻块。

2. **关闭上下文补全**。做检索质量评测必须只量**排序本身**，
   所以默认带上 `skip_context_enrichment=true`；否则命中里混进邻居块，
   指标会虚高。

## 直接查 FTS5 会用错口径

`lite_embeddings_fts` 是 **contentless** 表，正文不是原文：入库时先过了 Go 侧的
`tokenizeCJKBigram`（连续汉字切**重叠二元组**，非汉字整词保留），查询侧再过一遍
`sanitizeFTS5Query`（同样切二元组，再用 `OR` 拼）。所以：

- 直接 `MATCH '低空经济与管理'` 走的是 unicode61 的分词（整段汉字一个 token）
  → **0 命中**，会让你误以为关键词通道没索引中文；
- 走 HTTP 才是真实链路（查询被切成 `"低空" OR "空经" OR "经济" OR …`）→ 有命中。

结论：**不要绕过 HTTP 直接查 FTS 表下结论**，那是另一个分词器。

用法：

    from weknora_client import WeKnora
    wk = WeKnora()                      # 默认 http://127.0.0.1:8080
    hits = wk.search(KB_ID, "低空经济与管理", top=5)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE = os.environ.get("WEKNORA_URL", "http://127.0.0.1:8080")
DEFAULT_EMAIL = os.environ.get("WEKNORA_EMAIL", "agent.verify@local.test")
DEFAULT_PASSWORD = os.environ.get("WEKNORA_PASSWORD", "verify12345")


def _request(method, url, payload=None, token=None, timeout=120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            return error.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return error.code, {"raw": raw[:300]}


def unwrap(result):
    """把 `{success, data: [...]}` / `{data: {items: [...]}}` 统一成 list。"""
    body = result.get("data") if isinstance(result, dict) else None
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("items", "list", "results", "chunks", "knowledge"):
            if isinstance(body.get(key), list):
                return body[key]
    return []


class WeKnora:
    def __init__(self, base_url=DEFAULT_BASE, email=DEFAULT_EMAIL, password=DEFAULT_PASSWORD):
        self.base_url = base_url.rstrip("/")
        self._token = None
        self.email = email
        self.password = password

    @property
    def token(self):
        if self._token is None:
            status, body = _request("POST", self.base_url + "/api/v1/auth/login",
                                    {"email": self.email, "password": self.password})
            token = body.get("token") or body.get("access_token")
            if not token:
                raise SystemExit(f"登录失败 HTTP {status}: {str(body)[:200]}")
            self._token = token
        return self._token

    def search(self, kb_id, query, top=10, vector_only=False, keyword_only=False,
               enrich_context=False):
        """真实 hybrid-search。返回 [{id, score, content, knowledge_id, chunk_index, ...}]。

        `top` 落到 `match_count`；`enrich_context=False` 时加 `skip_context_enrichment`，
        保证返回的就是**融合后的前 N 名**，不含补进来的邻居块。
        """
        payload = {"query_text": query, "match_count": top}
        if not enrich_context:
            payload["skip_context_enrichment"] = True
        if vector_only:
            payload["disable_keywords_match"] = True
        if keyword_only:
            payload["disable_vector_match"] = True
        status, body = _request(
            "POST", f"{self.base_url}/api/v1/knowledge-bases/{kb_id}/hybrid-search",
            payload, token=self.token)
        if status != 200:
            raise SystemExit(f"检索失败 HTTP {status}: {str(body)[:200]}")
        return unwrap(body)

    def list_knowledge_bases(self):
        _, body = _request("GET", self.base_url + "/api/v1/knowledge-bases", token=self.token)
        return unwrap(body)

    def list_knowledge(self, kb_id, page_size=100):
        items, page = [], 1
        while True:
            _, body = _request(
                "GET", f"{self.base_url}/api/v1/knowledge-bases/{kb_id}/knowledge"
                       f"?page={page}&page_size={page_size}", token=self.token)
            batch = unwrap(body)
            items += [x for x in batch if isinstance(x, dict)]
            if len(batch) < page_size:
                break
            page += 1
            if page > 200:
                break
        return items

    def list_chunks(self, knowledge_id, page_size=200):
        chunks, page = [], 1
        while True:
            _, body = _request("GET", f"{self.base_url}/api/v1/chunks/{knowledge_id}"
                                      f"?page={page}&page_size={page_size}", token=self.token)
            batch = unwrap(body)
            chunks += [x for x in batch if isinstance(x, dict)]
            if len(batch) < page_size:
                break
            page += 1
            if page > 200:
                break
        return chunks


def kb_id_from_db(db_path):
    """从 WeKnora 的 sqlite 里取知识库 ID（只有一个库时最省事）。"""
    import sqlite3

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = list(conn.execute("SELECT id, name FROM knowledge_bases"))
    conn.close()
    if not rows:
        raise SystemExit(f"{db_path} 里没有知识库")
    return rows[0][0] if len(rows) == 1 else [r[0] for r in rows]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="WeKnora 检索探针")
    parser.add_argument("--kb", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--vector-only", action="store_true")
    parser.add_argument("--keyword-only", action="store_true")
    args = parser.parse_args()

    wk = WeKnora()
    for i, hit in enumerate(wk.search(args.kb, args.query, top=args.top,
                                      vector_only=args.vector_only,
                                      keyword_only=args.keyword_only), 1):
        text = (hit.get("content") or "").replace("\n", " ")
        print(f"{i:2}. {hit.get('score'):.6f}  {hit.get('id')}  {text[:100]}")
