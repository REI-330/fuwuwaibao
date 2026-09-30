#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
给 WeKnora 的检索结果套上同一套 LLM 重排，做「同后处理」的对照。

为什么要这一步：
  对照里 WeKnora 是它自己的混合检索（FTS5 + sqlite-vec），而我们的最佳档位是
  「融合 + LLM 重排」。直接比两者，比的是「检索栈 + 后处理」的合成差，说明不了问题。
  本脚本固定后处理（同一段提示、同一候选深度、同一模型），只让检索栈不同。

输入：`runs/wk-{dev,test}-weknora-topk.json` 里每条题目的 `topAll`（已按 knowledge_title
对齐回本地 chunkId 的 Top-50）。重排结果按候选段对缓存，可重复跑。

用法：
  python knowledge/eval/weknora_rerank_eval.py --input knowledge/eval/runs/wk-dev-weknora-topk.json --tag wk-dev
  python knowledge/eval/weknora_rerank_eval.py --input knowledge/eval/runs/wk-test-weknora-topk.json --tag wk-test
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from _shared_llm import chat  # 评测与产品共用的 LLM 入口（knowledge/eval/_shared_llm.py）

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
RUNS = os.path.join(K, "eval", "runs")
ENV_FILE = os.path.join(K, "eval", ".env")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")

RERANK_PROMPT = """下面是从职业知识库里检索出的候选片段，请选出最有助于回答该问题的 3 段。

要求：
- 只依据片段内容判断，不要用你自己的先验知识补全；
- 若多个片段来自同一章节且内容重复，只保留信息量最大的一段；
- 按相关度从高到低输出，每行一个片段 id，格式为 `id: <片段id>`，不要解释、不要输出片段正文。

问题：{question}

候选片段：
{candidates}
"""


def load_env():
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def llm(prompt, timeout=240, max_tokens=1500, attempts=6):
    """统一走 `_shared_llm`；重排脚本原来自己的退避（4s 起步、上限 20s）在这里指定。

    原 max_tokens 默认 300：对现役推理模型不够（思维链吃额度、正文回空串），
    重排结果会静默退化成一堆空输出。
    """
    return chat(prompt, max_tokens=max_tokens, timeout=timeout, attempts=attempts, backoff=(4.0, 20.0))


def parse_ids(text, allowed, k=3):
    found = []
    for m in re.finditer(r"([A-Za-z0-9][A-Za-z0-9#~._-]*)", text or ""):
        t = m.group(1)
        if t in allowed and t not in found:
            found.append(t)
    return found[:k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="weknora_eval.py 的产物")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--candidates", type=int, default=10)
    ap.add_argument("--pace", type=float, default=1.0)
    args = ap.parse_args()
    load_env()

    by_id = {json.loads(l)["chunkId"]: json.loads(l)
             for l in open(CHUNKS, encoding="utf-8") if l.strip()}
    doc = json.load(open(args.input, encoding="utf-8"))
    cache_path = os.path.join(RUNS, f"{args.tag}-weknora-rerank-cache.json")
    cache = json.load(open(cache_path, encoding="utf-8"))["picks"] if os.path.exists(cache_path) else {}

    call = 0
    per_q = []
    for pq in doc["perQuestion"]:
        qid = pq["questionId"]
        # 候选：WeKnora 的 Top-N（按其自身排序，去重后取前 N 个段）
        cand, seen = [], set()
        for it in pq.get("topAll", []):
            cid = it.get("chunkId")
            if cid and cid not in seen:
                seen.add(cid)
                cand.append(cid)
        cand = cand[:args.candidates]
        if not cand:
            per_q.append({**pq, "reranked": [], "hitRerank": False,
                          "hitsRerank": [], "note": "无已对齐候选，跳过"})
            continue
        if qid not in cache:
            blocks = []
            for cid in cand:
                c = by_id.get(cid, {})
                blocks.append(f"id: {cid}\n章节: {c.get('sectionPath') or ''}\n"
                              f"正文: {(c.get('text') or '')[:280]}")
            try:
                out = llm(RERANK_PROMPT.format(question=pq["question"],
                                               candidates="\n".join(blocks)))
                picks = parse_ids(out, set(cand), 3)
            except Exception as e:
                print(f"  {qid} 重排失败 {e}")
                picks = []
            cache[qid] = {"candidates": cand, "picked": picks}
            json.dump({"picks": cache}, open(cache_path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            call += 1
            time.sleep(args.pace)
        picks = list(cache[qid]["picked"])
        for cid in cand:                      # 不足 3 段时用原顺序补齐
            if len(picks) >= 3:
                break
            if cid not in picks:
                picks.append(cid)
        picks = picks[:3]
        refs = set(pq["referenceChunks"])
        hits = [c for c in picks if c in refs]
        per_q.append({"questionId": qid, "question": pq["question"],
                      "referenceChunks": pq["referenceChunks"],
                      "reranked": picks, "hitRerank": bool(hits), "hitsRerank": hits})

    n = len(per_q)
    hit = sum(1 for x in per_q if x.get("hitRerank"))
    refs_in = sum(len(x.get("hitsRerank") or []) for x in per_q)
    tot = sum(len(x["referenceChunks"]) for x in per_q)
    m = {"hitRate": f"{hit}/{n}", "hitRateValue": round(hit / n, 4),
         "precisionAt3": round(refs_in / (3 * n), 4),
         "recallAt3": round(refs_in / tot, 4)}
    out = {"schema": "career-graph-weknora-rerank/v1",
           "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "source": os.path.relpath(args.input, ROOT),
           "pipeline": "WeKnora hybrid-search → 对齐到本地 chunkId → 同一套 LLM 重排 Top-3",
           "candidates": args.candidates, "metrics": {"main": m}, "perQuestion": per_q}
    path = os.path.join(RUNS, f"{args.tag}-weknora-rerank.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\n=== WeKnora + 同套 LLM 重排（{os.path.basename(args.input)}）===")
    print(f"  命中率@3 {m['hitRate']} ({m['hitRateValue']})")
    print(f"  准确率 P@3 {m['precisionAt3']}")
    print(f"  召回率 R@3 {m['recallAt3']}")
    print(f"  本次新增 LLM 调用 {call} 次")
    print(f"  产物：{os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
