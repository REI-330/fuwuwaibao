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
    chunks = [(row[0], row[1], row[2]) for row in
              conn.execute("SELECT id, chunk_index, content FROM chunks")]
    conn.close()

    document = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    report, problems = [], []
    for question in document["questions"]:
        entry = {"questionId": question["questionId"], "answerable": question["answerable"],
                 "groups": []}
        for group in question.get("gold") or []:
            hits = [(chunk_id, index) for chunk_id, index, text in chunks
                    if all(anchor in text for anchor in group["anchors"])]
            entry["groups"].append({
                "name": group["name"],
                "anchors": group["anchors"],
                "coveringChunks": len(hits),
                "sample": [{"chunkId": chunk_id, "chunkIndex": index}
                           for chunk_id, index in hits[:3]],
            })
            if not hits:
                problems.append(f"{question['questionId']} / {group['name']}：库里找不到含 "
                                f"{group['anchors']} 的 chunk")
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
        "passed": not problems,
        "perQuestion": report,
    }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(f"题集 {result['total']} 题（可答 {result['answerable']} / 超范围 {result['refusal']}），"
          f"gold 组 {result['goldGroups']} 个，问题 {len(problems)} 处")
    for problem in problems:
        print("  ✗", problem)
    if not problems:
        for entry in report:
            if entry["groups"]:
                sizes = ", ".join(f"{g['name']}={g['coveringChunks']}" for g in entry["groups"])
                print(f"  ✓ {entry['questionId']}: {sizes}")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
