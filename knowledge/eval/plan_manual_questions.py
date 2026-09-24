#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
按配额挑出「待人工写题」的段对，并把两段原文摘录导成一份可读清单。

用途：批量出题受端点限流拖慢时，改由人工读原文写题（这也是 EVAL_SET_DESIGN.md
原本规定的方法）。本脚本只负责**选段**，不写题面——题面由人读摘录后手写，
避免机器生成的低质量题混进验收集。

用法：
  python knowledge/eval/plan_manual_questions.py --quota test:嵌入式开发=3,test:测试自动化=7 ... \
      --exclude _collected-dev.json,_collected-test.json \
      --out knowledge/evaluations/_manual-plan.md
"""
import argparse
import itertools
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
EV = os.path.join(K, "evaluations")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
POOL = os.path.join(EV, "candidate_pool.jsonl")

sys.path.insert(0, os.path.join(K, "eval"))
from author_questions_batch import (CATEGORY_OF, CATEGORY_MIN_RICHNESS, QUOTA,  # noqa: E402
                                    clean_for_prompt, richness)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quota", required=True,
                    help="形如 test:嵌入式开发=3,test:测试自动化=7,dev:AI框架与推理=1")
    ap.add_argument("--exclude", default="", help="逗号分隔的题集文件，其参考段不再使用")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-gap", type=int, default=3)
    ap.add_argument("--min-richness", type=float, default=0.8)
    ap.add_argument("--excerpt", type=int, default=700)
    args = ap.parse_args()

    chunks = {json.loads(l)["chunkId"]: json.loads(l)
              for l in open(CHUNKS, encoding="utf-8") if l.strip()}
    pool = [json.loads(l) for l in open(POOL, encoding="utf-8") if l.strip()]
    pool = [r for r in pool if not r["usedByCurrentSet"]]
    rich = {r["chunkId"]: richness(chunks[r["chunkId"]]["text"]) for r in pool}

    used = set()
    for path in [x for x in args.exclude.split(",") if x.strip()]:
        if os.path.exists(path):
            doc = json.load(open(path, encoding="utf-8"))
            for q in doc["questions"]:
                used.update(q.get("referenceChunks") or [])
    print(f"已排除 {len(used)} 段（来自 --exclude）")

    by_source = defaultdict(list)
    for r in pool:
        by_source[r["sourceId"]].append(r)
    for s in by_source:
        by_source[s].sort(key=lambda r: r["pos"])

    want = defaultdict(int)
    for item in args.quota.split(","):
        split, rest = item.split(":", 1)
        cat, n = rest.split("=")
        want[(split.strip(), cat.strip())] += int(n)

    lines = ["# 待人工写题的段对清单", "",
             "规则：每题 2 段、同来源、原文间隔 ≥ 3、不得使用已排除的段；",
             "题面要像真实用户会问的问题，且不得出现与原文连续相同 12 字以上的片段。", ""]
    picks = []
    for (split, cat), n in want.items():
        th = CATEGORY_MIN_RICHNESS.get(cat, args.min_richness)
        cands = []
        for s, c in CATEGORY_OF.items():
            if c != cat:
                continue
            rows = [r for r in by_source.get(s, [])
                    if rich[r["chunkId"]] >= th and r["chunkId"] not in used]
            for a, b in itertools.combinations(rows, 2):
                if abs(a["pos"] - b["pos"]) < args.min_gap:
                    continue
                if a["chunkId"] in used or b["chunkId"] in used:
                    continue
                same = a["sectionPath"] == b["sectionPath"]
                cands.append(((0 if same else 1, -abs(a["pos"] - b["pos"]),
                               -(rich[a["chunkId"]] + rich[b["chunkId"]]),
                               a["chunkId"], b["chunkId"])))
        cands.sort()
        taken = 0
        for _, _, _, a, b in cands:
            if taken >= n:
                break
            if a in used or b in used:
                continue
            picks.append((split, cat, a, b))
            used.update((a, b))
            taken += 1
        if taken < n:
            print(f"  ⚠ {split}/{cat} 只够 {taken} 题（需求 {n}）")

    for i, (split, cat, a, b) in enumerate(picks, 1):
        lines += [f"## {i:02d}. [{split}] {cat}  —— 参考段 `{a}` + `{b}`", "",
                  f"- 来源：{chunks[a]['sourceId']}；间隔："
                  f"{abs([r for r in pool if r['chunkId'] == a][0]['pos'] - [r for r in pool if r['chunkId'] == b][0]['pos'])}",
                  f"- A 章节：{chunks[a].get('sectionPath') or '（无）'}",
                  "", "```", clean_for_prompt(chunks[a]["text"], args.excerpt), "```",
                  f"- B 章节：{chunks[b].get('sectionPath') or '（无）'}",
                  "", "```", clean_for_prompt(chunks[b]["text"], args.excerpt), "```", ""]

    open(args.out, "w", encoding="utf-8").write("\n".join(lines))
    print(f"共 {len(picks)} 题待写 → {os.path.relpath(args.out, ROOT)}")
    json.dump([{"split": s, "category": c, "a": a, "b": b} for s, c, a, b in picks],
              open(os.path.join(EV, "_manual-picks.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
