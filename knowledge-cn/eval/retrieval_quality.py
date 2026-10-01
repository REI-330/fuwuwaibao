#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对中国官方语料知识库跑检索质量评测（只量检索，不调模型）。

## 与旧 24 题评测的区别

`knowledge/evaluations/report.md` 那份 24 题面向自建职业图谱（63 节点 / 226 边 /
1757 段 O*NET+国标语料），本题集面向 2026 年重建的中国官方语料（1519 条记录 /
4 类来源）。两套语料不重叠，**数字不能互相引用**。

## 判定口径（两个都报，别只看一个）

- **块内口径**（严）：前 k 条里存在**一条** chunk 同时含该 gold 组的全部锚点。
  这是「一条上下文就自足」的标准，但会被切块边界左右：目录页那种「代码一段、
  名称另一段」的排版，代码和名称之间隔着 300+ 字，边界一动就从命中变不命中。
- **窗口内口径**（宽）：前 k 条**合起来**含该组全部锚点。这才是 RAG 的真实形态
  ——回答层拿到的是 k 条上下文，证据分在两块里也照样能答。
- 两个口径都要求**每组证据到齐**（多组题不许只召回一半）；位次取「最后一组到齐」
  的排名，MRR 取它的倒数。
- **preview@1**：第 1 条 chunk 的前 120 字里是否含全部锚点 —— 这就是
  `verify_import.py` 打印 `topPreview` 的口径。
- **judgment 组**：作者裁定的跨源关联，不是语料里的直接事实，不进头部指标。
- **超范围题**不做命中判定，只量分数分布（检索层没有拒答能力）。

用法：

    python knowledge-cn/eval/retrieval_quality.py \
        --kb aacc4889-a347-44ea-9dd1-b60ec1917cbb \
        --questions knowledge-cn/evaluations/questions-cn-v1.json \
        --top 10 --out knowledge-cn/evidence/retrieval-quality-<日期>.json
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


def first_rank_of_anchors(hits, anchors, same_chunk):
    """锚点到齐的位次。

    same_chunk=True：必须一条 chunk 全含；False：只要在前 k 条里各出现过即可，
    取「最后一个锚点出现的位次」。
    """
    if same_chunk:
        for rank, hit in enumerate(hits, 1):
            text = hit.get("content") or ""
            if all(anchor in text for anchor in anchors):
                return rank
        return None
    ranks = []
    for anchor in anchors:
        rank = next((i for i, hit in enumerate(hits, 1)
                     if anchor in (hit.get("content") or "")), None)
        if rank is None:
            return None
        ranks.append(rank)
    return max(ranks)


def evaluate_question(question, hits):
    groups = question.get("gold") or []
    details = []
    for group in groups:
        anchors = group["anchors"]
        strict = first_rank_of_anchors(hits, anchors, same_chunk=True)
        loose = first_rank_of_anchors(hits, anchors, same_chunk=False)
        details.append({
            "name": group["name"],
            "anchors": anchors,
            "judgment": bool(group.get("judgment")),
            "firstRankSameChunk": strict,
            "firstRankAnywhere": loose,
            "chunkId": next((hit.get("id") for hit in hits
                             if all(anchor in (hit.get("content") or "") for anchor in anchors)),
                            None),
        })

    def evidence_rank(field):
        ranks = [d[field] for d in details if not d["judgment"]]
        return max(ranks) if ranks and all(ranks) else None

    judgment_ranks = [d["firstRankSameChunk"] for d in details if d["judgment"]]
    top1_text = (hits[0].get("content") or "") if hits else ""
    strict_groups = [g for g in groups if not g.get("judgment")]
    top1_covered = bool(strict_groups) and all(
        all(anchor in top1_text for anchor in group["anchors"]) for group in strict_groups)
    groups_covered_at_top1 = sum(
        1 for group in groups
        if all(anchor in top1_text for anchor in group["anchors"]))
    preview_ok = (top1_covered and all(
        top1_text.find(anchor) < PREVIEW_CHARS
        for group in strict_groups for anchor in group["anchors"])) if len(strict_groups) == 1 else None

    return {
        "questionId": question["questionId"],
        "category": question["category"],
        "answerable": question["answerable"],
        "groupCount": len(strict_groups),
        "judgmentGroupCount": len(details) - len(strict_groups),
        "evidenceRankSameChunk": evidence_rank("firstRankSameChunk"),
        "evidenceRankAnywhere": evidence_rank("firstRankAnywhere"),
        "judgmentEvidenceRank": (max(judgment_ranks)
                                 if judgment_ranks and all(judgment_ranks) else None),
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

    for label, field in (("SameChunk", "evidenceRankSameChunk"),
                         ("Anywhere", "evidenceRankAnywhere")):
        block = {}
        for k in (1, 3, 5, 10):
            if k > top:
                continue
            hits = sum(1 for r in answerable if r[field] and r[field] <= k)
            block[f"hit@{k}"] = {"hits": hits, "total": len(answerable),
                                 "rate": round(hits / len(answerable), 4) if answerable else None}
        values = [1 / r[field] for r in answerable if r[field]]
        block["mrr"] = round(sum(values) / len(answerable), 4) if answerable else None
        summary[label] = block

    single = [r for r in answerable if r["groupCount"] == 1]
    preview = [r for r in single if r["previewVisibleAt1"]]
    summary["preview@1"] = {"hits": len(preview), "total": len(single),
                            "rate": round(len(preview) / len(single), 4) if single else None}
    top1 = [r for r in single if r["top1CoversAllGroups"]]
    summary["top1IsTarget"] = {"hits": len(top1), "total": len(single),
                               "rate": round(len(top1) / len(single), 4) if single else None}
    multi = [r for r in answerable if r["groupCount"] > 1]
    if multi:
        summary["multiGroup"] = {
            "total": len(multi),
            "sameChunkRanks": {r["questionId"]: r["evidenceRankSameChunk"] for r in multi},
            "anywhereRanks": {r["questionId"]: r["evidenceRankAnywhere"] for r in multi},
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
        summary["refusalTop1Score"] = ({
            "min": min(scores), "median": statistics.median(scores), "max": max(scores)
        } if scores else None)
        if answerable_scores:
            p25 = (statistics.quantiles(answerable_scores, n=4)[0]
                   if len(answerable_scores) > 3 else min(answerable_scores))
            summary["answerableTop1Score"] = {
                "min": min(answerable_scores), "p25": p25,
                "median": statistics.median(answerable_scores), "max": max(answerable_scores)}
            summary["naiveThresholdMisfires"] = sum(1 for score in scores if score >= p25)
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
        print(f"[{mode}] 块内 @1 {summary['SameChunk'].get('hit@1')} / MRR {summary['SameChunk']['mrr']}"
              f" | 窗口内 @1 {summary['Anywhere'].get('hit@1')} / MRR {summary['Anywhere']['mrr']}"
              f" | preview@1 {summary.get('preview@1')}")

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print("证据写入", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
