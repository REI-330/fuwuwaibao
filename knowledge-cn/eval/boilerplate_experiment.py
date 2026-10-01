#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""量一下「出处样板」到底占了多少区分度——不重建索引的对照实验。

## 为什么要做这个实验

改造后结构化目录变成「一条记录一个 chunk」，但每块里仍有 ~58% 的字是同一段出处样板
（`_出处：moe-majors-2026 · 类型：FACT · 记录 ID：… · 审核状态：… · 豁免人：业主_`）。
怀疑点：862 个 chunk 的向量因为这段重复文本而彼此高度相似，导致向量通道排不出名次
（重建后纯向量命中@1 从 12/26 掉到 8/26）。

直接重建一次验证要十几分钟且会牵连别的变量；这里用更便宜的办法：

1. 从库里取出结构化目录的全部 chunk（正文 A）；
2. 用同一个嵌入服务（127.0.0.1:8090）分别算 A 与「剥掉出处行」的 B 的向量；
3. 对同一批 query，算目标 chunk 在 A/B 两套向量里的名次，并比较整体两两相似度。

**这只量向量通道**（关键词通道的 BM25 本身有长度归一化，口径不同），
所以结论只用于「要不要精简出处行」这一件事。

用法：

    python knowledge-cn/eval/boilerplate_experiment.py \
        --db knowledge-v1/weknora-src/data/weknora-cn.db \
        --questions knowledge-cn/evaluations/questions-cn-v1.json \
        --out knowledge-cn/evidence/boilerplate-experiment.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

EMBED_URL = "http://127.0.0.1:8090/v1/embeddings"
PROVENANCE = re.compile(r"_(?:来源|出处)：.*?_", re.S)


def embed(texts, batch=32):
    vectors = []
    for start in range(0, len(texts), batch):
        payload = json.dumps({"model": "bge-small-zh-v1.5", "input": texts[start:start + batch]}).encode()
        request = urllib.request.Request(EMBED_URL, data=payload, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", "Bearer local-tei-no-auth")
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read())
        vectors += [item["embedding"] for item in body["data"]]
    return vectors


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rank_of(query_vector, vectors, target_index):
    scores = [(cosine(query_vector, vector), i) for i, vector in enumerate(vectors)]
    scores.sort(reverse=True)
    for position, (_, index) in enumerate(scores, 1):
        if index == target_index:
            return position
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--document", default="moe-majors-2026-structured.md")
    parser.add_argument("--out")
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = list(conn.execute(
        "SELECT c.content FROM chunks c JOIN knowledges k ON k.id = c.knowledge_id "
        "WHERE k.deleted_at IS NULL AND k.file_name = ? ORDER BY c.chunk_index", (args.document,)))
    conn.close()
    texts = [row[0] for row in rows]
    stripped = [PROVENANCE.sub("", text) for text in texts]

    document = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    probes = []
    for question in document["questions"]:
        for group in question.get("gold") or []:
            if group.get("judgment"):
                continue
            target = next((i for i, text in enumerate(stripped)
                           if all(anchor in text for anchor in group["anchors"])), None)
            if target is None:
                continue
            probes.append({"questionId": question["questionId"], "group": group["name"],
                           "question": question["question"], "targetIndex": target})
            break  # 每题取第一个能在结构化目录里找到的组

    vectors_a = embed(texts)
    vectors_b = embed(stripped)
    query_vectors = embed([probe["question"] for probe in probes])

    results = []
    for probe, qv in zip(probes, query_vectors):
        results.append({
            "questionId": probe["questionId"],
            "group": probe["group"],
            "rankWithProvenance": rank_of(qv, vectors_a, probe["targetIndex"]),
            "rankWithoutProvenance": rank_of(qv, vectors_b, probe["targetIndex"]),
        })

    def mean_pairwise(vectors, sample=120, stride=7):
        picked = vectors[::stride][:sample]
        total, count = 0.0, 0
        for i in range(len(picked)):
            for j in range(i + 1, len(picked)):
                total += cosine(picked[i], picked[j])
                count += 1
        return round(total / count, 4) if count else None

    report = {
        "document": args.document,
        "chunks": len(texts),
        "provenanceShareMedian": round(sorted(
            (len(text) - len(text2)) / len(text) for text, text2 in zip(texts, stripped) if text
        )[len(texts) // 2], 4),
        "meanPairwiseCosineWithProvenance": mean_pairwise(vectors_a),
        "meanPairwiseCosineWithoutProvenance": mean_pairwise(vectors_b),
        "probes": results,
        "improved": sum(1 for r in results
                        if r["rankWithoutProvenance"] and r["rankWithProvenance"]
                        and r["rankWithoutProvenance"] < r["rankWithProvenance"]),
        "worsened": sum(1 for r in results
                        if r["rankWithoutProvenance"] and r["rankWithProvenance"]
                        and r["rankWithoutProvenance"] > r["rankWithProvenance"]),
        "unchanged": sum(1 for r in results
                         if r["rankWithoutProvenance"] == r["rankWithProvenance"]),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "probes"}, ensure_ascii=False, indent=2))
    for row in results:
        print(f"  {row['questionId']:5} 带出处 {row['rankWithProvenance']} → 剥掉 {row['rankWithoutProvenance']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
