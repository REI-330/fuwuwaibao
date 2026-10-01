"""把已审核（含业主豁免）候选导入 WeKnora。

## 为什么走「上传 .md 文件」而不是 `knowledge/manual`

`docparser.NewReader` 的判据是：
``engine == "" and not isURL and IsSimpleFormat(fileType)`` → Go 原生 ``SimpleFormatReader``，
否则路由到 Python **docreader** 服务。而 ``simpleFormats`` 只有
``md / markdown / txt / text / csv / json`` —— **``manual`` 不在其中**。

所以「手工录入」建的 knowledge（``file_type=manual``）一定会走 docreader；
本机 docreader 没跑（50051 无监听），结果就是解析永远停在 processing。
上传真实 ``.md`` 文件则命中 ``md`` → Go 原生解析，**不需要 docreader**。
（2026-10-01 实测：单条 .md 上传 → parse_status 从 pending → processing → completed，
enable_status 变 enabled，hybrid-search 能召回。）

## 两类候选，渲染方式不同

- **带 content 的**（moe-majors-2026 / occupation-2022 / occupation-2022-replacement /
  nbs-wages-2025）：正文就是 ``content``，标题用 ``title``。
- **只有结构化字段的**（``moe-majors-2026-structured`` 850 条）：这些记录**没有**
  ``content``/``title``，只有 ``major_code`` / ``major_name`` / ``page``。
  历史实现直接取 ``record['content']`` 会拿到 ``None`` —— 也就是说**这 850 条从来没有可导入的内容**。
  这里按记录自身的字段渲染（专业代码 + 专业名称 + 出处页码），**不新增任何记录里没有的信息**。

## 一条记录一个 chunk（2026-10-01 改造）

首版导入用默认切块（512 字），850 条结构化记录被塞进 **287 个 chunk（3 条/chunk）**，
后果是「答案在 chunk 中部、预览只看得见邻座记录」（排查报告
``knowledge-cn/evaluations/召回排查-低空经济与管理-20261001.md``），
且出处样板占了 chunk 的 58%，把向量和 BM25 的区分度都稀释掉了。

改造两件事，都用接口实测确认过（``POST /api/v1/chunker/preview``，只读、不落库）：

1. **渲染**：结构化记录的小标题从 ``## <记录 ID>`` 换成 ``## <专业代码> <专业名称>``，
   正文改为逐行字段；出处行精简为 ``_出处：… · 记录 ID：… · 类型：… · 审核状态：…_``。
   记录头本身就是答案，前 120 字的预览不再指向别人。
2. **切块参数**：按文件给 ``process_config.chunking_config`` 覆盖。
   结构化目录用 ``strategy=heading`` + ``chunk_size=200`` —— 预览实测 30 条样本
   切成 30 块、**一块一条记录**（默认参数下是 3 条/块）。

   ```bash
   python -m acquisition.import_weknora --input data/reviewed/moe-majors-2026-structured.jsonl \
       --kb-id <kb-id> --strategy heading --chunk-size 200 --replace
   ```

## 幂等

首次导入是「每次都新建一条 knowledge」，于是 ``nbs-wages-2025.md`` 被导入了两次
（36+36 个 chunk、content_hash 序列完全一致）。现在默认**同名文档已存在就拒绝导入**，
要覆盖必须显式 ``--replace``（会先删掉同名文档再传）。这样重跑不会静默堆重复。

## 出处随文入库

每段渲染都带上来源 id / record_kind / 记录 id，并标注这条是**业主豁免闸门**
（``WAIVED``，不是人工审核通过）。检索召回的内容因此自带出处，不需要外部再查表。
"""

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_BASE = "http://127.0.0.1:8080"
POLL_SECONDS = 120


def api(method, url, token=None, payload=None, timeout=60):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read() or b"{}")


def login(base, email, password):
    """真实登录换 token。

    先试注册（首次跑），已存在则忽略 409/400，再登录 —— 这样脚本可反复跑。
    """
    try:
        api("POST", f"{base}/api/v1/auth/register",
            payload={"email": email, "password": password, "username": email.split("@")[0]})
    except urllib.error.HTTPError:
        pass  # 已存在
    _, body = api("POST", f"{base}/api/v1/auth/login", payload={"email": email, "password": password})
    token = body.get("token")
    if not token:
        raise SystemExit(f"login did not return a token: {str(body)[:200]}")
    return token


def render_markdown(records, title):
    """把一批已审核记录渲染成一个 .md。每段都带出处。

    结构化记录（只有 major_code/major_name/page）的**小标题就是「代码 + 名称」**，
    这样一方块记录的头两个字就是答案本身；出处行保持精简，避免样板字数
    把 200 字的切块预算吃掉。
    """
    lines = [f"# {title}", "", "> 本文件由中国官方语料候选渲染而来，逐条标注出处与审核状态。", ""]
    for record in records:
        source = record.get("source") or {}
        source_id = source.get("id", "?")
        kind = record.get("record_kind", "?")
        waiver = record.get("review_waiver") or {}
        # 审核状态如实写：WAIVED 是「业主豁免」，不许写成「已审核通过」
        status = record.get("review_status", "")
        status_zh = {"WAIVED": "业主豁免闸门（非人工审核通过）", "APPROVED": "人工审核通过"}.get(status, status)
        major = " ".join(part for part in (record.get("major_code"), record.get("major_name")) if part)
        if major:
            lines.append(f"## {major}")
        elif record.get("title"):
            lines.append(f"## {record['title']}")
        else:
            lines.append(f"## {record.get('id', '')}")
        lines.append("")
        if record.get("content"):
            lines.append(str(record["content"]).strip())
        else:
            # 结构化记录：只用它自己的字段，不补充任何额外信息
            if record.get("major_code"):
                lines.append(f"- 专业代码：{record['major_code']}")
            if record.get("major_name"):
                lines.append(f"- 专业名称：{record['major_name']}")
            if record.get("page"):
                lines.append(f"- 来源页码：第 {record['page']} 页")
            if not any(record.get(key) for key in ("major_code", "major_name", "page")):
                lines.append("（该记录没有可渲染的字段）")
        lines.append("")
        meta = [f"出处：{source_id}", f"类型：{kind}", f"记录 ID：{record.get('id', '')}",
                f"审核状态：{status_zh}"]
        if waiver.get("by"):
            meta.append(f"豁免人：{waiver['by']}")
        lines.append("_" + " · ".join(meta) + "_")
        lines.append("")
    return "\n".join(lines)


def upload(base, token, kb_id, filename, text, process_config=None):
    """multipart 上传一个 .md（stdlib，不落临时文件）。

    `process_config` 是 WeKnora 的 `KnowledgeProcessOverrides` JSON 串，
    用来给**这一个文件**覆盖切块参数（见模块 docstring 的改造说明）。
    """
    boundary = "----weknora" + uuid.uuid4().hex
    chunks = [
        b"--" + boundary.encode() + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="' + filename.encode("utf-8") + b'"\r\n'
        b"Content-Type: text/markdown\r\n\r\n" + text.encode("utf-8") + b"\r\n",
    ]
    if process_config:
        chunks.append(
            b"--" + boundary.encode() + b"\r\n"
            b'Content-Disposition: form-data; name="process_config"\r\n\r\n'
            + process_config.encode("utf-8") + b"\r\n")
    chunks.append(b"--" + boundary.encode() + b"--\r\n")
    parts = b"".join(chunks)
    request = urllib.request.Request(f"{base}/api/v1/knowledge-bases/{kb_id}/knowledge/file",
                                     data=parts, method="POST")
    request.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read() or b"{}")


def existing_documents(base, token, kb_id):
    """同名文档检查用：返回 {文件名: [knowledgeId, ...]}。"""
    _, body = api("GET", f"{base}/api/v1/knowledge-bases/{kb_id}/knowledge?page_size=200", token)
    rows = body.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("items") or []
    found = {}
    for row in rows:
        name = row.get("file_name") or row.get("title")
        if name:
            found.setdefault(name, []).append(row.get("id"))
    return found


def delete_document(base, token, knowledge_id):
    request = urllib.request.Request(f"{base}/api/v1/knowledge/{knowledge_id}", method="DELETE")
    request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read() or b"{}")


def wait_for_parse(base, token, kb_id, names, timeout=POLL_SECONDS):
    """轮询直到这些文档都不是 pending/processing。返回最终状态。"""
    deadline = time.time() + timeout
    latest = {}
    while time.time() < deadline:
        _, body = api("GET", f"{base}/api/v1/knowledge-bases/{kb_id}/knowledge?page_size=200", token)
        rows = body.get("data") or []
        if isinstance(rows, dict):
            rows = rows.get("items") or []
        latest = {r.get("title"): r for r in rows if r.get("title") in names}
        if latest and all(r.get("parse_status") in ("completed", "failed", "error") for r in latest.values()):
            return latest
        time.sleep(3)
    return latest


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="已审核 jsonl（knowledge-cn/data/reviewed/*.jsonl）")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--email", default="agent.verify@local.test")
    parser.add_argument("--password", default="verify12345")
    parser.add_argument("--title", help="入库文档标题（默认取输入文件名）")
    parser.add_argument("--out", help="把渲染好的 .md 另存一份（便于人工核对入库内容）")
    parser.add_argument("--strategy", help="覆盖切块策略（heading / heuristic / recursive / legacy）")
    parser.add_argument("--chunk-size", type=int, help="覆盖切块字符数（结构化目录用 200 能做到一条记录一块）")
    parser.add_argument("--replace", action="store_true",
                        help="同名文档已存在时先删掉再传（**会作废既有 chunkId**，仅重建时用）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    path = Path(args.input)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    eligible = [r for r in records if r.get("eligible_for_import")
                and r.get("review_status") in ("APPROVED", "WAIVED")]
    title = args.title or path.stem
    markdown = render_markdown(eligible, title)

    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")

    chunking = {}
    if args.strategy:
        chunking["strategy"] = args.strategy
    if args.chunk_size:
        chunking["chunk_size"] = args.chunk_size
    process_config = json.dumps({"chunking_config": chunking}, ensure_ascii=False) if chunking else None

    summary = {"file": path.name, "records": len(records), "eligible": len(eligible),
               "markdown_chars": len(markdown), "dry_run": args.dry_run, "kb_id": args.kb_id,
               "chunking": chunking or "默认(512)"}
    if args.dry_run or not eligible:
        print(json.dumps(summary, ensure_ascii=False))
        return

    token = login(base, args.email, args.password)
    name = f"{title}.md"

    # 幂等闸门：同名已存在就停下来，除非显式 --replace。
    # 首版导入没有这道闸门，nbs-wages-2025.md 被导入了两次（36+36 chunk、哈希序列一致）。
    existing = existing_documents(base, token, args.kb_id).get(name, [])
    if existing and not args.replace:
        summary["skipped"] = "同名文档已存在"
        summary["existingKnowledgeIds"] = existing
        print(json.dumps(summary, ensure_ascii=False))
        raise SystemExit(2)
    if existing:
        for knowledge_id in existing:
            delete_document(base, token, knowledge_id)
        summary["deletedBeforeImport"] = existing
        # 删除是异步任务（返回的是 task_id），必须等到同名确实不在列表里再传，
        # 否则新文档会和还没删掉的旧文档同名共存 —— 那就是重复导入的成因。
        deadline = time.time() + 120
        while time.time() < deadline:
            if not existing_documents(base, token, args.kb_id).get(name):
                break
            time.sleep(2)
        else:
            raise SystemExit(f"同名文档删除超时，未上传：{name}")

    created = upload(base, token, args.kb_id, name, markdown, process_config)
    knowledge_id = (created.get("data") or {}).get("id")
    summary["knowledge_id"] = knowledge_id
    statuses = wait_for_parse(base, token, args.kb_id, {name})
    doc = statuses.get(name) or {}
    summary["parse_status"] = doc.get("parse_status")
    summary["enable_status"] = doc.get("enable_status")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
