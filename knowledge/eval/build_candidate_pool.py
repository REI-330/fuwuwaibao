#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
按现行选题规则，把语料里「还能出题」的段落清点出来，并核算题量上限。

为什么需要：
  题集要扩到 dev/test 两集，但题量不是想定多少就定多少——每题要 2 段参考答案，
  而参考答案必须满足「未被图谱引用」（否则任何用图策略都会被泄漏抬高）。
  所以先算出硬上限，再定配额，避免定出一个做不到的题量。

选题规则（与 questions-v2.json 的 selectionRule 一致）：
  1) 未被 graph.json 的 nodes/edges sourceRefs 引用
  2) 文本长度 >= --min-chars（默认 300）
  3) 含中文（S16/S17 是英文 O*NET 数据，本版规则不含）
  4) 每题 2 段参考答案，同来源、且在原文里不相邻（--min-gap 控制最小间隔）

「不相邻」为什么是硬规则：v2 题集实测 7/14 题的两段答案在原文直接相邻，
把邻域/窗口类技术的增益结构性放大了（见 检索命中率优化实验.md §3.2）。

用法：
  python knowledge/eval/build_candidate_pool.py
  python knowledge/eval/build_candidate_pool.py --min-gap 3 --min-chars 300
"""
import argparse
import json
import os
import re
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
GRAPH = os.path.join(K, "graph", "graph.json")
QUESTIONS = os.path.join(K, "evaluations", "questions-v2.json")
OUT_DIR = os.path.join(K, "evaluations")
OUT_POOL = os.path.join(OUT_DIR, "candidate_pool.jsonl")
OUT_SUMMARY = os.path.join(K, "eval", "runs", "candidate-pool-summary.json")

CJK = re.compile(r"[\u4e00-\u9fff]")


def max_questions(n_positions, min_gap):
    """同一来源内最多能配出多少对「间隔 >= min_gap」的段。
    取上/下半区对配（i 与 i+half），这是最大化配对数且间隔最大的简单构造。"""
    if n_positions < 2:
        return 0
    half = (n_positions + 1) // 2
    if half < min_gap:
        return 0
    return n_positions - half


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-chars", type=int, default=300)
    ap.add_argument("--min-gap", type=int, default=2,
                    help="同来源两段答案在原文里的最小间隔（2 = 不允许直接相邻）")
    ap.add_argument("--cap-report", type=int, default=3, help="另报一组更严的间隔上限")
    ap.add_argument("--lang", default="cjk", choices=["cjk", "any"],
                    help="cjk=只收含中文的段（现行规则）；any=连英文段一起收（S16/S17 是 O*NET 英文）")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    by_id = {c["chunkId"]: c for c in chunks}

    # 图谱引用集合：节点与边的 sourceRefs 都算
    g = json.load(open(GRAPH, encoding="utf-8"))
    cited = set()
    for n in g["nodes"]:
        cited.update(n.get("sourceRefs") or [])
    for e in g["edges"]:
        cited.update(e.get("sourceRefs") or [])
    cited &= set(by_id)

    # 现役题集占用的段落：这些段已被 v2 题集用过，算「已占用」，但不妨碍它同时出现在池子里
    qdoc = json.load(open(QUESTIONS, encoding="utf-8"))
    used = set()
    for q in qdoc.get("questions", []):
        used.update(q.get("referenceChunks") or [])
    used &= set(by_id)

    # 同来源按 charRange 排序 → 位置索引
    by_source = defaultdict(list)
    for c in chunks:
        by_source[c["sourceId"]].append(c)
    for src in by_source:
        by_source[src].sort(key=lambda c: c["charRange"][0])
    pos_of = {}
    for src, rows in by_source.items():
        for i, c in enumerate(rows):
            pos_of[c["chunkId"]] = i

    def usable(c):
        if c["chunkId"] in cited or len(c["text"]) < args.min_chars:
            return False
        return True if args.lang == "any" else CJK.search(c["text"]) is not None

    pool = [c for c in chunks if usable(c)]
    free = [c for c in pool if c["chunkId"] not in used]

    # 写候选池清单（一行为一段，含人工写题需要的字段）
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_POOL, "w", encoding="utf-8") as f:
        for c in sorted(pool, key=lambda c: (c["sourceId"], pos_of[c["chunkId"]])):
            f.write(json.dumps({
                "chunkId": c["chunkId"], "sourceId": c["sourceId"],
                "pos": pos_of[c["chunkId"]],
                "sectionPath": c.get("sectionPath") or "",
                "heading": c.get("heading") or "",
                "chars": len(c["text"]),
                "charRange": c["charRange"],
                "usedByCurrentSet": c["chunkId"] in used,
                "prevId": (by_source[c["sourceId"]][pos_of[c["chunkId"]] - 1]["chunkId"]
                           if pos_of[c["chunkId"]] > 0 else None),
                "nextId": (by_source[c["sourceId"]][pos_of[c["chunkId"]] + 1]["chunkId"]
                           if pos_of[c["chunkId"]] + 1 < len(by_source[c["sourceId"]]) else None),
            }, ensure_ascii=False) + "\n")

    # 容量核算：逐来源算「剩余可用段能配出多少题」
    per_source = []
    total_cap, total_free = 0, 0
    for src in sorted(by_source):
        free_rows = [c for c in by_source[src] if c["chunkId"] not in used and usable(c)]
        cap = max_questions(len(free_rows), args.min_gap)
        total_cap += cap
        total_free += len(free_rows)
        per_source.append({
            "sourceId": src,
            "chunksTotal": len(by_source[src]),
            "poolUsable": sum(1 for c in by_source[src] if usable(c)),
            "reservedByCurrentSet": sum(1 for c in by_source[src] if c["chunkId"] in used),
            "freeUsable": len(free_rows),
            "maxQuestions": cap,
        })

    summary = {
        "schema": "career-graph-candidate-pool/v1",
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rules": {"minChars": args.min_chars, "minGap": args.min_gap, "lang": args.lang,
                  "cited": "未被 graph.json 的 nodes/edges sourceRefs 引用",
                  "cjk": "只收含中文段" if args.lang == "cjk" else "中英文段都收"},
        "totals": {
            "chunksAll": len(chunks),
            "citedChunks": len(cited),
            "poolUsable": len(pool),
            "reservedByCurrentSet": len([c for c in chunks if c["chunkId"] in used]),
            "freeUsable": len(free),
            "maxQuestionsFromFree": total_cap,
            "maxQuestionsPlusCurrent14": total_cap + len(qdoc.get("questions", [])),
        },
        "perSource": per_source,
    }
    os.makedirs(os.path.dirname(OUT_SUMMARY), exist_ok=True)
    json.dump(summary, open(OUT_SUMMARY, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"=== 候选池核算（规则：未被引用 / >= {args.min_chars} 字符 / 含中文）===")
    print(f"  语料 {len(chunks)} 段；被图谱引用 {len(cited)} 段")
    print(f"  合格候选 {len(pool)} 段，其中已被现役 14 题占用 {len(used)} 段 → 剩余可用 {len(free)} 段")
    print(f"\n  {'来源':6s} {'总段':>5s} {'合格':>5s} {'已占用':>6s} {'剩余':>5s} {'可出题(间隔>=' + str(args.min_gap) + ')':>16s}")
    for r in per_source:
        print(f"  {r['sourceId']:6s} {r['chunksTotal']:>5d} {r['poolUsable']:>5d}"
              f" {r['reservedByCurrentSet']:>6d} {r['freeUsable']:>5d} {r['maxQuestions']:>16d}")
    print(f"\n  剩余可用段 {total_free} → 最多再出 {total_cap} 题"
          f"；加上现役 14 题 = {total_cap + len(qdoc.get('questions', []))} 题")

    for gap in range(2, args.min_gap + args.cap_report):
        cap = sum(max_questions(r["freeUsable"], gap) for r in per_source)
        print(f"  若要求两段答案间隔 >= {gap}：最多再出 {cap} 题"
              f"（合计 {cap + len(qdoc.get('questions', []))} 题）")

    print(f"\n产物：{os.path.relpath(OUT_POOL, ROOT)}（逐段清单，供人工写题）")
    print(f"      {os.path.relpath(OUT_SUMMARY, ROOT)}（核算结果）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
