#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
邻域信号的三种公式对照：加法式会不会自己造出假阳性。

背景：additive 形式的 score = f + λ·max(f_前邻, f_后邻) 有一个已知缺陷——
两块互相毗邻时（A 的前邻是 B、B 的后邻是 A）会互相加分，形成正反馈，
把两个都很低的块一起抬进前列。实测 N12 上 S15#s281(0.4446)/S15#s282(0.7788)
就被抬成 0.8340/1.0011，s281 从榜外升到第 2 位。

本脚本对照三种公式，并逐题给出「前 3 位里命中了几段参考答案」：
  add       f + λ·max(nb)                    允许互相加分
  pull      f + λ·max(0, max(nb) − f)        只往上拉、不超过邻块，不会自反馈
  pool      max(f, λ·max(nb))                池化；λ<1 时对高分块完全无影响

用法：python knowledge/eval/locality_variants.py
"""
import json
import os
import sys

import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location(
    "opt", os.path.join(ROOT, "knowledge", "eval", "optimize_retrieval.py"))
opt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(opt)


def main():
    chunks, prev_of, next_of = opt.load_corpus()
    ids = [c["chunkId"] for c in chunks]
    qs = [q for q in json.load(open(opt.QUESTIONS, encoding="utf-8"))["questions"]
          if q.get("answerable", True)]
    refs_of = {q["questionId"]: set(q["referenceChunks"]) for q in qs}

    bm = json.load(open(opt.BM25_FULL, encoding="utf-8"))
    b_sc = {x["questionId"]: {r["chunkId"]: r["score"] for r in x["ranking"]}
            for x in bm["perQuestion"]}
    vecs = json.load(open(os.path.join(
        opt.CACHE_DIR, f"chunk-vectors-{opt.MODEL.replace('/', '_')}.json"),
        encoding="utf-8"))["vectors"]
    v_sc = {}
    for q, qv in zip(qs, opt.embed([q["question"] for q in qs])):
        v_sc[q["questionId"]] = {cid: sum(a * b for a, b in zip(qv, vecs[cid])) for cid in ids}

    alpha = 0.35

    def nbmax(f, cid):
        best = 0.0
        for nb in (prev_of.get(cid), next_of.get(cid)):
            if nb and nb in f:
                best = max(best, f[nb])
        return best

    def fusion(qid):
        b = opt.minmax(b_sc[qid])
        v = opt.minmax(v_sc[qid])
        return {c: alpha * b.get(c, 0.0) + (1 - alpha) * v.get(c, 0.0) for c in set(b) | set(v)}

    strategies = [("base", 0.0), ("add λ=0.2", 0.2), ("add λ=0.5", 0.5),
                  ("pull λ=0.5", 0.5), ("pull λ=1.0", 1.0), ("pool λ=1.0", 1.0)]

    print(f"{'题':5s} " + " ".join(f"{s:>12s}" for s, _ in strategies))
    print("-" * (6 + 13 * len(strategies)))
    totals = {s: {"refs": 0, "top1": 0, "hit": 0} for s, _ in strategies}
    fails = {s: [] for s, _ in strategies}
    for q in qs:
        qid = q["questionId"]
        f = fusion(qid)
        cells = []
        for name, lam in strategies:
            if lam == 0.0:
                s = dict(f)
            elif name.startswith("add"):
                s = {c: f[c] + lam * nbmax(f, c) for c in f}
            elif name.startswith("pull"):
                s = {c: f[c] + lam * max(0.0, nbmax(f, c) - f[c]) for c in f}
            else:
                s = {c: max(f[c], lam * nbmax(f, c)) for c in f}
            r = sorted(s, key=lambda c: -s[c])[:3]
            hits = [c for c in r if c in refs_of[qid]]
            totals[name]["refs"] += len(hits)
            totals[name]["hit"] += 1 if hits else 0
            totals[name]["top1"] += 1 if (r and r[0] in refs_of[qid]) else 0
            if not hits:
                fails[name].append(qid)
            cells.append(f"{len(hits)}段/{r[0][-6:]}")
        print(f"{qid:5s} " + " ".join(f"{c:>12s}" for c in cells))

    print("-" * (6 + 13 * len(strategies)))
    for name, _ in strategies:
        t = totals[name]
        prec = t["refs"] / (3 * len(qs))
        print(f"{name:12s} 命中 {t['hit']}/{len(qs)}  命中@1 {t['top1']}/{len(qs)}"
              f"  精确率@3 {prec:.4f}  未命中 {fails[name] or '-'}")


if __name__ == "__main__":
    sys.exit(main())
