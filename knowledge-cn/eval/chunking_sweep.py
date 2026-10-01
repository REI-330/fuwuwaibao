#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对**单个文档**扫切块参数，跑检索评测，给出对照表。

## 为什么要按文档扫、而且要在临时库里扫

切块参数的合理值取决于正文形态：结构化目录要「一条记录一块」（200 字就够），
表格型文档可能希望「一张表一块」，一页一条长正文又是另一种。整库重建一次要十几分钟
（职业大典 1665 个 chunk 的嵌入占大头），不可能每换一档就重来一遍。

所以这里把被测文档导入一个**临时知识库**（同名会自动复用/创建），每个档位用
`--title <名字>-cs<块长>` 区分、**全程不做删除**：删除是异步的，等它落地可能要好几分钟，
是上一版扫描脚本卡死的直接原因。扫完只保留对照表，临时库可以 `--delete-scratch` 清掉。

## 判定

复用 `retrieval_quality.py` 的同一套口径（锚点 gold、块内/窗口内、多组题要求证据齐），
但**只评 gold 出自被测来源的那几道题**（`--source`）——临时库里只有这一份文档，
别的题的池子不对，评了也没意义。

注意口径边界：临时库里的名次是「同一份文档内部」的名次，没有跨文档竞争。
切块粒度主要影响的就是文档内部的区分度，所以这个口径适合选参数；
选定后的绝对值要用正式库复跑确认。

用法：

    python knowledge-cn/eval/chunking_sweep.py \
        --file nbs-wages-2025.jsonl --source nbs-wages-2025 \
        --sizes 512,768,1024,1600 \
        --questions knowledge-cn/evaluations/questions-cn-v1.json \
        --out knowledge-cn/evidence/chunking-sweep-nbs.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import retrieval_quality as rq  # noqa: E402
from weknora_client import WeKnora  # noqa: E402

IMPORT_SCRIPT = ROOT / "knowledge-cn" / "acquisition" / "import_weknora.py"
REVIEWED = ROOT / "knowledge-cn" / "data" / "reviewed"
SCRATCH_NAME = "切片参数对照（临时）"


def run_import(kb_id, input_name, title, chunk_size, strategy, timeout=1800):
    command = [sys.executable, str(IMPORT_SCRIPT), "--input", str(REVIEWED / input_name),
               "--kb-id", kb_id, "--title", title]
    if chunk_size:
        command += ["--chunk-size", str(chunk_size)]
    if strategy:
        command += ["--strategy", strategy]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            timeout=timeout)
    tail = (result.stdout or "").strip().splitlines()
    return result.returncode, (tail[-1] if tail else (result.stderr or "")[-300:])


def wait_completed(wk, kb_id, name, timeout=1800):
    deadline = time.time() + timeout
    statuses = {}
    while time.time() < deadline:
        statuses = {row.get("file_name"): row.get("parse_status")
                    for row in wk.list_knowledge(kb_id)}
        if statuses.get(name) == "completed":
            return statuses
        time.sleep(5)
    return statuses


def chunk_ids_of(wk, kb_id, name):
    for row in wk.list_knowledge(kb_id):
        if row.get("file_name") == name or row.get("title") == name:
            return row.get("id"), len(wk.list_chunks(row.get("id")))
    return None, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, help="knowledge-cn/data/reviewed 下的 jsonl 文件名")
    parser.add_argument("--source", required=True, help="只评 gold 出自这个来源的题（question.gold[].source）")
    parser.add_argument("--sizes", default="512,768,1024,1600")
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--embedding-model", default="builtin-embedding-qwen3")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--modes", default="hybrid,vectorOnly,keywordOnly")
    parser.add_argument("--delete-scratch", action="store_true", help="跑完删掉临时库")
    parser.add_argument("--out")
    args = parser.parse_args()

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    document = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    subset = [q for q in document["questions"]
              if any(g.get("source") == args.source for g in (q.get("gold") or []))]
    if not subset:
        raise SystemExit(f"题集里没有 gold 出自 {args.source} 的题")

    wk = WeKnora()
    stem = Path(args.file).stem
    print(f"每题档位一个独立临时库（避免不同块长的同一份文档互相竞争），"
          f"本档评 {len(subset)} 题（gold 出自 {args.source}）")

    variants = []
    for size in sizes:
        title = f"{stem}-cs{size}" + (f"-{args.strategy}" if args.strategy else "")
        name = f"{title}.md"
        # 每个档位一个独立库：同一份文档的不同切块版本放进同一个库会互相竞争
        # （同一个 gold 同时被 3 个不同块长的副本命中），名次就没有可比性了。
        scratch = wk.ensure_knowledge_base(f"{SCRATCH_NAME}-cs{size}", args.embedding_model,
                                           "切块参数对照用临时库，可随时删除")
        statuses = {row.get("file_name"): row.get("parse_status")
                    for row in wk.list_knowledge(scratch)}
        reused = statuses.get(name) == "completed"
        if reused:
            code, tail = 0, "复用已存在的同名档（未重新导入）"
        else:
            code, tail = run_import(scratch, args.file, title, size, args.strategy)
        if code != 0:
            variants.append({"chunkSize": size, "exitCode": code, "importResult": tail})
            print(f"[{size}] 导入失败：{tail}")
            continue
        if not reused:
            statuses = wait_completed(wk, scratch, name)
        knowledge_id, chunk_count = chunk_ids_of(wk, scratch, name)
        measured = {}
        for mode in modes:
            per_question = [rq.evaluate_question(q, wk.search(scratch, q["question"],
                                                              top=args.top, **rq.MODES[mode]))
                            for q in subset]
            measured[mode] = {"summary": rq.summarise(per_question, args.top),
                              "perQuestion": per_question}
        entry = {"chunkSize": size, "title": title, "knowledgeId": knowledge_id,
                 "scratchKbId": scratch,
                 "chunksInDocument": chunk_count, "parseStatus": statuses.get(f"{title}.md"),
                 "reusedExisting": reused,
                 "exitCode": code, "importResult": tail, "modes": measured}
        variants.append(entry)
        hybrid = measured["hybrid"]["summary"]
        print(f"[{size:5}] chunk={chunk_count:5} {hybrid['SameChunk'].get('hit@1', {}).get('hits')}"
              f"/{hybrid['SameChunk'].get('hit@1', {}).get('total')} @1，"
              f"@10 {hybrid['SameChunk'].get(f'hit@{args.top}', {}).get('hits')}，"
              f"MRR {hybrid['SameChunk']['mrr']}")

    ok = [v for v in variants if v.get("modes")]
    report = {"file": args.file, "source": args.source, "questionsEvaluated": len(subset),
              "isolatedKbPerVariant": True, "top": args.top, "variants": variants}
    if ok:
        def mean_rank(variant):
            ranks = [row["evidenceRankSameChunk"] or (args.top + 1)
                     for row in variant["modes"]["hybrid"]["perQuestion"] if row["answerable"]]
            return round(statistics.mean(ranks), 2)
        for variant in ok:
            variant["hybridMeanRank"] = mean_rank(variant)
        best = min(ok, key=lambda v: (v["hybridMeanRank"],
                                      -v["modes"]["hybrid"]["summary"]["SameChunk"].get(
                                          f"hit@{args.top}", {}).get("hits", 0)))
        report["best"] = {"chunkSize": best["chunkSize"], "hybridMeanRank": best["hybridMeanRank"]}
        print("\n各档混合通道（只算这 %d 题，未命中按 %d 计）：" % (len(subset), args.top + 1))
        for variant in ok:
            print(f"  块长 {variant['chunkSize']:5} chunk={variant['chunksInDocument']:5}"
                  f" 均秩 {variant['hybridMeanRank']}")
        print(f"\n本档最好：{best['chunkSize']}")

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.delete_scratch:
        for variant in variants:
            kb_id = variant.get("scratchKbId")
            if kb_id:
                status, _ = wk.delete_knowledge_base(kb_id)
                print(f"已删除临时库 {kb_id}（HTTP {status}）")
    else:
        print("临时库保留（要清理加 --delete-scratch）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
