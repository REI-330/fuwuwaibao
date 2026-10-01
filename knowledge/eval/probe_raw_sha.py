#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""排查 raw 快照 sha256 与登记值对不上的成因（只读，不改任何文件）。

## 背景

`knowledge/README.md` 与 e2e 阶段 A 都记着同一件事：**25 份快照里只有 20 份逐字节复现**，
对不上的是 S15 / S18 / S19 / S20 / S22，字节差分别是 -230 / -1085 / -10 / -2400 / -1。
当时的结论是「成因未定」。这个脚本把成因查清楚。

## 判据（逐条可复算）

1. 行尾假说：把工作区文件里所有 `\n` 换成 `\r\n`，若 sha256 命中登记值 → 成因就是**行尾转换**；
   且此时「字节差」应当恰好等于 `\r\n` 的个数（每转一行少一个字节）。
2. 历史假说：遍历 `git log --all` 里这些文件的历史 blob，若有某个 blob 的 sha256 命中登记值，
   说明「登记时算的是那份字节，之后被某次提交改掉了」—— 直接定位到提交。

用法：

    python knowledge/eval/probe_raw_sha.py --manifest knowledge/raw/manifest.json --root knowledge/raw
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lf_to_crlf(data: bytes) -> bytes:
    """把裸 `\n` 换成 `\r\n`；已经是 `\r\n` 的不重复加 `\r`。"""
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", default="knowledge/raw/manifest.json")
    parser.add_argument("--root", default="knowledge/raw")
    parser.add_argument("--search-root", default="knowledge", help="在哪棵树里找「登记字节」的其他副本")
    parser.add_argument("--out", help="把取证结果写成 JSON（便于随证据一起留痕）")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    root = Path(args.root)
    report: Dict[str, object] = {"mismatched": {}, "matchedCount": 0, "total": 0}
    lines: List[str] = []

    for document in manifest["documents"]:
        source_id = document["sourceId"]
        snapshot = root / f"{source_id}.html"
        report["total"] += 1
        if not snapshot.is_file():
            report["mismatched"][source_id] = {"reason": "MISSING"}
            continue
        raw = snapshot.read_bytes()
        if sha256(raw) == document["sha256"]:
            report["matchedCount"] += 1
            continue

        crlf = raw.count(b"\r\n")
        lf = raw.count(b"\n") - crlf
        converted = lf_to_crlf(raw)
        entry = {
            "registeredBytes": document.get("bytes"),
            "registeredSha256": document["sha256"],
            "actualBytes": len(raw),
            "byteDelta": len(raw) - int(document.get("bytes") or 0),
            "bareLf": lf,
            "crlf": crlf,
            "crlfConvertedMatches": sha256(converted) == document["sha256"],
            "historyMatches": [],
        }

        # 历史 blob：找到哪一次提交的字节与登记值一致
        relative = f"{args.root}/{source_id}.html"
        commits = subprocess.run(
            ["git", "log", "--all", "--format=%H", "--", relative],
            capture_output=True, text=True,
        ).stdout.split()
        for commit in commits:
            blob = subprocess.run(["git", "show", f"{commit}:{relative}"], capture_output=True).stdout
            if sha256(blob) == document["sha256"]:
                entry["historyMatches"].append({"commit": commit[:12], "bytes": len(blob)})
        entry["historyCount"] = len(commits)
        report["mismatched"][source_id] = entry
        lines.append(
            f"{source_id}: 字节差 {entry['byteDelta']:+d}，裸 LF {lf}，CRLF 行 {crlf}，"
            f"行尾假说 {'成立' if entry['crlfConvertedMatches'] else '不成立'}，"
            f"历史里命中登记的提交 {entry['historyMatches'] or '无'}"
        )

    # 全仓搜索：有没有别的文件正好是「登记的那份字节」——有的话就是最硬的证据
    wanted = {entry["registeredSha256"] for entry in report["mismatched"].values() if isinstance(entry, dict)}
    copies: Dict[str, List[str]] = {}
    if wanted and args.search_root:
        for candidate in Path(args.search_root).rglob("*"):
            if not candidate.is_file():
                continue
            try:
                if sha256(candidate.read_bytes()) in wanted:
                    copies.setdefault(candidate.name, []).append(str(candidate))
            except OSError:
                continue
    report["copiesOfRegisteredBytes"] = copies

    report["lines"] = lines
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "lines"}, ensure_ascii=False, indent=2))
    for line in lines:
        print("  -", line)
    print("  登记字节的其他副本:", json.dumps(report["copiesOfRegisteredBytes"], ensure_ascii=False) if report["copiesOfRegisteredBytes"] else "无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
