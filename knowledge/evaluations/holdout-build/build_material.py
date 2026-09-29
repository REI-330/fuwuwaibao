#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
为新留出集准备选题材料：分片导出「可用候选段 + 全文」，供起草人逐段读原文出题。

为什么不能直接给整个 chunks.jsonl：1757 段全文约 260 万字符，谁也读不完。
所以按来源分片、按小节抽样，每片约 30–60 段，并**预先排掉三类不可能用的段**：

1. 已被 dev/test 当参考答案用过的段；
2. 落在图谱引用集合内的段（`validate_questionset.py` 的硬规则：会泄漏）；
3. **与已用段原文区间重叠的段**——语料是 ~1500 字重叠窗口，同一句话有多个 chunkId，
   不排掉就会挑出「同段正文的两个窗口」，看着合规其实内容重复。

输出：knowledge/evaluations/holdout-build/material-<shard>.json
      + 一个 material-index.txt 汇总每片的段数与层分布。

用法：
  python knowledge/evaluations/holdout-build/build_material.py
"""
import json
import os
import random
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
GRAPH = os.path.join(K, "graph", "graph.json")
EVAL = os.path.join(K, "evaluations")
OUT = os.path.join(EVAL, "holdout-build")

# 分片：片名 → (来源, 每来源最多几段, 跳过前面几段格点)
SHARDS = {
    "zh-s26-a": (["S26"], 30, 0),
    "zh-s26-b": (["S26"], 30, 30),
    "zh-other": (["S25", "S24", "S23", "S21", "S22", "S14", "S08", "S06", "S07", "S09", "S10", "S11"], 4, 0),
    "xl-onet": (["S16", "S17"], 14, 0),
    "xl-onet2": (["S17"], 14, 0),
    "xl-pytest": (["S13"], 30, 0),
    "xl-embedded": (["S02", "S03", "S04", "S05", "S12"], 8, 0),
    "xl-embedded2": (["S04", "S05", "S12"], 10, 0),
}


def cjk_ratio(text):
    if not text:
        return 0.0
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff") / len(text)


def main():
    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    by_id = {c["chunkId"]: c for c in chunks}

    cited = set()
    g = json.load(open(GRAPH, encoding="utf-8"))
    for n in g["nodes"]:
        cited.update(n.get("sourceRefs") or [])
    for e in g["edges"]:
        cited.update(e.get("sourceRefs") or [])

    used_ranges = defaultdict(list)
    for fn in ("questions-dev.json", "questions-test.json"):
        for q in json.load(open(os.path.join(EVAL, fn), encoding="utf-8"))["questions"]:
            for r in (q.get("referenceChunks") or []):
                c = by_id.get(r)
                if c:
                    used_ranges[c["sourceId"]].append(tuple(c["charRange"]))

    def overlaps_used(c):
        a, b = c["charRange"]
        return any(a < ub and ua < b for ua, ub in used_ranges.get(c["sourceId"], []))

    os.makedirs(OUT, exist_ok=True)
    index = []
    for name, (srcs, max_sec, skip_sec) in SHARDS.items():
        pool = [c for c in chunks if c["sourceId"] in srcs]
        pool = [c for c in pool if c["chunkId"] not in cited and not overlaps_used(c)]

        # 按**原文顺序**取两条交错的格点（步长 6、偏移 0 与 3）。这样同一来源内任意两段
        # 的位置差都是 3 的倍数且 ≥3，起草人怎么挑都不会违反「原文间隔 ≥3」。
        #
        # 之前按小节取 2 段是不行的：S26 的小节粒度极细（1207 个小节、多数只有 1 段），
        # 有 2 段的小节里那两段在原文里往往紧邻，导致 6 道题的顺序间隔只有 1–2。
        # （起草人当时按 candidates 数组下标核，数组是跨来源轮转出来的、不等于原文顺序，
        #   所以看着合规、实际违规——这是材料生成该背的锅，不是起草人的。）
        rows = []
        for src in srcs:
            lst = sorted([c for c in pool if c["sourceId"] == src],
                         key=lambda c: c["charRange"][0])
            lattice = [c for off in (0, 3) for i, c in enumerate(lst) if i % 6 == off]
            if skip_sec:
                lattice = lattice[skip_sec:]
            if max_sec and len(lattice) > max_sec:
                step = len(lattice) / max_sec      # 均匀抽稀到上限
                lattice = [lattice[int(i * step)] for i in range(max_sec)]
            for c in lattice:
                rows.append({
                    "chunkId": c["chunkId"], "sourceId": c["sourceId"],
                    "docIndex": lst.index(c),      # 该来源内按原文顺序的位置，间隔规则看它
                    "sectionPath": c.get("sectionPath") or c.get("heading") or "",
                    "cjkRatio": round(cjk_ratio(c.get("text") or ""), 4),
                    "length": len(c.get("text") or ""),
                    "charRange": c["charRange"],
                    "text": c.get("text") or "",
                })
        doc = {
            "shard": name, "sources": srcs,
            "note": "候选段均已排除：被 dev/test 引用过、在图谱引用集合内、与已用段原文区间重叠。"
                    "受格点抽样保证，同一 sourceId 内任意两段的 docIndex 差都是 3 的倍数且 ≥3"
                    "（起草时用 docIndex 差判断间隔，不要用数组下标）。",
            "count": len(rows), "candidates": rows,
        }
        p = os.path.join(OUT, f"material-{name}.json")
        json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        zh = sum(1 for r in rows if r["cjkRatio"] >= 0.20)
        bysrc = Counter(r["sourceId"] for r in rows)
        index.append(f"{name}: {len(rows)} 段（中文段 {zh} / 英文段 {len(rows)-zh}）"
                     f"  来源 {dict(bysrc)}")
        print(index[-1])

    open(os.path.join(OUT, "material-index.txt"), "w", encoding="utf-8").write(
        "\n".join(index) + "\n")
    print(f"\n→ {os.path.relpath(OUT, ROOT)}/material-*.json")


if __name__ == "__main__":
    random.seed(20260928)
    main()
