#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
拒答能力评测：阈值在 dev 上标定，test 上只报数。

为什么用「max 余弦」当信号：
  融合分数是**逐查询 minmax 归一化**的，跨查询不可比——这正是旧版阈值失效的根因
  （旧报告：阈值取「Top-1 分的下四分位」，检索一进步阈值就跟着漂）。
  max 余弦是绝对值：查询向量与全语料的最大余弦，与其它查询无关，所以可以定一个固定阈值。

判定口径：
  判定「资料不足」 ⟺ max 余弦 < 阈值
  正确 = (拒答题 且 判拒) 或 (可答题 且 未判拒)
  指标：拒答正确率（拒答集上判对的比例）、误拒率（可答集上被判拒的比例）、平衡准确率

答案表述核查（可选）：对拒答题用 Top-3 上下文生成回答，交裁判判断「是否明确说明资料不足」。

用法：
  python knowledge/eval/refusal_eval.py --set knowledge/evaluations/questions-dev.json --tune
  python knowledge/eval/refusal_eval.py --set knowledge/evaluations/questions-test.json --threshold 0.62
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
RUNS = os.path.join(K, "eval", "runs")
CACHE_DIR = os.path.join(K, "eval", "cache")
ENV_FILE = os.path.join(K, "eval", ".env")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")

ANSWER_PROMPT = """你是职业导航知识助手。仅依据下列检索片段回答问题，不要编造片段之外的内容。
若片段不足以回答，明确说明资料不足。

问题：{question}

检索片段：
{context}

回答："""

JUDGE_PROMPT = """下面是一个问答系统的回答。请判断：该回答是否**明确说明了资料不足以回答该问题**
（例如"资料中没有相关信息""无法从现有资料得出"）？还是**给出了实质回答**？

只输出一行，格式为 `结论：ABSTAIN` 或 `结论：ANSWER`，不要解释。

问题：{q}

回答：
{a}
"""


def load_env():
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def llm(prompt, timeout=180, max_tokens=500):
    base = os.environ["DEEPEVAL_BASE_URL"].rstrip("/")
    body = json.dumps({"model": os.environ["DEEPEVAL_MODEL"], "temperature": 0,
                       "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + os.environ["DEEPEVAL_API_KEY"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return (d["choices"][0]["message"]["content"] or "").strip()


def embed(texts, timeout=600, batch=8):
    out = []
    for i in range(0, len(texts), batch):
        body = json.dumps({"input": texts[i:i + batch], "model": MODEL}).encode("utf-8")
        req = urllib.request.Request(TEI, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        out += [it["embedding"] for it in sorted(d["data"], key=lambda x: x["index"])]
    return out


def max_cos(qdoc, ids, vecs):
    """每题与其他全语料的最大余弦（绝对值，跨查询可比）。"""
    qs = qdoc["questions"]
    res = {}
    qvecs = embed([q["question"] for q in qs])
    for q, qv in zip(qs, qvecs):
        best = max(sum(a * b for a, b in zip(qv, vecs[cid])) for cid in ids)
        res[q["questionId"]] = best
    return res


def balanced_accuracy(scores, answerable, threshold):
    tp = tn = n_ref = n_ans = 0
    for qid, s in scores.items():
        judged_refuse = s < threshold
        if answerable[qid]:
            n_ans += 1
            if not judged_refuse:
                tn += 1
        else:
            n_ref += 1
            if judged_refuse:
                tp += 1
    rejection = tp / n_ref if n_ref else None      # 拒答正确率
    false_reject = 1 - (tn / n_ans) if n_ans else None  # 误拒率
    bal = ((rejection or 0) + (tn / n_ans if n_ans else 0)) / 2
    return {"threshold": round(threshold, 4), "rejectionRate": round(rejection, 4) if rejection is not None else None,
            "falseRejectRate": round(false_reject, 4) if false_reject is not None else None,
            "balancedAccuracy": round(bal, 4), "nRefusal": n_ref, "nAnswerable": n_ans}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--tune", action="store_true", help="扫描阈值并选出最优")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--judge", action="store_true", help="另做答案表述核查（调裁判）")
    args = ap.parse_args()
    load_env()

    qdoc = json.load(open(args.set, encoding="utf-8"))
    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    ids = [c["chunkId"] for c in chunks]
    vecs = json.load(open(os.path.join(
        CACHE_DIR, f"chunk-vectors-{MODEL.replace('/', '_')}.json"), encoding="utf-8"))["vectors"]

    answerable = {q["questionId"]: q.get("answerable", True) for q in qdoc["questions"]}
    refs = {q["questionId"]: q.get("referenceChunks") or [] for q in qdoc["questions"]}
    print(f"题集 {os.path.relpath(args.set, ROOT)}：可回答 "
          f"{sum(answerable.values())} / 拒答 {sum(1 for v in answerable.values() if not v)}")

    scores = max_cos(qdoc, ids, vecs)

    chosen = args.threshold
    if args.tune:
        print("\n阈值扫描（max 余弦越低越像「资料不足」）：")
        grid = [round(0.30 + 0.005 * i, 3) for i in range(140)]
        rows = [balanced_accuracy(scores, answerable, t) for t in grid]
        best = max(rows, key=lambda r: (r["balancedAccuracy"], r["rejectionRate"] or 0))
        for r in rows[::10]:
            print(f"  t={r['threshold']:.3f}  拒答正确率 {r['rejectionRate']}  "
                  f"误拒率 {r['falseRejectRate']}  平衡准确率 {r['balancedAccuracy']}")
        print(f"  → 最优 t={best['threshold']}  拒答正确率 {best['rejectionRate']}  "
              f"误拒率 {best['falseRejectRate']}  平衡准确率 {best['balancedAccuracy']}")
        chosen = best["threshold"]
        json.dump({"tunedOn": os.path.relpath(args.set, ROOT), "threshold": chosen,
                   "curve": rows}, open(os.path.join(RUNS, "refusal-threshold.json"), "w",
                                        encoding="utf-8"), ensure_ascii=False, indent=2)

    if chosen is None:
        print("既未 --tune 也未给 --threshold，只报分布：")
        for qid, s in sorted(scores.items(), key=lambda x: x[1]):
            print(f"  {qid} {'可答' if answerable[qid] else '拒答'} {s:.4f}")
        return 0

    m = balanced_accuracy(scores, answerable, chosen)
    print(f"\n=== 固定阈值 t={chosen} ===")
    print(f"  拒答正确率 {m['rejectionRate']}（{m['nRefusal']} 道拒答题）")
    print(f"  误拒率     {m['falseRejectRate']}（{m['nAnswerable']} 道可答题）")
    print(f"  平衡准确率 {m['balancedAccuracy']}")

    wrong = [(qid, s, answerable[qid]) for qid, s in scores.items()
             if (answerable[qid] and s < chosen) or (not answerable[qid] and s >= chosen)]
    if wrong:
        print(f"\n  判错的 {len(wrong)} 题：")
        for qid, s, ans in sorted(wrong, key=lambda x: x[1]):
            print(f"    {qid} {'可答题被误拒' if ans else '拒答题被当成可答'}  max余弦={s:.4f}")

    out = {"schema": "career-graph-refusal-eval/v1",
           "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "set": os.path.relpath(args.set, ROOT), "signal": "max cosine（绝对值，跨查询可比）",
           "threshold": chosen, "metrics": m,
           "perQuestion": [{"questionId": qid, "answerable": answerable[qid],
                            "maxCos": round(s, 6),
                            "judgedRefusal": s < chosen,
                            "correct": (s < chosen) == (not answerable[qid]),
                            "referenceChunks": refs[qid]} for qid, s in sorted(scores.items())]}

    if args.judge:
        print("\n=== 答案表述核查（对拒答题生成回答后交裁判）===")
        by_id = {c["chunkId"]: c for c in chunks}
        jr = []
        for q in qdoc["questions"]:
            if q.get("answerable", True):
                continue
            qid = q["questionId"]
            qv = embed([q["question"]])[0]
            scored = sorted(((sum(a * b for a, b in zip(qv, vecs[cid])), cid) for cid in ids),
                            reverse=True)[:3]
            ctx = "\n".join(f"[{i+1}] {by_id[cid]['text'][:1200]}"
                            for i, (_, cid) in enumerate(scored))
            try:
                ans = llm(ANSWER_PROMPT.format(question=q["question"], context=ctx), max_tokens=400)
                verdict = llm(JUDGE_PROMPT.format(q=q["question"], a=ans[:1500]), max_tokens=30)
            except Exception as e:
                verdict = f"ERROR {type(e).__name__}"
            ok = "ABSTAIN" in verdict.upper()
            jr.append({"questionId": qid, "judgedRefusal": ok, "verdict": verdict[:60],
                       "answerHead": ans[:200]})
            print(f"  {qid} {'✓ 明确说资料不足' if ok else '✗ 未说明资料不足'}  {verdict[:40]}")
        rate = sum(1 for r in jr if r["judgedRefusal"]) / len(jr) if jr else None
        print(f"  → 拒答表述正确率 {rate:.4f}（{sum(1 for r in jr if r['judgedRefusal'])}/{len(jr)}）")
        out["answerSideCheck"] = {"rate": round(rate, 4) if rate is not None else None, "perQuestion": jr}

    path = os.path.join(RUNS, f"refusal-eval-{qdoc.get('split', 'set')}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n产物：{os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
