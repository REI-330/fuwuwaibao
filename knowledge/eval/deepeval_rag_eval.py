#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用 DeepEval 对职业知识库的检索链路做评测。

评测对象：同一套 26 道题（主集 18 道可回答 + 留出集 8 道）上的三档检索
          ① BM25 基线（读 knowledge/evaluations/results.json 里既有档位）
          ② 向量检索（读 knowledge/eval/runs/vector-topk.json，由 vector_retrieval.py 产出）
          ③ 全量档（BM25 + 图遍历 + wiki，既有产物里的最强档）

评测方式（完整 RAG）：
  1) 用裁判模型基于检索片段生成答案 → actual_output
  2) 跑 deepeval 指标：
     - ContextualPrecisionMetric  检索片段里相关内容是否排在前面
     - ContextualRecallMetric     expected_output 的信息是否都在检索片段里
     - ContextualRelevancyMetric  检索片段与问题是否相关
     - FaithfulnessMetric         答案是否忠实于检索片段（不编造）
     - AnswerRelevancyMetric      答案是否切题

为什么需要 LLM：deepeval 4.2.3 的 56 个指标类中，43 个源码里直接调用 LLM；
与本任务相关的检索/答案类指标全部属于这 43 个。不接 LLM 就只能跑
ExactMatchMetric / PatternMatchMetric，对 RAG 评估没有意义。

配置（二选一，推荐文件方式，避免 key 出现在对话或 shell 历史里）：
  knowledge/eval/.env 里写：
    DEEPEVAL_BASE_URL=https://api.deepseek.com/v1
    DEEPEVAL_API_KEY=sk-xxx
    DEEPEVAL_MODEL=deepseek-chat
  或设置同名环境变量。

用法：
  python knowledge/eval/deepeval_rag_eval.py --dry-run        # 只建用例、不调模型
  python knowledge/eval/deepeval_rag_eval.py --limit 2        # 小样本试跑
  python knowledge/eval/deepeval_rag_eval.py                  # 全量
"""
import argparse
import json
import os
import random
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
Q_MAIN = os.path.join(K, "evaluations", "questions-v2.json")
# 旧留出集已随旧题集删除；新题集每题统一 2 段参考答案，不再区分主集/留出集
Q_HELD = os.path.join(K, "evaluations", "heldout-questions.json")
R_BM25 = os.path.join(K, "evaluations", "results.json")
R_BM25_NEW = os.path.join(K, "eval", "runs", "bm25-topk.json")
R_HYBRID = os.path.join(K, "eval", "runs", "hybrid-topk.json")
R_RERANKED = os.path.join(K, "eval", "runs", "reranked-topk.json")
R_VECTOR = os.path.join(K, "eval", "runs", "vector-topk.json")
R_WEKNORA = os.path.join(K, "eval", "runs", "weknora-topk.json")
OUT_DIR = os.path.join(K, "eval", "results")
ENV_FILE = os.path.join(K, "eval", ".env")

# 既有 results.json 里要对比的两档
RUN_BM25 = "A-bm25-only"
RUN_FULL = "E-full"

ANSWER_PROMPT = """你是职业导航知识助手。仅依据下列检索片段回答问题，不要编造片段之外的内容。
若片段不足以回答，明确说明资料不足。

问题：{question}

检索片段：
{context}

回答："""


def load_env_file():
    """从 knowledge/eval/.env 读取配置；不打印 value。"""
    if not os.path.exists(ENV_FILE):
        return
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_chunks():
    m = {}
    with open(CHUNKS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                m[d["chunkId"]] = d
    return m


def load_questions(path=None):
    if path:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        items = doc.get("questions", doc) if isinstance(doc, dict) else doc
        return [("set", q) for q in items if q.get("answerable", True)]
    qs = []
    with open(Q_MAIN, encoding="utf-8") as f:
        doc = json.load(f)
    items = doc.get("questions", doc) if isinstance(doc, dict) else doc
    qs += [("main", q) for q in items if q.get("answerable", True)]
    if os.path.exists(Q_HELD):
        with open(Q_HELD, encoding="utf-8") as f:
            qs += [("heldout", q) for q in json.load(f)["questions"]]
    else:
        print("[题集] 未发现留出集文件，只跑主集（新题集每题统一 2 段参考答案，无需再分集）")
    return qs


def load_retrieval(spec=None):
    """返回 {档位: {questionId: [{"chunkId":..., "text":...|None}]}}

    文本来源分两种：
      - 本地档位（BM25/向量）：只有 chunkId，正文需回本地 chunks.jsonl 查；
      - WeKnora 档位：结果里自带 text（WeKnora 的 chunk 不在本地语料里）。

    spec 形如 "D2-BM25=runs/dev2-bm25-topk.json,D2-RERANK=runs/dev2-adopted-a035-topk.json"；
    不给则用下方写死的旧产物路径（向后兼容）。
    """
    if spec:
        out = {}
        for item in [x for x in spec.split(",") if x.strip()]:
            name, _, path = item.partition("=")
            name, path = name.strip(), path.strip()
            if not os.path.isabs(path):
                path = os.path.join(ROOT, path)
            if not os.path.exists(path):
                print(f"  （档位 {name} 的产物不存在，跳过：{os.path.relpath(path, ROOT)}）")
                continue
            with open(path, encoding="utf-8") as f:
                v = json.load(f)
            d = {}
            for pq in v.get("perQuestion", []):
                d[pq["questionId"]] = [{"chunkId": t.get("chunkId"), "text": t.get("text")}
                                       for t in (pq.get("topk") or [])]
            out[name] = d
        return out
    out = {}
    if os.path.exists(R_BM25):
        with open(R_BM25, encoding="utf-8") as f:
            res = json.load(f)
        for run in res.get("runs", []):
            d = {}
            for pq in run.get("perQuestion", []):
                d[pq["questionId"]] = [{"chunkId": t["chunkId"], "text": None}
                                       for t in pq.get("top3", [])]
            out[run["id"]] = d
    for path, key in ((R_BM25_NEW, "BM25"), (R_VECTOR, "VECTOR"),
                      (R_HYBRID, "HYBRID"), (R_RERANKED, "RERANKED"),
                      (R_WEKNORA, "WEKNORA")):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                v = json.load(f)
            d = {}
            for pq in v.get("perQuestion", []):
                d[pq["questionId"]] = [{"chunkId": t.get("chunkId"), "text": t.get("text")}
                                       for t in pq.get("topk", [])]
            out[key] = d
    return out


def reference_text(q, chunks):
    parts = []
    for cid in q.get("referenceChunks") or []:
        c = chunks.get(cid)
        if c:
            parts.append(c["text"])
    return "\n\n".join(parts)


def build_cases(questions, retrieval, level, chunks):
    cases = []
    for set_name, q in questions:
        ids = retrieval.get(level, {}).get(q["questionId"])
        if not ids:
            continue
        ctx = []
        for it in ids:
            txt = it.get("text")
            if not txt:  # 本地档位：正文回 chunks.jsonl 查
                c = chunks.get(it.get("chunkId"))
                txt = c["text"] if c else None
            if txt:
                ctx.append(txt)
        if not ctx:
            continue
        cases.append({
            "set": set_name,
            "questionId": q["questionId"],
            "category": q.get("category"),
            "input": q["question"],
            "retrieval_context": ctx,
            "expected_output": reference_text(q, chunks),
            "retrieved_chunk_ids": [it.get("chunkId") for it in ids],
        })
    return cases


def build_model():
    from deepeval.models import OpenAIModel
    base = os.environ.get("DEEPEVAL_BASE_URL", "https://api.deepseek.com/v1")
    key = os.environ.get("DEEPEVAL_API_KEY", "")
    name = os.environ.get("DEEPEVAL_MODEL", "deepseek-chat")
    if not key:
        return None, base, name
    return OpenAIModel(model=name, api_key=key, base_url=base, temperature=0), base, name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只建用例与校验，不调用任何模型")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（试跑用）")
    ap.add_argument("--workers", type=int, default=4,
                    help="题目级并发；每题需 1 次生成 + 5 次裁判，串行会很慢")
    ap.add_argument("--no-resume", action="store_true",
                    help="忽略 partial.json，全部重跑（默认会续跑已完成的题目）")
    ap.add_argument("--metrics", default="all",
                    help="逗号分隔的指标名，或 all。可用：contextual_precision,"
                         "contextual_recall,contextual_relevancy,faithfulness,answer_relevancy")
    ap.add_argument("--levels", default="BM25,VECTOR")
    ap.add_argument("--questions", help="题集文件（默认用写死的旧题集）")
    ap.add_argument("--retrieval",
                    help='档位到产物的映射，形如 "D2-BM25=runs/dev2-bm25-topk.json,'
                         'D2-RERANK=runs/dev2-adopted-a035-topk.json"（相对仓库根或绝对路径）')
    args = ap.parse_args()

    load_env_file()
    chunks = load_chunks()
    questions = load_questions(args.questions)
    retrieval = load_retrieval(args.retrieval)

    levels = [x.strip() for x in args.levels.split(",") if x.strip()]
    levels = [x for x in levels if x in retrieval]
    print(f"语料 {len(chunks)} 段 | 题集 {len(questions)} 题 "
          f"(主集 {sum(1 for s,_ in questions if s=='main')} / "
          f"留出 {sum(1 for s,_ in questions if s=='heldout')})")
    print("可用检索档位:", ", ".join(sorted(retrieval.keys())))
    print("本次评测档位:", ", ".join(levels))

    plan = {}
    for lv in levels:
        cs = build_cases(questions, retrieval, lv, chunks)
        plan[lv] = cs
        miss = [c["questionId"] for c in cs if not c["expected_output"]]
        print(f"  {lv}: {len(cs)} 个用例"
              + (f" | {len(miss)} 题无参考答案正文" if miss else ""))

    if args.dry_run:
        print("\n[dry-run] 用例构建完成，未调用模型。示例：")
        for lv in levels[:1]:
            if plan[lv]:
                c = plan[lv][0]
                print(json.dumps({k: (v[:180] + "…" if isinstance(v, str) and len(v) > 180 else v)
                                  for k, v in c.items() if k != "retrieval_context"},
                                 ensure_ascii=False, indent=2))
                print("  retrieval_context 段数:", len(c["retrieval_context"]))
        model, base, name = build_model()
        print(f"\n模型配置：base_url={base} model={name} "
              f"key={'已配置' if model else '未配置（需要 knowledge/eval/.env）'}")
        return 0

    model, base, name = build_model()
    if model is None:
        print("\n缺少 LLM 配置，无法评分。请在 knowledge/eval/.env 写入：\n"
              "  DEEPEVAL_BASE_URL=https://api.deepseek.com/v1\n"
              "  DEEPEVAL_API_KEY=...\n"
              "  DEEPEVAL_MODEL=deepseek-chat", file=sys.stderr)
        return 2

    from deepeval.metrics import (ContextualPrecisionMetric, ContextualRecallMetric,
                                  ContextualRelevancyMetric, FaithfulnessMetric,
                                  AnswerRelevancyMetric)
    from deepeval.test_case import LLMTestCase

    metric_factories = {
        "contextual_precision": lambda: ContextualPrecisionMetric(model=model, threshold=0.5, include_reason=True),
        "contextual_recall": lambda: ContextualRecallMetric(model=model, threshold=0.5, include_reason=True),
        "contextual_relevancy": lambda: ContextualRelevancyMetric(model=model, threshold=0.5, include_reason=True),
        "faithfulness": lambda: FaithfulnessMetric(model=model, threshold=0.5, include_reason=True),
        "answer_relevancy": lambda: AnswerRelevancyMetric(model=model, threshold=0.5, include_reason=True),
    }
    if args.metrics != "all":
        want = {x.strip() for x in args.metrics.split(",") if x.strip()}
        unknown = want - set(metric_factories)
        if unknown:
            print(f"未知指标：{sorted(unknown)}", file=sys.stderr)
            return 2
        metric_factories = {k: v for k, v in metric_factories.items() if k in want}
    print(f"本次评测指标：{', '.join(metric_factories)}")

    print(f"\n裁判模型：{name} @ {base}")
    os.makedirs(OUT_DIR, exist_ok=True)
    all_results = {}

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def with_retry(fn, attempts=6, base_delay=5.0, label=""):
        """端点会间歇返回 503（Upstream service temporarily unavailable）。
        虽然 openai SDK 自身也会重试，但它的退避很激进，实测会造成长时间无进展。
        这里包一层确定性的重试（指数退避 + 上限 + 抖动），并把失败原因记下来。"""
        last = None
        for i in range(attempts):
            try:
                return fn()
            except Exception as e:
                last = e
                if i < attempts - 1:
                    time.sleep(min(base_delay * (2 ** i), 60) + random.uniform(0, 2))
        raise last

    def process_case(c):
        """单题：生成答案 + 跑全部指标。异常写进 row，不向外抛。"""
        t_start = time.time()
        ctx_block = "\n".join(f"[{j+1}] {t[:1200]}"
                              for j, t in enumerate(c["retrieval_context"]))
        row = {"questionId": c["questionId"], "set": c["set"], "category": c["category"],
               "retrieved_chunk_ids": c["retrieved_chunk_ids"],
               "answer": None, "scores": {}, "reasons": {}}
        try:
            gen = with_retry(lambda: model.generate(
                ANSWER_PROMPT.format(question=c["input"], context=ctx_block)), label="generate")
            # 注意：deepeval 的 generate() 返回 (文本, 成本) 元组，不是纯字符串。
            # 直接把它当字符串传给 LLMTestCase 会报 "'actual_output' must be a string"。
            answer = gen[0] if isinstance(gen, tuple) else gen
            if not isinstance(answer, str):
                answer = str(answer)
            row["answer"] = answer
            print(f"      {row['questionId']} 答案已生成（{len(answer)} 字符）", flush=True)
        except Exception as e:
            row["reasons"]["generate"] = f"{type(e).__name__}: {e}"
            row["elapsedSec"] = round(time.time() - t_start, 1)
            return row

        tc = LLMTestCase(input=c["input"], actual_output=answer,
                         expected_output=c["expected_output"],
                         retrieval_context=c["retrieval_context"])
        for mname, factory in metric_factories.items():
            try:
                m = with_retry(lambda: (lambda mm: (mm.measure(tc), mm)[1])(factory()),
                               label=mname)
                row["scores"][mname] = round(float(m.score), 4)
                row["reasons"][mname] = (m.reason or "")[:400]
            except Exception as e:
                row["scores"][mname] = None
                row["reasons"][mname] = f"{type(e).__name__}: {e}"
            print(f"      {row['questionId']} {mname}={row['scores'][mname]}", flush=True)
        row["elapsedSec"] = round(time.time() - t_start, 1)
        return row

    for lv in levels:
        cases = plan[lv]
        if args.limit:
            cases = cases[:args.limit]
        per_q = []
        sums = {k: 0.0 for k in metric_factories}
        counts = {k: 0 for k in metric_factories}
        t0 = time.time()

        # 断点续跑：partial.json 里同档位已完成的题目直接复用，不重跑。
        # 本轮单题 2-4 分钟、总量按小时计，重跑代价太高。
        resumed = 0
        partial_path = os.path.join(OUT_DIR, f"partial-{lv}.json")
        # 兼容早期版本的单文件命名 partial.json（其 level 字段可判定归属）
        legacy_path = os.path.join(OUT_DIR, "partial.json")
        for cand in (() if args.no_resume else (partial_path, legacy_path)):
            if not os.path.exists(cand):
                continue
            try:
                with open(cand, encoding="utf-8") as f:
                    prev = json.load(f)
                if prev.get("level") != lv:
                    continue
                want = {c["questionId"] for c in cases}
                # 只有「本轮要的指标全都尝试过」的行才算完成。
                # 旧行可能只跑过更少的指标（比如先跑 3 个再改成 5 个）：
                # 若把它当已完成，缺的指标会永远不补，均值 n 静默变小。
                # 注意判定的是"键存在"而非"值非空"——失败会记成 None，
                # 那也算尝试过，否则每次调用都会无限重跑同一题。
                need_metrics = set(metric_factories)
                seen = {x["questionId"] for x in per_q}
                for r in prev.get("perQuestion", []):
                    qid = r["questionId"]
                    if qid not in want or qid in seen:
                        continue
                    if not need_metrics.issubset(set((r.get("scores") or {}).keys())):
                        continue
                    per_q.append(r)
                    seen.add(qid)
                    resumed += 1
                    for mname, v in (r.get("scores") or {}).items():
                        # 续跑的行可能含本次未启用的指标（旧行是 5 指标跑的），
                        # 不在 sums 里的直接跳过，否则 KeyError
                        if mname in sums and isinstance(v, (int, float)):
                            sums[mname] += float(v)
                            counts[mname] += 1
                if resumed:
                    print(f"  [续跑] 从 {os.path.basename(cand)} 复用 {resumed} 题")
                    break
            except Exception as e:
                print(f"  [续跑] 读取 {os.path.basename(cand)} 失败，忽略：{e}")
        done_ids = {r["questionId"] for r in per_q}
        todo = [c for c in cases if c["questionId"] not in done_ids]

        print(f"\n=== 档位 {lv}：共 {len(cases)} 题，续跑跳过 {resumed}，"
              f"本次跑 {len(todo)}，并发 {args.workers} ===")
        if not todo:
            print("  该档位已全部完成")
        done_n = resumed
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(process_case, c) for c in todo]
            for fut in as_completed(futures):
                row = fut.result()
                per_q.append(row)
                done_n += 1
                for mname, v in row["scores"].items():
                    if isinstance(v, (int, float)):
                        sums[mname] += float(v)
                        counts[mname] += 1
                brief = " ".join(f"{k[:4]}={row['scores'].get(k)}" for k in metric_factories)
                print(f"  [{done_n}/{len(cases)}] {row['questionId']} "
                      f"({row.get('elapsedSec')}s) {brief}", flush=True)
                # 增量落盘（按档位分开存，避免跑下一档时覆盖上一档进度）
                try:
                    with open(partial_path, "w", encoding="utf-8") as f:
                        json.dump({"level": lv, "done": done_n, "total": len(cases),
                                   "perQuestion": per_q, "sums": sums, "counts": counts},
                                  f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
        per_q.sort(key=lambda r: r["questionId"])

        summary = {k: {"mean": round(sums[k] / counts[k], 4) if counts[k] else None,
                       "n": counts[k]} for k in metric_factories}
        all_results[lv] = {"n": len(per_q), "summary": summary,
                           "elapsedSec": round(time.time() - t0, 1), "perQuestion": per_q}
        print(f"  --- {lv} 均值 ---")
        for k, v in summary.items():
            print(f"      {k:22s} {v['mean']}  (n={v['n']})")

    out = os.path.join(OUT_DIR, f"deepeval-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"schema": "career-graph-deepeval/v1",
                   "judge": {"model": name, "base_url": base},
                   "levels": all_results}, f, ensure_ascii=False, indent=2)
    print(f"\n产物：{os.path.relpath(out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
