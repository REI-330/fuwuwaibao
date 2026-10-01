#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按固定口径把 knowledge-cn 的已审核语料重建进 WeKnora（可复现，别人也能跑）。

## 为什么要一个「重建」脚本

首版导入是逐文件手工敲命令的，结果 `nbs-wages-2025.md` 被导入了两次
（36+36 个 chunk、content_hash 序列一致），850 条结构化记录也被默认 512 字切块
塞成了 3 条/chunk。手工流程里没有「这些文件各自该用什么切块参数」的唯一出处，
所以每次重跑都可能再犯。

这里把**每个文件该用的切块参数**写成一张表，重建就是照表执行：

| 文件 | 切块 | 依据 |
|---|---|---|
| `moe-majors-2026-structured.jsonl` | `strategy=heading`、`chunk_size=200` | 一条记录一个 chunk（`/chunker/preview` 实测 393/393） |
| `nbs-wages-2025.jsonl` | `chunk_size=2400` | 一张表一个 chunk（`eval/chunking_sweep.py` 扫 512…3200 六档，2400 最好） |
| 其余 3 个 | 默认（512 字） | 正文是页级长文本，512 字能装下 1 个完整段落/定义 |

脚本对每个文件都走 `import_weknora.py --replace`（先删同名再传），因此**可重复执行**：
重跑不会堆重复文档。跑完还会轮询到所有文档 `parse_status=completed` 才退出。

用法：

    python knowledge-cn/acquisition/rebuild_weknora.py \
        --kb-id aacc4889-a347-44ea-9dd1-b60ec1917cbb

跑完接着跑核验与评测（README 的「已验收」两节就是这两条命令的产物）：

    python knowledge-cn/verify_import.py --db knowledge-v1/weknora-src/data/weknora-cn.db --kb <kb-id>
    python knowledge-cn/eval/retrieval_quality.py --kb <kb-id> \
        --questions knowledge-cn/evaluations/questions-cn-v1.json --top 10 --out <证据路径>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
REVIEWED = ROOT / "knowledge-cn" / "data" / "reviewed"
IMPORT_SCRIPT = ROOT / "knowledge-cn" / "acquisition" / "import_weknora.py"

# 一个文件的切块参数只有这一处出处；改口径请同时更新模块 docstring 与 README。
# 依据见 knowledge-cn/evaluations/检索质量评测-20261001.md 的「切块参数对照」一节：
#   结构化目录 200（一块一条记录，/chunker/preview 实测 393/393）
#   工资统计   2400（一张表一块；扫描 512/768/1024/1600/2400/3200 六档，2400 最好）
FILES = [
    {"input": "moe-majors-2026-structured.jsonl", "strategy": "heading", "chunk_size": 200},
    {"input": "moe-majors-2026.jsonl", "strategy": None, "chunk_size": None},
    {"input": "nbs-wages-2025.jsonl", "strategy": None, "chunk_size": 2400},
    {"input": "occupation-2022.jsonl", "strategy": None, "chunk_size": None},
    {"input": "occupation-2022-replacement.jsonl", "strategy": None, "chunk_size": None},
]


def run_import(kb_id, item, timeout=900):
    command = [sys.executable, str(IMPORT_SCRIPT),
               "--input", str(REVIEWED / item["input"]), "--kb-id", kb_id, "--replace"]
    if item["strategy"]:
        command += ["--strategy", item["strategy"]]
    if item["chunk_size"]:
        command += ["--chunk-size", str(item["chunk_size"])]
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", timeout=timeout)
    tail = (result.stdout or "").strip().splitlines()
    return result.returncode, (tail[-1] if tail else (result.stderr or "")[-300:])


def wait_all_completed(kb_id, timeout=1800):
    """轮询到所有文档都不是 pending/processing；返回最终状态表。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_weknora import resolve_token  # 复用门禁脚本的登录（同账号来源）

    import urllib.request

    base = "http://127.0.0.1:8080"
    token, _ = resolve_token(base, None)
    deadline = time.time() + timeout
    statuses = {}
    while time.time() < deadline:
        request = urllib.request.Request(
            f"{base}/api/v1/knowledge-bases/{kb_id}/knowledge?page_size=200")
        request.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
        rows = body.get("data") or []
        if isinstance(rows, dict):
            rows = rows.get("items") or []
        statuses = {row.get("file_name"): row.get("parse_status") for row in rows}
        if statuses and all(status == "completed" for status in statuses.values()):
            return statuses
        time.sleep(5)
    return statuses


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--skip-import", action="store_true", help="只做状态轮询")
    args = parser.parse_args()

    summary = {"kb_id": args.kb_id, "files": []}
    if not args.skip_import:
        for item in FILES:
            code, tail = run_import(args.kb_id, item)
            print(f"[{code}] {item['input']}: {tail}")
            summary["files"].append({"input": item["input"], "config": item,
                                     "exitCode": code, "result": tail})
            if code != 0:
                summary["statuses"] = wait_all_completed(args.kb_id)
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                raise SystemExit(1)
    summary["statuses"] = wait_all_completed(args.kb_id)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not all(status == "completed" for status in summary["statuses"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
