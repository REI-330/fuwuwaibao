#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""排查「低空经济与管理」这条查询到底是不是召回失败。

## 起因

导入核验脚本 `verify_import.py` 的活检索探针打印的是
`probe.topPreview = hits[0]["content"][:120]`。用「低空经济与管理」当探针词时，
那 120 个字显示的是「专业代码：020108T；专业名称：经济工程」——看起来像是
**「低空经济与管理」检索到了「经济工程」**，也就是一次典型的召回错误。

## 这个脚本要回答的事

不是靠读代码猜，而是把三种口径分别量出来：

1. **目标 chunk 的真实名次**：混合检索 / 纯向量 / 纯关键词各排第几；
2. **RRF 分数能不能对上名次**：`0.7/(60+rank_v) + 0.3/(60+rank_k)` 反算，
   证明名次表不是猜的；
3. **为什么 topPreview 会显示别的专业**：统计「有多少条记录不是所在 chunk 的首条」、
   「chunk 里装几条记录」、「出处样板占 chunk 多少字」——这三项决定了
   前 120 字会显示谁。

结论以 JSON 证据落盘，供报告引用；脚本本身可反复重跑复算同一组数字。

用法：

    python knowledge-cn/eval/diagnose_recall.py \
        --db knowledge-v1/weknora-src/data/weknora-cn.db \
        --kb aacc4889-a347-44ea-9dd1-b60ec1917cbb \
        --query 低空经济与管理 --record moe-major-020110TK \
        --out knowledge-cn/evidence/recall-diagnosis-lowaltitude.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from weknora_client import WeKnora  # noqa: E402

RRF_K = 60
RRF_VECTOR_WEIGHT = 0.7
RRF_KEYWORD_WEIGHT = 0.3
RECORD_ID_PATTERN = re.compile(r"记录 ID：([A-Za-z0-9\-]+)")
PROVENANCE_PATTERN = re.compile(r"_(?:来源|出处)：.*?业主[^\n]*_", re.S)


def connect(db_path):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def document_stats(conn):
    """每个**在库**文档的 chunk 数 / 长度；顺带找出内容哈希序列完全相同的重复文档。

    只统计 `deleted_at IS NULL` 的文档：删除是软删除，旧 chunk 行不会立刻消失，
    不排掉就会把重建前的旧块也当成现状（这正是第一次复跑时看到的假重复）。
    """
    documents = []
    for kid, name in conn.execute(
            "SELECT id, file_name FROM knowledges WHERE deleted_at IS NULL"):
        rows = list(conn.execute(
            "SELECT chunk_index, length(content), content, content_hash FROM chunks "
            "WHERE knowledge_id = ? ORDER BY chunk_index", (kid,)))
        if not rows:
            continue
        lengths = sorted(length for _, length, _, _ in rows)
        documents.append({
            "knowledgeId": kid,
            "fileName": name,
            "chunks": len(rows),
            "medianChars": lengths[len(lengths) // 2],
            "maxChars": lengths[-1],
            "contentHashes": [row[3] for row in rows],
        })
    duplicates = []
    for i, left in enumerate(documents):
        for right in documents[i + 1:]:
            if left["contentHashes"] and left["contentHashes"] == right["contentHashes"]:
                duplicates.append({
                    "a": left["knowledgeId"], "b": right["knowledgeId"],
                    "fileName": left["fileName"], "chunksEach": left["chunks"]})
    for document in documents:
        document.pop("contentHashes")
    return documents, duplicates


def ledger_stats(conn, knowledge_id):
    """统计「记录—chunk」的装填关系。

    只对渲染成多条记录的文档有意义（教育部的结构化目录：一条记录一个
    `## moe-major-<代码>` 段）。`boilerplateRatio` 是出处样板占 chunk 的比例。
    """
    rows = list(conn.execute(
        "SELECT id, chunk_index, content FROM chunks WHERE knowledge_id = ? ORDER BY chunk_index",
        (knowledge_id,)))
    records, head_records, per_chunk, ratios, offsets = [], set(), [], [], {}
    for chunk_id, chunk_index, text in rows:
        ids = RECORD_ID_PATTERN.findall(text)
        records += ids
        if ids:
            head_records.add(ids[0])
        per_chunk.append(len(ids))
        if text:
            boilerplate = sum(len(m.group(0)) for m in PROVENANCE_PATTERN.finditer(text))
            ratios.append(boilerplate / len(text))
        for position, record_id in enumerate(ids):
            offsets[record_id] = {"chunkId": chunk_id, "chunkIndex": chunk_index,
                                  "positionInChunk": position}
    distribution = {}
    for count in per_chunk:
        distribution[str(count)] = distribution.get(str(count), 0) + 1
    ratios.sort()
    return {
        "chunks": len(rows),
        "records": len(records),
        "recordsThatHeadAChunk": len(head_records),
        "recordsNotAtChunkHead": len(records) - len(head_records),
        "recordsPerChunk": distribution,
        "boilerplateRatioMedian": round(ratios[len(ratios) // 2], 3) if ratios else None,
        "boilerplateRatioMax": round(ratios[-1], 3) if ratios else None,
        "recordOffsets": offsets,
    }


def rank_of(hits, chunk_ids):
    for position, hit in enumerate(hits, 1):
        if hit.get("id") in chunk_ids:
            return position
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--kb", required=True)
    parser.add_argument("--query", default="低空经济与管理")
    parser.add_argument("--record", default="moe-major-020110TK",
                        help="目标记录 ID（出现在 chunk 正文的「记录 ID：」里）")
    parser.add_argument("--depth", type=int, default=50, help="每个通道最多取多少条")
    parser.add_argument("--out")
    args = parser.parse_args()

    conn = connect(args.db)
    documents, duplicates = document_stats(conn)

    target_rows = list(conn.execute(
        "SELECT c.id, c.knowledge_id, c.chunk_index, c.content FROM chunks c "
        "JOIN knowledges k ON k.id = c.knowledge_id "
        "WHERE k.deleted_at IS NULL AND c.content LIKE ?",
        (f"%记录 ID：{args.record}%",)))
    if not target_rows:
        raise SystemExit(f"库里找不到记录 {args.record} 所在的 chunk")
    target_ids = {row[0] for row in target_rows}
    target_knowledge = target_rows[0][1]

    ledger = ledger_stats(conn, target_knowledge)
    conn.close()

    wk = WeKnora()
    modes = {
        "hybrid": {},
        "vectorOnly": {"vector_only": True},
        "keywordOnly": {"keyword_only": True},
    }
    ranks = {}
    for label, kwargs in modes.items():
        hits = wk.search(args.kb, args.query, top=args.depth, **kwargs)
        ranks[label] = {
            "hits": len(hits),
            "targetRank": rank_of(hits, target_ids),
            "top1": {
                "chunkId": hits[0].get("id") if hits else None,
                "score": hits[0].get("score") if hits else None,
                "head": (hits[0].get("content") or "")[:120] if hits else None,
                "headRecordId": (RECORD_ID_PATTERN.search(hits[0].get("content") or "")
                                 .group(1) if hits and RECORD_ID_PATTERN.search(
                                     hits[0].get("content") or "") else None),
            } if hits else None,
        }

    # RRF 反算：用两个通道的名次算分，和接口报的分对照，验证名次表可信
    vector_rank = ranks["vectorOnly"]["targetRank"]
    keyword_rank = ranks["keywordOnly"]["targetRank"]
    expected = 0.0
    if vector_rank:
        expected += RRF_VECTOR_WEIGHT / (RRF_K + vector_rank)
    if keyword_rank:
        expected += RRF_KEYWORD_WEIGHT / (RRF_K + keyword_rank)
    hybrid_target = next((h for h in wk.search(args.kb, args.query, top=args.depth)
                          if h.get("id") in target_ids), None)

    record_offset = ledger["recordOffsets"].get(args.record)
    target_rank = ranks["hybrid"]["targetRank"]
    at_head = bool(record_offset and record_offset["positionInChunk"] == 0)
    top1 = ranks["hybrid"]["top1"] or {}
    preview_shows_target = bool(top1.get("headRecordId") == args.record)
    if target_rank is None:
        explanation = (f"目标 chunk 在混合检索前 {args.depth} 名里**没有出现**，这是真召回失败。")
    elif at_head and preview_shows_target:
        explanation = ("目标 chunk 排第 %d，且该记录就是 chunk 首条 —— 预览前 120 字里直接看得到它。"
                       % target_rank)
    elif target_rank == 1:
        explanation = ("目标 chunk 排第 1（向量第 %s、关键词第 %s），但这条记录在 chunk 里排第 %d，"
                       "所以前 120 字的预览显示的是同 chunk 的邻座记录。"
                       % (vector_rank, keyword_rank, (record_offset or {}).get("positionInChunk", -1) + 1))
    else:
        explanation = ("目标 chunk 排第 %d（向量第 %s、关键词第 %s），记录在 chunk 内位置第 %d。"
                       % (target_rank, vector_rank, keyword_rank,
                          (record_offset or {}).get("positionInChunk", -1) + 1))

    report = {
        "query": args.query,
        "targetRecord": args.record,
        "targetChunkIds": sorted(target_ids),
        "targetKnowledgeId": target_knowledge,
        "targetOffset": record_offset,
        "retrieval": ranks,
        "rrfCheck": {
            "formula": "score = 0.7/(60+vectorRank) + 0.3/(60+keywordRank)",
            "vectorRank": vector_rank,
            "keywordRank": keyword_rank,
            "recomputed": round(expected, 6),
            "reported": round(hybrid_target.get("score"), 6) if hybrid_target else None,
            "matches": bool(hybrid_target) and abs(expected - hybrid_target.get("score", 0)) < 1e-6,
        },
        "corpusShape": ledger,
        "documents": documents,
        "duplicateDocuments": duplicates,
        "verdict": {
            "recallFailure": target_rank is None,
            "targetRankInHybrid": target_rank,
            "targetIsAtChunkHead": at_head,
            "previewShowsTarget": preview_shows_target,
            "explanation": explanation,
        },
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
