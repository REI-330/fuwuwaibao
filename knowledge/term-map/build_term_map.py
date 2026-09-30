#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
建「语料内双语术语词典」：给中文查询补上**精确的英文术语**，让 BM25 能字面命中英文段。

动机（实测）：跨语言题（中文问 / 英文段）的瓶颈是**术语对不上**，不是召回不到。
T20/T21 在 BM25 里完全不出现，但把官方英文职业名与 O*NET 软件类别名原样写进查询后
直接排到**第 1 名**；而向量通道对它俩压根够不着（171/1757、33/59）。
详见 `knowledge/evaluations/查询扩展与跨语言缺口-20260927.md`。

做法分两步，**第一步完全无 LLM**：

1. 提取（确定性）：从语料里把英文术语面抽出来
   - `occupation` 职业名：英文的 `sectionPath` 顶层段（如 `Software Quality Assurance Analysts and Testers`）
   - `section` 小节名：`sectionPath` 第二层段（Tasks / Work Activities / Software Skills …）
   - `software_category` 软件类别：正文里 `X software —` 的 X（如 `Operating system software`）
2. 翻译（一次性离线 LLM）：每个英文术语给 1 个中文标准译名 + 若干中文短词
   （短词是因为提问用的是短词：语料叫 `Data base user interface and query software`，
   人问的是「数据库软件」）。**翻译过程只看语料术语，不看题集**，所以不构成对题集的拟合。

检索时（确定性、零 per-query LLM）：词典里任一中文字面出现在查询里 → 把对应英文术语
追加进 BM25 查询。

用法：
  export TEI_MODEL=Qwen3-Embedding-0.6B-onnx-int8
  python knowledge/term-map/build_term_map.py --extract          # 只提清单，不调 LLM
  python knowledge/term-map/build_term_map.py                    # 提取 + 翻译，写出词典
  python knowledge/term-map/build_term_map.py --reuse-translations   # 翻译结果已有，不重调
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K = os.path.join(ROOT, "knowledge")
HERE = os.path.join(K, "term-map")
CHUNKS = os.path.join(K, "chunks", "chunks.jsonl")
ENV_FILE = os.path.join(K, "eval", ".env")
INVENTORY = os.path.join(HERE, "inventory.json")
TERM_MAP = os.path.join(HERE, "zh-en.json")

# 与评测脚本共用同一份 LLM 实现（本脚本不在 knowledge/eval 下，所以显式补一次 sys.path）
sys.path.insert(0, os.path.join(K, "eval"))
from _shared_llm import chat  # noqa: E402 —— 必须在 sys.path 调整之后导入

# 软件类别标签：正文里写成 `Operating system software — ...`。标签一定以 software 结尾。
SOFTWARE_LABEL = re.compile(r"([A-Z][A-Za-z0-9 ,\-/&.()]{2,70}?\bsoftware)\s*[—\-]")
HAN = re.compile(r"[\u4e00-\u9fff]")

TRANSLATE_PROMPT = """你在为中文检索系统建术语表。下面是英文职业/软件类别术语，
请给每条术语配**中文职业与技术文献里的标准译名**，再给 1-3 个中文用户实际会输入的短词。

规则：
- `zh` 是标准译名，尽量用国标/职业分类大典里的说法；
- `zh_short` 是短词，只保留核心名词（如「数据库软件」「操作系统软件」），不要带"和/与"；
- 不要编造不存在的术语，拿不准就用直译；
- 只输出 JSON，形如 {{"items":[{{"key":"<原样照抄的英文术语>","zh":"…","zh_short":["…"]}}]}}，
  不要解释、不要代码块围栏。

术语（每行一条）：
{terms}
"""


def load_env():
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def llm(prompt, timeout=180, attempts=4):
    """统一走 `_shared_llm`（原实现自己拼 HTTP：失败静默退化成空结果，正是要避免的）。"""
    return chat(prompt, max_tokens=4000, timeout=timeout, attempts=attempts)


def parse_json_block(text):
    """模型可能裹 ```json 围栏或前后带话，只取最外层 JSON。"""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("响应里没有 JSON 对象")
    return json.loads(s[i:j + 1])


def extract_inventory():
    chunks = [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]
    occ, sec, soft = {}, {}, {}
    for c in chunks:
        parts = [x.strip() for x in (c.get("sectionPath") or "").split(">") if x.strip()]
        if parts and not HAN.search(parts[0]):
            # 顶层是纯英文 → 这条来源的职业名（语料里只有 O*NET 的两个职业如此）
            occ.setdefault(parts[0], set()).add(c["sourceId"])
            if len(parts) > 1 and not HAN.search(parts[1]):
                sec.setdefault(parts[1], set()).add(c["sourceId"])
        text = c.get("text") or ""
        for m in SOFTWARE_LABEL.finditer(text):
            soft.setdefault(m.group(1).strip(), set()).add(c["sourceId"])
    items = []
    for kind, table in (("occupation", occ), ("section", sec), ("software_category", soft)):
        for term, srcs in table.items():
            items.append({"en": term, "kind": kind, "sources": sorted(srcs)})
    items.sort(key=lambda x: (x["kind"], x["en"]))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", action="store_true", help="只提取英文术语清单，不调 LLM")
    ap.add_argument("--reuse-translations", action="store_true", help="复用已有翻译，不调 LLM")
    ap.add_argument("--batch", type=int, default=30, help="每次请求塞多少条术语")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条（调试用）")
    args = ap.parse_args()
    load_env()
    os.makedirs(HERE, exist_ok=True)

    items = extract_inventory()
    if args.limit:
        items = items[:args.limit]
    by_kind = {}
    for it in items:
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
    print(f"术语清单：{len(items)} 条  " + "  ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
    json.dump({"schema": "career-graph-term-inventory/v1",
               "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "corpus": os.path.relpath(CHUNKS, ROOT), "items": items},
              open(INVENTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"→ {os.path.relpath(INVENTORY, ROOT)}")
    if args.extract:
        return 0

    # 翻译：先用已有词典跳过已翻过的，省调用
    done = {}
    if os.path.exists(TERM_MAP):
        for e in json.load(open(TERM_MAP, encoding="utf-8")).get("terms", []):
            done[e["en"]] = e
    if args.reuse_translations:
        print(f"复用已有翻译 {len(done)} 条")
        todo = []
    else:
        todo = [it for it in items if it["en"] not in done]
        print(f"已有翻译 {len(done)} 条，待翻译 {len(todo)} 条")

    for start in range(0, len(todo), args.batch):
        batch = todo[start:start + args.batch]
        prompt = TRANSLATE_PROMPT.format(terms="\n".join(it["en"] for it in batch))
        try:
            got = parse_json_block(llm(prompt))
        except Exception as e:  # noqa: BLE001
            print(f"  批 {start//args.batch + 1} 失败 {type(e).__name__}: {e}", file=sys.stderr)
            continue
        zh = {}
        for entry in got.get("items", []):
            key = (entry.get("key") or "").strip()
            if key in {it["en"] for it in batch}:
                zh[key] = entry
        missed = [it["en"] for it in batch if it["en"] not in zh]
        if missed:
            print(f"  批 {start//args.batch + 1}：模型漏了 {len(missed)} 条 {missed[:3]}", file=sys.stderr)
        for it in batch:
            e = zh.get(it["en"])
            if e:
                done[it["en"]] = {"en": it["en"], "kind": it["kind"], "sources": it["sources"],
                                  "zh": (e.get("zh") or "").strip(),
                                  "zh_short": [x.strip() for x in (e.get("zh_short") or []) if x and x.strip()]}
        print(f"  完成 {min(start+args.batch, len(todo))}/{len(todo)}")

    terms = [done[it["en"]] for it in items if it["en"] in done]
    unmapped = [it["en"] for it in items if it["en"] not in done]
    json.dump({"schema": "career-graph-term-map/v1",
               "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "model": os.environ.get("DEEPEVAL_MODEL"),
               "note": "英文术语源自语料（inventory.json）；中文译名由 LLM 一次性离线生成，只看术语不看题集。",
               "terms": terms, "unmapped": unmapped},
              open(TERM_MAP, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    empty = [t["en"] for t in terms if not t["zh"]]
    print(f"→ {os.path.relpath(TERM_MAP, ROOT)}：{len(terms)} 条"
          f"（译名为空 {len(empty)}，未翻到 {len(unmapped)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
