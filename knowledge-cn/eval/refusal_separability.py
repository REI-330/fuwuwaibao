#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""超范围题（拒答题）的分数分布**跨语料复核**：换一个语料库，结论还成立吗？

## 为什么

`检索质量评测-20261001.md` §十一 留白里写着「超范围题的分数分布只在本题集上量过，
没有做跨语料/跨库复核」。当时量到的现象是：**超范围题的得分与可答题的得分不可分**，
所以「检索层无法拒答」——但这只在现役那个 6 文档 / 2000+ chunk 的库上量过。

这个脚本换一个**小得多的语料**（临时库里只放一份文档）再量一遍：
如果两个语料上「可答题与超范围题的 top-1 分数区间都重叠」，那「分数阈值不可分」
就不是某一个库的偶然。

## 怎么算（可复算）

对每个库：分别跑可答题与超范围题，记 top-1 分数，报出各自的 min / 中位 / max，
并检查「是否存在一个阈值能把两类完全分开」。**没有**任何阈值 → 检索层不可拒答。

用法：

    python knowledge-cn/eval/refusal_separability.py \
        --kb aacc4889-... --scratch-kb <临时库 id> \
        --questions knowledge-cn/evaluations/questions-cn-v1.json \
        --out knowledge-cn/evidence/refusal-separability.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from weknora_client import WeKnora  # noqa: E402


def top1_score(hits: List[Dict[str, Any]]) -> float:
    if not hits:
        return 0.0
    return float(hits[0].get("score") or 0.0)


def stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": round(min(values), 4),
        "median": round(statistics.median(values), 4),
        "max": round(max(values), 4),
    }


def separable(answerable: List[float], refusal: List[float]) -> Dict[str, Any]:
    """有没有一个阈值能把两类完全分开（答案：没有才是我们要证明的）。"""
    if not answerable or not refusal:
        return {"separable": None, "reason": "样本不足"}
    if min(answerable) > max(refusal):
        return {"separable": True, "thresholdBetween": [max(refusal), min(answerable)]}
    if max(answerable) < min(refusal):
        return {"separable": True, "thresholdBetween": [max(answerable), min(refusal)]}
    return {"separable": False, "overlapLow": round(min(max(answerable), max(refusal)), 4),
            "overlapHigh": round(max(min(answerable), min(refusal)), 4)}


def measure(wk: WeKnora, kb: str, questions: List[Dict[str, Any]], top: int) -> Dict[str, Any]:
    answerable, refusal, rows = [], [], []
    for question in questions:
        hits = wk.search(kb, question["question"], top=top)
        score = top1_score(hits)
        (answerable if question.get("answerable") else refusal).append(score)
        rows.append({"questionId": question["questionId"], "answerable": bool(question.get("answerable")),
                     "top1Score": round(score, 4), "top1Id": str(hits[0].get("id")) if hits else None})
    return {"kb": kb, "answerable": stats(answerable), "refusal": stats(refusal),
            "separation": separable(answerable, refusal), "perQuestion": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kb", required=True, help="现役库（可答题 + 超范围题都在这个题集里）")
    parser.add_argument("--scratch-kb", help="另一个语料库（例如只放一份文档的临时库）")
    parser.add_argument("--questions", required=True)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--out")
    args = parser.parse_args()

    wk = WeKnora()
    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    corpora = [{"label": "现役库", "kb": args.kb}]
    if args.scratch_kb:
        corpora.append({"label": "跨语料（临时库）", "kb": args.scratch_kb})

    report: Dict[str, Any] = {"questions": args.questions, "top": args.top, "corpora": []}
    for corpus in corpora:
        measured = measure(wk, corpus["kb"], questions, args.top)
        measured["label"] = corpus["label"]
        report["corpora"].append(measured)
        print(f"[{corpus['label']}] 可答 {measured['answerable']}")
        print(f"[{corpus['label']}] 超范围 {measured['refusal']}")
        print(f"[{corpus['label']}] 可分? {measured['separation']}")

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
