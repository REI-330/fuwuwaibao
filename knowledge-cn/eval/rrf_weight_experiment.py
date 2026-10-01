#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫 RRF 融合权重，看混合检索能不能追上/超过「纯关键词」那条线。

## 为什么值得扫

两版评测都出现同一个现象：**纯关键词通道 @1 高于混合**（本题集偏字面事实，专业代码、
表格数值、职业编码都是词面匹配），而混合里向量占 0.7 权重、关键词只占 0.3
（`internal/types/retrieval_config.go` 的默认值）。既然关键词更强，那 0.3 的权重
很可能把精度拉低了。

权重是**租户级配置**（`tenants.retrieval_config`，JSON 字段 `rrf_vector_weight` /
`rrf_keyword_weight`），改它不需要重建索引、不需要重启服务，所以可以逐档扫、每档几秒钟
就出数——比拿重建去试参数便宜得多。

## 做法

用 `PUT /api/v1/tenants/kv/retrieval-config`（需要 Admin）逐档改写，每档跑一遍**混合**检索
（纯向量/纯关键词不受权重影响，不用重复跑），记录 @1/@3/@5/@10 与 MRR；
`--keep` 指定的档位如果最好就保留，否则扫完**恢复成扫描前的配置**。

用法：

    python knowledge-cn/eval/rrf_weight_experiment.py \
        --kb <kb-id> --questions knowledge-cn/evaluations/questions-cn-v1.json \
        --weights 0.7:0.3,0.5:0.5,0.3:0.7,0.2:0.8,0.1:0.9 \
        --out knowledge-cn/evidence/rrf-weight-sweep.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retrieval_quality as rq  # noqa: E402
from weknora_client import WeKnora, _request  # noqa: E402

KV_PATH = "/api/v1/tenants/kv/retrieval-config"


def get_config(wk):
    status, body = _request("GET", wk.base_url + KV_PATH, token=wk.token)
    if status != 200:
        raise SystemExit(f"读 retrieval-config 失败 HTTP {status}: {str(body)[:200]}")
    return body.get("data") or {}


def put_config(wk, config):
    status, body = _request("PUT", wk.base_url + KV_PATH, config, token=wk.token)
    return status, body


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kb", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--weights", default="0.7:0.3,0.5:0.5,0.3:0.7,0.2:0.8,0.1:0.9",
                        help="逗号分隔的 向量权重:关键词权重")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--keep-best", action="store_true",
                        help="把最好的一档留在配置里（否则扫完恢复原配置）")
    parser.add_argument("--out")
    args = parser.parse_args()

    document = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    pairs = []
    for item in args.weights.split(","):
        vector, keyword = item.split(":")
        pairs.append((float(vector), float(keyword)))

    wk = WeKnora()
    original = get_config(wk)
    print("扫描前配置:", json.dumps(original, ensure_ascii=False))

    variants = []
    for vector, keyword in pairs:
        config = dict(original)
        config["rrf_vector_weight"] = vector
        config["rrf_keyword_weight"] = keyword
        status, body = put_config(wk, config)
        if status != 200:
            print(f"写入失败 HTTP {status}: {str(body)[:160]}")
            break
        per_question = [rq.evaluate_question(q, wk.search(args.kb, q["question"], top=args.top))
                        for q in document["questions"]]
        summary = rq.summarise(per_question, args.top)
        ranks = [row["evidenceRankSameChunk"] or (args.top + 1)
                 for row in per_question if row["answerable"]]
        entry = {"vectorWeight": vector, "keywordWeight": keyword,
                 "summary": summary, "meanRank": round(statistics.mean(ranks), 2),
                 "perQuestion": per_question}
        variants.append(entry)
        same = summary["SameChunk"]
        print(f"向量 {vector} / 关键词 {keyword}: @1 {same['hit@1']['hits']}/{same['hit@1']['total']}"
              f" @3 {same['hit@3']['hits']} @5 {same['hit@5']['hits']}"
              f" @{args.top} {same[f'hit@{args.top}']['hits']} MRR {same['mrr']} 均秩 {entry['meanRank']}")

    best = None
    if variants:
        best = max(variants, key=lambda v: (v["summary"]["SameChunk"]["mrr"], -v["meanRank"]))
        print(f"\n最好的一档：向量 {best['vectorWeight']} / 关键词 {best['keywordWeight']}"
              f"（MRR {best['summary']['SameChunk']['mrr']}）")
        if args.keep_best:
            config = dict(original)
            config["rrf_vector_weight"] = best["vectorWeight"]
            config["rrf_keyword_weight"] = best["keywordWeight"]
            status, _ = put_config(wk, config)
            print(f"已保留该档（HTTP {status}）")
        else:
            status, _ = put_config(wk, original)
            print(f"已恢复原配置（HTTP {status}）")

    report = {"kb": args.kb, "questionsFile": args.questions, "top": args.top,
              "configBefore": original, "keptBest": args.keep_best,
              "best": {"vectorWeight": best["vectorWeight"], "keywordWeight": best["keywordWeight"]}
              if best else None,
              "variants": variants}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
