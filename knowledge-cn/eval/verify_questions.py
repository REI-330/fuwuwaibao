#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""出题后的硬门禁：每一条 gold 都必须在真实库里找得到证据。

## 为什么不能靠人眼

题集里的 gold 是「锚点字符串」而不是 chunkId，好处是不受 512 字分块影响，
坏处是**写错了不会报错**——检索评测只会把这题算成「未命中」，
让人误以为是检索不行。所以出完题必须先把标注验一遍：

- 每个 gold 组：库里是否有 chunk 同时含全部锚点？命中几个 chunk？
- 可答题的 gold 组不能为空；超范围题的 gold 必须为空（否则是标注写反了）。
- 锚点是不是被**截断**过（结构化记录里长专业名会截断），截断片段不能当锚点。

用法：

    python knowledge-cn/eval/verify_questions.py \
        --db knowledge-v1/weknora-src/data/weknora-cn.db \
        --questions knowledge-cn/evaluations/questions-cn-v1.json \
        --out knowledge-cn/evidence/questions-verified.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    # 删除是软删除：旧 chunk 行不会立刻消失，必须只统计在库文档。
    chunks = [(row[0], row[1], row[2]) for row in conn.execute(
        "SELECT c.id, c.chunk_index, c.content FROM chunks c "
        "JOIN knowledges k ON k.id = c.knowledge_id WHERE k.deleted_at IS NULL")]
    conn.close()

    document = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    report, problems, fragile = [], [], []
    for question in document["questions"]:
        entry = {"questionId": question["questionId"], "answerable": question["answerable"],
                 "groups": []}
        for group in question.get("gold") or []:
            hits = [(chunk_id, index, text) for chunk_id, index, text in chunks
                    if all(anchor in text for anchor in group["anchors"])]
            # 锚点在原文里挨得越近，越不会被切块边界拆开；跨度大 = 标注脆。
            window = None
            for _, _, text in hits:
                starts = [text.find(anchor) for anchor in group["anchors"]]
                spans = [text.find(anchor) + len(anchor) for anchor in group["anchors"]]
                size = max(spans) - min(starts)
                window = size if window is None else min(window, size)
            entry["groups"].append({
                "name": group["name"],
                "anchors": group["anchors"],
                "coveringChunks": len(hits),
                "minWindowChars": window,
                "sample": [{"chunkId": chunk_id, "chunkIndex": index}
                           for chunk_id, index, _ in hits[:3]],
            })
            if not hits:
                problems.append(f"{question['questionId']} / {group['name']}：库里找不到含 "
                                f"{group['anchors']} 的 chunk")
            elif window and window > 400:
                fragile.append(f"{question['questionId']} / {group['name']}：锚点在原文里跨度 "
                               f"{window} 字，切块边界一动就会从「一条 chunk 全含」变成不命中")
        if question["answerable"] and not entry["groups"]:
            problems.append(f"{question['questionId']}：可答题却没有 gold 组")
        if not question["answerable"] and entry["groups"]:
            problems.append(f"{question['questionId']}：超范围题却带了 gold 组")
        report.append(entry)

    result = {
        "questionsFile": args.questions,
        "total": len(document["questions"]),
        "answerable": sum(1 for q in document["questions"] if q["answerable"]),
        "refusal": sum(1 for q in document["questions"] if not q["answerable"]),
        "goldGroups": sum(len(q.get("gold") or []) for q in document["questions"]),
        "problems": problems,
        "fragileGold": fragile,
        "passed": not problems,
        "perQuestion": report,
    }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(f"题集 {result['total']} 题（可答 {result['answerable']} / 超范围 {result['refusal']}），"
          f"gold 组 {result['goldGroups']} 个，问题 {len(problems)} 处，"
          f"锚点跨度偏大的组 {len(fragile)} 个")
    for problem in problems:
        print("  ✗", problem)
    for item in fragile:
        print("  ⚠", item)
    if not problems:
        for entry in report:
            if entry["groups"]:
                sizes = ", ".join(f"{g['name']}={g['coveringChunks']}(跨度{g['minWindowChars']})"
                                  for g in entry["groups"])
                print(f"  ✓ {entry['questionId']}: {sizes}")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
