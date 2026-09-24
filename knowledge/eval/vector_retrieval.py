#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用本机 TEI（Qwen3-Embedding-0.6B）对职业知识库语料做向量检索，产出 Top-3 结果。

为什么要这个脚本：
  既有评估里唯一被实测过的检索基线是 BM25（knowledge/evaluations/results.json）。
  本脚本补上「稠密向量检索」这一档，输出与 results.json 同构的 perQuestion 结构，
  供 deepeval 的 context 类指标在同一套题上做对比。

不依赖任何 LLM，只依赖已启动的 TEI（bash knowledge-v1/scripts/start_tei.sh）。

两个工程要点（都是实测踩出来的）：
  1) 语料向量必须缓存并增量落盘。494 段在本机 CPU 上约需 15-20 分钟，
     一旦进程被中断（如 shell 超时）就全部白跑。缓存后重跑只算新增部分。
  2) 并发发请求收益有限（实测 0.36 → 0.44 段/秒），真正的瓶颈是 TEI 的
     推理算力——容器默认 RAYON_NUM_THREADS=8，而机器有 32 核。

用法：
  python knowledge/eval/vector_retrieval.py                 # 默认 4 并发
  python knowledge/eval/vector_retrieval.py --workers 4 --batch 16
  python knowledge/eval/vector_retrieval.py --no-cache      # 忽略缓存重算
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHUNKS = os.path.join(ROOT, "knowledge", "chunks", "chunks.jsonl")
Q_FILE = os.path.join(ROOT, "knowledge", "evaluations", "questions-v2.json")
OUT_DIR = os.path.join(ROOT, "knowledge", "eval", "runs")
CACHE_DIR = os.path.join(ROOT, "knowledge", "eval", "cache")
OUT = os.path.join(OUT_DIR, "vector-topk.json")

TEI = os.environ.get("TEI_EMBED_URL", "http://127.0.0.1:8090/v1/embeddings")
MODEL = os.environ.get("TEI_MODEL", "Qwen3-Embedding-0.6B")


def embed(texts, timeout=600):
    """调用 TEI 的 OpenAI 兼容接口，返回向量列表（已 L2 归一化，点积即余弦）。"""
    body = json.dumps({"input": texts, "model": MODEL}).encode("utf-8")
    req = urllib.request.Request(TEI, data=body,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    data = sorted(d["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in data]


def cache_path(model, dim=None):
    safe = model.replace("/", "_")
    return os.path.join(CACHE_DIR, f"chunk-vectors-{safe}.json")


def load_cache(model):
    p = cache_path(model)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("model") == model:
                return d.get("vectors", {})
        except Exception as e:
            print(f"   [warn] 缓存读取失败，忽略：{e}")
    return {}


def save_cache(model, vectors):
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = cache_path(model)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"model": model, "count": len(vectors), "vectors": vectors}, f)
    os.replace(tmp, p)


def load_chunks():
    rows = []
    with open(CHUNKS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_questions():
    """读当前题集：只有「可回答」题参与检索评测（超范围题没有参考答案）。

    题集已换为 questions-v2.json（每题恰好 2 段参考答案、且全部取自未被图谱引用的段落）。
    旧题集文件已删除，这里不再回退到旧路径。
    """
    with open(Q_FILE, encoding="utf-8") as f:
        qs = json.load(f)["questions"]
    # 用 get(..., True)：题集若漏写 answerable 字段，不应被当成超范围题静默丢掉
    return [("main", q) for q in qs if q.get("answerable", True)]


def main():
    global Q_FILE, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--questions", default=Q_FILE, help="题集文件（默认 questions-v2.json）")
    ap.add_argument("--out", default=OUT, help="产物路径（默认 runs/vector-topk.json）")
    ap.add_argument("--batch", type=int, default=16,
                    help="每请求送多少条文本；TEI 侧 max_batch_tokens=2048，会自动再切分")
    ap.add_argument("--workers", type=int, default=4,
                    help="并发请求数；TEI 侧 max_batch_requests=4，默认与之对齐")
    ap.add_argument("--no-cache", action="store_true", help="忽略缓存，全部重算")
    args = ap.parse_args()

    Q_FILE, OUT = args.questions, args.out

    os.makedirs(OUT_DIR, exist_ok=True)

    print("== 探测 TEI ==")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8090/health", timeout=5) as r:
            print("   health:", r.status)
    except Exception as e:
        print(f"   TEI 不可达：{e}\n   请先运行 bash knowledge-v1/scripts/start_tei.sh",
              file=sys.stderr)
        return 1
    with urllib.request.urlopen("http://127.0.0.1:8090/info", timeout=10) as r:
        info = json.load(r)
    print(f"   模型={info['model_id']} dtype={info['model_dtype']} "
          f"max_batch_tokens={info['max_batch_tokens']}")

    chunks = load_chunks()
    cache = {} if args.no_cache else load_cache(MODEL)
    todo = [c for c in chunks if c["chunkId"] not in cache]
    print(f"\n== 语料 {len(chunks)} 段：缓存命中 {len(chunks) - len(todo)} 段，"
          f"待向量化 {len(todo)} 段 ==")

    t0 = time.time()
    el = 0.0
    new = 0
    if todo:
        batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]
        in_cache_before = len(chunks) - len(todo)

        def work(batch):
            return list(zip([c["chunkId"] for c in batch],
                            embed([c["text"] for c in batch])))

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(work, b) for b in batches]
            try:
                for fut in as_completed(futures):
                    pairs = fut.result()
                    for cid, vec in pairs:
                        cache[cid] = vec
                    new += len(pairs)
                    save_cache(MODEL, cache)  # 增量落盘：被中断也不丢已完成部分
                    el = time.time() - t0
                    print(f"   {in_cache_before + new}/{len(chunks)} 段  {el:.1f}s  "
                          f"({new / max(el, 1e-9):.2f} 段/秒)", flush=True)
            except KeyboardInterrupt:
                save_cache(MODEL, cache)
                print("\n   已中断，进度已保存到缓存", file=sys.stderr)
                return 130
        el = time.time() - t0
        print(f"   本次向量化 {new} 段，用时 {el:.1f}s，"
              f"吞吐 {new / max(el, 1e-9):.2f} 段/秒")
    else:
        print("   全部命中缓存，跳过向量化")

    missing = [c["chunkId"] for c in chunks if c["chunkId"] not in cache]
    if missing:
        print(f"   仍缺 {len(missing)} 段向量，中止", file=sys.stderr)
        return 1
    cvecs = [cache[c["chunkId"]] for c in chunks]
    dims = {len(v) for v in cvecs}
    if len(dims) != 1:
        print(f"   维度不一致 {dims}，中止", file=sys.stderr)
        return 1

    questions = load_questions()
    print(f"\n== 检索 {len(questions)} 题（主集 "
          f"{sum(1 for s, _ in questions if s == 'main')} / 留出 "
          f"{sum(1 for s, _ in questions if s == 'heldout')}），top-{args.topk} ==")

    per_question = []
    t1 = time.time()
    for set_name, q in questions:
        [qv] = embed([q["question"]])
        scored = []
        for c, v in zip(chunks, cvecs):
            scored.append((sum(a * b for a, b in zip(qv, v)), c["chunkId"]))
        scored.sort(reverse=True)
        top = scored[:args.topk]
        refs = set(q.get("referenceChunks") or [])
        hits = [cid for _, cid in top if cid in refs]
        per_question.append({
            "questionId": q["questionId"],
            "set": set_name,
            "category": q.get("category"),
            "question": q["question"],
            "referenceChunks": sorted(refs),
            "topk": [{"chunkId": cid, "score": round(s, 6)} for s, cid in top],
            "hit": bool(hits),
            "hits": hits,
        })
    q_el = time.time() - t1

    main_qs = [r for r in per_question if r["set"] == "main"]
    hit_main = sum(1 for r in main_qs if r["hit"])
    prec_main = sum(len(r["hits"]) / args.topk for r in main_qs) / max(len(main_qs), 1)

    result = {
        "schema": "career-graph-vector-retrieval/v1",
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "retriever": {
            "type": "dense-vector",
            "service": TEI,
            "model": info["model_id"],
            "dtype": info["model_dtype"],
            "dim": len(cvecs[0]),
            "metric": "cosine（点积；TEI 输出已 L2 归一化）",
            "topk": args.topk,
            "rayonThreads": os.environ.get("TEI_RAYON_THREADS", "unknown"),
        },
        "corpus": {"chunks": len(chunks)},
        "metrics": {
            "main": {"hitRate": f"{hit_main}/{len(main_qs)}",
                     "hitRateValue": round(hit_main / max(len(main_qs), 1), 4),
                     "meanPrecisionAtK": round(prec_main, 4)},
        },
        "timing": {"embedCorpusSec": round(el, 1) if todo else 0,
                   "querySec": round(q_el, 1),
                   "perQueryMs": round(q_el / max(len(questions), 1) * 1000, 1)},
        "perQuestion": per_question,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("\n=== 向量检索结果（判据：Top-k 是否含标注 chunk）===")
    print(f"  主集 命中 {result['metrics']['main']['hitRate']}"
          f"  平均精确率@{args.topk} {result['metrics']['main']['meanPrecisionAtK']}")
    if "heldout" in result["metrics"]:
        print(f"  留出 命中 {result['metrics']['heldout']['hitRate']}")
    else:
        print("  留出 集已随旧题集删除，本档只报主集")
    print(f"  耗时：查询 {result['timing']['perQueryMs']} ms/题")
    print(f"\n产物：{os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
