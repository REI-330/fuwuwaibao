#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
抓取并解析《国家职业技能标准》→ `knowledge/import/raw/osta-standards/`

    python knowledge/pipeline/import/fetch-osta-standards.py            # 全量 702 份
    python knowledge/pipeline/import/fetch-osta-standards.py --limit 5  # 只跑前 5 份，验证用
    python knowledge/pipeline/import/fetch-osta-standards.py --force    # 忽略已解析的，重抓

数据来源：技能人才评价工作网（人力资源和社会保障部）国家职业标准查询系统
    https://www.osta.org.cn/skillStandard
    GET /api/public/skillStandardList?pageSize=100&pageNum=N&total=0&nameCode=&status=1
    GET /api/sys/downloadFile/decrypt?fileName=<standardInfo>      → PDF 全文

产物（**PDF 不留**，只留抽好的文本与表格）：
    raw/osta-standards/records/<id>.json   一份标准的元信息（sha256 / 页数 / 定义 / 知识域 …）
    raw/osta-standards/text/<id>.txt       全文纯文本，带 `[[page N]]` 分页标记
    raw/osta-standards/tables/<id>.jsonl   工作要求表格逐行（已判定所属职业技能等级）
    raw/osta-standards/manifest.json       上面三者的汇总，每次跑完重建
    raw/osta-standards/tables.jsonl        所有表格拼在一起，供 Node 侧一次读完

为什么 PDF 不留：每份约 1.1 MB，702 份 ≈ 750 MB。留文本（约 30 MB）既能逐字核对、又能 grep，
sha256 也记在 records/ 里，需要原件时可以按 standardInfo 重新下载。

**为什么一标准一文件**：这一批要跑半小时，中断是常态。每条标准的三份产物都在它自己跑完时立刻落盘，
续跑时按 `records/<id>.json` 是否存在来判断跳过 —— 不会出现「跑到一半被杀、前面两小时白干」。

**为什么用进程池而不是线程池**：pdfplumber 是纯 Python，表格分析全部受 GIL 限制 ——
实测 4 线程 → 12 线程不但没有加速，反而更慢（每份从 ~15s 掉到 ~60s）。解析必须靠多进程
才能真正并行；下载那点 I/O 顺带也就并行了。默认 8 个进程。

**为什么文本用 pypdf、表格用 pdfplumber**：两者都基于 pdfminer，但 pdfplumber 为了做表格分析，
每页都要建一遍对象树，实测慢一个数量级。文本用 pypdf 抽，只在第 3 章「工作要求」那几页
才叫 pdfplumber 上表格 —— 同样的结果，快数倍。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pdfplumber
import pypdf

BASE = "https://www.osta.org.cn"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": f"{BASE}/skillStandard",
    "Accept": "application/json, text/plain, */*",
}

HERE = Path(__file__).resolve()
# 文件在 <root>/knowledge/pipeline/import/ 下，往上四级才是仓库根
WORKSPACE_ROOT = HERE.parent.parent.parent.parent
OUT_DIR = WORKSPACE_ROOT / "knowledge" / "import" / "raw" / "osta-standards"
TEXT_DIR = OUT_DIR / "text"
TABLE_DIR = OUT_DIR / "tables"
RECORD_DIR = OUT_DIR / "records"

# 职业技能等级：五级=1（最低）… 一级=5（最高）。doc 里写在「3. 1 五级/初级工」这类小标题上。
LEVEL_HEADING_RE = re.compile(r"3\s*\.\s*([1-5])\s+([一二三四五]级[^\n]{0,12})")

CONCURRENCY = 8
RETRY = 3

_print_lock = threading.Lock()


def log(message: str) -> None:
    with _print_lock:
        print(message, flush=True)


def fetch(url: str, binary: bool = False, timeout: int = 90):
    last = None
    for attempt in range(RETRY):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read()
            return raw if binary else json.loads(raw.decode("utf-8"))
        except Exception as error:  # noqa: BLE001 — 网络抖动要重试，具体异常类型不重要
            last = error
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url} 重试 {RETRY} 次仍失败：{last}")


def norm(text: str | None) -> str:
    """去空白 + NFKC 归一（PDF 里竖排括号是 ︵︶，全角数字也时有出现）。编号保留，由调用方剥。"""
    if not text:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def strip_heading_number(text: str) -> str:
    return re.sub(r"^\d+(?:\.\d+)*[.．、]?", "", text).strip()


def level_from_heading(line: str) -> int | None:
    match = LEVEL_HEADING_RE.search(line)
    return int(match.group(1)) if match else None


def split_numbered(text: str) -> list[str]:
    """把「1.1.1能…1.1.2能…」拆成逐条。编号是 3 段（如 1.1.1），据此切分。"""
    if not text:
        return []
    parts = re.split(r"(?=\d+\.\d+\.\d+)", text)
    return [part for part in (strip_heading_number(piece) for piece in parts) if part]


def section_window(pages: list[str], start_re: str, end_re: str) -> tuple[int, int]:
    """第 N 章到下一章之间的页区间 [start, end)。章节号必须锚行首，否则交叉引用会被误判成标题。"""
    start, end = None, len(pages)
    for index, text in enumerate(pages):
        if start is None and re.search(start_re, text, re.M):
            start = index
        elif start is not None and re.search(end_re, text, re.M):
            end = index
            break
    return (start if start is not None else -1), end


def extract_definitions(pages: list[str]) -> dict:
    """第 1 章的几个客观字段。每个都从原文按固定小节号取，取不到就留空，不猜。"""
    joined = "\n".join(pages)
    out: dict[str, str] = {}

    def section(number: str) -> str:
        pattern = re.compile(rf"{re.escape(number)}\s*[^\n]*\n(.*?)(?=\n\s*\d+\s*\.\s*\d|\Z)", re.S)
        match = pattern.search(joined)
        return re.sub(r"\s+", "", match.group(1)) if match else ""

    out["definition"] = section("1. 3")[:300]
    out["levels"] = section("1. 4")[:200]
    out["environment"] = section("1. 5")[:200]
    out["education"] = section("1. 7")[:120]
    return out


def extract_knowledge_domains(pages: list[str]) -> list[str]:
    """第 2.2 节「基础知识」下的小标题 —— 这才是跨职业可复用的知识领域。

    两个坑都实测踩过：
      · 不锚行首：正文里的「1. 2. 2. 1 …」也含 `2. 2. 1`；
      · 不限定章节：第 3 章「工作要求」里的技能要求编号也是 `x.y.z`，`2.2.2` 就是个合法的
        技能条目编号，会被当成知识域收进来（实测出现过「能排查研学设施和2.2.2设施安全隐患的排」）。
    所以先框定 2→3 章之间，再要求行首锚定，最后把以「能」开头的（那是技能要求）排除。
    """
    start, end = section_window(pages, r"^\s*2\s*\.\s*基本要求", r"^\s*3\s*\.\s*工作要求")
    if start < 0:
        return []
    domains: list[str] = []
    pattern = re.compile(r"^\s*2\s*\.\s*2\s*\.\s*(\d+)\s*([^\n]+)", re.M)
    for page in pages[start:end]:
        for match in pattern.finditer(page):
            name = re.sub(r"^\d+(\.\d+)*", "", norm(match.group(2)))
            name = re.split(r"\d+[）)]", name)[0]
            if not name or len(name) > 40 or name.startswith("能"):
                continue
            if name not in domains:
                domains.append(name)
    return domains


def extract_tables(pdf, pages: list[str]) -> list[dict]:
    """第 3 章「工作要求」的四列表格。切段规则见模块顶部注释。"""
    start, end = section_window(pages, r"^\s*3\s*\.\s*工作要求", r"^\s*4\s*\.\s*权重表")
    if start < 0:
        return []

    rows: list[dict] = []
    current_level: int | None = None
    carried_function = ""
    for index in range(start, min(end, len(pdf.pages))):
        # 1) 先看这一页有没有等级小标题，决定本页表格行的等级
        for line in pages[index].split("\n"):
            found = level_from_heading(line)
            if found:
                current_level = found
                carried_function = ""  # 换等级就清掉，避免上一级的职业功能串到下一级
        # 2) 取表（只有这几页才叫 pdfplumber 上表格分析）
        for table in pdf.pages[index].extract_tables():
            for row in table:
                cells = [norm(cell) for cell in row]
                if len(cells) < 4:
                    continue
                joined = "".join(cells)
                if "技能要求" in joined and "相关知识要求" in joined:
                    continue  # 表头
                if joined.startswith("续表") or joined in {"", "合计"}:
                    continue  # 跨页续表标记
                # 「职业功能」是纵向合并单元格：只有合并块的第一行带值，其余为空。
                # 直接留空会让下游拿到一堆没有归类的行，所以在这里向下填充。
                function = strip_heading_number(cells[0])
                if function:
                    carried_function = function
                else:
                    function = carried_function
                work_item = strip_heading_number(cells[1])
                skill_text = cells[2]
                knowledge_text = cells[3]
                if not work_item and not skill_text:
                    continue
                rows.append(
                    {
                        "page": index + 1,
                        "level": current_level,
                        "function": function,
                        "workItem": work_item,
                        "skill": skill_text,
                        "knowledge": knowledge_text,
                        "skillItems": split_numbered(skill_text),
                        "knowledgeItems": split_numbered(knowledge_text),
                    }
                )
    return rows


def process(item: dict) -> dict:
    standard_id = str(item["id"])
    code = str(item["code"]).strip()
    url = f"{BASE}/api/sys/downloadFile/decrypt?fileName={urllib.parse.quote(item['standardInfo'])}"
    raw = fetch(url, binary=True)
    digest = hashlib.sha256(raw).hexdigest()

    # 1) 文本用 pypdf（快）
    reader = pypdf.PdfReader(io.BytesIO(raw))
    pages = [page.extract_text() or "" for page in reader.pages]
    body = "\n".join(f"[[page {i + 1}]]\n{text}" for i, text in enumerate(pages))
    (TEXT_DIR / f"{standard_id}.txt").write_text(body, encoding="utf-8")

    # 2) 表格用 pdfplumber，且只在「工作要求」那几页
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        tables = extract_tables(pdf, pages)

    (TABLE_DIR / f"{standard_id}.jsonl").write_text(
        "".join(json.dumps({**row, "standardId": standard_id, "code": code}, ensure_ascii=False) + "\n" for row in tables),
        encoding="utf-8",
    )

    record = {
        "standardId": standard_id,
        "code": code,
        "name": item["name"],
        "issueTime": item.get("issueTime"),
        "issueNumber": item.get("issueNumber"),
        "standardInfo": item["standardInfo"],
        "standardInfoName": item.get("standardInfoName"),
        "pdfSha256": digest,
        "pdfBytes": len(raw),
        "pages": len(pages),
        "textChars": len(body),
        "tableRows": len(tables),
        "textFile": f"text/{standard_id}.txt",
        **extract_definitions(pages),
        "knowledgeDomains": extract_knowledge_domains(pages),
    }
    (RECORD_DIR / f"{standard_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def run_one(entry: dict) -> dict:
    """进程池的入口。必须是模块级函数 —— 放在 main() 里会因为无法 pickle 而报
    `Can't pickle local object 'main.<locals>.worker'`（实测踩过）。"""
    try:
        return process(entry)
    except Exception as error:  # noqa: BLE001 — 单份失败不能拖垮整批
        return {"error": {"standardId": str(entry["id"]), "code": entry["code"], "message": str(error)[:200]}}


def rebuild_aggregates() -> list[dict]:
    """把一标准一文件的产物拼成 manifest.json 与 tables.jsonl（每次跑完都重建）。"""
    records = [json.loads(path.read_text(encoding="utf-8")) for path in RECORD_DIR.glob("*.json")]
    records.sort(key=lambda row: int(row["standardId"]))

    merged = []
    for record in records:
        path = TABLE_DIR / f"{record['standardId']}.jsonl"
        if path.exists():
            merged.append(path.read_text(encoding="utf-8"))
    (OUT_DIR / "tables.jsonl").write_text("".join(merged), encoding="utf-8")

    (OUT_DIR / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "career-graph-osta-standards/v1",
                "source": "人力资源和社会保障部 技能人才评价工作网 · 国家职业标准查询系统",
                "page": f"{BASE}/skillStandard",
                "note": "PDF 不留，只留 text/<id>.txt 与 tables/<id>.jsonl；原件可按 standardInfo 重新下载，sha256 在 records/ 里可校验。",
                "counts": {
                    "standards": len(records),
                    "tableRows": sum(row["tableRows"] for row in records),
                    "pdfBytes": sum(row["pdfBytes"] for row in records),
                    "textChars": sum(row["textChars"] for row in records),
                },
                "standards": records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 份（验证用）")
    parser.add_argument("--force", action="store_true", help="忽略已解析过的，重抓")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=CONCURRENCY,
        help=f"并发进程数（默认 {CONCURRENCY}）。必须是进程不是线程 —— pdfplumber 受 GIL 限制，"
        "线程池实测会变慢；这里换进程池才能真并行。",
    )
    args = parser.parse_args()

    for directory in (TEXT_DIR, TABLE_DIR, RECORD_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    # 1) 清单：翻页直到最后一页
    items: list[dict] = []
    for page in range(1, 20):
        body = fetch(f"{BASE}/api/public/skillStandardList?pageSize=100&pageNum={page}&total=0&nameCode=&status=1")["body"]
        items.extend(body["list"])
        if page >= body["pages"] or not body["list"]:
            break
    if args.limit:
        items = items[: args.limit]
    log(f"清单：{len(items)} 份标准")

    todo = [item for item in items if args.force or not (RECORD_DIR / f"{item['id']}.json").exists()]
    log(f"已解析 {len(items) - len(todo)} 份，待处理 {len(todo)} 份")

    failures: list[dict] = []
    started = time.time()

    if todo:
        with ProcessPoolExecutor(max_workers=args.concurrency) as pool:
            for index, out in enumerate(pool.map(run_one, todo), start=1):
                if "error" in out:
                    failures.append(out["error"])
                if index % 20 == 0 or index == len(todo):
                    elapsed = time.time() - started
                    log(f"  {index}/{len(todo)}  用时 {elapsed:.0f}s  约 {index / elapsed if elapsed else 0:.2f} 份/秒  失败 {len(failures)}")

    records = rebuild_aggregates()
    print(
        f"\n完成：{len(records)} 份标准 / {sum(row['tableRows'] for row in records)} 行工作要求表格"
        f"\n文本 {sum(row['textChars'] for row in records) / 1024 / 1024:.1f} MB"
        f"，PDF 原始 {sum(row['pdfBytes'] for row in records) / 1024 / 1024:.0f} MB（未留存）"
    )
    if failures:
        print(f"失败 {len(failures)} 份：")
        for row in failures[:10]:
            print(f"  {row['code']} {row['standardId']} {row['message']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
