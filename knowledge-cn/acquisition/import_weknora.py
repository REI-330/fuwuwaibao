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

## 出处随文入库

每段渲染都带上来源 id / record_kind / 记录 id / 页码，并标注这条是**业主豁免闸门**
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
    """把一批已审核记录渲染成一个 .md。每段都带出处。"""
    lines = [f"# {title}", "", "> 本文件由中国官方语料候选渲染而来，逐条标注出处与审核状态。", ""]
    for record in records:
        source = record.get("source") or {}
        source_id = source.get("id", "?")
        kind = record.get("record_kind", "?")
        waiver = record.get("review_waiver") or {}
        # 审核状态如实写：WAIVED 是「业主豁免」，不许写成「已审核通过」
        status = record.get("review_status", "")
        status_zh = {"WAIVED": "业主豁免闸门（非人工审核通过）", "APPROVED": "人工审核通过"}.get(status, status)
        lines.append(f"## {record.get('id', '')}")
        if record.get("title"):
            lines.append(f"**{record['title']}**")
        lines.append("")
        if record.get("content"):
            lines.append(str(record["content"]).strip())
        else:
            # 结构化记录：只用它自己的字段，不补充任何额外信息
            parts = []
            if record.get("major_code"):
                parts.append(f"专业代码：{record['major_code']}")
            if record.get("major_name"):
                parts.append(f"专业名称：{record['major_name']}")
            if record.get("page"):
                parts.append(f"来源页码：第 {record['page']} 页")
            lines.append("；".join(parts) if parts else "（该记录没有可渲染的字段）")
        lines.append("")
        meta = [f"来源：{source_id}", f"类型：{kind}", f"记录 ID：{record.get('id','')}",
                f"审核状态：{status_zh}"]
        if source.get("page"):
            meta.append(f"页码：{source['page']}")
        if waiver.get("by"):
            meta.append(f"豁免人：{waiver['by']}")
        lines.append("_" + " · ".join(meta) + "_")
        lines.append("")
    return "\n".join(lines)


def upload(base, token, kb_id, filename, text):
    """multipart 上传一个 .md（stdlib，不落临时文件）。"""
    boundary = "----weknora" + uuid.uuid4().hex
    body = text.encode("utf-8")
    parts = (
        b"--" + boundary.encode() + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="' + filename.encode("utf-8") + b'"\r\n'
        b"Content-Type: text/markdown\r\n\r\n" + body + b"\r\n"
        b"--" + boundary.encode() + b"--\r\n"
    )
    request = urllib.request.Request(f"{base}/api/v1/knowledge-bases/{kb_id}/knowledge/file",
                                     data=parts, method="POST")
    request.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=180) as response:
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

    summary = {"file": path.name, "records": len(records), "eligible": len(eligible),
               "markdown_chars": len(markdown), "dry_run": args.dry_run, "kb_id": args.kb_id}
    if args.dry_run or not eligible:
        print(json.dumps(summary, ensure_ascii=False))
        return

    token = login(base, args.email, args.password)
    created = upload(base, token, args.kb_id, f"{title}.md", markdown)
    knowledge_id = (created.get("data") or {}).get("id")
    summary["knowledge_id"] = knowledge_id
    statuses = wait_for_parse(base, token, args.kb_id, {f"{title}.md"})
    doc = statuses.get(f"{title}.md") or {}
    summary["parse_status"] = doc.get("parse_status")
    summary["enable_status"] = doc.get("enable_status")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
