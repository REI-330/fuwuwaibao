#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
题集校验器：把「题集设计规则」变成可执行检查，而不是靠人记。

要防的四类问题（都是本项目实测踩过的）：
  1) 参考答案落在图谱引用集合里 → 任何「用图」策略都会被泄漏抬高
     （旧主集 134 段参考答案 100% 落在引用集合内，属于同源偏差）
  2) 两段参考答案在原文相邻 → 邻域/窗口类技术的增益被结构性放大
     （v2 题集实测 7/14 题两段直接相邻）
  3) dev 与 test 用同一段落 → test 不再是干净的留出集
  4) 拒答题带参考答案 → 口径自相矛盾

用法：
  # 校验单个题集
  python knowledge/eval/validate_questionset.py --set knowledge/evaluations/questions-v2.json

  # 校验 dev/test 两集并检查两者不重叠（推荐）
  python knowledge/eval/validate_questionset.py \
    --dev knowledge/evaluations/questions-dev.json \
    --test knowledge/evaluations/questions-test.json

退出码：0 = 全部通过；1 = 有硬错误。
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
GRAPH = os.path.join(K, "graph", "graph.json")


def load_chunks():
    rows = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    by_source = defaultdict(list)
    for c in rows:
        by_source[c["sourceId"]].append(c)
    for src in by_source:
        by_source[src].sort(key=lambda c: c["charRange"][0])
    pos = {}
    for src, lst in by_source.items():
        for i, c in enumerate(lst):
            pos[c["chunkId"]] = i
    return {c["chunkId"]: c for c in rows}, pos


def load_cited():
    g = json.load(open(GRAPH, encoding="utf-8"))
    cited = set()
    for n in g["nodes"]:
        cited.update(n.get("sourceRefs") or [])
    for e in g["edges"]:
        cited.update(e.get("sourceRefs") or [])
    return cited


def load_questions(path):
    doc = json.load(open(path, encoding="utf-8"))
    items = doc.get("questions", doc) if isinstance(doc, dict) else doc
    return items


def check_set(name, path, chunks, pos, cited, min_gap, refs_per_q, expect_split):
    items = load_questions(path)
    errors, warns = [], []
    used_refs = {}
    cats = Counter()

    if expect_split:
        got = set()
        for q in items:
            got.add(q.get("split") or expect_split)
        if got - {expect_split}:
            warns.append(f"{name}: 发现与预期不符的 split 标记 {sorted(got - {expect_split})}")

    for q in items:
        qid = q.get("questionId", "?")
        answerable = q.get("answerable", True)
        refs = q.get("referenceChunks") or []
        cats[q.get("category", "(未分类)")] += 1

        if not answerable:
            if refs:
                errors.append(f"{qid}: 拒答题却带 {len(refs)} 段参考答案")
            continue

        if len(refs) != refs_per_q:
            errors.append(f"{qid}: 参考答案 {len(refs)} 段，要求恰 {refs_per_q} 段")

        for r in refs:
            if r not in chunks:
                errors.append(f"{qid}: 参考段 {r} 不存在于 chunks.jsonl")
                continue
            if r in cited:
                errors.append(f"{qid}: 参考段 {r} 落在图谱引用集合内（泄漏风险）")
            if r in used_refs:
                warns.append(f"{qid}: 参考段 {r} 与 {used_refs[r]} 重复")
            used_refs[r] = qid

        # 同来源 + 最小间隔
        srcs = {chunks[r]["sourceId"] for r in refs if r in chunks}
        if len(srcs) > 1:
            warns.append(f"{qid}: 两段参考答案跨来源 {sorted(srcs)}（本设计约定同来源）")
        if len(refs) == 2 and all(r in pos for r in refs):
            gap = abs(pos[refs[0]] - pos[refs[1]])
            if gap < min_gap:
                msg = (f"{qid}: 两段参考答案在原文间隔 {gap} < {min_gap}"
                       f"（{refs[0]} / {refs[1]}）")
                # 历史遗留题（题集自身带 legacy 标记）降级为提示：
                # 它们已被调参污染、只作 dev 用，保留是为了与历史结果对照。
                (warns if q.get("legacy") else errors).append(
                    msg + ("（legacy，仅作提示）" if q.get("legacy") else ""))

    return {"name": name, "path": path, "count": len(items),
            "answerable": sum(1 for q in items if q.get("answerable", True)),
            "outOfScope": sum(1 for q in items if not q.get("answerable", True)),
            "categories": dict(cats), "refs": used_refs,
            "errors": errors, "warns": warns}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", help="单个题集文件")
    ap.add_argument("--dev", help="开发集文件")
    ap.add_argument("--test", help="测试集文件")
    ap.add_argument("--min-gap", type=int, default=3,
                    help="两段参考答案在原文的最小间隔（默认 3：不相邻、且不隔一段）")
    ap.add_argument("--refs-per-q", type=int, default=2)
    args = ap.parse_args()

    if not (args.set or args.dev or args.test):
        ap.error("至少给 --set 或 --dev/--test")

    chunks, pos = load_chunks()
    cited = load_cited()

    reports = []
    if args.set:
        reports.append(check_set("set", args.set, chunks, pos, cited,
                                 args.min_gap, args.refs_per_q, None))
    if args.dev:
        reports.append(check_set("dev", args.dev, chunks, pos, cited,
                                 args.min_gap, args.refs_per_q, "dev"))
    if args.test:
        reports.append(check_set("test", args.test, chunks, pos, cited,
                                 args.min_gap, args.refs_per_q, "test"))

    print(f"语料 {len(chunks)} 段；图谱引用集合 {len(cited)} 段；最小间隔要求 {args.min_gap}\n")
    total_errors = 0
    for r in reports:
        print(f"=== {r['name']}: {os.path.relpath(r['path'], ROOT)} ===")
        print(f"  题数 {r['count']}（可回答 {r['answerable']} / 拒答 {r['outOfScope']}）")
        print(f"  类目分布 {r['categories']}")
        print(f"  参考答案段（去重后 {len(r['refs'])} 段）")
        if r["errors"]:
            total_errors += len(r["errors"])
            print(f"  ✗ 硬错误 {len(r['errors'])} 条：")
            for e in r["errors"][:40]:
                print(f"      - {e}")
            if len(r["errors"]) > 40:
                print(f"      … 还有 {len(r['errors']) - 40} 条")
        else:
            print("  ✓ 硬规则全部通过")
        if r["warns"]:
            print(f"  ⚠ 提示 {len(r['warns'])} 条：")
            for w in r["warns"][:20]:
                print(f"      - {w}")
        print()

    # 跨集重叠
    if args.dev and args.test:
        d = next(r for r in reports if r["name"] == "dev")
        t = next(r for r in reports if r["name"] == "test")
        overlap = set(d["refs"]) & set(t["refs"])
        if overlap:
            total_errors += len(overlap)
            print(f"✗ dev/test 参考答案段重叠 {len(overlap)} 段：{sorted(overlap)}")
        else:
            print("✓ dev/test 参考答案段无重叠")
        dq = {q["questionId"] for q in load_questions(args.dev)}
        tq = {q["questionId"] for q in load_questions(args.test)}
        if dq & tq:
            total_errors += 1
            print(f"✗ dev/test 题号重复：{sorted(dq & tq)}")

    print()
    print("结论：" + ("存在硬错误，需修" if total_errors else "全部通过"))
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
