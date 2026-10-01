"""导入后的独立核验：1519 条是不是**真的**都进了 WeKnora，而且可检索。

为什么需要它：`parse_status=completed` 只说明「解析这一步没报错」，不等于
「每条记录都在检索库里」。这个脚本直接查 WeKnora 的 SQLite 库，逐条比对：

1. **逐条命中**：1519 个记录 ID 是否都能在某个 chunk 的正文里找到（0 缺失才算过）；
2. **向量完整**：`vec_embeddings_1024` 的向量行数是否等于 chunk 数（少一条就是有 chunk 没算向量）；
3. **维度一致**：`lite_embeddings.dimension` 是否唯一且等于模型声明的维度；
4. **活检索探针**：通过 HTTP `hybrid-search` 真发一次查询，确认不是「库里躺着但检索不到」。

## 删除是软删除，计数必须排掉它

`DELETE /api/v1/knowledge/:id` 走的是 `parse_status='deleting'` + `deleted_at` 置位，
`chunks` 表里的旧行**不会立刻消失**（embedding / FTS 会另行清理）。所以核验必须
`JOIN knowledges ON deleted_at IS NULL`，否则重建一次就会把旧 chunk 也算进来，
数字虚高、还会误报「有文档停在未完成状态」。

用法（仓库根目录）：

    python knowledge-cn/verify_import.py --db <weknora.db> --kb <kb-id>
    python knowledge-cn/verify_import.py --db <db> --kb <kb-id> --out knowledge-cn/evidence/import-verify.json

退出码 0 = 全部通过；非 0 = 有项没过（便于接进 CI / 交付核验清单）。
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REVIEWED = str(ROOT / "knowledge-cn" / "data" / "reviewed" / "*.jsonl")


def login(base, email, password):
    payload = {"email": email, "password": password}
    request = urllib.request.Request(base + "/api/v1/auth/login",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read() or b"{}")["token"]


def hybrid_search(base, token, kb_id, query, top_k=3):
    request = urllib.request.Request(
        f"{base}/api/v1/knowledge-bases/{kb_id}/hybrid-search",
        data=json.dumps({"query_text": query, "top_k": top_k}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.loads(response.read() or b"{}")
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("items") or data.get("results") or []
    return []


def expected_ids(pattern):
    ids = []
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("eligible_for_import") and record.get("review_status") in ("APPROVED", "WAIVED"):
                    ids.append(record["id"])
    return ids


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="WeKnora 的 sqlite 文件（如 knowledge-v1/weknora-src/data/weknora-cn.db）")
    parser.add_argument("--reviewed", default=DEFAULT_REVIEWED, help="已审核 jsonl 的 glob")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--kb", help="知识库 ID（给了才做活检索探针）")
    parser.add_argument("--query", default="哲学 专业代码", help="活检索探针的查询词")
    parser.add_argument("--email", default="agent.verify@local.test")
    parser.add_argument("--password", default="verify12345")
    parser.add_argument("--out", help="把核验结果写成 JSON 证据")
    args = parser.parse_args()

    ids = expected_ids(args.reviewed)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True) if not args.db.startswith("file:") \
        else sqlite3.connect(args.db, uri=True)
    live_chunks = list(conn.execute(
        "SELECT c.content FROM chunks c JOIN knowledges k ON k.id = c.knowledge_id "
        "WHERE k.deleted_at IS NULL"))
    chunks = [row[0] or "" for row in live_chunks]
    blob = "\n".join(chunks)
    missing = [record_id for record_id in ids if record_id not in blob]

    vector_rows = conn.execute("SELECT COUNT(*) FROM vec_embeddings_1024_rowids").fetchone()[0]
    dimensions = sorted({row[0] for row in conn.execute("SELECT DISTINCT dimension FROM lite_embeddings")})
    documents = conn.execute(
        "SELECT COUNT(*) FROM knowledges WHERE deleted_at IS NULL").fetchone()[0]
    soft_deleted = conn.execute(
        "SELECT COUNT(*) FROM knowledges WHERE deleted_at IS NOT NULL").fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM knowledges WHERE deleted_at IS NULL "
        "AND parse_status NOT IN ('completed')").fetchone()[0]
    per_document = [{"fileName": name, "chunks": count} for name, count in conn.execute(
        "SELECT k.file_name, COUNT(*) FROM chunks c JOIN knowledges k ON k.id = c.knowledge_id "
        "WHERE k.deleted_at IS NULL GROUP BY k.file_name ORDER BY k.file_name")]

    report = {
        "reviewedRecords": len(ids),
        "chunks": len(chunks),
        "chunksWithVector": vector_rows,
        "documents": documents,
        "softDeletedDocuments": soft_deleted,
        "documentsNotCompleted": pending,
        "missingRecordIds": len(missing),
        "missingSample": missing[:10],
        "embeddingDimensions": dimensions,
        "chunksPerDocument": per_document,
        "checks": {},
    }

    if args.kb:
        token = login(args.base_url, args.email, args.password)
        hits = hybrid_search(args.base_url, token, args.kb, args.query)
        top = (hits[0].get("content") or "") if hits else ""
        # 只打印前 120 字会误判命中：一个 chunk 装 3 条记录时，答案可能在 chunk 中部，
        # 预览显示的却是邻座记录（「低空经济与管理」那次就是这么看错的）。
        # 所以这里连「这条 chunk 里都有哪些记录 ID」一起报出来。
        report["probe"] = {"query": args.query, "hits": len(hits),
                           "topPreview": top[:120],
                           "topRecordIds": re.findall(r"记录 ID：([A-Za-z0-9\-]+)", top)}

    checks = report["checks"]
    checks["每条记录都能在 chunk 里找到"] = len(missing) == 0 and len(ids) > 0
    checks["每个 chunk 都有向量"] = vector_rows == len(chunks) and len(chunks) > 0
    checks["向量维度唯一且为 1024"] = dimensions == [1024]
    checks["没有文档停在未完成状态"] = pending == 0
    if args.kb:
        checks["活检索有命中"] = report["probe"]["hits"] > 0

    ok = all(checks.values())
    report["passed"] = ok

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
