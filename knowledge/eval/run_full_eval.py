#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一键跑完一个题集的所有档位，并输出合并对照表。

档位（全部用已采纳配置）：
  BM25            ← lib/graph.mjs（中文 segmenter 分词）
  VECTOR          ← TEI Qwen3-Embedding-0.6B，语料向量走缓存
  FUSION α=0.35   ← BM25 与向量逐查询 minmax 归一化后加权
  FUSION+RERANK   ← 融合 Top-10 交 LLM 挑 3 段（采用配置）

产物全部带 --tag 前缀，dev / test 两套题集互不覆盖。

用法：
  python knowledge/eval/run_full_eval.py --questions knowledge/evaluations/questions-test.json --tag test-
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
RUNS = os.path.join(K, "eval", "runs")
PY = sys.executable


def sh(cmd, label):
    print(f"\n$ {label}")
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        print(f"  ⚠ 退出码 {rc}（{label}）", file=sys.stderr)
    return rc


def stat(path, label):
    if not os.path.exists(path):
        return None
    d = json.load(open(path, encoding="utf-8"))
    pq = d.get("perQuestion", [])
    n = len(pq)
    if not n:
        return None
    hit = sum(1 for x in pq if x.get("hit"))
    top1 = sum(1 for x in pq if x.get("topk") and x["topk"][0].get("chunkId")
               in set(x.get("referenceChunks") or []))
    refs = sum(len(x.get("hits") or []) for x in pq)
    return {"label": label, "n": n, "hit": hit, "hitRate": round(hit / n, 4),
            "hitAt1": round(top1 / n, 4), "refsInSlots": refs, "slots": 3 * n,
            "precisionAtK": round(refs / (3 * n), 4), "path": os.path.relpath(path, ROOT)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--tag", required=True, help="产物前缀，如 dev- / test-")
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--alpha", type=float, default=0.35)
    ap.add_argument("--skip-rerank", action="store_true")
    ap.add_argument("--exclude", default="",
                    help="逗号分隔的 chunkId：检索期剔除（导航/列表段等）")
    args = ap.parse_args()

    q = args.questions
    t = args.tag
    bm25_full = os.path.join(RUNS, f"{t}bm25-full.json")
    bm25_topk = os.path.join(RUNS, f"{t}bm25-topk.json")
    vec_topk = os.path.join(RUNS, f"{t}vector-topk.json")
    fusion_topk = os.path.join(RUNS, f"{t}fusion-a{str(args.alpha).replace('.', '')}-topk.json")
    adopted_topk = os.path.join(RUNS, f"{t}adopted-a{str(args.alpha).replace('.', '')}-topk.json")
    rr_cache = os.path.join(RUNS, f"{t}rerank-cache.json")

    sh(["node", "knowledge/pipeline/bm25-full.mjs", "--questions", q, "--out", bm25_full],
       "BM25 完整排序")
    sh(["node", "knowledge/pipeline/bm25-run.mjs", "--questions", q, "--out", bm25_topk,
        "--topk", str(args.topk)], "BM25 Top-K")
    sh([PY, "knowledge/eval/vector_retrieval.py", "--questions", q, "--out", vec_topk,
        "--topk", str(args.topk)], "向量 Top-K")

    cmd = [PY, "knowledge/eval/run_adopted.py", "--questions", q, "--bm25", bm25_full,
           "--rerank-cache", rr_cache, "--tag", t, "--alpha", str(args.alpha),
           "--topk", str(args.topk)]
    if args.exclude:
        cmd += ["--exclude", args.exclude]
    if not args.skip_rerank:
        cmd.append("--do-rerank")
    rc = sh(cmd, "融合 + LLM 重排（已采纳配置）")
    if rc != 0:
        # 重排没跑完就不许出汇总表：否则重排档会静默退化成融合档，
        # 而报告里看起来只像"重排没有增益"。
        print(f"\n✗ 重排环节未成功完成（退出码 {rc}），**不输出对照表**。"
              f"修好后直接重跑本命令即可续跑。", file=sys.stderr)
        return rc

    rows = [stat(bm25_topk, "BM25"), stat(vec_topk, "VECTOR"),
            stat(fusion_topk, f"FUSION α={args.alpha}"), stat(adopted_topk, "FUSION+RERANK")]
    rows = [r for r in rows if r]

    print(f"\n{'='*78}\n合并对照（{os.path.relpath(q, ROOT)}）\n{'='*78}")
    print(f"{'档位':22s} {'n':>4s} {'命中':>8s} {'命中@1':>8s} {'精确率@3':>9s} {'答案段':>9s}")
    for r in rows:
        print(f"{r['label']:22s} {r['n']:>4d} {r['hit']}/{r['n']:<5d} {r['hitAt1']:>8.4f}"
              f" {r['precisionAtK']:>9.4f} {str(r['refsInSlots']) + '/' + str(r['slots']):>9s}")

    out = os.path.join(RUNS, f"{t}summary.json")
    json.dump({"questions": os.path.relpath(q, ROOT), "tag": t,
               "levels": rows}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n汇总产物：{os.path.relpath(out, ROOT)}")

    # 拒答题只统计数量，判定需另跑 refusal_eval.py
    qdoc = json.load(open(q, encoding="utf-8"))
    n_ref = sum(1 for x in qdoc["questions"] if not x.get("answerable", True))
    if n_ref:
        print(f"本集含 {n_ref} 道拒答题，需另跑：\n"
              f"  python knowledge/eval/refusal_eval.py --set {q} --tune   # dev 上标定阈值\n"
              f"  python knowledge/eval/refusal_eval.py --set {q} --threshold <t>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
