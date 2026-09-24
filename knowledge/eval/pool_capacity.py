#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
候选池容量核算（按「当前有效题集」的真实占用口径）。

为什么另写一个：`build_candidate_pool.py` 里写死的「已占用」只统计
`questions-v2.json`（那 14 段历史题），不包括后来产出的
`questions-dev.json` / `questions-test.json`。扩题时若沿用它，会把
已经用掉的段落算成可用，配额定得出来但实际配不出段。

本脚本的「已占用」= dev 集 ∪ test 集 ∪ v2 集的全部参考答案段。

规则（与 validate_questionset.py 的硬规则对齐）：
  1) 未被 graph.json 的 nodes/edges sourceRefs 引用
  2) 文本长度 >= --min-chars
  3) --lang cjk 时只收含中文的段；any 时中英文都收
  4) 每题 2 段参考答案、同来源、原文间隔 >= --min-gap
  5) 两段不同时被上面的「已占用」集合占用

用法：
  python knowledge/eval/pool_capacity.py                      # 中文段，间隔 >= 3
  python knowledge/eval/pool_capacity.py --lang any           # 中英文都收
  python knowledge/eval/pool_capacity.py --json out.json      # 另存机读结果
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
EV = os.path.join(K, "evaluations")

CATEGORY_OF = {
    "S01": "嵌入式开发", "S02": "嵌入式开发", "S03": "嵌入式开发", "S04": "嵌入式开发",
    "S05": "AI框架与推理", "S06": "AI框架与推理", "S07": "AI框架与推理",
    "S08": "计算机视觉", "S09": "计算机视觉", "S10": "计算机视觉",
    "S11": "测试自动化", "S12": "测试自动化", "S13": "测试自动化",
    "S14": "测试自动化", "S15": "测试自动化",
    "S16": "职业能力", "S17": "职业能力",
    "S18": "标准与政策", "S19": "标准与政策", "S20": "标准与政策",
    "S21": "标准与政策", "S22": "标准与政策",
}
CJK = re.compile(r"[\u4e00-\u9fff]")


def max_pairs(n_positions, min_gap):
    """同一来源内最多能配出多少对「间隔 >= min_gap」的段（上下半区对配，最大化配对数）。"""
    if n_positions < 2:
        return 0
    half = (n_positions + 1) // 2
    return n_positions - half if half >= min_gap else 0


def cjk_ratio(text):
    stripped = re.sub(r"\s", "", text)
    if not stripped:
        return 0.0
    return len(CJK.findall(stripped)) / len(stripped)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-chars", type=int, default=300)
    ap.add_argument("--min-gap", type=int, default=3)
    ap.add_argument("--lang", default="cjk", choices=["cjk", "any"])
    ap.add_argument("--sets", default="questions-dev.json,questions-test.json,questions-v2.json",
                    help="逗号分隔，被这些题集占用的段视为「已占用」")
    ap.add_argument("--json", help="把结果另存为 JSON")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in open(os.path.join(K, "chunks", "chunks.jsonl"),
                                          encoding="utf-8") if l.strip()]
    by_id = {c["chunkId"]: c for c in chunks}

    graph = json.load(open(os.path.join(K, "graph", "graph.json"), encoding="utf-8"))
    cited = set()
    for n in graph["nodes"]:
        cited.update(n.get("sourceRefs") or [])
    for e in graph["edges"]:
        cited.update(e.get("sourceRefs") or [])
    cited = cited.intersection(set(by_id))

    used = set()
    per_set = {}
    for name in [x.strip() for x in args.sets.split(",") if x.strip()]:
        path = os.path.join(EV, name)
        if not os.path.exists(path):
            print(f"  （缺 {name}，跳过）")
            continue
        refs = set()
        for q in json.load(open(path, encoding="utf-8")).get("questions", []):
            refs.update(q.get("referenceChunks") or [])
        per_set[name] = len(refs.intersection(set(by_id)))
        used.update(refs)
    used = used.intersection(set(by_id))

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
        return True if args.lang == "any" else bool(CJK.search(c["text"]))

    print(f"=== 容量核算（lang={args.lang} / >= {args.min_chars} 字符 / 间隔 >= {args.min_gap}）===")
    print(f"  语料 {len(chunks)} 段；图谱引用 {len(cited)} 段；"
          f"题集已占用 {len(used)} 段 {per_set}")

    per_cat = defaultdict(lambda: {"free": 0, "maxQ": 0, "srcs": {}})
    per_src = []
    total_free = total_cap = 0
    for src in sorted(by_source):
        rows = by_source[src]
        free_rows = [c for c in rows if c["chunkId"] not in used and usable(c)]
        cap = max_pairs(len(free_rows), args.min_gap)
        cat = CATEGORY_OF.get(src, "?")
        per_cat[cat]["free"] += len(free_rows)
        per_cat[cat]["maxQ"] += cap
        if free_rows:
            per_cat[cat]["srcs"][src] = len(free_rows)
        total_free += len(free_rows)
        total_cap += cap
        per_src.append({"sourceId": src, "category": cat, "freeUsable": len(free_rows),
                        "maxQuestions": cap,
                        "freeIds": [c["chunkId"] for c in free_rows]})

    print(f"\n  {'类目':14s} {'剩余可用段':>10s} {'可出题':>8s}   来源分解")
    for cat, v in sorted(per_cat.items(), key=lambda x: -x[1]["maxQ"]):
        print(f"  {cat:14s} {v['free']:>10d} {v['maxQ']:>8d}   {v['srcs']}")
    print(f"\n  合计：剩余可用段 {total_free} → 最多再出 {total_cap} 题")

    result = {"lang": args.lang, "minChars": args.min_chars, "minGap": args.min_gap,
              "cited": len(cited), "usedBySets": per_set, "totalFree": total_free,
              "maxQuestions": total_cap,
              "perCategory": {k: {"freeUsable": v["free"], "maxQuestions": v["maxQ"],
                                  "sources": v["srcs"]} for k, v in per_cat.items()},
              "perSource": per_src}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"  机读结果 → {os.path.relpath(args.json, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
