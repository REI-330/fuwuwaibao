#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 holdout-build/ 下的各片草稿合并成正式题集，并做完整校验。

比 `validate_questionset.py` 多查两条（这两条是重叠窗口语料特有的坑）：

1. **与 dev/test 参考答案的原文区间重叠**——同一句话有多个 chunkId，只查 id 不等不够；
2. **同一段在题集内被两道题重复引用**——会让两题共享同一个"运气"。

另外统一重算 `minRefCjkRatio` / `meanRefCjkRatio` / `layer`（起草人不填这三项，
`layer` 规则取自现有 dev/test：minRefCjkRatio < 0.20 即 xl，两套题集 100% 一致）。

用法：
  python knowledge/evaluations/holdout-build/assemble_holdout.py            # 校验并写出
  python knowledge/evaluations/holdout-build/assemble_holdout.py --check    # 只校验不写
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
GRAPH = os.path.join(K, "graph", "graph.json")
EVAL = os.path.join(K, "evaluations")
BUILD = os.path.join(EVAL, "holdout-build")
OUT = os.path.join(EVAL, "questions-holdout.json")
MIN_GAP = 3


def cjk_ratio(text):
    return (sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff") / len(text)) if text else 0.0


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    by_id = {c["chunkId"]: c for c in chunks}
    pos = {}
    for c in chunks:
        pos.setdefault(c["sourceId"], []).append(c)
    for src in pos:
        pos[src].sort(key=lambda c: c["charRange"][0])
    order = {src: {c["chunkId"]: i for i, c in enumerate(lst)} for src, lst in pos.items()}

    cited = set()
    g = json.load(open(GRAPH, encoding="utf-8"))
    for n in g["nodes"]:
        cited.update(n.get("sourceRefs") or [])
    for e in g["edges"]:
        cited.update(e.get("sourceRefs") or [])

    used_ranges = defaultdict(list)          # 既有题集（dev/test）的参考段原文区间
    used_ids = set()
    for fn in ("questions-dev.json", "questions-test.json"):
        for q in json.load(open(os.path.join(EVAL, fn), encoding="utf-8"))["questions"]:
            for r in (q.get("referenceChunks") or []):
                used_ids.add(r)
                c = by_id.get(r)
                if c:
                    used_ranges[c["sourceId"]].append(tuple(c["charRange"]))

    def overlaps_used(c):
        a, b = c["charRange"]
        return any(a < ub and ua < b for ua, ub in used_ranges.get(c["sourceId"], []))

    errors, warns = [], []
    questions, seen_refs, ids = [], {}, set()
    for path in sorted(glob.glob(os.path.join(BUILD, "draft-*.json"))):
        shard = os.path.basename(path)[len("draft-"):-len(".json")]
        for q in json.load(open(path, encoding="utf-8"))["questions"]:
            qid = q["questionId"]
            if qid in ids:
                errors.append(f"{qid}: questionId 重复")
            ids.add(qid)
            if q["answerable"]:
                refs = q.get("referenceChunks") or []
                if len(refs) != 2:
                    errors.append(f"{qid}: 参考答案 {len(refs)} 段，要求恰 2 段")
                srcs = {by_id[r]["sourceId"] for r in refs if r in by_id}
                for r in refs:
                    c = by_id.get(r)
                    if not c:
                        errors.append(f"{qid}: 参考段 {r} 不存在于 chunks.jsonl")
                        continue
                    if r in cited:
                        errors.append(f"{qid}: 参考段 {r} 落在图谱引用集合内（泄漏风险）")
                    if r in used_ids or overlaps_used(c):
                        errors.append(f"{qid}: 参考段 {r} 与 dev/test 参考答案重叠（含原文区间重叠）")
                    if r in seen_refs:
                        errors.append(f"{qid}: 参考段 {r} 已被 {seen_refs[r]} 引用（题集内重复）")
                    seen_refs[r] = qid
                if len(srcs) > 1:
                    errors.append(f"{qid}: 两段跨来源 {sorted(srcs)}")
                if len(refs) == 2 and all(r in by_id for r in refs) and len(srcs) == 1:
                    src = next(iter(srcs))
                    gap = abs(order[src][refs[0]] - order[src][refs[1]])
                    if gap < MIN_GAP:
                        errors.append(f"{qid}: 两段原文间隔 {gap} < {MIN_GAP}（{refs[0]} / {refs[1]}）")
                ratios = [cjk_ratio(by_id[r]["text"]) for r in refs if r in by_id]
                q = dict(q)
                q["minRefCjkRatio"] = round(min(ratios), 4)
                q["meanRefCjkRatio"] = round(sum(ratios) / len(ratios), 4)
                # 与现有题集同一规则：minRefCjkRatio < 0.20 即 xl
                q["layer"] = "xl" if min(ratios) < 0.20 else "zh"
            else:
                if q.get("referenceChunks"):
                    errors.append(f"{qid}: 拒答题却带参考答案")
                if q.get("category") != "超范围":
                    warns.append(f"{qid}: 拒答题 category 为 {q.get('category')}")
            for k in ("gradingNotes",):
                if q["answerable"] and not q.get(k):
                    errors.append(f"{qid}: 缺 {k}")
            questions.append(q)

    ans = [q for q in questions if q["answerable"]]
    layers = Counter(q["layer"] for q in ans)
    cats = Counter(q["category"] for q in questions)
    print(f"片数 {len(glob.glob(os.path.join(BUILD, 'draft-*.json')))}"
          f" | 题数 {len(questions)}（可回答 {len(ans)} / 拒答 {len(questions)-len(ans)}）")
    print(f"  layer {dict(layers)}")
    print(f"  category {dict(cats)}")
    print(f"  参考答案段 {len(seen_refs)} 段（去重）")
    if errors:
        print(f"\n✗ 硬错误 {len(errors)} 条：")
        for e in errors[:40]:
            print("   -", e)
    else:
        print("\n✓ 全部硬规则通过（含与 dev/test 原文区间重叠、题集内重复引用）")
    for w in warns[:10]:
        print("  ⚠", w)

    if errors:
        print("\n结论：存在硬错误，未写出题集。")
        return 1
    if not args.check:
        doc = {
            "schema": "career-graph-questions/v1",
            "generatedAt": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
            "split": "holdout",
            "note": "新建留出集。参考答案段均已排除 dev/test 已用段（含原文区间重叠）、图谱引用集合；"
                    "layer 由 minRefCjkRatio<0.20 判定，与 dev/test 同规则。",
            "counts": {"answerable": len(ans), "refusal": len(questions) - len(ans),
                       "byLayer": dict(layers)},
            "questions": questions,
        }
        json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n→ {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
