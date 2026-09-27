#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
跨语言检索缺口定位：把某几道题的参考答案段，在**四条排序**里各自的名次打出来。

为什么需要它：查询扩展实验只报「命中/没命中」，命中了差多少、没命中差多少全被吞掉。
而跨语言题（中文问 / 英文段）恰恰是「能召回、排不进前三」——它的名次分布才是要看的量。
把同一道题在 基线BM25 / 基线向量 / 扩展BM25 / 扩展向量 里的名次并排放，就能看出：
是字面通道（BM25）压根找不到，还是语义通道能找到但被融合权重压下去。

用法：
  export TEI_MODEL=Qwen3-Embedding-0.6B-onnx-int8
  python knowledge/eval/xl_gap_diagnose.py \
      --questions knowledge/evaluations/questions-dev.json --qids N12,N13 \
      --bm25-baseline knowledge/eval/runs/q1024-dev-bm25-full.json
"""
import argparse
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
RUNS = os.path.join(K, "eval", "runs")
CACHE_DIR = os.path.join(K, "eval", "cache")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
TEI_MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")


def embed(texts, timeout=300):
    body = json.dumps({"input": texts, "model": TEI_MODEL}).encode("utf-8")
    req = urllib.request.Request(TEI, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return [it["embedding"] for it in sorted(d["data"], key=lambda x: x["index"])]


def han_ratio(text):
    if not text:
        return 0.0
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff") / len(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--qids", required=True, help="逗号分隔的问题 id，如 N12,N13,T20")
    ap.add_argument("--bm25-baseline", required=True)
    ap.add_argument("--bm25-expanded", default=os.path.join(RUNS, "bm25-full-expanded.json"))
    ap.add_argument("--expansions", default=os.path.join(RUNS, "expanded-queries.json"))
    ap.add_argument("--topn", type=int, default=5, help="每题打印前 n 名命中的中文占比")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    by_id = {c["chunkId"]: c for c in chunks}
    ids = [c["chunkId"] for c in chunks]
    vecs = json.load(open(os.path.join(
        CACHE_DIR, f"chunk-vectors-{TEI_MODEL.replace('/', '_')}.json"), encoding="utf-8"))["vectors"]
    missed = [i for i in ids if i not in vecs]
    if missed:
        raise SystemExit(f"向量缓存与语料不同步（缺 {len(missed)} 段，如 {missed[:3]}）："
                         f"TEI_MODEL={TEI_MODEL} 是否指向当前 1757 段语料对应的模型？")

    qdoc = json.load(open(args.questions, encoding="utf-8"))
    allq = qdoc.get("questions", qdoc)
    want = {x.strip() for x in args.qids.split(",") if x.strip()}
    qs = [q for q in allq if q["questionId"] in want]
    absent = want - {q["questionId"] for q in qs}
    if absent:
        raise SystemExit(f"题集里没有这些 id：{sorted(absent)}")

    def bm_ranks(path):
        d = json.load(open(path, encoding="utf-8"))
        return {x["questionId"]: [r["chunkId"] for r in x["ranking"]] for x in d["perQuestion"]}

    bm_base = bm_ranks(args.bm25_baseline)
    bm_exp = bm_ranks(args.bm25_expanded) if os.path.exists(args.bm25_expanded) else {}
    expanded = {}
    if os.path.exists(args.expansions):
        expanded = json.load(open(args.expansions, encoding="utf-8"))["queries"]

    qtexts, labels = [], []
    for q in qs:
        qid = q["questionId"]
        qtexts.append(q["question"])
        labels.append((qid, "基线", q["question"]))
        if qid in expanded and expanded[qid] != q["question"]:
            qtexts.append(expanded[qid])
            labels.append((qid, "扩展", expanded[qid]))
    qvecs = embed(qtexts)

    vec_rank, vec_sc = {}, {}
    for (qid, label, _), qv in zip(labels, qvecs):
        sims = sorted(((sum(a * b for a, b in zip(qv, vecs[cid])), cid) for cid in ids), reverse=True)
        vec_rank[(qid, label)] = [c for _, c in sims]
        vec_sc[(qid, label)] = [s for s, _ in sims]

    for q in qs:
        qid = q["questionId"]
        refs = q.get("referenceChunks") or []
        print("=" * 78)
        print(f"{qid}  [{q.get('layer','?')}]  {q['question']}")
        for r in refs:
            c = by_id.get(r)
            t = (c or {}).get("text") or ""
            print(f"  参考段 {r}  长度 {len(t)}  中文占比 {han_ratio(t):.0%}  "
                  f"source {c.get('sourceId') if c else '缺失'}")
        for label, _ in (("基线", None), ("扩展", None)):
            if (qid, label) not in vec_rank:
                continue
            exp_q = expanded.get(qid)
            if label == "扩展":
                print(f"  扩展查询：{exp_q}")
            b_rank = bm_base.get(qid, []) if label == "基线" else bm_exp.get(qid, [])
            v_rank = vec_rank[(qid, label)]
            for r in refs:
                br = b_rank.index(r) + 1 if r in b_rank else None
                vr = v_rank.index(r) + 1 if r in v_rank else None
                print(f"    {label}：BM25 名次 {br if br else '未出现(>50)'}   "
                      f"向量名次 {vr}/{len(v_rank)}")
            top = v_rank[:args.topn]
            print(f"    {label}：向量 Top{args.topn} 中文占比 "
                  + ", ".join(f"{han_ratio((by_id.get(c) or {}).get('text') or ''):.0%}" for c in top)
                  + f"   相似度 {', '.join(f'{s:.3f}' for s in vec_sc[(qid,label)][:args.topn])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
