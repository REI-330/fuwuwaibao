#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""中国官方语料的**引用支持度 / 回答质量**评测（要调模型，属上层能力）。

## 为什么单列一层

`retrieval_quality.py` 量的是**排序**：gold 锚点有没有进前 k。但真实产品里用户看到的是
**一段回答**，中间还隔着「把 k 条上下文喂给模型」这一步。检索全绿也不代表回答有据——
模型可能引用了不存在的片段，也可能在上下文里根本没有答案时硬答。

这一步只量三件**可机械判定**的事（不请模型当裁判，避免自评自）：

1. **引用合法**：回答里出现的 `[片段N]` 必须落在本次真的检索到的 1..k 之内（编编号=不合法）；
2. **引用支持答案**：被引用的片段里是否含有该题的 gold 锚点；可答题还要求答案正文里
   也出现锚点（即「答到了」），跨块题沿用检索那套「证据齐」口径；
3. **该拒答时拒答**：对超范围题（gold 为空），模型若给出「无法回答」类表述记 pass，
   否则记 hallucination（这条是**启发式**，见下）。

## 诚实边界

* 第 3 条靠关键词正则判定「拒答」，是**启发式**，不是人工评；模型绕圈子说「不确定」会被算作没拒；
* 「回答质量」在这里**只**是上面三条的机械口径，**不做**流畅度/有用性/风格评价，
  也没有请第二个模型当裁判（那会引入裁判与被评同源的问题）；
* gold 仍是作者标注 + 机器复核出处，不是业主人工判定相关性。

用法：

    python knowledge-cn/eval/answer_support.py \
        --kb aacc4889-... \
        --questions knowledge-cn/evaluations/questions-cn-semantic-v1.json \
        --refusals knowledge-cn/evaluations/questions-cn-v1.json \
        --top 5 --out knowledge-cn/evidence/answer-support-semantic.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "knowledge" / "eval"))

from _shared_llm import chat  # noqa: E402
from weknora_client import WeKnora  # noqa: E402

CITE_RE = re.compile(r"\[?\s*片段\s*(\d+)\s*\]?")
REFUSAL_RE = re.compile(
    r"(无法(回答|确定|判断)|不能(回答|确定)|不足以|没有(足够的)?(信息|依据|相关(信息|内容))|"
    r"未(提供|包含|提及)|不包含|没有提到|超出(已知|给定)|抱歉)"
)

PROMPT_TEMPLATE = """你是一个严谨的问答助手。只能依据下面给出的【片段】回答，不要使用片段以外的知识。

要求：
1. 每一条结论后面用 [片段N] 标出它来自哪个片段；没有片段支撑就别说。
2. 如果【片段】里没有足以回答问题的信息，**直接回答「无法回答」**并说明缺什么，不要猜测。
3. 回答用中文，不超过 200 字。

【片段】
{snippets}

【问题】
{question}
"""


def build_snippets(hits: List[Dict[str, Any]], top: int, snippet_chars: int) -> str:
    lines = []
    for index, hit in enumerate(hits[:top], start=1):
        content = str(hit.get("content") or hit.get("text") or "").strip().replace("\n", " ")
        lines.append(f"[片段{index}] {content[:snippet_chars]}")
    return "\n".join(lines) if lines else "（没有任何片段）"


def anchors_of(question: Dict[str, Any]) -> List[List[str]]:
    groups: List[List[str]] = []
    for group in question.get("gold") or []:
        anchors = [str(item).strip() for item in (group.get("anchors") or []) if str(item).strip()]
        if anchors:
            groups.append(anchors)
    return groups


def contains_all(text: str, anchors: List[str]) -> bool:
    return all(anchor in text for anchor in anchors)


def judge_answer(question: Dict[str, Any], hits: List[Dict[str, Any]], answer: str, top: int) -> Dict[str, Any]:
    answerable = bool(question.get("answerable"))
    cited = sorted({int(item) for item in CITE_RE.findall(answer)})
    valid = [index for index in cited if 1 <= index <= min(top, len(hits))]
    invalid = [index for index in cited if index not in valid]
    cited_text = "\n".join(str(hits[index - 1].get("content") or "") for index in valid)
    groups = anchors_of(question)
    refusal_like = bool(REFUSAL_RE.search(answer))

    record: Dict[str, Any] = {
        "questionId": question.get("questionId"),
        "answerable": answerable,
        "citedRefs": cited,
        "invalidRefs": invalid,
        "refusalLike": refusal_like,
        "answer": answer,
    }
    if answerable:
        record["answeredAnchorsInAnswer"] = all(contains_all(answer, group) for group in groups)
        record["citationSupportsGold"] = bool(groups) and all(contains_all(cited_text, group) for group in groups)
        record["citationSupportedAnyGroup"] = any(contains_all(cited_text, group) for group in groups)
    else:
        # 超范围题：gold 为空，唯一的要求是别硬答
        record["refused"] = refusal_like
    return record


def summarise(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    answerable = [item for item in records if item["answerable"]]
    refusals = [item for item in records if not item["answerable"]]

    def ratio(hits: int, total: int) -> Dict[str, Any]:
        return {"hits": hits, "total": total, "rate": round(hits / total, 4) if total else None}

    cite_valid = sum(1 for item in answerable if item["citedRefs"] and not item["invalidRefs"])
    asked = sum(1 for item in answerable if item["answerable"] and item["answer"])
    supported = sum(1 for item in answerable if item.get("citationSupportsGold"))
    answered = sum(1 for item in answerable if item.get("answeredAnchorsInAnswer"))
    supported_any = sum(1 for item in answerable if item.get("citationSupportedAnyGroup"))
    refused = sum(1 for item in refusals if item.get("refused"))
    refused_but_answerable = sum(1 for item in answerable if item.get("refusalLike"))
    return {
        "answerable": {
            "total": len(answerable),
            "有引用且引用都合法": ratio(cite_valid, len(answerable)),
            "引用支持全部 gold 组": ratio(supported, len(answerable)),
            "引用支持至少一个 gold 组": ratio(supported_any, len(answerable)),
            "答案正文命中全部锚点": ratio(answered, len(answerable)),
            "有回答": ratio(asked, len(answerable)),
            "可答题里出现拒答表述（启发式）": ratio(refused_but_answerable, len(answerable)),
        },
        "refusal": {
            "total": len(refusals),
            "正确拒答（启发式）": ratio(refused, len(refusals)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kb", required=True)
    parser.add_argument("--questions", required=True, help="可答题集")
    parser.add_argument("--refusals", help="超范围题所在题集（同文件里 answerable=false 的那些）")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--snippet-chars", type=int, default=2400,
                        help="每条片段喂给模型的字符上限。**别调小**：本库的块约 2400 字，"
                             "截到 600 会把表格深处的锚点截掉，模型只能拒答（实测踩过一次）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（0=全跑）")
    parser.add_argument("--model-note", default="")
    parser.add_argument("--out")
    args = parser.parse_args()

    wk = WeKnora()
    items: List[Dict[str, Any]] = json.loads(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    if args.refusals:
        base = json.loads(Path(args.refusals).read_text(encoding="utf-8"))["questions"]
        items = items + [q for q in base if not q.get("answerable")]
    if args.limit:
        items = items[:args.limit]

    records: List[Dict[str, Any]] = []
    started = time.time()
    for question in items:
        hits = wk.search(args.kb, question["question"], top=args.top)
        prompt = PROMPT_TEMPLATE.format(snippets=build_snippets(hits, args.top, args.snippet_chars),
                                        question=question["question"])
        try:
            answer = chat(prompt, timeout=180, max_tokens=1500).strip()
        except Exception as error:  # noqa: BLE001 —— 单题失败不许把整轮评测拖没
            answer = ""
            print(f"  [WARN] {question.get('questionId')} 模型调用失败：{type(error).__name__}: {error}")
        record = judge_answer(question, hits, answer, args.top)
        record["hitIds"] = [str(hit.get("id")) for hit in hits[:args.top]]
        records.append(record)
        print(f"  {record['questionId']}: 引用{record['citedRefs']} 非法{record['invalidRefs']}"
              f" 支持gold={record.get('citationSupportsGold')} 命中锚点={record.get('answeredAnchorsInAnswer')}"
              f" 拒答={record.get('refused')}")

    report = {
        "kb": args.kb,
        "questions": args.questions,
        "top": args.top,
        "snippetChars": args.snippet_chars,
        "modelNote": args.model_note,
        "elapsedSeconds": round(time.time() - started, 1),
        "summary": summarise(records),
        "perQuestion": records,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
