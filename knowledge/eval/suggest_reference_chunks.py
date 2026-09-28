#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
挑参考答案候选：把「哪一段可以当参考答案」从"看着像"变成"过得了校验器"。

为什么需要它：`标注修正提案-20260927.md` 是读原文写出来的，语义都对，但推荐的替代段
一跑 `validate_questionset.py` 就报 4 个问题（2 段落在图谱引用集合里 → 泄漏风险；
2 段与 dev 集重叠 → test 不再干净）。人工读原文时看不到这些约束，所以必须用工具筛。

对给定小节，逐段标出它是否可用，并打印摘录：

  python knowledge/eval/suggest_reference_chunks.py --source S02 --section "GPIO 矩阵"
  python knowledge/eval/suggest_reference_chunks.py --source S04 --section "IO 管脚配置"
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
GRAPH = os.path.join(K, "graph", "graph.json")
EVAL = os.path.join(K, "evaluations")


def load_cited():
    g = json.load(open(GRAPH, encoding="utf-8"))
    cited = set()
    for n in g["nodes"]:
        cited.update(n.get("sourceRefs") or [])
    for e in g["edges"]:
        cited.update(e.get("sourceRefs") or [])
    return cited


def load_used(sets):
    used = {}
    for fn in sets:
        p = os.path.join(EVAL, fn)
        if not os.path.exists(p):
            continue
        for q in json.load(open(p, encoding="utf-8"))["questions"]:
            for r in (q.get("referenceChunks") or []):
                used.setdefault(r, []).append(f"{fn}:{q['questionId']}")
    return used


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="来源 id，如 S02")
    ap.add_argument("--section", default="", help="sectionPath 子串过滤（可选）")
    ap.add_argument("--exclude", default="questions-dev.json,questions-test.json",
                    help="逗号分隔：把哪些题集里已用的段标为不可用")
    ap.add_argument("--max-len", type=int, default=180, help="每段摘录字符数")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    rows = [c for c in chunks if c["sourceId"] == args.source]
    rows.sort(key=lambda c: c["charRange"][0])
    if args.section:
        rows = [c for c in rows if args.section in (c.get("sectionPath") or "")]
    cited = load_cited()
    used = load_used([x.strip() for x in args.exclude.split(",") if x.strip()])

    print(f"{args.source}"
          f"{' > ' + args.section if args.section else ''}：{len(rows)} 段"
          f"（图谱引用集合 {len(cited)} 段；已用段见括号）\n")
    pos = {c["chunkId"]: i for i, c in enumerate(rows)}
    for c in rows:
        cid = c["chunkId"]
        flags = []
        if cid in cited:
            flags.append("✗图谱引用(泄漏风险)")
        if cid in used:
            flags.append("✗已被引用 " + ",".join(used[cid]))
        mark = "可用" if not flags else "  "
        text = " ".join((c.get("text") or "").split())
        print(f"[{mark}] #{pos[cid]:3d} {cid:18s} len={len(text):5d} {' '.join(flags)}")
        print(f"       {text[:args.max_len]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
