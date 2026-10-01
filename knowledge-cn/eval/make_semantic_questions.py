#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成「偏语义改写题集」：同一批**已机器复核**的 gold，题面改成不含锚点词面的说法。

## 为什么需要它

现役 RRF 权重 0.2/0.8（关键词占大头）是在**字面型**题集上选出来的：
33 题里绝大多数问法直接复述了正文里的词（专业代码、表格数值、职业编码）。
权重虽然过了对半交叉验证，但两半来自同一批字面题 —— 所以「0.2/0.8 是否也适合语义型问题」
一直没定论（见 `评估/检索质量评测-20261001.md` §十一）。

这套题集就是来补这个洞的：**gold 一个字都不改**（沿用 `verify_questions.py` 在真实库里
复核过的那批锚点），只把提问改成绕开锚点词面的说法。

## 「偏语义」是可校验的，不是自称的

脚本会逐题断言：**改写后的题面里不出现该题任何 gold 组的任何一个锚点**。
不满足就报错退出 —— 所以这套题集是「字面通道拿不到词面优势」的一条**事实**，
而不是一句形容词。

## 诚实边界（写进输出文件，也写进报告）

* gold 与预期答案仍然是**作者标注 + 机器复核出处**，不是业主人工判定相关性；
* 改写的只是提问措辞，**没有**做跨语言、跨方言或口语化改写；
* 题集规模小（16 题），只够回答「权重在语义型问题上会不会翻车」，不足以定权重。

用法：

    python knowledge-cn/eval/make_semantic_questions.py \
        --base knowledge-cn/evaluations/questions-cn-v1.json \
        --out knowledge-cn/evaluations/questions-cn-semantic-v1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

# 题号 → 改写后的题面（绕开该题 gold 锚点的词面）
REWRITES: Dict[str, str] = {
    "CN01": "2026 年新设的本科专业里，有一个与无人机产业直接相关、毕业授予管理学学士学位的特设方向，它的专业代码是什么？",
    "CN06": "那个面向低空产业的本科特设专业，在专业目录里被归到哪一大类之下？",
    "CN09": "2025 年，全国城镇非私营单位的就业人员，一年平均能挣多少钱？跟上一年比涨了多少？",
    "CN10": "2025 年，城镇里私营性质的单位，就业人员的年平均收入是多少？",
    "CN11": "2025 年规模以上企业里，坐到管理岗位的那些人，平均一年能拿多少钱？",
    "CN12": "2025 年城镇非私营单位中，哪一个行业门类的平均工资排在第一？具体是多少？",
    "CN13": "2025 年城镇非私营单位里，平均工资垫底的是哪个行业？金额是多少？",
    "CN15": "2025 年城镇私营单位中，薪水最高的两个行业各自是多少钱？",
    "CN16": "2025 年城镇非私营单位里，外资背景的单位平均一年发多少钱？比纯内资的单位高出多少？",
    "CN17": "《职业分类大典》里，负责给模型标注、准备训练数据的那类从业者，职业编码是多少？",
    "CN18": "大典里，专门把企业业务流程搬上信息化系统、并做数据治理的那类岗位，编码是多少？",
    "CN19": "大典里「用遥控设备驾驶无人航空器」的那类职业，编码是多少？它下面还分了哪几个具体工种？",
    "CN20": "大典里有一种专门做「以物换物」贸易分析与信用评估的职业，它的编码是多少？",
    "CN21": "大典里那个借助多种媒介与技术手段，对信息做加工、匹配、分发、传播和反馈的岗位，编码是多少？",
    "CN22": "大典里负责给企事业单位编制发展规划、做系统分析与设计评价的那类工程技术岗，编码是多少？",
    "CN23": "大典里专门做家务料理、照看家庭成员的那类岗位，编码是多少？",
}


def anchors_of(question: Dict) -> List[str]:
    out: List[str] = []
    for group in question.get("gold") or []:
        for anchor in group.get("anchors") or []:
            text = str(anchor).strip()
            if text:
                out.append(text)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    base = json.loads(Path(args.base).read_text(encoding="utf-8"))
    picked = []
    problems: List[str] = []
    for question in base["questions"]:
        question_id = question["questionId"]
        rewrite = REWRITES.get(question_id)
        if rewrite is None:
            continue
        # 这是这套题集的**定义**：题面里不许出现任何一个 gold 锚点
        leaked = [anchor for anchor in anchors_of(question) if anchor in rewrite]
        if leaked:
            problems.append(f"{question_id}: 改写后的题面里仍然出现锚点 {leaked}")
            continue
        item = dict(question)
        item["question"] = rewrite
        item["literalQuestion"] = question["question"]
        item["rewriteKind"] = "paraphrase-no-anchor"
        picked.append(item)

    if problems:
        print("改写不合规（题面里出现了 gold 锚点，那就不叫偏语义）：", file=sys.stderr)
        for line in problems:
            print("  - " + line, file=sys.stderr)
        return 1
    if not picked:
        print("没有任何一题被改写：REWRITES 里的题号对不上基础题集", file=sys.stderr)
        return 1

    document = {
        "schema": "career-kb-cn-questions/v1",
        "generatedAt": None,
        "split": "retrieval-quality-semantic-v1",
        "derivedFrom": str(Path(args.base).as_posix()),
        "kb": base.get("kb"),
        "note": (
            "偏语义改写子集：gold（锚点、来源、预期答案）与基础题集**逐字相同**，只把提问改成"
            "不出现任何锚点词面的说法；`literalQuestion` 保留了原问法便于对照。"
            "生成脚本会逐题断言「题面里不含任何 gold 锚点」，所以「偏语义」是机器校验过的事实，"
            "不是自称。**仍然**不是业主人工判定相关性，也没有做跨语言/口语化改写。"
        ),
        "counts": {"total": len(picked), "answerable": len(picked), "refusal": 0,
                   "goldGroups": sum(len(item.get("gold") or []) for item in picked)},
        "judging": base.get("judging"),
        "questions": picked,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写出 {len(picked)} 题 → {args.out}")
    for item in picked:
        print(f"  {item['questionId']}: {item['question']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
