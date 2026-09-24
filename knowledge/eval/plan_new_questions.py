#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
按「中文占比门槛」挑出待人工写题的段对，并导出带原文摘录的清单。

与 `plan_manual_questions.py` 的区别（两处都是实测踩出来的）：
  1) **候选池现算**。老脚本读 `evaluations/candidate_pool.jsonl`，而那份池子的
     「已占用」只算了 questions-v2.json 的 28 段，不含后来产出的 dev/test 的 82 段
     → 会把已用掉的段当成可用，配额定得出来、段却配不出。
  2) **可加中文占比门槛**。老脚本只要求「含至少 1 个汉字」，而本语料里有大量
     `esp_err_t spi_bus_initialize(...)` 这种中文占比 0.01 的英文 API 段。
     要出「中文问→中文答」的题，必须按占比筛，不能让它们混进来。

规则（与 validate_questionset.py 的硬规则对齐）：
  未被图谱引用 / 未被指定题集占用 / 长度 >= --min-chars / 两段答案中文占比 >= --min-cjk-ratio /
  同来源 / 原文间隔 >= --min-gap / 一道题不重复用同一段

用法：
  python knowledge/eval/plan_new_questions.py \
    --quota "dev:AI框架与推理=8,test:AI框架与推理=5,dev:测试自动化=5,test:测试自动化=4,dev:嵌入式开发=2,test:嵌入式开发=2,dev:标准与政策=1,test:标准与政策=1" \
    --out knowledge/evaluations/_plan-new.md \
    --picks knowledge/evaluations/_picks-new.json
"""
import argparse
import itertools
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
EV = os.path.join(K, "evaluations")

CATEGORY_OF = {
    "S01": "嵌入式开发", "S02": "嵌入式开发", "S03": "嵌入式开发", "S04": "嵌入式开发",
    "S05": "AI框架与推理", "S06": "AI框架与推理", "S07": "AI框架与推理",
    "S08": "计算机视觉", "S09": "计算机视觉", "S10": "计算机视觉",
    "S11": "测试自动化", "S12": "测试自动化", "S13": "测试自动化",
    "S14": "测试自动化", "S15": "测试自动化",
    "S16": "职业能力", "S17": "职业能力",
    "S18": "标准与政策", "S19": "标准与政策", "S20": "标准与政策",
    "S21": "标准与政策", "S22": "标准与政策",
}
CJK = re.compile(r"[\u4e00-\u9fff]")
BOILER = re.compile(
    r"^\s*(-{3,}|Related occupations|View the list of|external site|"
    r"此页面对您有帮助吗？|感谢您对我们工作的肯定！|感谢您告诉我们本页内容还需要完善。).*$",
    re.MULTILINE)


def cjk_ratio(text):
    s = re.sub(r"\s", "", text)
    return len(CJK.findall(s)) / len(s) if s else 0.0


def richness(text):
    """段落的「可出题性」粗估：中文占比 × 长度饱和项。
    高占比但 200 字的段不如中占比但 900 字的段好写题。"""
    return cjk_ratio(text) * min(1.0, len(text) / 600)


def clean_for_prompt(text, limit):
    t = BOILER.sub("", text)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t[:limit] + ("…" if len(t) > limit else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quota", required=True, help="形如 dev:AI框架与推理=8,test:测试自动化=4")
    ap.add_argument("--out", required=True, help="带摘录的计划文件（md）")
    ap.add_argument("--picks", required=True, help="机读选段结果（json）")
    ap.add_argument("--exclude", default="questions-dev.json,questions-test.json,questions-v2.json",
                    help="逗号分隔：被这些题集占用的段视为不可用")
    ap.add_argument("--min-chars", type=int, default=300)
    ap.add_argument("--min-gap", type=int, default=3)
    ap.add_argument("--min-cjk-ratio", type=float, default=0.2)
    ap.add_argument("--excerpt", type=int, default=900)
    ap.add_argument("--reject", default="",
                    help="人工判废的段，形如 'S14#s278:导航目录非正文,S21#s349:...'（判废理由会写进计划文件）")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in open(os.path.join(K, "chunks", "chunks.jsonl"),
                                          encoding="utf-8") if l.strip()]
    by_id = {c["chunkId"]: c for c in chunks}

    graph = json.load(open(os.path.join(K, "graph", "graph.json"), encoding="utf-8"))
    cited = set()
    for n in graph["nodes"]:
        cited.update(n.get("sourceRefs") or [])
    for e in graph["edges"]:
        cited.update(e.get("sourceRefs") or [])
    cited = cited.intersection(set(by_id))

    frozen = set()
    for name in [x.strip() for x in args.exclude.split(",") if x.strip()]:
        path = os.path.join(EV, name)
        if not os.path.exists(path):
            print(f"  （缺 {name}，跳过）")
            continue
        for q in json.load(open(path, encoding="utf-8")).get("questions", []):
            frozen.update(q.get("referenceChunks") or [])
    frozen = frozen.intersection(set(by_id))
    print(f"图谱引用 {len(cited)} 段；题集已占用 {len(frozen)} 段（--exclude）")

    by_source = defaultdict(list)
    for c in chunks:
        by_source[c["sourceId"]].append(c)
    for s in by_source:
        by_source[s].sort(key=lambda c: c["charRange"][0])
    pos_of = {}
    for s, rows in by_source.items():
        for i, c in enumerate(rows):
            pos_of[c["chunkId"]] = i

    # 人工判废：机械指标分不开「正文」与「导航目录/目录页」——
    # 实测散文度（含句读的行占比）对 S07#4.2 这种合法 API 列表也是 0.04，
    # 与导航目录同档；短行占比又把 S21#s349 这类政策文档（0.92）与导航目录混在一起。
    # 所以判废只能靠读，这里只是把判断显式记录下来，避免下次重复捡起同一段。
    rejected = {}
    for item in [x for x in args.reject.split(",") if x.strip()]:
        cid, _, reason = item.partition(":")
        rejected[cid.strip()] = reason.strip() or "（未写理由）"
    if rejected:
        print(f"人工判废 {len(rejected)} 段：{ {k: v for k, v in rejected.items()} }")

    def usable(c):
        return (c["chunkId"] not in cited and c["chunkId"] not in frozen
                and c["chunkId"] not in rejected
                and len(c["text"]) >= args.min_chars
                and cjk_ratio(c["text"]) >= args.min_cjk_ratio)

    want = defaultdict(int)
    for item in args.quota.split(","):
        split, rest = item.split(":", 1)
        cat, n = rest.split("=")
        want[(split.strip(), cat.strip())] += int(n)

    # 关键：按来源做「最大不相交配对」再分配，而不是逐（集合×类目）各挑一遍。
    # 逐类目各挑一次会把同一来源的剩余段拆成单数、配不成对——实测这一条
    # 让 28 题的容量只配出 24 题（test/AI 只够 2/5）。
    # 上/下半区对配（i 与 i+half）是同一来源上既最大化配对数、又最大化间隔的构造，
    # 且 half >= min_gap 时天然满足间隔要求。
    pairs_by_cat = defaultdict(list)
    for s, rows in by_source.items():
        free_rows = [r for r in rows if usable(r)]
        n = len(free_rows)
        if n < 2:
            continue
        half = (n + 1) // 2
        if half < args.min_gap:
            continue
        for i in range(n - half):
            a, b = free_rows[i], free_rows[i + half]
            ga, gb = pos_of[a["chunkId"]], pos_of[b["chunkId"]]
            same_section = (a.get("sectionPath") or "") == (b.get("sectionPath") or "")
            pairs_by_cat[CATEGORY_OF.get(s, "?")].append({
                "a": a["chunkId"], "b": b["chunkId"], "sourceId": s,
                "gap": abs(ga - gb), "sameSection": same_section,
                "score": richness(a["text"]) + richness(b["text"]),
            })

    picks = []
    for (split, cat), n in want.items():
        cands = sorted(pairs_by_cat.get(cat, []),
                       key=lambda p: (0 if p["sameSection"] else 1, -p["gap"], -p["score"]))
        taken = 0
        for p in cands:
            if taken >= n:
                break
            if p.get("taken"):
                continue
            p["taken"] = True
            picks.append({"split": split, "category": cat, "a": p["a"], "b": p["b"],
                          "sourceId": p["sourceId"], "gap": p["gap"],
                          "sameSection": p["sameSection"],
                          "cjkRatioA": round(cjk_ratio(by_id[p["a"]]["text"]), 3),
                          "cjkRatioB": round(cjk_ratio(by_id[p["b"]]["text"]), 3)})
            taken += 1
        if taken < n:
            print(f"  ⚠ {split}/{cat} 只够 {taken} 题（需求 {n}）")

    left = sum(1 for cat in pairs_by_cat for p in pairs_by_cat[cat] if not p.get("taken"))
    if left:
        print(f"  提示：配对池还剩 {left} 组未分配（可上调配额）")

    lines = [
        "# 待写题清单（中文答案层）", "",
        f"筛选规则：未被图谱引用 / 未被现役题集占用 / >= {args.min_chars} 字符 / "
        f"两段答案中文占比 >= {args.min_cjk_ratio} / 同来源 / 原文间隔 >= {args.min_gap}",
        "",
        "写题要求：题面像真实用户会问的问题、**中文提问**；"
        "不得出现与原文连续相同 12 字以上的片段；gradingNotes 用中文写两条要点，分别对应 A/B 两段。",
        "",
    ]
    if rejected:
        lines += ["## 人工判废的段（不再回收）", ""]
        lines += [f"- `{k}`：{v}" for k, v in rejected.items()]
        lines += [""]
    for i, p in enumerate(picks, 1):
        a, b = p["a"], p["b"]
        lines += [
            f"## {i:02d}. [{p['split']}] {p['category']} — 参考段 `{a}` + `{b}`", "",
            f"- 来源 {p['sourceId']}；原文间隔 {p['gap']}；"
            f"中文占比 A={p['cjkRatioA']} B={p['cjkRatioB']}",
            f"- A 章节：{by_id[a].get('sectionPath') or '（无）'}",
            "", "```", clean_for_prompt(by_id[a]["text"], args.excerpt), "```",
            f"- B 章节：{by_id[b].get('sectionPath') or '（无）'}",
            "", "```", clean_for_prompt(by_id[b]["text"], args.excerpt), "```", "",
        ]
    open(args.out, "w", encoding="utf-8").write("\n".join(lines))
    json.dump({"rules": {"minChars": args.min_chars, "minGap": args.min_gap,
                         "minCjkRatio": args.min_cjk_ratio, "exclude": args.exclude},
               "picks": picks},
              open(args.picks, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\n共选出 {len(picks)} 题：{Counter(p['split'] for p in picks)}")
    print(f"  类目：{dict(Counter(p['category'] for p in picks))}")
    print(f"  → {os.path.relpath(args.out, ROOT)}")
    print(f"  → {os.path.relpath(args.picks, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
