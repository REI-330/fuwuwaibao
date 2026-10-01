#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对中国官方语料知识库跑检索质量评测（只量检索，不调模型）。

## 与旧 24 题评测的区别

`knowledge/evaluations/report.md` 那份 24 题面向自建职业图谱（63 节点 / 226 边 /
1757 段 O*NET+国标语料），本题集面向 2026 年重建的中国官方语料（1519 条记录 /
2167 chunk / 4 类来源）。两套语料不重叠，**数字不能互相引用**。

## 判定口径

- **gold 用锚点字符串**，不用 chunkId。512 字分块会把同一条记录切进不同 chunk，
  chunkId 口径会系统性误判；锚点在分块和重复导入下都稳定。
- **命中@k**：前 k 条里，每个 gold 组都能找到一条同时含该组全部锚点的 chunk。
  多组题必须组组到齐才算命中——「只召回一半证据」不算答对。
- **证据齐位次**：最后一组被覆盖时的排名，MRR 取它的倒数。
- **preview@1**：第 1 条 chunk 的前 120 字里是否含全部锚点。这正是
  `verify_import.py` 的 `topPreview` 口径——「低空经济与管理」那次误判就出在这里：
  chunk 装了 3 条记录，答案在 chunk 中部，预览显示的是邻座的「经济工程」。
- **超范围题不做命中判定**：检索接口没有拒答能力，这里只量「分数是否与可答题可分」，
  并在报告里写明拒答判定属于上层 agent，不在检索层断言。

用法：

    python knowledge-cn/eval/retrieval_quality.py \
        --kb aacc4889-a347-44ea-9dd1-b60ec1917cbb \
        --questions knowledge-cn/evaluations/questions-cn-v1.json \
        --top 10 --out knowledge-cn/evidence/retrieval-quality-20261001.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from weknora_client import WeKnora  # noqa: E402

PREVIEW_CHARS = 120
MODES = {
    "hybrid": {},
    "vectorOnly": {"vector_only": True},
    "keywordOnly": {"keyword_only": True},
}


def head_record_id(text):
    marker = "记录 ID："
    start = text.find(marker)
    if start < 0:
        return None
    end = start + len(marker)
    while end < len(text) and (text[end].isalnum() or text[end] in "-_"):
        end += 1
    return text[start + len(marker):end]


def evaluate_question(question, hits):
    """返回单题结果：每组证据的到达位次、命中@k、preview@1。

    `judgment: true` 的 gold 组是**作者裁定的跨源关联**，不是语料里的直接事实，
    因此不进头部指标，单独统计——否则「我自己的判断没被召回」会被算成检索的错。
    """
    groups = question.get("gold") or []
    details = []
    for group in groups:
        anchors = group["anchors"]
        first_rank = None
        first_chunk = None
        for rank, hit in enumerate(hits, 1):
            text = hit.get("content") or ""
            if all(anchor in text for anchor in anchors):
                first_rank, first_chunk = rank, hit
                break
        details.append({
            "name": group["name"],
            "anchors": anchors,
            "judgment": bool(group.get("judgment")),
            "firstRank": first_rank,
            "chunkId": first_chunk.get("id") if first_chunk else None,
        })

    strict = [d for d in details if not d["judgment"]]
    judgment = [d for d in details if d["judgment"]]
    strict_ranks = [d["firstRank"] for d in strict]
    evidence_rank = (max(strict_ranks)
                     if strict_ranks and all(rank for rank in strict_ranks) else None)
    judgment_rank = (max([d["firstRank"] for d in judgment])
                     if judgment and all(d["firstRank"] for d in judgment) else None)

    top1_text = (hits[0].get("content") or "") if hits else ""
    groups_covered_at_top1 = sum(
        1 for group in groups
        if all(anchor in top1_text for anchor in group["anchors"]))
    top1_covered = bool(strict) and all(
        all(anchor in top1_text for anchor in group["anchors"]) for group in strict)
    preview_ok = (top1_covered and all(
        top1_text.find(anchor) < PREVIEW_CHARS
        for group in strict for anchor in group["anchors"])) if len(strict) == 1 else None

    return {
        "questionId": question["questionId"],
        "category": question["category"],
        "answerable": question["answerable"],
        "groupCount": len(strict),
        "judgmentGroupCount": len(judgment),
        "evidenceRank": evidence_rank,
        "judgmentEvidenceRank": judgment_rank,
        "groups": details,
        "top1Score": hits[0].get("score") if hits else None,
        "top1ChunkId": hits[0].get("id") if hits else None,
        "top1HeadRecordId": head_record_id(top1_text) if hits else None,
        "groupsCoveredAtTop1": groups_covered_at_top1,
        "top1CoversAllGroups": top1_covered,
        "previewVisibleAt1": preview_ok,
    }


def summarise(results, top):
    answerable = [r for r in results if r["answerable"] and r["groupCount"] > 0]
    skipped = [r for r in results if r["answerable"] and r["groupCount"] == 0]
    refusal = [r for r in results if not r["answerable"]]
    summary = {"answerable": len(answerable), "refusal": len(refusal),
               "skippedAllJudgment": [r["questionId"] for r in skipped]}
    for k in (1, 3, 5, 10):
        if k > top:
            continue
        hits = sum(1 for r in answerable if r["evidenceRank"] and r["evidenceRank"] <= k)
        summary[f"hit@{k}"] = {"hits": hits, "total": len(answerable),
                               "rate": round(hits / len(answerable), 4) if answerable else None}
    mrr_values = [1 / r["evidenceRank"] for r in answerable if r["evidenceRank"]]
    summary["mrr"] = round(sum(mrr_values) / len(answerable), 4) if answerable else None
    single = [r for r in answerable if r["groupCount"] == 1]
    preview = [r for r in single if r["previewVisibleAt1"]]
    summary["preview@1"] = {"hits": len(preview), "total": len(single),
                            "rate": round(len(preview) / len(single), 4) if single else None}
    # 单组题才谈得上「top-1 就是答案那一块」；多组题结构上不可能一条 chunk 装下全部证据
    top1 = [r for r in single if r["top1CoversAllGroups"]]
    summary["top1IsTarget"] = {"hits": len(top1), "total": len(single),
                               "rate": round(len(top1) / len(single), 4) if single else None}
    multi = [r for r in answerable if r["groupCount"] > 1]
    if multi:
        summary["multiGroup"] = {
            "total": len(multi),
            "evidenceRanks": {r["questionId"]: r["evidenceRank"] for r in multi},
            "allEvidenceWithin5": sum(1 for r in multi
                                      if r["evidenceRank"] and r["evidenceRank"] <= 5),
        }
    judgment = [r for r in results if r["judgmentGroupCount"] > 0]
    if judgment:
        summary["judgmentGroups"] = {
            "questions": [r["questionId"] for r in judgment],
            "coveredWithinTop": sum(1 for r in judgment
                                    if r["judgmentEvidenceRank"]
                                    and r["judgmentEvidenceRank"] <= top),
            "total": len(judgment),
            "note": "作者裁定的跨源关联，不计入头部命中率",
        }
    if refusal:
        scores = [r["top1Score"] for r in refusal if r["top1Score"] is not None]
        answerable_scores = [r["top1Score"] for r in answerable if r["top1Score"] is not None]
        summary["refusalTop1Score"] = {
            "min": min(scores), "median": statistics.median(scores), "max": max(scores)
        } if scores else None
        if answerable_scores:
            summary["answerableTop1Score"] = {
                "min": min(answerable_scores),
                "p25": statistics.quantiles(answerable_scores, n=4)[0] if len(answerable_scores) > 3 else min(answerable_scores),
                "median": statistics.median(answerable_scores),
                "max": max(answerable_scores),
            }
            summary["naiveThresholdMisfires"] = sum(
                1 for score in scores
                if score >= (statistics.quantiles(answerable_scores, n=4)[0]
                             if len(answerable_scores) > 3 else min(answerable_scores)))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kb", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--modes", default="hybrid,vectorOnly,keywordOnly",
                        help="要跑的通道：hybrid / vectorOnly / keywordOnly")
    parser.add_argument("--out")
    args = parser.parse_args()

    document = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    question_ids = [q["questionId"] for q in document["questions"]]
    if len(set(question_ids)) != len(question_ids):
        raise SystemExit("题集里有重复的 questionId")

    wk = WeKnora()
    report = {
        "questionsFile": args.questions,
        "kb": document.get("kb", {}),
        "top": args.top,
        "previewChars": PREVIEW_CHARS,
        "judging": document.get("judging"),
        "modes": {},
    }
    for mode in [m.strip() for m in args.modes.split(",") if m.strip()]:
        started = time.time()
        results = []
        for question in document["questions"]:
            hits = wk.search(args.kb, question["question"], top=args.top, **MODES[mode])
            results.append(evaluate_question(question, hits))
        report["modes"][mode] = {
            "elapsedSeconds": round(time.time() - started, 1),
            "summary": summarise(results, args.top),
            "perQuestion": results,
        }
        summary = report["modes"][mode]["summary"]
        print(f"[{mode}] 命中@1 {summary.get('hit@1')} / @{args.top} {summary.get(f'hit@{args.top}')} "
              f"MRR {summary['mrr']} preview@1 {summary.get('preview@1')}")

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print("证据写入", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
