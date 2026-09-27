#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
查询扩展实验：用 LLM 把中文问题扩展成中英双语关键词查询，再测命中率。

动机（实测）：唯一失败题 N12 是「中文问 / 英文 O*NET 语料」——它的参考答案在向量
检索里排第 8 与第 20（能召回、排不进前三），BM25 完全找不到（字面零重叠）。这是
跨语言匹配问题，靠调融合权重解决不了。

做法：对每题生成一条扩展查询（中文原词 + 英文对应词），分别对扩展查询重跑 BM25 与
向量检索，再与基线在同一套题上比。

代价必须如实报：LLM 调用使检索非确定、并增加延迟与外部依赖。因此本脚本同时输出
「基线（无扩展）」与「扩展后」两组数字，供权衡。

用法：
  # dev 集（含 zh/xl 分层），基线 BM25 必须换成同一题集的产物
  python knowledge/eval/query_expansion_experiment.py \
      --questions knowledge/evaluations/questions-dev.json \
      --bm25-baseline knowledge/eval/runs/q1024-dev-bm25-full.json \
      --tag dev

  # 已有一份扩展结果时，只重算指标、不再调 LLM（结果落盘 query-expansion-<tag>.json）
  python knowledge/eval/query_expansion_experiment.py --reuse-expansions --tag dev-reuse

  # 用语料内双语术语词典做确定性展开（零 LLM）；产物是 expanded-queries-termmap.json
  python knowledge/eval/query_expansion_experiment.py --expand-with term-map --tag dev-termmap
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
QUESTIONS = os.path.join(K, "evaluations", "questions-v2.json")
RUNS = os.path.join(K, "eval", "runs")
CACHE_DIR = os.path.join(K, "eval", "cache")
ENV_FILE = os.path.join(K, "eval", ".env")
TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
TEI_MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")

# 旧版提示词只说「补上对应的英文术语」，deepseek-v4-flash 实测 34/34 题全都无视，
# 输出的是纯中文改写（新增英文词 0 个），跨语言假设根本没被触发。加两个中英对照示例后才稳定。
EXPAND_PROMPT = """你是检索查询改写器。待检索的语料大量是英文技术文档，所以必须把问题里的
中文技术概念换成对应的英文术语——只有中英混排的查询才能同时命中中文与英文语料。

要求：
1) 专有名词（型号、库名、API 名、参数名、文件名）原样保留；
2) 中文技术概念后面紧跟其英文术语，写成「中文(English)」；
3) 只输出一行查询串。不要解释、不要编号、不要引号、不要换行。

示例 1
问题：用飞桨怎么把一个 Python 列表变成 Tensor？
查询：飞桨 PaddlePaddle 把 Python list 列表转成 Tensor 张量用哪个接口 paddle.to_tensor

示例 2
问题：ESP32 的深度睡眠模式下 RTC 定时器怎么配置？
查询：ESP32 深度睡眠 deep sleep RTC 定时器 timer 怎么配置 configure 配置方法

问题：{q}
查询："""


def load_env():
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def llm(prompt, timeout=120, attempts=4):
    """带退避重试。端点对突发调用会直接拒（实测：连打 30+ 次后大量 HTTPError），
    不加重试会让"扩展失败"静默退化成"用原问题"——表面上实验跑完了，实际基线=实验组。"""
    base = os.environ["DEEPEVAL_BASE_URL"].rstrip("/")
    # max_tokens 要够：端点上现役的 deepseek-v4.1-flash 是推理模型，思维链也吃这个额度，
    # 给 200 时 content 常被挤空（实测 reasoning_tokens 已占十余个）。
    body = json.dumps({"model": os.environ["DEEPEVAL_MODEL"], "temperature": 0,
                       "max_tokens": 1500,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(base + "/chat/completions", data=body,
                                         headers={"Authorization": "Bearer " + os.environ["DEEPEVAL_API_KEY"],
                                                  "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            text = (d["choices"][0]["message"].get("content") or "").strip()
            if text:
                return text.splitlines()[0].strip()
            last = "返回内容为空"
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__} {error}"
        time.sleep(min(2 ** i, 8))
    raise RuntimeError(str(last))


def embed(texts, timeout=300):
    body = json.dumps({"input": texts, "model": TEI_MODEL}).encode("utf-8")
    req = urllib.request.Request(TEI, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return [it["embedding"] for it in sorted(d["data"], key=lambda x: x["index"])]


def minmax(d):
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    return {k: 0.0 for k in d} if hi - lo < 1e-12 else {k: (v - lo) / (hi - lo) for k, v in d.items()}


TERM_MAP = os.path.join(K, "term-map", "zh-en.json")
# 只认长度 ≥3 的中文键：短到 2 个字的键（"能力""教育"）几乎每道题都命中，只会往查询里灌噪声。
TERM_KEY_MIN = 3


def norm_zh(s):
    """中文归一化：去掉通用中心词「软件」。

    语料的类别标签是 `Data base ... software`，而人提问会写成「在数据库和操作系统这两类
    软件上」——「数据库软件」不是原文的子串。不归一化就永远匹配不上，词典形同虚设。
    """
    return (s or "").replace("软件", "").strip()


def term_map_expand(question, terms, kinds):
    """确定性展开：词典里任一中文字面（标准译名或短词）出现在问题里 → 追加对应英文术语。

    零 LLM 调用、零随机性；这是"术语对不上"的直接解法（见 term-map/README 与
    `knowledge/evaluations/查询扩展与跨语言缺口-20260927.md`）。
    """
    q = norm_zh(question)
    hits, fired, seen = [], [], set()
    for t in terms:
        if t["kind"] not in kinds:
            continue
        for key in [t["zh"]] + list(t.get("zh_short") or []):
            k = norm_zh(key)
            if len(k) >= TERM_KEY_MIN and k in q:
                fired.append({"en": t["en"], "kind": t["kind"], "matchedKey": key})
                if t["en"] not in seen:
                    seen.add(t["en"])
                    hits.append(t["en"])
                break
    return hits, fired


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--alpha", type=float, default=0.35, help="BM25 权重（基线实验的最优值）")
    ap.add_argument("--questions", default=QUESTIONS,
                    help="题集文件；用 questions-dev.json 可看 zh/xl 分层（本脚本原先把题集写死为 questions-v2.json）")
    ap.add_argument("--bm25-baseline", default=os.path.join(RUNS, "bm25-full.json"),
                    help="基线 BM25 完整排序产物；换题集时必须同步换（如 q1024-dev-bm25-full.json）")
    ap.add_argument("--expand-with", choices=["llm", "term-map"], default="llm",
                    help="term-map = 用语料内双语术语词典做确定性展开（零 LLM 调用、零随机性）")
    ap.add_argument("--term-map", default=TERM_MAP, help="术语词典路径（build_term_map.py 产出）")
    ap.add_argument("--term-kinds", default="occupation,software_category,section",
                    help="启用哪些类型的术语：occupation / software_category / section")
    ap.add_argument("--reuse-expansions", action="store_true",
                    help="复用已有的 expanded-queries*.json / bm25-full-expanded*.json，不再调 LLM（同一份扩展可复现分析）")
    ap.add_argument("--tag", default="", help="结果文件名后缀，如 dev → query-expansion-dev.json")
    ap.add_argument("--save-best", action="store_true")
    args = ap.parse_args()
    load_env()

    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    by_id = {c["chunkId"]: c for c in chunks}
    ids = [c["chunkId"] for c in chunks]
    qs = [q for q in json.load(open(args.questions, encoding="utf-8"))["questions"]
          if q.get("answerable", True)]
    print(f"题集：{os.path.relpath(args.questions, ROOT)}（{len(qs)} 题可回答）")
    vecs = json.load(open(os.path.join(CACHE_DIR,
        f"chunk-vectors-{TEI_MODEL.replace('/', '_')}.json"), encoding="utf-8"))["vectors"]
    # 向量缓存与语料必须同源：语料中文化后是 1757 段，而 TEI_MODEL 默认值对应的是中文化
    # 之前的 494 段旧缓存。不同步时会在下面按 chunkId 取向量时抛一句没有信息量的 KeyError。
    missed = [i for i in ids if i not in vecs]
    if missed:
        raise SystemExit(
            f"向量缓存与语料不同步：chunks.jsonl 有 {len(ids)} 段，"
            f"chunk-vectors-{TEI_MODEL.replace('/', '_')}.json 只有 {len(vecs)} 段，"
            f"缺 {len(missed)} 段（如 {missed[:3]}）。\n"
            "  → 现役语料为 1757 段（中文化后），请显式指定 TEI_MODEL=Qwen3-Embedding-0.6B-onnx-int8；"
            "默认的 Qwen3-Embedding-0.6B 是 494 段时代留下的旧缓存。")

    # 两种展开方式不共用产物文件：否则跑完 term-map 会把 LLM 那版覆盖掉（上次就是这么丢的）。
    sfx = "" if args.expand_with == "llm" else "-termmap"
    out_exp = os.path.join(RUNS, f"expanded-queries{sfx}.json")
    bm_out = os.path.join(RUNS, f"bm25-full-expanded{sfx}.json")

    # 1) 生成扩展查询
    exp_time = 0.0
    fired_log = {}
    if args.reuse_expansions:
        print("== 1) 复用已有扩展查询（不调 LLM）==")
        doc_exp = json.load(open(out_exp, encoding="utf-8"))
        expanded = doc_exp["queries"]
        fired_log = doc_exp.get("fired", {})
        missing = [q["questionId"] for q in qs if q["questionId"] not in expanded]
        if missing:
            raise SystemExit(f"{os.path.basename(out_exp)} 里没有这些题，无法复用：{missing}")
        print(f"   {len(qs)} 题，来自 {doc_exp.get('generatedAt')}（{doc_exp.get('model')}）")
    elif args.expand_with == "term-map":
        print("== 1) 语料内双语术语词典展开（零 LLM 调用）==")
        doc_tm = json.load(open(args.term_map, encoding="utf-8"))
        kinds = {x.strip() for x in args.term_kinds.split(",") if x.strip()}
        print(f"   词典 {os.path.relpath(args.term_map, ROOT)}：{len(doc_tm['terms'])} 条"
              f"（{doc_tm.get('model')} 译），启用 {sorted(kinds)}")
        expanded = {}
        for q in qs:
            terms, fired = term_map_expand(q["question"], doc_tm["terms"], kinds)
            expanded[q["questionId"]] = " ".join([q["question"]] + terms) if terms else q["question"]
            fired_log[q["questionId"]] = fired
        n_fire = sum(1 for v in fired_log.values() if v)
        print(f"   {n_fire}/{len(qs)} 题命中术语（其余题查询不扩展）")
        json.dump({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "model": f"term-map:{os.path.relpath(args.term_map, ROOT)}",
                   "kinds": sorted(kinds), "queries": expanded, "fired": fired_log},
                  open(out_exp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    else:
        print("== 1) LLM 查询扩展 ==")
        expanded = {}
        t0 = time.time()
        for q in qs:
            try:
                expanded[q["questionId"]] = llm(EXPAND_PROMPT.format(q=q["question"]))
            except Exception as e:
                print(f"   {q['questionId']} 扩展失败 {type(e).__name__}，回退原问题")
                expanded[q["questionId"]] = q["question"]
        exp_time = time.time() - t0
        print(f"   {len(expanded)} 题，用时 {exp_time:.1f}s（{exp_time/len(qs):.1f}s/题）")
        for q in qs[:3]:
            print(f"   {q['questionId']}: {expanded[q['questionId']][:110]}")
        json.dump({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "model": os.environ["DEEPEVAL_MODEL"], "queries": expanded},
                  open(out_exp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 1b) 诊断：扩展到底补进来多少英文词。这是本实验的唯一动机——如果扩展只是把中文问题
    # 换个说法，BM25 依旧与英文语料零字面重叠，那不叫跨语言查询扩展，后面的数字也白跑。
    def ascii_terms(s):
        return {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_.#+\-]{1,}", s)}

    added, no_new = [], []
    for q in qs:
        new = ascii_terms(expanded[q["questionId"]]) - ascii_terms(q["question"])
        added.append(len(new))
        if not new:
            no_new.append(q["questionId"])
    print(f"   新增英文词：{sum(added)/len(added):.1f} 个/题（中位 {sorted(added)[len(added)//2]}）；"
          f"零新增 {len(no_new)}/{len(qs)} 题 {no_new[:12]}")
    if fired_log:
        for q in qs:
            f = fired_log.get(q["questionId"]) or []
            if f:
                print(f"   {q['questionId']} ← " + "、".join(f"{x['matchedKey']}→{x['en']}" for x in f))

    # 2) 用扩展查询重跑 BM25（复用 lib/graph.mjs，不另写实现）
    if args.reuse_expansions:
        print("\n== 2) 复用扩展 BM25 产物 ==")
        print("   " + os.path.relpath(bm_out, ROOT))
    else:
        print("\n== 2) 扩展查询上的 BM25 ==")
        tmp_q = os.path.join(RUNS, f"_expanded_questions{sfx}.json")
        doc = {"questions": [dict(q, question=expanded[q["questionId"]]) for q in qs]}
        json.dump(doc, open(tmp_q, "w", encoding="utf-8"), ensure_ascii=False)
        rc = subprocess.call(["node", "knowledge/pipeline/bm25-full.mjs",
                              "--questions", tmp_q, "--out", bm_out], cwd=ROOT)
        print("   node 退出码:", rc)

    # 3) 基线 & 扩展后的完整排序
    def rankings(queries):
        # 基线必须读**当前题集**的 BM25 产物：原先写死 bm25-full.json（旧 14 题），
        # 换题集后基线分支会缺键直接 KeyError。
        path = args.bm25_baseline if queries is None else bm_out
        bm = json.load(open(path, encoding="utf-8"))
        b_rank = {x["questionId"]: [r["chunkId"] for r in x["ranking"]] for x in bm["perQuestion"]}
        b_sc = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]} for x in bm["perQuestion"]}
        qtexts = [q["question"] if queries is None else queries[q["questionId"]] for q in qs]
        qvecs = embed(qtexts)
        v_rank, v_sc = {}, {}
        for q, qv in zip(qs, qvecs):
            sims = sorted(((sum(a * b for a, b in zip(qv, vecs[cid])), cid) for cid in ids), reverse=True)
            v_rank[q["questionId"]] = [c for _, c in sims]
            v_sc[q["questionId"]] = {c: s for s, c in sims}
        return b_rank, b_sc, v_rank, v_sc

    def score(strategy, b_rank, b_sc, v_rank, v_sc):
        hit = 0; prec = 0.0; fails = []
        for q in qs:
            qid = q["questionId"]; refs = set(q["referenceChunks"])
            if strategy == "bm25":
                top = b_rank[qid][:args.topk]
            elif strategy == "vector":
                top = v_rank[qid][:args.topk]
            else:  # 加权融合
                b = minmax(b_sc[qid]); v = minmax(v_sc[qid])
                s = {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * v.get(c, 0.0)
                     for c in set(b) | set(v)}
                top = sorted(s, key=lambda c: -s[c])[:args.topk]
            h = [c for c in top if c in refs]
            if h: hit += 1
            else: fails.append(qid)
            prec += len(h) / args.topk
        return {"hit": hit, "n": len(qs), "rate": round(hit / len(qs), 4),
                "prec": round(prec / len(qs), 4), "fails": fails}

    print("\n== 3) 结果对比 ==")
    base = rankings(None)
    exp = rankings(expanded)
    # 只换字面通道、向量通道仍用原问题：跨语言缺的是"英文术语的字面重叠"，而向量本来就
    # 是语义通道，喂一长串关键词反而会把语义冲淡（实测融合档 29→28 就是被这个拖的）。
    mixed = (exp[0], exp[1], base[2], base[3])  # 扩展BM25 + 基线向量
    print(f"{'策略':26s} {'命中':>8s} {'命中率':>8s} {'精确率':>8s}  失败题")
    print("-" * 74)
    table = {}
    for label, pack in (("基线", base), ("扩展后", exp), ("扩展BM25+基线向量", mixed)):
        for strategy in ("bm25", "vector", "hybrid"):
            r = score(strategy, *pack)
            name = f"{label} / {strategy}"
            table[name] = r
            print(f"{name:26s} {r['hit']:>4d}/{r['n']:<3d} {r['rate']:>8.4f} {r['prec']:>8.4f}  {r['fails']}")

    best_name = max(table, key=lambda k: (table[k]["rate"], table[k]["prec"]))
    best = table[best_name]
    print(f"\n最优：{best_name}  命中 {best['hit']}/{best['n']}  精确率 {best['prec']}")
    print(f"查询扩展额外开销：{exp_time/len(qs):.1f}s/题、{len(qs)} 次 LLM 调用")

    # 按语言分层看（zh=中文层 / xl=跨语言层）——查询扩展的假设正是「xl 层会受益」，
    # 只看总体会把这个效应和 zh 层的波动混在一起（旧实验只报了总体，这是它的盲点）。
    layers = {}
    if any(q.get("layer") for q in qs):
        # 分层要连通道一起分：跨语言题的症结往往不在"召回不到"，而在"融合把向量通道
        # 已经排到第 1 的英文段让给了字面重叠的中文段"。只报融合档会把这个效应盖住。
        def layer_hits(pack, label):
            br, bs, vr, vs = pack
            groups = {}
            for q in qs:
                qid = q["questionId"]
                refs = set(q.get("referenceChunks") or [])
                b = minmax(bs[qid]); v = minmax(vs[qid])
                fused = {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * v.get(c, 0.0)
                         for c in set(b) | set(v)}
                tops = {"bm25": br[qid][:args.topk], "vector": vr[qid][:args.topk],
                        "hybrid": sorted(fused, key=lambda c: -fused[c])[:args.topk]}
                for strategy, top_ids in tops.items():
                    g = groups.setdefault((q.get("layer", "(无)"), strategy), [0, 0])
                    g[1] += 1
                    if refs & set(top_ids):
                        g[0] += 1
            got = {}
            for (layer, strategy), (h, n) in sorted(groups.items()):
                got.setdefault(layer, {})[strategy] = {"hit": h, "n": n, "rate": round(h / n, 4)}
                print(f"  {label:<10}{layer:<6}{strategy:<8}{h}/{n}  = {h/n:.3f}")
            return {label: got}

        print("\n== 4) 按语言分层（融合档，与 --alpha 一致）==")
        layers.update(layer_hits(base, "基线"))
        layers.update(layer_hits(exp, "扩展后"))
        layers.update(layer_hits(mixed, "混BM25+基向量"))

    if args.save_best:
        # 用最优配置生成结果文件（供 deepeval）
        use_exp = best_name.startswith("扩展后")
        pack = exp if use_exp else base
        b_rank, b_sc, v_rank, v_sc = pack
        per_q = []
        for q in qs:
            qid = q["questionId"]
            b = minmax(b_sc[qid]); v = minmax(v_sc[qid])
            s = {c: args.alpha * b.get(c, 0.0) + (1 - args.alpha) * v.get(c, 0.0) for c in set(b) | set(v)}
            top_ids = sorted(s, key=lambda c: -s[c])[:args.topk]
            refs = set(q["referenceChunks"])
            hits = [c for c in top_ids if c in refs]
            per_q.append({
                "questionId": qid, "set": q.get("category", "main"),
                "question": q["question"],
                "query_used": expanded[qid] if use_exp else q["question"],
                "referenceChunks": sorted(refs),
                "topk": [{"chunkId": c, "score": round(s[c], 6),
                          "text": (by_id.get(c, {}).get("text") or "")[:2000]} for c in top_ids],
                "hit": bool(hits), "hits": hits,
            })
        out = os.path.join(RUNS, "hybrid-topk.json")
        json.dump({
            "schema": "career-graph-hybrid-retrieval/v1",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "retriever": {"type": "hybrid", "alpha_bm25": args.alpha, "topk": args.topk,
                          "queryExpansion": use_exp,
                          "components": ["bm25 (lib/graph.mjs)", f"dense (TEI {TEI_MODEL})"]},
            "metrics": {"main": {"hitRate": f"{best['hit']}/{best['n']}",
                                 "hitRateValue": best["rate"],
                                 "meanPrecisionAtK": best["prec"]}},
            "perQuestion": per_q,
        }, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"最优配置已存为 {os.path.relpath(out, ROOT)}")

    # 结果落盘：对比表原先只打到 stdout，上一次运行的结果就是这么丢的（会话中断即失）。
    report = {
        "schema": "career-graph-query-expansion-experiment/v1",
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "questions": os.path.relpath(args.questions, ROOT),
        "bm25Baseline": os.path.relpath(args.bm25_baseline, ROOT),
        "topk": args.topk, "alphaBm25": args.alpha, "teiModel": TEI_MODEL,
        "expansion": {
            "mode": args.expand_with,
            "termKinds": sorted(x.strip() for x in args.term_kinds.split(",") if x.strip()),
            "model": os.environ.get("DEEPEVAL_MODEL") if args.expand_with == "llm" else args.term_map,
            "reused": args.reuse_expansions,
            "secondsPerQuestion": round(exp_time / len(qs), 2),
            "meanNewEnglishTerms": round(sum(added) / len(added), 3),
            "noNewEnglishTerms": no_new,
            "queries": expanded,  # 原文留档：expanded-queries.json 会被下一次运行覆盖
        },
        "table": table,
        "best": best_name,
        "layers": layers,
    }
    rep = os.path.join(RUNS, "query-expansion" + (f"-{args.tag}" if args.tag else "") + ".json")
    json.dump(report, open(rep, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"结果已存为 {os.path.relpath(rep, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
