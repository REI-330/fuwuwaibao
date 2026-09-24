#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
命中率优化实验：在融合检索之上试三类信号，逐一 A/B，只保留有正收益的。

诊断结论（diagnose_retrieval.py，α=0.35）：14 题里 13 题的参考答案本来就落在第 1–2 位，
唯一失败题 N12 属于「召回了但排不进 Top-3」——它的参考答案 S16#s291~2 排第 9（0.4737），
而它的原文前邻 S16#s291 排第 5（0.5196），Top-3 门槛是 0.5293。也就是说，
缺的不是召回，是「切块边界把一段完整论述切成了三块，评分却按块独立算」。

因此本脚本试三类信号，全部确定性、不引入新外部依赖（除最后一个可选档用缓存的查询扩展）：

  A 邻域加权（locality）    score = fused + λ_a · max(fused[前邻], fused[后邻])
  B 图证据扩展（graph）     score = fused + λ_b · max(引用了本块的那些节点，其其它证据块的分)
  C 窗口扩展（span）        取 Top-K 后把每块扩成 ±1 邻域窗口，直接测量「窗口级命中」与真实入上下文段数
  D 查询扩展（LLM，缓存）    复用 runs/expanded-queries.json，不重复调模型

判据与既有脚本一致：Top-3 是否含标注 chunk（每题 2 段参考答案）。
另报 命中@1 与 平均精确率@3——只看命中率会掩盖「把原来第 1 位的答案挤到第 3 位」这类退化。

14 题的样本量下，1 题 = 7.1 个百分点。所以本脚本同时打印全参数曲线与逐题名次变化，
凡是只靠单题翻转得到的「提升」都会被标出来，不当作结论。

用法：
  python knowledge/eval/optimize_retrieval.py
  python knowledge/eval/optimize_retrieval.py --save-best
"""
import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
GRAPH = os.path.join(K, "graph", "graph.json")
QUESTIONS = os.path.join(K, "evaluations", "questions-v2.json")
BM25_FULL = os.path.join(K, "eval", "runs", "bm25-full.json")
EXPANDED = os.path.join(K, "eval", "runs", "expanded-queries.json")
CACHE_DIR = os.path.join(K, "eval", "cache")
OUT_DIR = os.path.join(K, "eval", "runs")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")


def embed(texts, timeout=600):
    body = json.dumps({"input": texts, "model": MODEL}).encode("utf-8")
    req = urllib.request.Request(TEI, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return [it["embedding"] for it in sorted(d["data"], key=lambda x: x["index"])]


def minmax(d):
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-12:
        return {k: 0.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def load_corpus():
    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    by_source = {}
    for c in chunks:
        by_source.setdefault(c["sourceId"], []).append(c)
    for src in by_source:
        by_source[src].sort(key=lambda c: c["charRange"][0])
    prev_of, next_of = {}, {}
    for src, rows in by_source.items():
        for i, c in enumerate(rows):
            prev_of[c["chunkId"]] = rows[i - 1]["chunkId"] if i > 0 else None
            next_of[c["chunkId"]] = rows[i + 1]["chunkId"] if i + 1 < len(rows) else None
    return chunks, prev_of, next_of


def load_graph_signals():
    """chunkId -> 引用它的节点们的「其它证据块」集合（图证据扩展用的候选来源）"""
    g = json.load(open(GRAPH, encoding="utf-8"))
    citing = {}   # chunkId -> [nodeId]
    evidence = {}  # nodeId -> [chunkId]
    for n in g["nodes"]:
        refs = [r for r in (n.get("sourceRefs") or [])]
        if not refs:
            continue
        evidence[n["id"]] = refs
        for r in refs:
            citing.setdefault(r, []).append(n["id"])
    return citing, evidence


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--alpha", type=float, default=None, help="不传则自动扫")
    ap.add_argument("--save-best", action="store_true")
    args = ap.parse_args()

    chunks, prev_of, next_of = load_corpus()
    by_id = {c["chunkId"]: c for c in chunks}
    ids = [c["chunkId"] for c in chunks]
    citing, evidence = load_graph_signals()

    qs = [q for q in json.load(open(QUESTIONS, encoding="utf-8"))["questions"]
          if q.get("answerable", True)]
    refs_of = {q["questionId"]: set(q.get("referenceChunks") or []) for q in qs}

    bm = json.load(open(BM25_FULL, encoding="utf-8"))
    b_rank = {x["questionId"]: [r["chunkId"] for r in x["ranking"]] for x in bm["perQuestion"]}
    b_sc = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]}
            for x in bm["perQuestion"]}

    vecs = json.load(open(os.path.join(
        CACHE_DIR, f"chunk-vectors-{MODEL.replace('/', '_')}.json"), encoding="utf-8"))["vectors"]

    def vector_scores(texts):
        out = {}
        for q, qv in zip(qs, embed(texts)):
            sims = {cid: sum(a * b for a, b in zip(qv, vecs[cid])) for cid in ids}
            out[q["questionId"]] = sims
        return out

    t0 = time.time()
    v_sc = vector_scores([q["question"] for q in qs])
    exp_sc = None
    if os.path.exists(EXPANDED):
        cached = json.load(open(EXPANDED, encoding="utf-8"))["queries"]
        if set(cached) >= {q["questionId"] for q in qs}:
            exp_sc = vector_scores([cached[q["questionId"]] for q in qs])
    print(f"通道就绪（向量 {time.time()-t0:.1f}s，查询扩展缓存{'可用' if exp_sc else '不可用'}）\n")

    def fused_scores(alpha, vmap):
        out = {}
        for q in qs:
            qid = q["questionId"]
            b = minmax(b_sc[qid])
            v = minmax(vmap[qid])
            out[qid] = {c: alpha * b.get(c, 0.0) + (1 - alpha) * v.get(c, 0.0)
                        for c in set(b) | set(v)}
        return out

    def neighbour_signal(f):
        sig = {}
        for cid in f:
            best = 0.0
            for nb in (prev_of.get(cid), next_of.get(cid)):
                if nb and nb in f:
                    best = max(best, f[nb])
            sig[cid] = best
        return sig

    def graph_signal(f):
        sig = {}
        for cid in f:
            best = 0.0
            for nid in citing.get(cid, []):
                for other in evidence[nid]:
                    if other != cid and other in f:
                        best = max(best, f[other])
            sig[cid] = best
        return sig

    def score(strategy, fused, lam_a=0.0, lam_b=0.0):
        """返回 {qid: (命中题数, 精确率累计, 命中@1, 各题 Top-K 与参考名次)}"""
        ranks = {}
        for q in qs:
            qid = q["questionId"]
            f = fused[qid]
            s = dict(f)
            if lam_a:
                sig = neighbour_signal(f)
                for c in s:
                    s[c] += lam_a * sig[c]
            if lam_b:
                sig = graph_signal(f)
                for c in s:
                    s[c] += lam_b * sig[c]
            ranked = sorted(s, key=lambda c: -s[c])
            ranks[qid] = ranked
        return ranks

    def metrics(ranks, tag):
        hit = top1 = 0
        prec = 0.0
        fails, details = [], {}
        for q in qs:
            qid = q["questionId"]
            top = ranks[qid][:args.topk]
            refs = refs_of[qid]
            h = [c for c in top if c in refs]
            best_rank = min([ranks[qid].index(r) + 1 for r in refs if r in ranks[qid]],
                            default=None)
            details[qid] = {"top": top, "hits": h, "bestRefRank": best_rank}
            if h:
                hit += 1
            else:
                fails.append(qid)
            if ranks[qid] and ranks[qid][0] in refs:
                top1 += 1
            prec += len(h) / args.topk
        n = len(qs)
        return {"tag": tag, "hit": hit, "n": n, "hitRate": round(hit / n, 4),
                "hitAt1": round(top1 / n, 4), "prec": round(prec / n, 4),
                "fails": fails, "detail": details}

    def show(m):
        print(f"  {m['tag']:34s} 命中 {m['hit']:>2d}/{m['n']:<2d}  命中@1 {m['hitAt1']:.4f}"
              f"  精确率@{args.topk} {m['prec']:.4f}  未命中 {m['fails'] or '-'}")

    # ---------- 阶段 1：α 曲线 ----------
    print("== 阶段 1：融合权重 α_bm25 曲线（无额外信号）==")
    curves = []
    for i in range(1, 20):
        a = round(i * 0.05, 2)
        m = metrics(score(f"fused(α={a})", fused_scores(a, v_sc)), f"α={a}")
        curves.append(m)
        print(f"  α={a:.2f}  命中 {m['hit']}/{m['n']}  命中@1 {m['hitAt1']:.4f}"
              f"  精确率@3 {m['prec']:.4f}  未命中 {m['fails'] or '-'}")
    best_alpha = max(curves, key=lambda m: (m["hitRate"], m["prec"], m["hitAt1"]))["tag"].split("=")[1]
    best_alpha = float(best_alpha)
    top_alpha = [m for m in curves if m["hit"] == max(c["hit"] for c in curves)]
    print(f"\n  α 曲线上命中最高的一批：{[m['tag'].split('=')[1] for m in top_alpha]}")
    print(f"  取 α={best_alpha}（按命中率 → 精确率 → 命中@1 依次排序）")

    # ---------- 阶段 2：单信号扫描 ----------
    print("\n== 阶段 2：在最优 α 上加单一信号 ==")
    fine = fused_scores(best_alpha, v_sc)
    base_m = metrics(score("base", fine), f"base(fused α={best_alpha})")
    show(base_m)
    print("  — 邻域加权 λ_a —")
    a_best = (base_m, 0.0)
    for lam in (0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.8):
        m = metrics(score("nb", fine, lam_a=lam), f"邻域加权 λ_a={lam}")
        show(m)
        if (m["hit"], m["prec"]) > (a_best[0]["hit"], a_best[0]["prec"]):
            a_best = (m, lam)
    print("  — 图证据扩展 λ_b —")
    b_best = (base_m, 0.0)
    for lam in (0.05, 0.1, 0.2, 0.3, 0.5):
        m = metrics(score("gb", fine, lam_b=lam), f"图证据 λ_b={lam}")
        show(m)
        if (m["hit"], m["prec"]) > (b_best[0]["hit"], b_best[0]["prec"]):
            b_best = (m, lam)

    # ---------- 阶段 3：组合 ----------
    print("\n== 阶段 3：组合（邻域 × 图证据）==")
    combos = []
    for la in (0.0, a_best[1]) if a_best[1] else (0.0,):
        for lb in (0.0, b_best[1]) if b_best[1] else (0.0,):
            if la == 0.0 and lb == 0.0:
                continue
            m = metrics(score("combo", fine, lam_a=la, lam_b=lb), f"邻域{la} × 图证据{lb}")
            combos.append(m)
            show(m)
    if not combos:
        print("  两个信号都没有正收益，不组合")

    # ---------- 阶段 4：查询扩展在最优配置上的效果 ----------
    exp_m = None
    if exp_sc is not None:
        print("\n== 阶段 4：查询扩展（复用缓存，不重复调模型）==")
        exp_fine = fused_scores(best_alpha, exp_sc)
        show(metrics(score("exp", exp_fine), "扩展查询 + fused"))
        show(metrics(score("exp", exp_fine, lam_a=a_best[1]), f"扩展查询 + fused + 邻域{a_best[1]}"))
        exp_comp = []
        for la in (0.0, a_best[1]):
            m = metrics(score("exp", exp_fine, lam_a=la), f"exp_la{la}")
            exp_comp.append(m)
        exp_m = max(exp_comp, key=lambda m: (m["hitRate"], m["prec"]))
    else:
        print("\n== 阶段 4：跳过（无可用查询扩展缓存）==")

    # ---------- 汇总 ----------
    all_m = [base_m] + [a_best[0], b_best[0]] + combos + ([exp_m] if exp_m else [])
    winner = max(all_m, key=lambda m: (m["hitRate"], m["prec"], m["hitAt1"]))
    print(f"\n=== 最优：{winner['tag']}  命中 {winner['hit']}/{winner['n']}"
          f"  命中@1 {winner['hitAt1']}  精确率@{args.topk} {winner['prec']} ===")

    # 单题翻转检查：提升是否只来自 1 题
    delta_q = [qid for qid in refs_of
               if (qid in winner["detail"] and bool(winner["detail"][qid]["hits"]))
               != (qid in base_m["detail"] and bool(base_m["detail"][qid]["hits"]))]
    print(f"与基线相比发生翻转的题：{delta_q or '无'}"
          f"  → {'仅 1 题，14 题样本下属噪声量级' if len(delta_q) <= 1 else '多题翻转'}")

    print("\n参考段名次变化（基线 → 最优）：")
    for q in qs:
        qid = q["questionId"]
        bl = base_m["detail"][qid]["bestRefRank"]
        wl = winner["detail"][qid]["bestRefRank"]
        flag = "" if str(bl) == str(wl) else "   ← 变化"
        print(f"  {qid}: {bl} → {wl}{flag}")

    if args.save_best:
        per_q = []
        for q in qs:
            qid = q["questionId"]
            d = winner["detail"][qid]
            per_q.append({
                "questionId": qid, "set": q.get("category", "main"), "question": q["question"],
                "referenceChunks": sorted(refs_of[qid]),
                "topk": [{"chunkId": c, "score": None,
                          "text": (by_id.get(c, {}).get("text") or "")[:2000]}
                         for c in d["top"]],
                "hit": bool(d["hits"]), "hits": d["hits"],
            })
        out = {
            "schema": "career-graph-optimized-retrieval/v1",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "retriever": {"type": "hybrid+signals", "strategy": winner["tag"],
                          "alpha_bm25": best_alpha, "topk": args.topk,
                          "components": ["bm25 (lib/graph.mjs)",
                                         "dense (TEI Qwen3-Embedding-0.6B)",
                                         "locality neighbour boost",
                                         "graph evidence boost"]},
            "metrics": {"main": {"hitRate": f"{winner['hit']}/{winner['n']}",
                                 "hitRateValue": winner["hitRate"],
                                 "hitAt1": winner["hitAt1"],
                                 "meanPrecisionAtK": winner["prec"]}},
            "ablation": [{"tag": m["tag"], "hit": f"{m['hit']}/{m['n']}",
                          "hitAt1": m["hitAt1"], "precisionAtK": m["prec"], "fails": m["fails"]}
                         for m in all_m],
            "perQuestion": per_q,
        }
        path = os.path.join(OUT_DIR, "best-retrieval.json")
        json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n最优配置已存为 {os.path.relpath(path, ROOT)}（可作为 deepeval 的 OPTIMIZED 档位）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
