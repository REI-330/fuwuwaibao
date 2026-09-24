#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把拒答题并入 dev / test 题集（幂等：已并入则不重复加）。

用法：python knowledge/eval/merge_refusal_set.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
EV = os.path.join(K, "evaluations")
REFUSAL = os.path.join(EV, "questions-refusal.json")


def main():
    ref = json.load(open(REFUSAL, encoding="utf-8"))["questions"]
    by_split = {"dev": [], "test": []}
    for q in ref:
        by_split[q["split"]].append(q)

    for split, path in (("dev", os.path.join(EV, "questions-dev.json")),
                        ("test", os.path.join(EV, "questions-test.json"))):
        doc = json.load(open(path, encoding="utf-8"))
        have = {q["questionId"] for q in doc["questions"]}
        add = [q for q in by_split[split] if q["questionId"] not in have]
        if not add:
            print(f"  {split}: 拒答题已并入（{sum(1 for q in doc['questions'] if not q.get('answerable', True))} 道），跳过")
            continue
        doc["questions"] += add
        ans = sum(1 for q in doc["questions"] if q.get("answerable", True))
        ref_total = sum(len(q.get("referenceChunks") or []) for q in doc["questions"])
        doc["counts"] = {"total": len(doc["questions"]), "answerable": ans,
                         "outOfScope": len(doc["questions"]) - ans,
                         "referenceChunksTotal": ref_total}
        doc["refusalMergedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        doc["refusalSource"] = "evaluations/questions-refusal.json"
        json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  {split}: 并入 {len(add)} 道拒答题 → {doc['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
