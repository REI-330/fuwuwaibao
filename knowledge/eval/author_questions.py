#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
按配额自动出题：候选段对 → 质量预筛 → LLM 起草 → 机械查重 + 自检 → 写出题集。

产出（配额见 EVAL_SET_DESIGN.md）：dev 新增 40 题、test 40 题，每题 2 段参考答案。

四个关键设计（都是被实测逼出来的）：
  1) **内容富集度预筛**：候选池里不少段是导航串/链接列表/表格碎片，拿它们出题必然被模型判 SKIP。
     先按「中文占比、短行比例、去重字符率」过滤，省下大量无效调用。
  2) **提示里剥掉样板噪声**：O*NET 页面的 "Related occupations" 每行重复上百次，
     直接送进去模型只会判 SKIP；提示文本里剥掉，但参考答案仍用原始 chunk。
  3) **SKIP 就换下一对，不当作失败**：模型说"这两段撑不起问题"是有效信号，
     按质量顺序继续取下一对，直到凑满配额或候选耗尽。
  4) **HTTP 失败带退避重试**：端点对突发调用会直接拒（实测 60/78 次），必须重试 + 调用间小睡。

草稿按「段对」缓存到 runs/author-cache.json，重跑不再调模型。

用法：
  python knowledge/eval/author_questions.py --dry-run
  python knowledge/eval/author_questions.py
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
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
POOL = os.path.join(K, "evaluations", "candidate_pool.jsonl")
OLD_QUESTIONS = os.path.join(K, "evaluations", "questions-v2.json")
OUT_DEV = os.path.join(K, "evaluations", "questions-dev.json")
OUT_TEST = os.path.join(K, "evaluations", "questions-test.json")
RUNS = os.path.join(K, "eval", "runs")
CACHE = os.path.join(RUNS, "author-cache.json")
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

# 个别类目语料稀薄，全局阈值会把仅有的可用段也筛掉，这里做按类目覆盖。
# 标准与政策：S21 在 0.8 阈值下只剩 2 段且间隔仅 2（不满足 R3）；放到 0.6 后可配出间隔 3 的对。
CATEGORY_MIN_RICHNESS = {"标准与政策": 0.60}

DRAFT_PROMPT = """你是职业导航知识库的评测出题人。下面是同一份文档里的两段原文。
请据此写一道**中文检索评测题**。

硬要求：
1. 题目要像真实用户会问的职业导航或技术问题，语气自然，不要写成考试题干，不要以"根据资料/根据片段"开头。
2. 答案必须**只能**从这两段原文得出。
3. **严禁照抄原文**：题面里不得出现与原文连续相同 12 个字以上的片段。
4. 不要提到"片段""原文""参考资料"等字样。
5. 若这两段内容撑不起一个像样的问题（只是导航、目录、链接列表、纯表格碎片、重复样板），
   只输出一行：SKIP

输出格式（严格三行）：
问题：<一句话问题>
要点1：<回答时必须覆盖的信息点>
要点2：<回答时必须覆盖的另一个信息点>

原文 A（章节：{secA}）
{textA}

原文 B（章节：{secB}）
{textB}
"""

VERIFY_PROMPT = """下面是一道评测题和它的两段参考原文。严格按三行回答，每行只有 Y 或 N，不要解释：

可答性：只用这两段原文能否完整回答该问题？（Y/N）
抄写：题面中是否出现了与原文连续相同 12 个字以上的片段？（Y/N）
歧义：该问题是否存在两种以上合理解读，导致无法判定对错？（Y/N）

问题：{q}

原文 A：
{textA}

原文 B：
{textB}
"""


def load_env():
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def llm(prompt, timeout=180, max_tokens=600, attempts=8, base_delay=5.0):
    """带退避重试：端点会突发返回 503（实测一次中断可持续 1–3 分钟），
    必须重试到能扛住一次完整中断，否则缓存里会留下"因限流而失败"的假记录。"""
    base = os.environ["DEEPEVAL_BASE_URL"].rstrip("/")
    body = json.dumps({"model": os.environ["DEEPEVAL_MODEL"], "temperature": 0,
                       "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                base + "/chat/completions", data=body,
                headers={"Authorization": "Bearer " + os.environ["DEEPEVAL_API_KEY"],
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            return (d["choices"][0]["message"]["content"] or "").strip()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            try:
                last += " " + e.read().decode("utf-8", "replace")[:120]
            except Exception:
                pass
        except Exception as e:
            last = f"{type(e).__name__} {e}"
        time.sleep(min(base_delay * (2 ** i), 40) + random.uniform(0, 2))
    raise RuntimeError(last)


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


def clean_for_prompt(text, limit=1400):
    """剥掉样板噪声后再给模型看（参考答案仍用原始 chunk）。"""
    t = BOILER.sub("", text)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()[:limit]


def richness(text):
    """内容富集度：用于预筛掉导航串/链接列表/数值表格碎片。越大越像成段正文。

    必须**语言中立**：早先版本用「中文占比 ×2」，把 S16/S17 这类英文职业数据整片打成负分，
    导致「职业能力」类目候选对为 0。现在改用三个与语言无关的量：
      prose  = 长度 >= 30 字符的行所占字符比例（导航/链接列表基本都是短行，prose 低）
      uniq   = 去重字符率（重复表格/样板会很低）
      digits = 数字字符占比（纯数值表格惩罚）
    """
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


def parse_verify(text):
    """把自检回复解析成 (可答性, 抄写, 歧义) 三个布尔值。

    实测模型**只回三行 Y/N，不回显标签**（例如 "Y\\nN\\nN"），
    早先版本要求响应里含「可答性：Y」字面字样，导致所有题都被误判为不合格。
    """
    vals = []
    for line in (text or "").splitlines():
        line = line.strip().replace("：", ":")
        seg = line.split(":", 1)[-1] if ":" in line else line
        for ch in seg.strip().upper():
            if ch in ("Y", "N"):
                vals.append(ch == "Y")
                break
    while len(vals) < 3:
        vals.append(None)
    return vals[0], vals[1], vals[2]


def verify_ok(text):
    a, c, _ = parse_verify(text)
    return a is True and c is False


def verify_usable(text):
    """响应是否已经能解析出三行结论（用于判断要不要重送检）。"""
    a, c, d = parse_verify(text)
    return None not in (a, c, d)


def max_pairs(n_rows, min_gap):
    if n_rows < 2:
        return 0
    half = (n_rows + 1) // 2
    return 0 if half < min_gap else n_rows - half


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-gap", type=int, default=3)
    ap.add_argument("--min-chars", type=int, default=300)
    ap.add_argument("--min-richness", type=float, default=0.8)
    ap.add_argument("--max-lcs", type=int, default=12)
    ap.add_argument("--max-attempts", type=int, default=4,
                    help="每个配额槽位最多试几对候选（SKIP 会消耗一次）")
    ap.add_argument("--max-calls", type=int, default=400, help="LLM 调用总上限，防跑飞")
    ap.add_argument("--pace", type=float, default=1.5, help="每次调用后的间隔秒数（端点会突发限流）")
    ap.add_argument("--max-per-section", type=int, default=2,
                    help="同一章节最多出几题（防近似重复：实测不限流时半数题挤在同一章节）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    load_env()

    chunks = {json.loads(l)["chunkId"]: json.loads(l)
              for l in open(CHUNKS, encoding="utf-8") if l.strip()}
    pool = [json.loads(l) for l in open(POOL, encoding="utf-8") if l.strip()]
    pool = [r for r in pool if not r["usedByCurrentSet"] and r["chars"] >= args.min_chars]
    rich = {r["chunkId"]: richness(chunks[r["chunkId"]]["text"]) for r in pool}
    keep = [r for r in pool if rich[r["chunkId"]] >= args.min_richness]
    print(f"候选池 {len(pool)} 段 → 全局富集度预筛后 {len(keep)} 段"
          f"（阈值 {args.min_richness}，剔除 {len(pool)-len(keep)} 段导航/列表/数值表）")

    by_source = defaultdict(list)
    for r in pool:                       # 注意：这里存全量，阈值在 build_candidates 里按类目施加
        by_source[r["sourceId"]].append(r)
    for s in by_source:
        by_source[s].sort(key=lambda r: r["pos"])

    def threshold_of(cat):
        return CATEGORY_MIN_RICHNESS.get(cat, args.min_richness)

    def rows_for(cat, s):
        th = threshold_of(cat)
        return [r for r in by_source.get(s, []) if rich[r["chunkId"]] >= th]

    # 每个类目的候选对，按质量排序：同章节优先 → 间隔大 → 两段富集度之和
    def build_candidates(cat):
        out = []
        for s, c in CATEGORY_OF.items():
            if c != cat:
                continue
            rows = rows_for(cat, s)
            for a, b in itertools.combinations(rows, 2):
                gap = abs(a["pos"] - b["pos"])
                if gap < args.min_gap:
                    continue
                same = a["sectionPath"] == b["sectionPath"]
                richs = rich[a["chunkId"]] + rich[b["chunkId"]]
                out.append(((0 if same else 1, -gap, -richs, a["chunkId"], b["chunkId"])))
        out.sort()
        return interleave_by_section(out)

    def interleave_by_section(cands):
        """按「章节」轮转取，避免早期全靠同一章节的高分对 →
        实测不轮转时前 13 题里有一半来自 S03#s61 的相邻碎片，近似重复。"""
        buckets = defaultdict(list)
        for c in cands:
            sec = chunks[c[3]].get("sectionPath") or chunks[c[3]]["sourceId"]
            buckets[(chunks[c[3]]["sourceId"], sec)].append(c)
        order = sorted(buckets, key=lambda k: buckets[k][0])
        out, i = [], 0
        while any(buckets[k] for k in order):
            for k in order:
                if buckets[k]:
                    out.append(buckets[k].pop(0))
            i += 1
            if i > 10000:
                break
        return out

    plan = {}
    for cat, q in QUOTA.items():
        cands = build_candidates(cat)
        plan[cat] = {"cands": cands, "need": {"dev": q["dev"], "test": q["test"]}}
        print(f"  {cat}: 候选对 {len(cands)}，配额 dev {q['dev']} / test {q['test']}")

    if args.dry_run:
        for cat, p in plan.items():
            if p["cands"]:
                print(f"  {cat} 最高质量对：{p['cands'][0][3]} + {p['cands'][0][4]}")
        return 0

    cache = {}
    if os.path.exists(CACHE) and not args.refresh:
        cache = json.load(open(CACHE, encoding="utf-8"))["items"]
        print(f"复用出题缓存 {len(cache)} 条")

    out = {"dev": [], "test": []}
    used_chunks, calls, skips = set(), 0, 0
    section_used = defaultdict(int)
    t0 = time.time()

    def cache_put(k, rec):
        cache[k] = rec
        json.dump({"items": cache}, open(CACHE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    for cat, p in plan.items():
        idx = 0
        while (p["need"]["dev"] > 0 or p["need"]["test"] > 0) and idx < len(p["cands"]):
            _, _, _, a, b = p["cands"][idx]
            idx += 1
            if a in used_chunks or b in used_chunks:
                continue
            sec_key = (chunks[a]["sourceId"], chunks[a].get("sectionPath") or "")
            if section_used[sec_key] >= args.max_per_section:
                continue
            # 目标集：剩余配额多的那个
            split = "dev" if p["need"]["dev"] >= p["need"]["test"] else "test"
            if p["need"][split] <= 0:
                break
            key = f"{a}|{b}"
            rec = cache.get(key)
            ca, cb = chunks[a], chunks[b]
            if rec is None:
                if calls >= args.max_calls:
                    print(f"  达到调用上限 {args.max_calls}，停止出题")
                    break
                try:
                    raw = llm(DRAFT_PROMPT.format(
                        secA=ca.get("sectionPath") or "", textA=clean_for_prompt(ca["text"]),
                        secB=cb.get("sectionPath") or "", textB=clean_for_prompt(cb["text"])))
                    calls += 1
                except Exception as e:
                    print(f"  起草失败 {key}: {e}")
                    continue
                q, notes = "", []
                for line in raw.splitlines():
                    line = line.strip()
                    if line.startswith("问题："):
                        q = line[3:].strip()
                    elif line.startswith("要点"):
                        notes.append(line.split("：", 1)[-1].strip())
                lcs = max(longest_common_substring(norm(q), norm(ca["text"])),
                          longest_common_substring(norm(q), norm(cb["text"]))) if q else 999
                verdict = ""
                if q and not raw.strip().startswith("SKIP") and lcs <= args.max_lcs:
                    try:
                        verdict = llm(VERIFY_PROMPT.format(
                            q=q, textA=clean_for_prompt(ca["text"], 1200),
                            textB=clean_for_prompt(cb["text"], 1200)), max_tokens=60)
                        calls += 1
                    except Exception as e:
                        verdict = f"ERROR {e}"
                rec = {"question": q, "notes": notes, "lcs": lcs, "raw": raw[:600],
                       "verify": verdict[:300],
                       "chunkA": a, "chunkB": b,
                       "secA": ca.get("sectionPath") or "", "secB": cb.get("sectionPath") or ""}
                cache_put(key, rec)
                time.sleep(args.pace)

            # 自检缺失或"因限流出错"→ 重新送检：不能把端点的锅算到题目头上
            # （早先版本就是这么把 31 道已起草的题里好几道误判掉的）
            if rec.get("question") and not rec["raw"].strip().startswith("SKIP"):
                v0 = rec.get("verify") or ""
                if not verify_usable(v0) or "ERROR" in v0.upper():
                    try:
                        rec["verify"] = llm(VERIFY_PROMPT.format(
                            q=rec["question"], textA=clean_for_prompt(ca["text"], 1200),
                            textB=clean_for_prompt(cb["text"], 1200)), max_tokens=60)[:300]
                        calls += 1
                    except Exception as e:
                        rec["verify"] = f"ERROR {e}"
                    cache_put(key, rec)
                    time.sleep(args.pace)

            q = rec["question"]
            v = (rec.get("verify") or "").replace(" ", "")
            if not q or rec["raw"].strip().startswith("SKIP"):
                skips += 1
                continue
            if rec["lcs"] > args.max_lcs or not verify_ok(rec.get("verify")):
                skips += 1
                continue

            prefix = "D" if split == "dev" else "T"
            out[split].append({
                "questionId": f"{prefix}{len(out[split])+1:02d}",
                "split": split, "category": cat, "question": q, "answerable": True,
                "referenceChunks": [a, b],
                "gradingNotes": "；".join(rec["notes"]) or "（要点未生成，需人工补）",
                "sourceId": chunks[a]["sourceId"],
                "crossLingual": not (CJK.search(chunks[a]["text"]) and CJK.search(chunks[b]["text"])),
                "provenance": {"chunkA": a, "chunkB": b, "secA": rec["secA"], "secB": rec["secB"],
                               "lcsWithSource": rec["lcs"], "llmSelfCheck": (rec.get("verify") or "")[:160]},
            })
            used_chunks.update((a, b))
            section_used[sec_key] += 1
            p["need"][split] -= 1
            print(f"  [{split} {len(out[split])}] {cat} {a}+{b} → {q[:40]}…", flush=True)

        if p["need"]["dev"] or p["need"]["test"]:
            print(f"  ⚠ {cat} 未凑满配额，缺 dev {p['need']['dev']} / test {p['need']['test']}"
                  f"（候选对已耗尽）")

    el = time.time() - t0
    print(f"\n出题完成：dev {len(out['dev'])} / test {len(out['test'])}；"
          f"LLM 调用 {calls} 次，SKIP/不合格 {skips} 次，用时 {el:.1f}s")

    meta = {"schema": "career-graph-eval-questions/v3",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "note": "由 author_questions.py 按 EVAL_SET_DESIGN.md 配额出题：候选段先做内容富集度预筛，"
                    "题面经机械查重（与原文最长公共连续字符 <= 12）与 LLM 自检（可答性 / 未抄写）。"
                    "参考答案段满足：未落在图谱引用集合内 / 同来源 / 间隔 >= 3 / dev-test 不共用。",
            "selectionRule": {"minGap": args.min_gap, "minChars": args.min_chars,
                              "minRichness": args.min_richness, "maxLcs": args.max_lcs,
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
                            "referenceChunksTotal": sum(len(q["referenceChunks"])
                                                        for q in out["test"])},
                    questions=out["test"])
    json.dump(test_doc, open(OUT_TEST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"产物：{os.path.relpath(OUT_DEV, ROOT)} {dev_doc['counts']}")
    print(f"      {os.path.relpath(OUT_TEST, ROOT)} {test_doc['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
