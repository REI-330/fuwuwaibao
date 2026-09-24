#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
补齐 DeepEval 评测里因端点 503 而记为 None 的指标值。

背景：裁判端点（用户提供的中转服务）在持续负载下会间歇返回
`InternalServerError`，deepeval 内部重试耗尽后，我们的外层重试也兜不住，
于是该 (题, 指标) 被记为 None。实测缺失率约 3%。

本脚本只补这些缺失项，用更强的重试（默认 8 次、指数退避、带抖动），
补完后就地回写结果文件（先备份为 *.bak），并重算均值。

用法：
  python knowledge/eval/fill_missing.py                      # 自动挑最新产物
  python knowledge/eval/fill_missing.py --file knowledge/eval/results/deepeval-xxx.json
  python knowledge/eval/fill_missing.py --attempts 10
"""
import argparse
import glob
import json
import os
import random
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
RES = os.path.join(K, "eval", "results")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
Q_MAIN = os.path.join(K, "evaluations", "questions.json")
Q_HELD = os.path.join(K, "evaluations", "heldout-questions.json")
ENV_FILE = os.path.join(K, "eval", ".env")

METRICS = ["contextual_precision", "contextual_recall", "contextual_relevancy",
           "faithfulness", "answer_relevancy"]


def load_env_file():
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def pick_file():
    """取最新的结果文件（最终产物与增量产物放在一起按修改时间比）。"""
    cands = (glob.glob(os.path.join(RES, "deepeval-*.json")) +
             glob.glob(os.path.join(RES, "partial-*.json")))
    cands = [c for c in cands if not c.endswith(".bak")]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def iter_levels(doc):
    """统一成 [(level, container, rows)]，兼容最终产物与增量产物两种结构。"""
    if "levels" in doc:
        for lv, v in doc["levels"].items():
            yield lv, v, v.get("perQuestion", [])
    else:
        yield doc.get("level"), doc, doc.get("perQuestion", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--attempts", type=int, default=8)
    ap.add_argument("--max-fill", type=int, default=0, help="最多补多少项（0=不限）")
    args = ap.parse_args()

    load_env_file()
    path = args.file or pick_file()
    if not path or not os.path.exists(path):
        print("找不到结果文件", file=sys.stderr)
        return 1

    doc = json.load(open(path, encoding="utf-8"))

    # 找出所有缺失项
    todo = []
    for lv, container, rows in iter_levels(doc):
        for r in rows:
            sc = r.setdefault("scores", {})
            for m in METRICS:
                if m in sc and sc[m] is None:
                    todo.append((r, m))
    print(f"文件：{os.path.relpath(path, ROOT)}")
    print(f"缺失项：{len(todo)}")
    if not todo:
        return 0
    if args.max_fill:
        todo = todo[:args.max_fill]

    from deepeval.models import OpenAIModel
    from deepeval.test_case import LLMTestCase
    from deepeval.metrics import (ContextualPrecisionMetric, ContextualRecallMetric,
                                  ContextualRelevancyMetric, FaithfulnessMetric,
                                  AnswerRelevancyMetric)

    model = OpenAIModel(model=os.environ["DEEPEVAL_MODEL"],
                        api_key=os.environ["DEEPEVAL_API_KEY"],
                        base_url=os.environ["DEEPEVAL_BASE_URL"], temperature=0)
    factories = {
        "contextual_precision": lambda: ContextualPrecisionMetric(model=model, threshold=0.5),
        "contextual_recall": lambda: ContextualRecallMetric(model=model, threshold=0.5),
        "contextual_relevancy": lambda: ContextualRelevancyMetric(model=model, threshold=0.5),
        "faithfulness": lambda: FaithfulnessMetric(model=model, threshold=0.5),
        "answer_relevancy": lambda: AnswerRelevancyMetric(model=model, threshold=0.5),
    }

    chunks = {}
    for line in open(CHUNKS, encoding="utf-8"):
        line = line.strip()
        if line:
            d = json.loads(line)
            chunks[d["chunkId"]] = d

    # 结果行里只存了 questionId 与 retrieved_chunk_ids，题面与参考答案需回题集反查
    questions = {}
    for p in (Q_MAIN, Q_HELD):
        with open(p, encoding="utf-8") as f:
            for q in json.load(f)["questions"]:
                questions[q["questionId"]] = q

    filled = failed = 0
    for i, (row, metric) in enumerate(todo, 1):
        ids = row.get("retrieved_chunk_ids") or []
        ctx = [chunks[c]["text"] for c in ids if c in chunks]
        answer = row.get("answer")
        q = questions.get(row["questionId"]) or {}
        q_input = q.get("question") or ""
        expected = "\n\n".join(chunks[c]["text"] for c in (q.get("referenceChunks") or [])
                               if c in chunks)
        if not ctx or not isinstance(answer, str) or not answer or not q_input or not expected:
            print(f"  [{i}] {row['questionId']} {metric}: 缺少 context/answer/题面，跳过")
            failed += 1
            continue
        tc = LLMTestCase(input=q_input, actual_output=answer,
                         expected_output=expected, retrieval_context=ctx)
        last = None
        for a in range(args.attempts):
            try:
                m = factories[metric]()
                m.measure(tc)
                row["scores"][metric] = round(float(m.score), 4)
                (row.setdefault("reasons", {}))[metric] = (m.reason or "")[:400]
                print(f"  [{i}/{len(todo)}] {row['questionId']} {metric} = {row['scores'][metric]}")
                filled += 1
                break
            except Exception as e:
                last = e
                time.sleep(min(5 * (2 ** a), 60) + random.uniform(0, 2))
        else:
            print(f"  [{i}/{len(todo)}] {row['questionId']} {metric}: 仍失败 {type(last).__name__}")
            failed += 1

        # 每补一项就落盘，避免中断后重来
        shutil.copyfile(path, path + ".bak")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)

    # 重算均值
    for lv, container, rows in iter_levels(doc):
        sums = {m: 0.0 for m in METRICS}
        counts = {m: 0 for m in METRICS}
        for r in rows:
            for m in METRICS:
                v = (r.get("scores") or {}).get(m)
                if isinstance(v, (int, float)):
                    sums[m] += float(v)
                    counts[m] += 1
        if "summary" in container and container["summary"]:
            for m in METRICS:
                if m in container["summary"] and isinstance(container["summary"][m], dict):
                    container["summary"][m]["mean"] = (round(sums[m] / counts[m], 4)
                                                       if counts[m] else None)
                    container["summary"][m]["n"] = counts[m]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    print(f"\n补齐 {filled} 项，失败 {failed} 项；均值已重算并回写 {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
