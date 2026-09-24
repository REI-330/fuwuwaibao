#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WeKnora 端到端链路验证：TEI(embedding) → WeKnora → 向量检索

流程：
  1. 登录（首次自动注册）
  2. 从 /models 取默认 Embedding 模型 id
  3. 确保知识库存在且**已绑定该模型**（关键：不绑定会导致分块阶段报
     "processChunks get embedding model failed"，解析永远卡在 processing）
  4. 写入一条手工 Markdown 知识，轮询解析状态直到终态
  5. 用「字面不重合、语义相关」的查询做检索

第 5 步是判别设计：查询与正文没有共同实词，只有语义关联。
若仍能命中，说明走的是向量语义检索；退化成关键词匹配则会返回空。

用法：
  python weknora_lite_check.py                 # 完整流程
  python weknora_lite_check.py --search-only   # 只跑检索
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080/api/v1"
EMAIL = "agent.verify@local.test"
USERNAME = "agentverify"
PASSWORD = "verify12345"

KB_NAME = "职业导航验证库"
DOC_TITLE = "端侧模型压缩技术说明"
# 正文刻意使用「低比特整数」「权重映射」等技术词
DOC_CONTENT = """# 端侧模型压缩技术说明

## 量化

模型量化是把浮点权重映射为低比特整数的压缩技术，通常从 FP16 压到 INT8 甚至 INT4，
可显著降低显存占用与访存带宽需求，常用于端侧推理加速。

## 剪枝

结构化剪枝按通道或注意力头移除冗余参数，配合再训练可恢复大部分精度。

## 蒸馏

知识蒸馏让小模型拟合大模型的输出分布，在延迟敏感场景下性价比高。
"""

# 与正文没有共同实词的查询（语义应命中文档「量化 / 端侧加速」段落）
SEMANTIC_QUERY = "怎么让神经网络在手机上跑得更快"
# 对照组：字面高度重合
LEXICAL_QUERY = "模型量化"


def req(method, path, body=None, token=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
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
            return e.code, {"raw": raw[:400]}
    except Exception as e:
        return None, {"err": f"{type(e).__name__}: {e}"}


def dig(obj, keys):
    """递归找第一个键（响应包装层级不固定）。"""
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
    """取出响应里的列表（data / data.items / data.list / 顶层）。"""
    d = r.get("data") if isinstance(r, dict) else None
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("items", "list", "models", "results", "chunks"):
            if isinstance(d.get(k), list):
                return d[k]
    for k in ("items", "list", "models"):
        if isinstance(r.get(k), list):
            return r[k]
    return None


def login():
    req("POST", "/auth/register",
        {"username": USERNAME, "email": EMAIL, "password": PASSWORD})
    s, r = req("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
    tok = dig(r, ("access_token", "token"))
    if not tok:
        print(f"[auth] 登录失败 HTTP {s}: {str(r)[:200]}")
        sys.exit(1)
    print(f"[auth] 登录成功 (HTTP {s})")
    return tok


def get_embedding_model(token):
    s, r = req("GET", "/models", token=token)
    items = unwrap_list(r) or []
    emb = [m for m in items if isinstance(m, dict) and m.get("type") == "Embedding"]
    if not emb:
        print(f"[model] 未找到 Embedding 模型（HTTP {s}）: {str(r)[:300]}")
        sys.exit(1)
    pick = next((m for m in emb if m.get("is_default")), emb[0])
    print(f"[model] Embedding 模型: id={pick.get('id')} name={pick.get('name')} "
          f"default={pick.get('is_default')}")
    return pick.get("id")


def ensure_kb(token, model_id):
    """挑选一个「名称匹配且已绑定正确向量模型」的知识库；没有则创建。

    注意：embedding_model_id 只能在**创建时**指定，PUT 更新会被静默忽略
    （实测 PUT 返回 200 但字段仍为空，随后解析报
    "processChunks get embedding model failed" 并永久卡在 processing）。
    因此这里不做"改绑"，找不到就直接新建。
    """
    s, r = req("GET", "/knowledge-bases", token=token)
    items = unwrap_list(r) or []
    by_name = {}
    for kb in items:
        if isinstance(kb, dict) and isinstance(kb.get("name"), str):
            by_name.setdefault(kb["name"], []).append(kb)

    for name in kb_name_candidates():
        for kb in by_name.get(name, []):
            if kb.get("embedding_model_id") == model_id:
                print(f"[kb] 复用已绑定模型的知识库 {kb.get('id')} ({name})")
                return kb.get("id")

    for name in kb_name_candidates():
        if name in by_name:
            continue  # 名字被占用但绑定不对，跳过
        s, r = req("POST", "/knowledge-bases",
                   {"name": name, "description": "TEI + WeKnora 链路验证",
                    "embedding_model_id": model_id}, token=token)
        kb_id = dig(r, ("id",))
        if kb_id:
            print(f"[kb] 新建知识库 {kb_id} ({name})，已绑定 {model_id}")
            return kb_id
        print(f"[kb] 创建 {name} 失败 HTTP {s}: {str(r)[:200]}")
    print("[kb] 无法获得可用知识库")
    sys.exit(1)


def kb_name_candidates():
    return [KB_NAME] + [f"{KB_NAME}-{i}" for i in range(2, 6)]


def add_doc(token, kb_id):
    """上传一个真实的 .md 文件（而不是「手工录入」）。

    关键原因：docparser.NewReader 里只有 IsSimpleFormat(fileType) 为真时才用 Go 原生
    SimpleFormatReader；「手工录入」的 file_type 是 "manual"，不属于 simple 格式，
    会被路由到 Python docreader 服务。本机没有 docreader，gRPC 调用又没有超时，
    结果是解析永久卡在 processing 且日志无任何报错。
    上传 .md 走 md 分支，完全绕开 docreader。
    """
    boundary = "----weknora-verify-boundary"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="edge_model_compression.md"\r\n',
        b"Content-Type: text/markdown\r\n\r\n",
        DOC_CONTENT.encode("utf-8"),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    r = urllib.request.Request(
        f"{BASE}/knowledge-bases/{kb_id}/knowledge/file", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            s, payload = resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        s, payload = e.code, {"raw": e.read().decode()[:300]}
    except Exception as e:
        s, payload = None, {"err": f"{type(e).__name__}: {e}"}
    kid = dig(payload, ("id",))
    if not kid:
        print(f"[doc] 上传失败 HTTP {s}: {str(payload)[:300]}")
        sys.exit(1)
    print(f"[doc] 已上传 .md 知识 {kid}")
    return kid


def wait_parse(token, kb_id, kid, timeout=240):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        s, r = req("GET", f"/knowledge/{kid}", token=token)
        st = dig(r, ("parse_status",))
        err = dig(r, ("error_message",)) or ""
        if (st, err) != last:
            print(f"[parse] parse_status={st}" + (f" error={err[:120]}" if err else ""))
            last = (st, err)
        if st in ("completed", "failed", "cancelled"):
            return st
        time.sleep(4)
    print(f"[parse] 超时（最后 {last}）")
    return last[0] if isinstance(last, tuple) else last


def search(token, kb_id, query, label):
    # hybrid-search 的查询字段是 query_text（不是 query）
    s, r = req("POST", f"/knowledge-bases/{kb_id}/hybrid-search",
               {"query_text": query, "top_k": 5}, token=token)
    if s != 200:
        s, r = req("POST", "/knowledge-search",
                   {"query": query, "knowledge_base_id": kb_id}, token=token)
    items = unwrap_list(r) or []
    print(f"\n[{label}] 「{query}」 -> HTTP {s}，命中 {len(items)} 条")
    for i, it in enumerate(items[:3], 1):
        if not isinstance(it, dict):
            continue
        score = it.get("score", it.get("similarity", it.get("distance")))
        content = (it.get("content") or "").replace("\n", " ")[:100]
        print(f"    {i}. score={score} title={it.get('knowledge_title') or it.get('title')}")
        print(f"       {content}…")
    if not items:
        print(f"    原始响应: {str(r)[:300]}")
    return len(items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search-only", action="store_true")
    args = ap.parse_args()

    token = login()
    model_id = get_embedding_model(token)
    kb_id = ensure_kb(token, model_id)

    if not args.search_only:
        kid = add_doc(token, kb_id)
        state = wait_parse(token, kb_id, kid)
        if state != "completed":
            print(f"[warn] 解析未正常完成（{state}）")

    n_lex = search(token, kb_id, LEXICAL_QUERY, "字面重合")
    n_sem = search(token, kb_id, SEMANTIC_QUERY, "语义相关")

    print("\n=== 结论 ===")
    if n_sem:
        print("语义查询命中：向量检索链路可用（TEI embedding 生效）")
    elif n_lex:
        print("仅字面查询命中：疑似退化为关键词检索，需确认 embedding 是否真被调用")
    else:
        print("两种查询均无命中：解析或向量化阶段仍有问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
