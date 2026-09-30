"""简历解析（`POST /api/resumes/extract`）的测试（**不联网**）。

跑法：``python -m pytest backend/tests -q``。

钉住的是这套设计里最容易被做坏的五件事：

* **证据可回填**：每条抽取的 ``charRange`` 必须真的能切出那个值（不是随手编的区间）；
* **认不出就不写**：图谱没有的技能词只进 ``unrecognizedSkills``，不进画像技能表；
* **不支持的形态明确拒绝**：PDF / 图片 / .doc 一律 415 + 可执行建议，不假装解析；
* **授权闸门不变**：抽取结果只落 ``candidate``，确认前不进注入上下文；
* **模型只兜底**：兜底失败/未配置时规则结果照常返回，并如实标注错误码。
"""

from __future__ import annotations

import html
import io
import json
import zipfile

import pytest

from backend.llm import LlmClient, LlmConfig
from backend.memories import MemoryStore
from backend.resume import (
    MAX_FILE_BYTES,
    ResumeFormatError,
    extract_docx_text,
    memory_candidates,
    parse_multipart_form,
    read_upload,
    split_sections,
)
from backend.knowledge import GraphStore
from backend.server import CareerApi, ProfileStore

RESUME_TEXT = """张小明
杭州 | 13800000000 | xm@example.com
教育背景
2022.09-2026.06  浙江大学  自动化 专业  大三
求职意向：边缘 AI 工程师
专业技能
Python（熟练）、模型量化与部署、轻量级推理引擎集成、ROS、炖菜
项目经历
项目名称：模型量化部署实践
把 YOLOv5 量化到 INT8 并在 RK3588 上跑通，帧率提升 40%。
自我评价
喜欢折腾边缘设备，能自己啃论文，也在做移动端部署的实验。
"""


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.bodies = []

    def __call__(self, url, body, headers, timeout):
        self.bodies.append(json.loads(body.decode("utf-8")))
        if not self.responses:
            raise AssertionError("FakeTransport 收到的调用次数超出预期")
        return self.responses.pop(0)

    @property
    def calls(self) -> int:
        return len(self.bodies)

    @property
    def prompt(self) -> str:
        return self.bodies[-1]["messages"][0]["content"]


def completion(content: str) -> str:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}, ensure_ascii=False)


def make_client(transport: FakeTransport) -> LlmClient:
    return LlmClient(
        LlmConfig(base_url="http://llm.invalid", api_key="test-key", model="test-model", source="test"),
        transport=transport,
        sleep=lambda _seconds: None,
    )


def multipart(field: str, filename: str, data: bytes, boundary: str = "----resumetest") -> tuple[bytes, str]:
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def make_docx(lines) -> bytes:
    """手搓一个最小 DOCX（zip + word/document.xml），用来验证 stdlib 解析路径。"""
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{html.escape(line)}</w:t></w:r></w:p>' for line in lines
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def store() -> GraphStore:
    return GraphStore()


@pytest.fixture()
def memories(store: GraphStore) -> MemoryStore:
    instance = MemoryStore(":memory:", vocabulary=store.terms_in)
    yield instance
    instance.close()


@pytest.fixture()
def api(store: GraphStore, memories: MemoryStore) -> CareerApi:
    return CareerApi(store=store, profiles=ProfileStore(store), memories=memories)


def post_text(api: CareerApi, text: str, generator: str = ""):
    raw = json.dumps({"text": text}).encode("utf-8")
    query = {"generator": [generator]} if generator else {}
    return api.handle("POST", "/api/resumes/extract", query, {"text": text}, raw=raw, content_type="application/json")


# --------------------------------------------------------------------- 文本路径

def test_text_resume_extracts_fields_and_sections(api: CareerApi) -> None:
    status, payload = post_text(api, RESUME_TEXT)
    assert status == 200
    data = payload["data"]
    draft = data["profileDraft"]

    assert draft["school"] == "浙江大学"
    assert draft["major"] == "自动化"  # 反例保护：不能把「大三」当专业
    assert draft["grade"] == "大三"
    assert draft["identity"] == "在校生"
    assert draft["question"] == "边缘 AI 工程师"
    assert draft["directions"] == "边缘 AI 工程师"
    assert draft["source"] == "resume"
    assert data["resumeId"].startswith("resume_")
    assert data["originalFileRetained"] is False
    # 章节切分确实分开了（不是整篇当一段）
    for key in ("education", "skills", "projects", "objective", "summary"):
        assert key in data["extraction"]["sections"], data["extraction"]["sections"]


def test_every_evidence_range_actually_cuts_the_value(api: CareerApi) -> None:
    """出处必须是真的：按 charRange 去解析器自己的归一化文本里切，切出来必须是 value 本身。"""
    from backend.resume import normalize_text

    _, payload = post_text(api, RESUME_TEXT)
    data = payload["data"]
    text = normalize_text(RESUME_TEXT)
    for entry in data["evidence"]:
        start, end = entry["charRange"]
        assert entry["located"] is True, entry
        assert 0 <= start < end
        assert text[start:end] == entry["value"], entry
    # 技能证据同样要能定位到字面形式（可能是别名，比如「模型量化」）
    for skill in data["skills"]:
        evidence = skill["evidence"]
        assert evidence["located"] is True, skill
        start, end = evidence["charRange"]
        assert text[start:end] == evidence["value"]


def test_unknown_skills_are_listed_not_written(api: CareerApi) -> None:
    """图谱不认识的名词（Python/ROS/炖菜）只进 unrecognizedSkills，不进画像技能表。"""
    _, payload = post_text(api, RESUME_TEXT)
    data = payload["data"]
    tokens = {entry["token"] for entry in data["unrecognizedSkills"]}
    assert {"Python", "ROS", "炖菜"} <= tokens, tokens
    assert "Python" not in (data["profileDraft"].get("skills") or [])
    assert any("unrecognizedSkills" in item for item in data["warnings"])


def test_candidates_are_pending_and_gated(api: CareerApi) -> None:
    _, payload = post_text(api, RESUME_TEXT)
    data = payload["data"]
    assert data["candidateCount"] == len(data["memoryCandidates"]) >= 3
    assert all(item["status"] == "candidate" for item in data["memoryCandidates"])
    assert all(item["sourceType"] == "resume" for item in data["memoryCandidates"])
    assert all(item["sourceId"].startswith(data["resumeId"] + ":") for item in data["memoryCandidates"])

    # 授权闸门：确认前不进注入上下文
    _, context = api.handle("GET", "/api/memories/context", {"query": ["模型量化与部署"]}, None)
    assert context["data"]["count"] == 0

    memory_id = data["memoryCandidates"][0]["id"]
    api.handle("PATCH", f"/api/memories/{memory_id}", {}, {"status": "confirmed"})
    _, after = api.handle("GET", "/api/memories/context", {"query": ["轻量级推理引擎集成"]}, None)
    assert after["data"]["count"] >= 1


def test_same_resume_twice_is_idempotent(api: CareerApi) -> None:
    first = post_text(api, RESUME_TEXT)[1]["data"]
    second = post_text(api, RESUME_TEXT)[1]["data"]
    assert first["resumeId"] == second["resumeId"]
    assert second["candidateCount"] == 0, "同一份简历重复上传不应产生重复候选"
    _, listing = api.handle("GET", "/api/memories", {}, None)
    assert listing["data"]["count"] == first["candidateCount"]


# --------------------------------------------------------------------- DOCX / 上传

def test_docx_is_parsed_with_stdlib(api: CareerApi) -> None:
    data = make_docx(["张小明", "教育背景", "浙江大学 自动化 专业 大四", "专业技能", "模型量化与部署", "Python"])
    assert "浙江大学" in extract_docx_text(data)
    raw, content_type = multipart("file", "resume.docx", data)
    status, payload = api.handle("POST", "/api/resumes/extract", {}, None, raw=raw, content_type=content_type)
    assert status == 200
    body = payload["data"]
    assert body["extraction"]["source"] == "docx"
    assert body["extraction"]["filename"] == "resume.docx"
    assert body["profileDraft"]["school"] == "浙江大学"
    assert body["profileDraft"]["skills"] == ["模型量化与部署"]


def test_docx_without_document_xml_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/styles.xml", "<x/>")
    with pytest.raises(ResumeFormatError):
        extract_docx_text(buffer.getvalue())


@pytest.mark.parametrize(
    "filename,data,expected",
    [
        ("resume.pdf", b"%PDF-1.7\n%\xe2\xe3", ".pdf"),
        ("resume.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, ".png"),
        ("resume.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".doc"),
    ],
)
def test_unsupported_formats_report_415(api: CareerApi, filename: str, data: bytes, expected: str) -> None:
    raw, content_type = multipart("file", filename, data)
    status, payload = api.handle("POST", "/api/resumes/extract", {}, None, raw=raw, content_type=content_type)
    assert status == 415
    assert payload["error"]["code"] == "RESUME_FORMAT_UNSUPPORTED"
    assert payload["error"]["suffix"] == expected
    assert payload["error"]["message"], "必须给出可执行的替代建议，而不是一句「不支持」"


def test_pdf_renamed_to_docx_is_still_rejected() -> None:
    """改名的 PDF 也要拦住 —— 嗅探魔数，不信扩展名。"""
    with pytest.raises(ResumeFormatError) as error:
        read_upload("resume.docx", b"%PDF-1.4 whatever")
    assert error.value.suffix == ".pdf"


def test_file_size_limit_is_enforced(api: CareerApi) -> None:
    raw, content_type = multipart("file", "big.txt", b"x" * (MAX_FILE_BYTES + 1))
    status, payload = api.handle("POST", "/api/resumes/extract", {}, None, raw=raw, content_type=content_type)
    assert status == 413
    assert payload["error"]["code"] == "RESUME_FILE_TOO_LARGE"


def test_multipart_parser_reads_fields_and_filenames() -> None:
    raw, content_type = multipart("file", "简历.txt", "模型量化与部署".encode("utf-8"))
    fields, filenames = parse_multipart_form(raw, content_type)
    assert filenames["file"] == "简历.txt"
    assert fields["file"].decode("utf-8") == "模型量化与部署"
    with pytest.raises(ResumeFormatError):
        parse_multipart_form(raw, "application/json")


def test_bad_requests_are_rejected(api: CareerApi) -> None:
    assert api.handle("POST", "/api/resumes/extract", {}, None, raw=b"", content_type="")[0] == 400
    assert api.handle("POST", "/api/resumes/extract", {}, None, raw=b"not multipart",
                      content_type="multipart/form-data")[0] == 400
    bad_generator = api.handle("POST", "/api/resumes/extract", {"generator": ["magic"]}, {"text": RESUME_TEXT})
    assert bad_generator[0] == 400 and bad_generator[1]["error"]["code"] == "INVALID_GENERATOR"


# --------------------------------------------------------------------- 模型只兜底

def test_llm_fallback_merges_hints(store: GraphStore) -> None:
    transport = FakeTransport((200, completion(
        '{"directions": ["边缘AI部署", "端侧推理"], "highlights": ["把 YOLOv5 量化到 INT8"]}'
    )))
    memories = MemoryStore(":memory:", vocabulary=store.terms_in, llm=make_client(transport))
    api = CareerApi(store=store, profiles=ProfileStore(store), memories=memories)
    try:
        _, payload = post_text(api, RESUME_TEXT)
        data = payload["data"]
        assert transport.calls == 1
        assert data["extraction"]["llm"]["used"] is True
        assert data["extraction"]["llm"]["model"] == "test-model"
        assert data["llmHints"]["directions"] == ["边缘AI部署", "端侧推理"]
        # 兜底内容**不**写进画像技能表（不新增图谱结论），只作为提示
        assert "边缘AI部署" not in data["profileDraft"].get("skills", [])
        assert any("边缘AI部署" in item for item in data["warnings"])
    finally:
        memories.close()


def test_llm_failure_degrades_to_rules(store: GraphStore) -> None:
    transport = FakeTransport((500, "boom"), (500, "boom"), (500, "boom"), (500, "boom"))
    memories = MemoryStore(":memory:", vocabulary=store.terms_in, llm=make_client(transport))
    api = CareerApi(store=store, profiles=ProfileStore(store), memories=memories)
    try:
        status, payload = post_text(api, RESUME_TEXT)
        data = payload["data"]
        assert status == 200  # 兜底失败绝不是 5xx
        assert data["extraction"]["llm"]["used"] is False
        assert data["extraction"]["llm"]["error"]["code"] == "LLM_HTTP_ERROR"
        assert data["profileDraft"]["school"] == "浙江大学"  # 规则结果完好
        assert any("模型兜底未生效" in item for item in data["warnings"])
    finally:
        memories.close()


def test_generator_rule_based_never_calls_model(store: GraphStore) -> None:
    transport = FakeTransport()
    memories = MemoryStore(":memory:", vocabulary=store.terms_in, llm=make_client(transport))
    api = CareerApi(store=store, profiles=ProfileStore(store), memories=memories)
    try:
        _, payload = post_text(api, RESUME_TEXT, generator="rule-based")
        assert transport.calls == 0
        assert payload["data"]["extraction"]["llm"]["used"] is False
    finally:
        memories.close()


# --------------------------------------------------------------------- 纯函数

def test_split_sections_and_candidates_helpers(store: GraphStore) -> None:
    sections = split_sections("技能：Python、模型量化与部署\n项目经历\n项目名称：X")
    assert "模型量化与部署" in sections["skills"]  # 「技能：」同行内容也要收进该章节
    assert "X" in sections["projects"]

    candidates = memory_candidates({
        "profileDraft": {"directions": "边缘 AI 工程师、嵌入式开发", "major": "自动化", "location": "杭州",
                         "question": "先补哪块"},
        "skills": [{"name": "模型量化与部署"}],
    })
    contents = [entry["content"] for entry in candidates]
    assert "具备或正在学习：模型量化与部署" in contents
    assert "目标职业：边缘 AI 工程师" in contents
    assert "目标职业：嵌入式开发" in contents
    assert "目标职业：边" not in contents  # 反例保护：不能逐字符拆方向
    assert "专业：自动化" in contents
