#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量出题：一次送 N 个段对，让模型一次产出 N 道题 + N 条自检。

为什么不沿用「一次一对」：实测每题要 2 次串行调用（起草 + 自检），
80 题 = 160 次调用，按 0.6 题/分钟要 2 小时；批量后调用数降一个数量级。

质量闸门（一道题要全过才收）：
  1) 机械查重：题面与两段原文的最长公共连续字符 <= --max-lcs
  2) 模型自检：可答性 Y、抄写 N、歧义 N
  3) 章节限流：同一 (来源, 章节) 最多 --max-per-section 题，防近似重复
  4) 段落独占：dev 与 test 不共用参考段（R5）

用法：
  python knowledge/eval/author_questions_batch.py --batch 5 --pace 1.0
"""
import argparse
import itertools
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

from _shared_llm import chat  # 评测与产品共用的 LLM 入口（knowledge/eval/_shared_llm.py）
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
POOL = os.path.join(K, "evaluations", "candidate_pool.jsonl")
OLD_QUESTIONS = os.path.join(K, "evaluations", "questions-v2.json")
OUT_DEV = os.path.join(K, "evaluations", "questions-dev.json")
OUT_TEST = os.path.join(K, "evaluations", "questions-test.json")
RUNS = os.path.join(K, "eval", "runs")
CACHE = os.path.join(RUNS, "author-batch-cache.json")
ENV_FILE = os.path.join(K, "eval", ".env")

CJK = re.compile(r"[\u4e00-\u9fff]")
BOILER = re.compile(r"^\s*(-{3,}|Related occupations|View the list of|external site|"
                    r"此页面对您有帮助吗？|感谢您对我们工作的肯定！|感谢您告诉我们本页内容还需要完善。).*$",
                    re.MULTILINE)

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
QUOTA = {
    "嵌入式开发":   {"dev": 13, "test": 13},
    "测试自动化":   {"dev": 14, "test": 11},
    "AI框架与推理": {"dev": 8,  "test": 9},
    "职业能力":     {"dev": 5,  "test": 5},
    "计算机视觉":   {"dev": 0,  "test": 1},
    "标准与政策":   {"dev": 0,  "test": 1},
}
CATEGORY_MIN_RICHNESS = {"标准与政策": 0.60}

BATCH_PROMPT = """你是职业导航知识库的评测出题人。下面有 {n} 组原文（每组两段，来自同一份文档）。
请为**每一组**写一道中文检索评测题。

硬要求（对每一组都适用）：
1. 题目要像真实用户会问的职业导航或技术问题，语气自然，不要以"根据资料/根据片段"开头。
2. 答案必须只能从该组的两段原文得出。
3. 严禁照抄原文：题面里不得出现与原文连续相同 12 个字以上的片段。
4. 不要提到"片段""原文""参考资料"。
5. 某一组若撑不起像样的问题（只是导航、目录、链接列表、纯表格碎片、重复样板），
   该组的 question 填空字符串 ""。

只输出 JSON 数组，不要输出任何解释、不要用代码块包裹。每个元素格式：
{{"i": 组号, "q": "问题", "n1": "要点1", "n2": "要点2"}}

{candidates}
"""

BATCH_VERIFY_PROMPT = """下面是 {n} 道评测题及其参考原文。请逐题判断，只输出 JSON 数组，不要解释、不要代码块。
每个元素格式（三个字段都必须填 true 或 false，不要写别的）：
{{"i": 组号, "answerable": true, "copied": false, "ambiguous": false}}
含义：answerable=只用该组两段原文能否完整回答该问题；copied=题面是否出现与原文连续相同 12 字以上的片段；ambiguous=该问题是否存在两种以上合理解读。

{cases}
"""


def load_env():
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def llm(prompt, timeout=240, max_tokens=2500, attempts=8, base_delay=5.0):
    """统一走 `_shared_llm`（出题脚本要扛住端点 1–3 分钟的中断，所以 attempts/退避都留足）。"""
    return chat(prompt, max_tokens=max_tokens, timeout=timeout, attempts=attempts,
                backoff=(base_delay, 40.0))


def parse_json_array(text):
    """容忍代码块包裹与前后噪声。"""
    if not text:
        return []
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    start, end = t.find("["), t.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        return json.loads(t[start:end + 1])
    except Exception:
        # 逐个对象抢救
        out = []
        for m in re.finditer(r"\{[^{}]*\}", t[start:end + 1]):
            try:
                out.append(json.loads(m.group(0)))
            except Exception:
                pass
        return out


def tri(v):
    """把自检字段解析成 True / False / None（未知）。

    坑：早先提示里把占位符写成「Y或N」，模型就**原样回吐字符串 "Y或N"**，
    而判断又用 startswith("Y")，于是「抄写」被误判为"是"、整批题被丢。
    现在只认真布尔/true/false/Y/N，其余一律视为未知（不据此丢题，只标记未核验）。
    """
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().upper()
    if s in ("TRUE", "Y", "YES"):
        return True
    if s in ("FALSE", "N", "NO"):
        return False
    return None


def longest_common_substring(a, b):
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def norm(s):
    return re.sub(r"\s+", "", s or "")


def clean_for_prompt(text, limit=1100):
    t = BOILER.sub("", text)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()[:limit]


def richness(text):
    t = clean_for_prompt(text, 10 ** 9)
    if len(t) < 400:
        return 0.0
    lines = [l.strip() for l in t.split("\n") if l.strip()]
    if not lines:
        return 0.0
    prose = sum(len(l) for l in lines if len(l) >= 30) / len(t)
    digits = sum(ch.isdigit() for ch in t) / len(t)
    uniq = len(set(t)) / len(t)
    return round(prose + uniq - 1.5 * digits, 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--min-gap", type=int, default=3)
    ap.add_argument("--min-chars", type=int, default=300)
    ap.add_argument("--min-richness", type=float, default=0.8)
    ap.add_argument("--max-lcs", type=int, default=12)
    ap.add_argument("--max-per-section", type=int, default=2)
    ap.add_argument("--pace", type=float, default=1.0)
    ap.add_argument("--max-batches", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()

    chunks = {json.loads(l)["chunkId"]: json.loads(l)
              for l in open(CHUNKS, encoding="utf-8") if l.strip()}
    pool = [json.loads(l) for l in open(POOL, encoding="utf-8") if l.strip()]
    pool = [r for r in pool if not r["usedByCurrentSet"] and r["chars"] >= args.min_chars]
    rich = {r["chunkId"]: richness(chunks[r["chunkId"]]["text"]) for r in pool}

    by_source = defaultdict(list)
    for r in pool:
        by_source[r["sourceId"]].append(r)
    for s in by_source:
        by_source[s].sort(key=lambda r: r["pos"])

    def rows_for(cat, s):
        th = CATEGORY_MIN_RICHNESS.get(cat, args.min_richness)
        return [r for r in by_source.get(s, []) if rich[r["chunkId"]] >= th]

    def build_candidates(cat):
        cands = []
        for s, c in CATEGORY_OF.items():
            if c != cat:
                continue
            rows = rows_for(cat, s)
            for a, b in itertools.combinations(rows, 2):
                gap = abs(a["pos"] - b["pos"])
                if gap < args.min_gap:
                    continue
                same = a["sectionPath"] == b["sectionPath"]
                cands.append(((0 if same else 1, -gap, -(rich[a["chunkId"]] + rich[b["chunkId"]]),
                               a["chunkId"], b["chunkId"])))
        cands.sort()
        # 按章节轮转，避免早期全挤在同一章节
        buckets = defaultdict(list)
        for c in cands:
            buckets[(chunks[c[3]]["sourceId"], chunks[c[3]].get("sectionPath") or "")].append(c)
        order = sorted(buckets, key=lambda k: buckets[k][0])
        out = []
        while any(buckets[k] for k in order):
            for k in order:
                if buckets[k]:
                    out.append(buckets[k].pop(0))
        return out

    plans = {cat: build_candidates(cat) for cat in QUOTA}
    for cat, c in plans.items():
        print(f"  {cat}: 候选对 {len(c)}，配额 dev {QUOTA[cat]['dev']} / test {QUOTA[cat]['test']}")

    if args.dry_run:
        return 0

    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))["items"]
        print(f"复用批量出题缓存 {len(cache)} 组")

    out = {"dev": [], "test": []}
    used_chunks, section_used = set(), defaultdict(int)
    need = {c: dict(v) for c, v in QUOTA.items()}
    calls, t0 = 0, time.time()
    batches = 0

    def cache_put(k, v):
        cache[k] = v
        json.dump({"items": cache}, open(CACHE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    for cat, cands in plans.items():
        idx = 0
        while (need[cat]["dev"] > 0 or need[cat]["test"] > 0) and idx < len(cands):
            if batches >= args.max_batches:
                print(f"  达到批次上限 {args.max_batches}，停止")
                break
            # 攒一批：跳过已占用/超章节限额的对
            group, keys = [], []
            while idx < len(cands) and len(group) < args.batch:
                _, _, _, a, b = cands[idx]
                idx += 1
                if a in used_chunks or b in used_chunks:
                    continue
                sec = (chunks[a]["sourceId"], chunks[a].get("sectionPath") or "")
                if section_used[sec] >= args.max_per_section:
                    continue
                group.append((a, b))
                keys.append(f"{a}|{b}")
            if not group:
                break

            batch_key = "||".join(keys)
            rec = cache.get(batch_key)
            if rec is None:
                blocks = []
                for i, (a, b) in enumerate(group):
                    blocks.append(
                        f"【第 {i} 组】\n原文 A（章节：{chunks[a].get('sectionPath') or ''}）\n"
                        f"{clean_for_prompt(chunks[a]['text'])}\n\n"
                        f"原文 B（章节：{chunks[b].get('sectionPath') or ''}）\n"
                        f"{clean_for_prompt(chunks[b]['text'])}")
                try:
                    draft_raw = llm(BATCH_PROMPT.format(n=len(group),
                                                        candidates="\n\n".join(blocks)))
                    draft = parse_json_array(draft_raw)
                    calls += 1
                except Exception as e:
                    print(f"  批量起草失败 {batch_key[:40]}: {e}")
                    rec = {"draft": [], "verify": [], "raw": ""}
                    cache_put(batch_key, rec)
                    continue
                time.sleep(args.pace)

                # 只对通过机械查重的题做自检，省调用
                prelim = []
                for item in draft:
                    i = item.get("i")
                    q = (item.get("q") or "").strip()
                    if not isinstance(i, int) or not (0 <= i < len(group)) or not q:
                        continue
                    a, b = group[i]
                    lcs = max(longest_common_substring(norm(q), norm(chunks[a]["text"])),
                              longest_common_substring(norm(q), norm(chunks[b]["text"])))
                    if lcs > args.max_lcs:
                        continue
                    prelim.append({"i": i, "q": q, "n1": item.get("n1", ""), "n2": item.get("n2", ""),
                                   "lcs": lcs, "a": a, "b": b})
                verify = []
                if prelim:
                    cases = []
                    for p in prelim:
                        cases.append(f"【第 {p['i']} 题】\n问题：{p['q']}\n\n原文 A：\n"
                                     f"{clean_for_prompt(chunks[p['a']]['text'], 900)}\n\n原文 B：\n"
                                     f"{clean_for_prompt(chunks[p['b']]['text'], 900)}")
                    try:
                        vraw = llm(BATCH_VERIFY_PROMPT.format(n=len(prelim),
                                                              cases="\n\n".join(cases)),
                                   max_tokens=1200)
                        verify = parse_json_array(vraw)
                        calls += 1
                    except Exception as e:
                        print(f"  批量自检失败：{e}")
                    time.sleep(args.pace)
                rec = {"draft": draft, "verify": verify, "raw": draft_raw[:600]}
                cache_put(batch_key, rec)
            else:
                draft, verify = rec.get("draft") or [], rec.get("verify") or []

            vmap = {v.get("i"): v for v in (rec.get("verify") or []) if isinstance(v, dict)}
            for item in (rec.get("draft") or []):
                i = item.get("i")
                q = (item.get("q") or "").strip()
                if not isinstance(i, int) or not (0 <= i < len(group)) or not q:
                    continue
                a, b = group[i]
                if a in used_chunks or b in used_chunks:
                    continue
                sec = (chunks[a]["sourceId"], chunks[a].get("sectionPath") or "")
                if section_used[sec] >= args.max_per_section:
                    continue
                lcs = max(longest_common_substring(norm(q), norm(chunks[a]["text"])),
                          longest_common_substring(norm(q), norm(chunks[b]["text"])))
                if lcs > args.max_lcs:
                    continue
                v = vmap.get(i) or {}
                ay, copied, amb = tri(v.get("answerable")), tri(v.get("copied")), tri(v.get("ambiguous"))
                has_verify = None not in (ay, copied, amb)
                # 三项都能解析才严格执行；缺项一律视为"未核验"放行并标记，
                # 不能把"模型没按格式回"当成"题不合格"（这会成批误杀）。
                if has_verify and (ay is not True or copied is True or amb is True):
                    continue
                split = "dev" if need[cat]["dev"] >= need[cat]["test"] else "test"
                if need[cat][split] <= 0:
                    split = "test" if split == "dev" else "dev"
                    if need[cat][split] <= 0:
                        break
                prefix = "D" if split == "dev" else "T"
                out[split].append({
                    "questionId": f"{prefix}{len(out[split])+1:02d}",
                    "split": split, "category": cat, "question": q, "answerable": True,
                    "referenceChunks": [a, b],
                    "gradingNotes": "；".join(x for x in [item.get("n1", ""), item.get("n2", "")] if x)
                                    or "（要点未生成，需人工补）",
                    "sourceId": chunks[a]["sourceId"],
                    "crossLingual": not (CJK.search(chunks[a]["text"]) and CJK.search(chunks[b]["text"])),
                    "provenance": {"chunkA": a, "chunkB": b,
                                   "secA": chunks[a].get("sectionPath") or "",
                                   "secB": chunks[b].get("sectionPath") or "",
                                   "lcsWithSource": lcs,
                                   "llmVerified": has_verify,
                                   "llmSelfCheck": json.dumps(v, ensure_ascii=False)[:160]},
                })
                used_chunks.update((a, b))
                section_used[sec] += 1
                need[cat][split] -= 1
                print(f"  [{split} {len(out[split])}] {cat} {a}+{b} → {q[:38]}…", flush=True)
            batches += 1
        if need[cat]["dev"] or need[cat]["test"]:
            print(f"  ⚠ {cat} 未凑满：缺 dev {need[cat]['dev']} / test {need[cat]['test']}")

    print(f"\n完成：dev {len(out['dev'])} / test {len(out['test'])}；"
          f"批次 {batches}，LLM 调用 {calls}，用时 {time.time()-t0:.1f}s")

    meta = {"schema": "career-graph-eval-questions/v3",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "note": "由 author_questions_batch.py 出题：候选段先做内容富集度预筛（阈值 "
                    f"{args.min_richness}），按章节轮转取对，批量起草 + 批量自检；"
                    "题面经机械查重（与原文最长公共连续字符 <= "
                    f"{args.max_lcs}）与模型自检（可答性 / 未抄写 / 无歧义）。"
                    "参考答案段满足：未落在图谱引用集合内 / 同来源 / 间隔 >= "
                    f"{args.min_gap} / dev-test 不共用。",
            "selectionRule": {"minGap": args.min_gap, "minChars": args.min_chars,
                              "minRichness": args.min_richness, "maxLcs": args.max_lcs,
                              "maxPerSection": args.max_per_section, "batch": args.batch,
                              "candidatePool": "evaluations/candidate_pool.jsonl"}}

    old = json.load(open(OLD_QUESTIONS, encoding="utf-8"))["questions"]
    dev_doc = dict(meta, split="dev",
                   counts={"total": len(old) + len(out["dev"]), "carriedOver": len(old),
                           "new": len(out["dev"]),
                           "referenceChunksTotal": sum(len(q["referenceChunks"])
                                                       for q in old + out["dev"])},
                   questions=old + out["dev"])
    json.dump(dev_doc, open(OUT_DEV, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    test_doc = dict(meta, split="test",
                    counts={"total": len(out["test"]),
                            "referenceChunksTotal": sum(len(q["referenceChunks"]) for q in out["test"])},
                    questions=out["test"])
    json.dump(test_doc, open(OUT_TEST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"产物：{os.path.relpath(OUT_DEV, ROOT)} {dev_doc['counts']}")
    print(f"      {os.path.relpath(OUT_TEST, ROOT)} {test_doc['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
