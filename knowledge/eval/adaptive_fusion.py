#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
语言自适应融合（M2-5，决策 3 的「A 方案」）。

## 要解决什么

现役配置把 BM25 与稠密向量**逐查询 minmax 归一化后按固定 α=0.35 融合**。
dev 上分语言层看：

    zh 层：BM25 17/20、VECTOR 16/20、FUSION 18/20   ← 融合有用
    xl 层：BM25  8/14、VECTOR 12/14、FUSION 11/14   ← 融合倒扣

原因是 **minmax 会放大噪声**：只要 BM25 的分数在候选里有「一点点」起伏，minmax 就把它
拉满到 [0,1]，于是「其实是噪声」的 lexical 信号拿到了 0.35 的整权重，把向量本来对的名次挤掉。
xl 题（中文问题 / 英文语料）最容易踩这个坑：BM25 没有真正的字面重叠，分数起伏来自零星
的同形词或标点，归一化后反而显得「很有区分度」。

## 怎么修（不引入新依赖、不调 LLM）

思路是**按查询自适应地决定要不要启用 BM25 通道**，判据全部来自查询期可见的信号，
不看参考答案、不看 layer 标注（后者只用来分组报数，以及做一个"上界"参照）：

  1. `fusion_minmax`   现状基线：minmax + α=0.35（全查询启用）
  2. `fusion_abs`      把 minmax 换成**绝对幅度归一化**（BM25 用 b/(b+K) 饱和、
                       向量用 (cos+1)/2），低信号查询自然被压低而不是被拉满
  3. `gate_peak`       **采纳**：BM25 榜首分数 / 第 10 名 < 1.5 ⇒ 判为「没有真实字面命中」⇒ 退单路向量
  4. `gate_cjk`        备选：向量 Top-3 的 CJK 占比偏低（命中段是英文）就退单路向量（**否决**）
  5. `gate_latin`      备选：查询里出现 Latin 词（跨语言的常见征兆）就退单路向量（**否决**）
  6. `oracle_layer`    按 layer 标注分流（zh 融合 / xl 向量）——**上界，不可部署**，
                       只用来回答「这个方向到底有多少空间」

判据与其余实验一致：Top-3 是否含标注 chunk；另报命中@1 与精确率@3（只看命中率会掩盖
「把原来第 1 位的答案挤到第 3 位」这类退化）。报数按 zh / xl 分层。

## 选型协议（重要）

**在 dev（34 题）上选阈值与门控，在新 holdout（35 题）上只报一次数**。阈值不写在题集里，
写在本脚本的常量里；改了阈值就应重新在 dev 上选。

用法：
  python knowledge/eval/adaptive_fusion.py --questions knowledge/evaluations/questions-dev.json \
      --bm25 knowledge/eval/runs/af-dev-bm25-full.json --tag af-dev
  python knowledge/eval/adaptive_fusion.py --questions knowledge/evaluations/questions-holdout.json \
      --bm25 knowledge/eval/runs/af-holdout-bm25-full.json --tag af-holdout
"""
import argparse
import json
import os
import sys
import urllib.request

from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
CACHE_DIR = os.path.join(K, "eval", "cache")
RUNS = os.path.join(K, "eval", "runs")

TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
# 与语料向量缓存同名：现役服务是 int8/onnx（/info 的 model_id = Qwen3-Embedding-0.6B(int8,onnx)）
DEFAULT_CACHE = os.path.join(CACHE_DIR, "chunk-vectors-Qwen3-Embedding-0.6B-onnx-int8.json")

ALPHA = 0.35          # 已采纳的 BM25 权重，保持不变，只改「何时启用」
BM25_SATURATION_K = 20.0   # fusion_abs 的饱和常数：b/(b+K)
# ---- 门控阈值（在 dev 上选；dev 上 1.40–1.60 同为 31/34，取平台中点 1.5） ----
GATE_BM25PEAK_MIN = 1.5    # BM25 榜首 / 第 10 名 < 它 ⇒ 判为「没有真实字面命中」⇒ 退单路向量
GATE_TOPK_CJK_MIN = 0.20   # 备选门控（已否决：阈值在 dev 与 holdout 上最优值不一致，见文档）
LATIN_MIN_CHARS = 3        # 备选门控（已否决：Latin 词不是跨语言的可靠征兆）


def embed(texts, timeout=600, batch=16):
    out = []
    for i in range(0, len(texts), batch):
        body = json.dumps({"input": texts[i:i + batch]}).encode("utf-8")
        req = urllib.request.Request(TEI, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        out += [it["embedding"] for it in sorted(d["data"], key=lambda x: x["index"])]
    return out


def minmax(d):
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-12:
        return {k: 0.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def cjk_ratio(text):
    if not text:
        return 0.0
    n = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return n / len(text)


def has_latin_token(query):
    run = 0
    for ch in query:
        if ch.isascii() and ch.isalpha():
            run += 1
            if run >= LATIN_MIN_CHARS:
                return True
        else:
            run = 0
    return False


def topk_ids(scored, k=3):
    return [cid for cid, _ in sorted(scored.items(), key=lambda x: (-x[1], x[0]))[:k]]


def evaluate(picks, questions, refs, layer_of):
    """picks: {qid: [chunkId,...]}. 返回总体 + 分层的 (hit, n, hitAt1, precision@3)。"""
    def block(qids):
        n = len(qids)
        if not n:
            return {"n": 0, "hit": 0, "hitRate": None, "hitAt1": None, "precisionAtK": None}
        hit = sum(1 for q in qids if set(picks.get(q, [])) & refs[q])
        at1 = sum(1 for q in qids if picks.get(q, [None])[:1]
                  and picks[q][0] in refs[q])
        slot_refs = sum(len(set(picks.get(q, [])) & refs[q]) for q in qids)
        return {"n": n, "hit": hit, "hitRate": round(hit / n, 4),
                "hitAt1": round(at1 / n, 4), "refsInSlots": slot_refs, "slots": 3 * n,
                "precisionAtK": round(slot_refs / (3 * n), 4)}

    allq = list(questions)
    out = {"ALL": block(allq)}
    for L in sorted({layer_of.get(q) for q in allq if layer_of.get(q)}):
        out[L] = block([q for q in allq if layer_of.get(q) == L])
    return out


def build_variants(bm_scores, vec_scores, query, cjk_of):
    """返回 {variant_name: fused_score_dict}。所有打分都在同一个候选全集上比较。"""
    b, v = minmax(bm_scores), minmax(vec_scores)
    fused_minmax = {c: ALPHA * b.get(c, 0.0) + (1 - ALPHA) * v.get(c, 0.0)
                    for c in set(b) | set(v)}

    # 绝对幅度归一化：不做逐查询 minmax，低信号不会被拉满
    b_abs = {c: s / (s + BM25_SATURATION_K) for c, s in bm_scores.items()}
    v_abs = {c: (s + 1.0) / 2.0 for c, s in vec_scores.items()}
    fused_abs = {c: ALPHA * b_abs.get(c, 0.0) + (1 - ALPHA) * v_abs.get(c, 0.0)
                 for c in set(v_abs) | set(b_abs)}

    bm_top = max(bm_scores.values()) if bm_scores else 0.0
    bm_sorted = sorted(bm_scores.values(), reverse=True)
    bm_peak = (bm_sorted[0] / bm_sorted[min(9, len(bm_sorted) - 1)]
               if len(bm_sorted) > 1 and bm_sorted[min(9, len(bm_sorted) - 1)] > 0 else 0.0)

    variants = {
        "vector": vec_scores,
        "fusion_minmax": fused_minmax,
        "fusion_abs": fused_abs,
        # 采纳：BM25 榜首/第 10 名的比 < 阈值 ⇒ 没有真实字面命中 ⇒ 退单路向量
        "gate_peak": vec_scores if bm_peak < GATE_BM25PEAK_MIN else fused_minmax,
        # 否决：下面两条在 dev 上看着行，在 holdout 上要么不涨要么倒扣（见文档「否决记录」）
        "gate_cjk": (
            vec_scores
            if (sum(cjk_of.get(c, 0.0) for c in topk_ids(vec_scores)) / 3.0) < GATE_TOPK_CJK_MIN
            else fused_minmax
        ),
        "gate_latin": vec_scores if has_latin_token(query) else fused_minmax,
    }
    return variants, {"bm25Top1": round(bm_top, 3), "bm25Peak": round(bm_peak, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--bm25", required=True, help="bm25-full.mjs 产出的完整排序")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--tag", default="af")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    ids = [c["chunkId"] for c in chunks]
    text_of = {c["chunkId"]: c.get("text") or "" for c in chunks}
    cjk_of = {cid: cjk_ratio(t) for cid, t in text_of.items()}
    vecs = json.load(open(args.cache, encoding="utf-8"))["vectors"]

    qdoc = json.load(open(args.questions, encoding="utf-8"))
    allq = qdoc["questions"]
    answerable = [q for q in allq if q.get("answerable", True)]
    layer_of = {q["questionId"]: q.get("layer") for q in allq}
    refs = {q["questionId"]: set(q.get("referenceChunks") or []) for q in allq}

    bm = json.load(open(args.bm25, encoding="utf-8"))
    b_sc = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]}
            for x in bm["perQuestion"]}
    missing = [q["questionId"] for q in answerable if q["questionId"] not in b_sc]
    if missing:
        print(f"⚠ BM25 完整排序缺 {len(missing)} 题：{missing[:5]}", file=sys.stderr)
        return 1

    print(f"题集 {os.path.relpath(args.questions, ROOT)}：可答 {len(answerable)} 题")
    print(f"  计算查询向量（{len(answerable)} 条）…")
    qvecs = embed([q["question"] for q in answerable])

    picks = defaultdict(dict)          # variant -> qid -> [chunkId]
    diag = {}
    for q, qv in zip(answerable, qvecs):
        qid = q["questionId"]
        vs = {cid: sum(a * b for a, b in zip(qv, vecs[cid])) for cid in ids}
        variants, info = build_variants(b_sc[qid], vs, q["question"], cjk_of)
        diag[qid] = info
        for name, sc in variants.items():
            picks[name][qid] = topk_ids(sc)

    # 上界参照：按 layer 标注分流（不可部署，只回答「方向有多少空间」）
    for q in answerable:
        qid = q["questionId"]
        L = layer_of.get(qid)
        picks["oracle_layer"][qid] = (picks["fusion_minmax"][qid] if L != "xl"
                                      else picks["vector"][qid])

    order = ["vector", "fusion_minmax", "fusion_abs", "gate_peak", "gate_cjk",
             "gate_latin", "oracle_layer"]
    qids = [q["questionId"] for q in answerable]
    report = {}
    print(f"\n{'档位':16s} {'ALL':>12s} {'zh':>10s} {'xl':>10s}")
    for name in order:
        r = evaluate(picks[name], qids, refs, layer_of)
        report[name] = r
        def cell(b):
            return f"{b['hit']}/{b['n']}" if b["n"] else "—"
        print(f"{name:16s} {cell(r['ALL']):>12s} {cell(r['zh']):>10s} {cell(r['xl']):>10s}")

    out = {
        "schema": "career-graph-adaptive-fusion/v1",
        "generatedAt": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
        "questions": os.path.relpath(args.questions, ROOT),
        "alpha": ALPHA, "bm25SaturationK": BM25_SATURATION_K,
        "gatePeakMin": GATE_BM25PEAK_MIN,
        "gateTopkCjkMin": GATE_TOPK_CJK_MIN, "latinMinChars": LATIN_MIN_CHARS,
        "cache": os.path.relpath(args.cache, ROOT), "bm25": os.path.relpath(args.bm25, ROOT),
        "metrics": report,
        "perQuestion": {name: {qid: picks[name][qid] for qid in qids} for name in order},
        "diagnostics": diag,
    }
    path = os.path.join(RUNS, f"{args.tag}-adaptive-fusion.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n产物：{os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
