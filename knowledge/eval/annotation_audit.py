#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""标注质量审计：对每道题做 A/B 判定 —— 标注段 vs 模型选段，哪组更能回答问题。

## 为什么需要它

在「候选池 10→30」优化后，test 32 题的失败构成发生反转：召回损失从 9 题降到 1 题，
**重排损失升到 5 题**。逐题人工核对后发现 4 道的参考答案段指向了与问题无关的段落
（都是 API 签名/错误码碎片），而模型选中的段落才是真答案。

手工核对能发现现象，但不能回答两个问题：
  1. 这个问题有多普遍？只在失败的题里有，还是全题集都有？
  2. 我的判断是不是主观的？

本脚本把这件事变成可复算的审计：用裁判模型对**每道题**做 A/B 判定，
输入只有「问题」和两组候选片段（不给它看哪组是标注、哪组是模型选的，避免迎合），
输出 A / B / 平手。命中题的判定结果同时充当对照组。

## 判读口径

- 对**命中题**：预期判 A（标注段）或平手 —— 若判 B，说明连命中的题也可能标注偏弱
- 对**失败题**：若判 B，说明该题是标注问题而非检索问题
- 汇总后给出「疑似标注有误」的题清单，供人工复核（**本脚本不改题集**）

## ⚠️ 纪律：修正标注不得以模型输出为依据

本脚本的输出**只能用来定位"哪些题的标注需要人工复核"**，不能直接拿来改标注。
如果按「模型选了哪几段」去替换参考答案段，就是把评测集往模型上拟合，
后续所有指标都会变成自我实现——**评测随即失去意义**。

修正标注的正确做法：**暂时忘掉模型输出，回到原文**，独立回答
「哪一段真的回答了这个问题」，再据此定参考段。模型输出只在事后用于对照
（判断这次修正是修正了标注，还是迁就了模型）。

## ⚠️ 单一 LLM 裁判不可靠，必须跨模型交叉

实测同一套题：deepseek-v4-flash 判「命中题里模型选段更好」10/26（38%，10 题平手），
mimo-v2.6-pro 判 22/26（85%，0 题平手）。**绝对比例差异巨大，说明裁判口径不稳定。**
可用的做法是**跨家族交叉 + 取一致部分**：两裁判一致判「失败题标注存疑」的是 5/6（T05/T10/T12/T13/T15），
这部分结论稳健；而对照组比例两裁判不一致，**不可引用**，需人工抽样或第三裁判多数表决。

跑法：
  set -a; . knowledge/eval/.env; set +a
  .venv/Scripts/python.exe knowledge/eval/annotation_audit.py \
      --questions knowledge/evaluations/questions-test.json \
      --rerank-cache knowledge/eval/runs/q1024-c30-test-rerank-cache.json \
      --out knowledge/eval/runs/annotation-audit-test.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
ENV_FILE = os.path.join(K, "eval", ".env")

PROMPT = """你在核查一份职业知识库评测集的标注质量。

问题：
{question}

两组候选片段（A 组与 B 组，来源未告知）：

【A 组】
{group_a}

【B 组】
{group_b}

请判断：哪一组更能回答上面的问题（即读者只看这一组能不能得到答案）？
只输出一行，格式为：
判定: A 或 B 或 平手 | 理由: 一句话
不要输出其它内容。"""


def load_env() -> None:
    if not os.path.exists(ENV_FILE):
        return
    for line in open(ENV_FILE, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def llm(prompt: str, timeout: int = 180, attempts: int = 3) -> str:
    base = os.environ["DEEPEVAL_BASE_URL"].rstrip("/")
    body = json.dumps({"model": os.environ["DEEPEVAL_MODEL"], "temperature": 0,
                       "max_tokens": 200,
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                base + "/chat/completions", data=body,
                headers={"Authorization": "Bearer " + os.environ["DEEPEVAL_API_KEY"],
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            text = (d["choices"][0]["message"].get("content") or "").strip()
            if text:
                return text
            last = "返回内容为空"
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__} {error}"
        time.sleep(min(2 ** i, 8))
    raise RuntimeError(last)


def blocks(ids, chunks) -> str:
    parts = []
    for cid in ids:
        chunk = chunks.get(cid)
        if not chunk:
            continue
        sec = chunk.get("sectionPath") or chunk.get("heading") or ""
        parts.append(f"- id={cid} 章节={sec[:50]}\n  {chunk['text'][:280]}")
    return "\n".join(parts) if parts else "(空)"


def verdict_of(text: str) -> str:
    for line in text.splitlines():
        if "判定" in line:
            if "平手" in line:
                return "平手"
            if "A" in line.split("理由")[0]:
                return "A"
            if "B" in line.split("理由")[0]:
                return "B"
    if "平手" in text:
        return "平手"
    return "未解析"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--rerank-cache", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only-failed", action="store_true", help="只审计失败的题")
    args = ap.parse_args()

    load_env()
    chunks = {}
    with open(os.path.join(K, "chunks", "chunks.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                c = json.loads(line)
                chunks[c["chunkId"]] = c

    questions = [q for q in json.load(open(args.questions, encoding="utf-8"))["questions"]
                 if q.get("answerable", True)]
    picks = json.load(open(args.rerank_cache, encoding="utf-8"))["picks"]

    rows = []
    print(f"审计 {len(questions)} 题，裁判模型 {os.environ.get('DEEPEVAL_MODEL')}\n")
    for q in questions:
        qid = q["questionId"]
        refs = list(q.get("referenceChunks") or [])
        picked = (picks.get(qid) or {}).get("picked") or []
        hit = bool(set(refs) & set(picked))
        if args.only_failed and hit:
            continue
        # 顺序随机化做得更彻底些：按题号奇偶交换 A/B，避免模型对位置有偏好
        swap = (sum(ord(ch) for ch in qid) % 2 == 1)
        group_a = blocks(picked if swap else refs, chunks)
        group_b = blocks(refs if swap else picked, chunks)
        prompt = PROMPT.format(question=q["question"], group_a=group_a, group_b=group_b)
        try:
            raw = llm(prompt)
            verdict = verdict_of(raw)
        except Exception as error:  # noqa: BLE001
            raw, verdict = f"失败：{error}", "失败"
        if swap:  # 换回语义：A=标注段，B=模型选段
            verdict = {"A": "B", "B": "A"}.get(verdict, verdict)
        rows.append({"questionId": qid, "hit": hit, "verdict": verdict,
                     "referenceChunks": refs, "picked": picked, "raw": raw[:160]})
        flag = "命中" if hit else "未命中"
        print(f"  {qid:<5}{flag:<6}判定={'A(标注段)' if verdict=='A' else 'B(模型选段)' if verdict=='B' else verdict}")

    failed = [r for r in rows if not r["hit"]]
    b_on_failed = [r["questionId"] for r in failed if r["verdict"] == "B"]
    b_on_hit = [r["questionId"] for r in rows if r["hit"] and r["verdict"] == "B"]
    print(f"\n=== 汇总 ===")
    print(f"  审计题数 {len(rows)}（命中 {len(rows)-len(failed)} / 未命中 {len(failed)}）")
    print(f"  失败题里判「模型选段更好」: {len(b_on_failed)}/{len(failed)} → {b_on_failed}")
    print(f"  命中题里判「模型选段更好」: {len(b_on_hit)}/{len(rows)-len(failed)} → {b_on_hit}（对照组）")
    print(f"  平手: {[r['questionId'] for r in rows if r['verdict']=='平手']}")

    json.dump({"schema": "career-graph-annotation-audit/v1",
               "questions": os.path.relpath(args.questions, ROOT),
               "rerankCache": os.path.relpath(args.rerank_cache, ROOT),
               "judgeModel": os.environ.get("DEEPEVAL_MODEL"),
               "note": "A=标注段，B=模型选段；组序按题号哈希交换以避免位置偏好。本审计只出结论，不改题集。",
               "summary": {"total": len(rows), "failed": len(failed),
                           "bOnFailed": b_on_failed, "bOnHit": b_on_hit},
               "rows": rows},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n产物：{os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
