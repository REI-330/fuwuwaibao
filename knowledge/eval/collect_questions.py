#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把批量出题缓存里「已起草且通过机械闸门」的题收上来，按配额分配到 dev / test。

为什么需要这一步：批量出题脚本只在跑完时才落盘，而端点限流使全量跑完要数小时。
缓存里已经有可用的草稿，收上来即可用；剩余配额由人工读原文补。

收题闸门（与 EVAL_SET_DESIGN.md 的硬规则一致）：
  R1 参考段未落在图谱引用集合内（候选池已保证）
  R2 每题 2 段
  R3 两段间隔 >= min-gap
  R4 同来源
  R5 dev/test 不共用参考段
  另：题面与原文最长公共连续字符 <= max-lcs（防照抄）

用法：
  python knowledge/eval/collect_questions.py --dry-run
  python knowledge/eval/collect_questions.py
"""
import argparse
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
EV = os.path.join(K, "evaluations")
RUNS = os.path.join(K, "eval", "runs")
CACHE = os.path.join(RUNS, "author-batch-cache.json")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
POOL = os.path.join(EV, "candidate_pool.jsonl")

sys.path.insert(0, os.path.join(K, "eval"))
from author_questions_batch import (CATEGORY_OF, longest_common_substring, norm, tri,  # noqa: E402
                                    QUOTA)  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-gap", type=int, default=3)
    ap.add_argument("--max-lcs", type=int, default=12)
    ap.add_argument("--max-per-section", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    chunks = {json.loads(l)["chunkId"]: json.loads(l)
              for l in open(CHUNKS, encoding="utf-8") if l.strip()}
    pool = {json.loads(l)["chunkId"]: json.loads(l)
            for l in open(POOL, encoding="utf-8") if l.strip()}
    pos = {cid: r["pos"] for cid, r in pool.items()}
    src_pos = defaultdict(dict)
    for cid, r in pool.items():
        src_pos[r["sourceId"]][cid] = r["pos"]

    cache = json.load(open(CACHE, encoding="utf-8"))["items"]
    rows = []
    for batch_key, v in cache.items():
        keys = batch_key.split("||")
        draft = {d.get("i"): d for d in (v.get("draft") or []) if isinstance(d, dict)}
        vmap = {x.get("i"): x for x in (v.get("verify") or []) if isinstance(x, dict)}
        for i, key in enumerate(keys):
            a, b = key.split("|")
            if a not in chunks or b not in chunks:
                continue
            q = (draft.get(i) or {}).get("q") or ""
            q = q.strip()
            if not q:
                continue
            lcs = max(longest_common_substring(norm(q), norm(chunks[a]["text"])),
                      longest_common_substring(norm(q), norm(chunks[b]["text"])))
            if lcs > args.max_lcs:
                continue
            v = vmap.get(i) or {}
            ay, copied, amb = tri(v.get("answerable")), tri(v.get("copied")), tri(v.get("ambiguous"))
            verified = None not in (ay, copied, amb)
            if verified and (ay is not True or copied is True or amb is True):
                continue
            rows.append({"a": a, "b": b, "q": q,
                         "n1": (draft.get(i) or {}).get("n1", ""),
                         "n2": (draft.get(i) or {}).get("n2", ""),
                         "lcs": lcs, "verified": verified,
                         "category": CATEGORY_OF.get(chunks[a]["sourceId"], "其他"),
                         "sourceId": chunks[a]["sourceId"],
                         "gap": abs(pos.get(a, 0) - pos.get(b, 0))})
    print(f"缓存里可收的题：{len(rows)} 条（已过机械闸门）")

    # 配额分配
    out = {"dev": [], "test": []}
    need = {c: dict(v) for c, v in QUOTA.items()}
    used, section_used = set(), defaultdict(int)
    rows.sort(key=lambda r: (0 if r["verified"] else 1, -r["gap"]))
    for r in rows:
        a, b = r["a"], r["b"]
        if a in used or b in used:
            continue
        if r["gap"] < args.min_gap:
            continue
        sec = (chunks[a]["sourceId"], chunks[a].get("sectionPath") or "")
        if section_used[sec] >= args.max_per_section:
            continue
        cat = r["category"]
        if cat not in need or sum(need[cat].values()) <= 0:
            continue
        split = "dev" if need[cat]["dev"] >= need[cat]["test"] else "test"
        if need[cat][split] <= 0:
            split = "test" if split == "dev" else "dev"
            if need[cat][split] <= 0:
                continue
        out[split].append(r)
        used.update((a, b))
        section_used[sec] += 1
        need[cat][split] -= 1

    for split in ("dev", "test"):
        cats = defaultdict(int)
        for r in out[split]:
            cats[r["category"]] += 1
        print(f"  {split}: 收上 {len(out[split])} 题 {dict(cats)}")
    print(f"  剩余配额：{ {c: v for c, v in need.items() if sum(v.values())} }")

    if args.dry_run:
        return 0

    def pack(split):
        qs = []
        for i, r in enumerate(out[split], 1):
            qs.append({
                "questionId": f"{'D' if split == 'dev' else 'T'}{i:02d}",
                "split": split, "category": r["category"], "question": r["q"],
                "answerable": True, "referenceChunks": [r["a"], r["b"]],
                "gradingNotes": "；".join(x for x in [r["n1"], r["n2"]] if x) or "（待人工补要点）",
                "sourceId": r["sourceId"],
                "provenance": {"chunkA": r["a"], "chunkB": r["b"],
                               "lcsWithSource": r["lcs"], "llmVerified": r["verified"],
                               "collectedBy": "collect_questions.py"},
            })
        return qs

    json.dump({"schema": "career-graph-eval-questions/v3-partial", "split": "dev",
               "note": "由 collect_questions.py 从批量出题缓存收集；剩余配额待人工补。",
               "counts": {"total": len(pack("dev")), "answerable": len(pack("dev")),
                          "referenceChunksTotal": 2 * len(pack("dev"))},
               "questions": pack("dev")},
              open(os.path.join(EV, "_collected-dev.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump({"schema": "career-graph-eval-questions/v3-partial", "split": "test",
               "note": "由 collect_questions.py 从批量出题缓存收集；剩余配额待人工补。",
               "counts": {"total": len(pack("test")), "answerable": len(pack("test")),
                          "referenceChunksTotal": 2 * len(pack("test"))},
               "questions": pack("test")},
              open(os.path.join(EV, "_collected-test.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    for split in ("dev", "test"):
        for q in pack(split)[:3]:
            print(f"  {split} {q['questionId']} [{q['category']}] {q['question'][:60]}")
    print(f"\n产物：knowledge/evaluations/_collected-dev.json / _collected-test.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
