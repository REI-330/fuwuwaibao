#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
组装 questions-dev.json / questions-test.json：并入手写题、采集题与拒答题，统一编号。

组成（本文件即最终题集的唯一组装入口，避免手工拼 JSON 出错）：
  dev  = questions-v2.json（沿用 14 题，已被调参污染，只作 dev）
       + _collected-dev.json（从批量出题缓存采集）
       + _authored-dev-chinese.json（2026-09-17 新写的中文答案层题）
       + questions-refusal.json 中 split=dev 的拒答题
  test = _collected-test.json + _authored-questions.json（人工读原文手写）
       + _authored-test-chinese.json（2026-09-17 新写的中文答案层题）
       + questions-refusal.json 中 split=test 的拒答题

语言分层标记（layer）：
  本语料里没有「纯中文文档」——未被引用且 >=300 字符的 345 段里，中文占比 >90% 的
  是 0 段。所以「中文问→中文答」与「中文问→英文原文答」是两种不同的检索任务，
  混在一起报数会互相掩盖（实测：留出集 15/21 是跨语言题，融合权重 α 在这批题上
  反而有害，而在中文题为主的开发集上 α=0.35 最优）。
  因此每题按**实测**的参考答案中文占比打标记，两层分开报数：
    layer = "zh"   → 两段参考答案的中文占比都 >= 0.20
    layer = "xl"   → 否则（跨语言层）
  字段：layer、minRefCjkRatio（两段里较小的中文占比）、meanRefCjkRatio。
  标记由脚本按 chunks.jsonl 现算，不手写，可复算。
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
EV = os.path.join(K, "evaluations")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")

CJK = re.compile(r"[\u4e00-\u9fff]")
ZH_LAYER_MIN_RATIO = 0.20


def load_chunk_ratios():
    """每个 chunk 的中文占比（汉字数 / 非空白字符数）。"""
    out = {}
    with open(CHUNKS, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            c = json.loads(line)
            s = re.sub(r"\s", "", c["text"])
            out[c["chunkId"]] = len(CJK.findall(s)) / len(s) if s else 0.0
    return out


def tag_layer(q, ratios):
    """给可回答题打语言分层标记；拒答题没有参考答案，不打。"""
    refs = q.get("referenceChunks") or []
    if not refs or not q.get("answerable", True):
        return q
    vals = [ratios[r] for r in refs if r in ratios]
    if not vals:
        return q
    q["minRefCjkRatio"] = round(min(vals), 4)
    q["meanRefCjkRatio"] = round(sum(vals) / len(vals), 4)
    q["layer"] = "zh" if min(vals) >= ZH_LAYER_MIN_RATIO else "xl"
    return q


def load(name):
    p = os.path.join(EV, name)
    if not os.path.exists(p):
        print(f"  （缺 {name}，按空处理）")
        return []
    return json.load(open(p, encoding="utf-8")).get("questions", [])


def main():
    carried = load("questions-v2.json")
    coll_dev = load("_collected-dev.json")
    coll_test = load("_collected-test.json")
    authored = load("_authored-questions.json")
    auth_dev_zh = load("_authored-dev-chinese.json")
    auth_test_zh = load("_authored-test-chinese.json")
    refusal = json.load(open(os.path.join(EV, "questions-refusal.json"),
                             encoding="utf-8"))["questions"]
    ratios = load_chunk_ratios()

    def strip(q, split, legacy=False):
        q = dict(q)
        q.pop("provenance", None)
        # 保留原始编号与出处：组装会统一重编号（T01… / D01…），
        # 没有这两个字段就无法区分「本轮新写」与「沿用旧题」。
        if str(q.get("questionId", "")).startswith("C-"):
            q["authoredId"] = q["questionId"]
            q["origin"] = "authored-zh-20260917"
        q["split"] = split
        # 显式补 answerable：手写题文件里没写这个字段，
        # 而下游有脚本用 q.get("answerable")（缺字段=假）→ 会被静默漏掉。
        q["answerable"] = q.get("answerable", True)
        if legacy:
            q["legacy"] = True
            q["legacyReason"] = ("沿用 2026-09-16 版题集：它是 α 与重排选型的依据（已被调参污染，"
                                 "只能当 dev），且其中 10 题的两段参考答案在原文间隔 < 3。"
                                 "保留以便与历史结果对照；不计入新增题集的质检通过率。")
        return q

    dev_ans = ([strip(q, "dev", legacy=True) for q in carried]
               + [strip(q, "dev") for q in coll_dev]
               + [strip(q, "dev") for q in auth_dev_zh])
    test_ans = ([strip(q, "test") for q in coll_test]
                + [strip(q, "test") for q in authored]
                + [strip(q, "test") for q in auth_test_zh])

    for i, q in enumerate(dev_ans, 1):
        if not q["questionId"].startswith("N"):
            q["questionId"] = f"D{i - len(carried):02d}"
    for i, q in enumerate(test_ans, 1):
        q["questionId"] = f"T{i:02d}"
    # 语言分层标记：按参考答案段的实测中文占比现算，便于两层分开报数
    for q in dev_ans + test_ans:
        tag_layer(q, ratios)

    def assemble(split, answerable):
        ref = [q for q in refusal if q["split"] == split]
        qs = answerable + ref
        layer_counts = {}
        for q in answerable:
            key = q.get("layer", "(未标)")
            layer_counts[key] = layer_counts.get(key, 0) + 1
        counts = {"total": len(qs),
                  "answerable": len(answerable),
                  "outOfScope": len(ref),
                  "referenceChunksTotal": sum(len(q.get("referenceChunks") or []) for q in qs),
                  "layers": layer_counts}
        return {"schema": "career-graph-eval-questions/v3",
                "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "split": split,
                "note": "dev 用于调参与试错（沿用 14 题已被调参污染）；test 冻结后只跑一次。"
                        "可回答题的参考答案满足：未落在图谱引用集合内 / 每题 2 段 / 同来源 / "
                        "原文间隔 >= 3 / dev-test 不共用；拒答题不含参考答案。"
                        "语言分层：layer=zh 表示两段参考答案的中文占比都 >= 0.20，"
                        "layer=xl 为跨语言层（中文提问、英文原文作答），两层须分开报数。"
                        "题面来源见各题 provenance 与 EVAL_SET_DESIGN.md。",
                "counts": counts, "questions": qs}

    dev_doc = assemble("dev", dev_ans)
    test_doc = assemble("test", test_ans)
    json.dump(dev_doc, open(os.path.join(EV, "questions-dev.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump(test_doc, open(os.path.join(EV, "questions-test.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"dev : {dev_doc['counts']}")
    print(f"test: {test_doc['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
