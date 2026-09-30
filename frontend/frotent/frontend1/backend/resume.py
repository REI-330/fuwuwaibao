"""简历解析（``POST /api/resumes/extract``）：文本 / DOCX → 画像草稿 + 待确认记忆候选。

**为什么自己写、不搬队友那份。** 队友 `career-ai-system` 的 MCP 包里确实有
``extract_resume_profile`` / ``score_resume_job``，但它自己 README/HANDOFF 写明
「PDF/DOCX 提取仍是主系统的责任」「仍需要一个生产级简历解析器」；而且它的抽取口径有三个
实测缺陷：技能表是 35 个硬编码**英文**名、打分分母取「简历技能数」（简历多写几个技能反而
分数变低）、只回技能名没有原文出处。可借的只有字段级小细节，已在本文档相应位置注明。

本模块的设计约束（与本项目既有纪律逐条对齐）：

1. **零三方依赖**：DOCX 用 stdlib ``zipfile`` + ``xml.etree`` 读 ``word/document.xml``；
   PDF 走**运行时探测的可选后端**（pypdf / PyMuPDF / pdfminer，装了哪个用哪个，都不是硬依赖）；
   图片**明确不支持**（调用方返回 415 并给可执行建议），不假装、也不退回假数据。
2. **规则优先，模型只兜底**：技能锚点取自图谱名词表（``GraphStore.nodes_in``，161 条 label/alias，
   含中文技能名），字段用正则 + 章节定位；模型只处理**规则认不出的残差**，且必须在字段约束内，
   失败整段降级并如实标注（``llm.error``）。
3. **每条抽取都能指回原文**：``evidence[]`` 带 ``snippet`` 与 ``charRange`` ——
   与知识库 chunk 的 ``charRange`` 可回溯是同一个要求。
4. **AI 观察不直接成为结论**：只产出 ``profileDraft``（前端复核后才 PUT 画像）+ ``candidate`` 记忆
   （``source_type='resume'``），写入正式画像必须由用户显式确认。
5. **认不出就不写**：技能章节里图谱不认识的名词进 ``unrecognizedSkills`` 如实列出，
   不进画像技能表 —— 否则会进匹配算式却无依据。
"""

from __future__ import annotations

import hashlib
import importlib
import io
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from .knowledge import GraphStore

# ---------------------------------------------------------------------------- 常量

MODULE = "resume-extract/v1"
MAX_TEXT_CHARS = 200000
MAX_FILE_BYTES = 10 * 1024 * 1024  # 契约里的 10MB
SUPPORTED_SUFFIXES = (".pdf", ".docx", ".txt", ".md", ".text")
UNSUPPORTED_SUFFIXES = (".doc", ".jpg", ".jpeg", ".png", ".webp", ".zip", ".xlsx")

_W_P = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
_W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"

_PAGE_NOISE_RE = re.compile(r"^(?:第?\s*\d+\s*页(?:\s*/\s*共?\s*\d+\s*页)?|page\s*\d+(?:\s*of\s*\d+)?|\d+\s*/\s*\d+)$", re.I)
# 「项目名称：X」这类行看着像标题、其实是内容：不能当章节头吃掉（否则项目标题会丢前缀）
_NOT_HEADER_RE = re.compile(r"(?:名称|名|编号|时间|地点|描述|职责|收获|成果|工具|环境)$")

# 章节别名表（中英混排是简历常态；顺序即优先级，用于「同名标题取第一个」）
SECTION_ALIASES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("objective", ("求职意向", "职业目标", "期望岗位", "目标岗位", "应聘意向", "objective", "career objective")),
    ("education", ("教育背景", "教育经历", "学历信息", "学历", "education")),
    ("skills", ("专业技能", "技能清单", "技能特长", "技能", "技术栈", "skills", "technical skills")),
    ("projects", ("项目经历", "项目经验", "科研经历", "项目", "projects", "project experience")),
    ("experience", ("实习经历", "工作经历", "实习经验", "工作经验", "实习", "experience", "internship", "work experience")),
    ("honors", ("荣誉奖项", "获奖情况", "荣誉", "奖项", "honors", "awards")),
    ("certificates", ("证书", "资格证书", "证书情况", "certificates", "certifications")),
    ("summary", ("自我评价", "个人总结", "个人简介", "简介", "summary", "about me")),
)

_SCHOOL_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z]{2,20}(?:大学|学院|学校|职业技术学院))")
_MAJOR_RE = re.compile(r"(?:专业|主修|就读专业)\s*[:：]?\s*([^\n，,；;、/|]{2,20})")
_MAJOR_SUFFIX_RE = re.compile(r"([\u4e00-\u9fa5]{2,12})专业")
# 学校名后面那个短词不一定是专业：学历/年级/GPA 这些要排除掉
_NON_MAJOR_RE = re.compile(
    r"^(?:本科|硕士|博士|研究生|大专|专科|学士|专业|学位|学历|学院|大学|学校|大[一二三四五六]|研[一二三]|博[一二三四]|高[一二三]|GPA|均分|成绩|排名)$",
    re.I,
)
_GRADE_RE = re.compile(r"(大[一二三四五六]|研[一二三]|博[一二三四]|高[一二三]|\d{4}\s*届|应届毕业生|应届生|在校生)")
_GRADUATION_RE = re.compile(r"((?:19|20)\d{2})\s*年?\s*(?:6月|7月|12月)?\s*(?:毕业|毕业时间)")
_LOCATION_RE = re.compile(r"(?:现居|现居住地|所在地|期望(?:工作)?城市|意向城市|求职城市|城市)\s*[:：]?\s*([\u4e00-\u9fa5]{2,10})")
_GOAL_RE = re.compile(r"(?:求职意向|目标岗位|期望岗位|应聘岗位|意向岗位|目标职业)\s*[:：]?\s*([^\n]{2,60})")
_EXPERIENCE_TITLE_RE = re.compile(r"(?:项目名称|项目名|课题名称|项目)\s*[:：]\s*([^\n]{2,60})")
_YEAR_SPAN_RE = re.compile(r"((?:19|20)\d{2})\s*[年./-]\s*(\d{1,2})?\s*[-~—到至]\s*((?:19|20)\d{2})?\s*[年./-]?\s*(\d{1,2})?", re.I)

_SKILL_SPLIT_RE = re.compile(r"[\n,，、;；/|·•\t]+")
_SKILL_DECORATION_RE = re.compile(r"[（(][^）)]{0,20}[）)]")
_SKILL_PREFIX_RE = re.compile(r"^[^:：]{0,12}[:：]\s*")

IDENTITY_STUDENT = "在校生"
IDENTITY_GRADUATE = "应届生"
IDENTITY_NEW_EMPLOYEE = "职场新人"


class ResumeFormatError(ValueError):
    """输入形态不支持（调用方据此回 415），与「解析不出内容」分开。"""

    def __init__(self, message: str, *, suffix: str = "", code: str = "RESUME_FORMAT_UNSUPPORTED") -> None:
        super().__init__(message)
        self.code = code
        self.suffix = suffix


# ---------------------------------------------------------------------------- 文本读取

def extract_docx_text(data: bytes) -> str:
    """从 DOCX 里抽纯文本（stdlib）。

    DOCX 就是一个 zip，正文在 ``word/document.xml``；段落是 ``w:p``，文字在 ``w:t``。
    表格里的段落同样会被 ``iter`` 扫到，所以表格简历也能读。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                raise ResumeFormatError(
                    "这个文件不是 DOCX（缺少 word/document.xml）：.doc 老格式或改名的文件请另存为 .docx",
                    code="RESUME_FORMAT_UNSUPPORTED",
                )
            xml = archive.read("word/document.xml")
    except zipfile.BadZipFile as error:
        raise ResumeFormatError(f"DOCX 不是有效的 zip 包：{error}") from error

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise ResumeFormatError(f"DOCX 正文 XML 解析失败：{error}") from error

    paragraphs: List[str] = []
    for paragraph in root.iter(_W_P):
        line = "".join(node.text or "" for node in paragraph.iter(_W_T)).strip()
        if line:
            paragraphs.append(line)
    if not paragraphs:
        raise ResumeFormatError("DOCX 里没有可读文字（可能是纯图片扫描件）", code="RESUME_EMPTY_TEXT")
    return "\n".join(paragraphs)


def decode_text_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ResumeFormatError("文本简历不是 UTF-8 / GB18030 编码，无法读取", code="RESUME_ENCODING_UNSUPPORTED")


# ---------------------------------------------------------------------------- PDF（可选能力）

# PDF 解析是**运行时探测的可选能力**：本机装了哪个库就用哪个，一个都没有时退回 415 并说明原因。
# 刻意不把任何 PDF 库写进 requirements —— 后端「零三方依赖」的定位不因此改变，
# 只是「本机恰好有的话就别浪费」。这也让「有没有 PDF 能力」变成可由一次探测回答的事实，
# 而不是一句写死的「不支持」。
#
# 顺序是**故意的**：PyMuPDF(fitz) 排第一。实测（2026-09-30）：同一份中文 PDF，
# fitz 抽出「求职意向：嵌入式软件工程师」逐字正确，而 pypdf 抽出的是
# `lB\x80La\x0fT\x11...` 这种乱码 —— 因为该 PDF 的字体没有 ToUnicode 映射，
# pypdf 无法把字形映回 Unicode。中文简历里这种 PDF 不少，所以能选就选对中文更稳的后端。
_PDF_BACKENDS = ("fitz", "pypdf", "pdfminer.high_level")

# 抽出来的文本里若混进大量控制字符/替换字符，说明字形没能映回 Unicode（乱码），
# 这时**必须报错**：把乱码喂给规则/模型，只会得到一堆看似成功的垃圾技能词。
_MOJIBAKE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd]")
_MOJIBAKE_RATIO = 0.02


def pdf_backend() -> Optional[str]:
    """探测本机可用的 PDF 文本后端；一个都没有就返回 None。"""
    for name in _PDF_BACKENDS:
        try:
            importlib.import_module(name)
        except Exception:  # 装不上/版本不兼容都算「这个后端不可用」，继续试下一个
            continue
        return name
    return None


def extract_pdf_text(data: bytes) -> str:
    """从 PDF 抽纯文本。扫描件（抽不出文字）单独报错，不假装识别成功。"""
    backend = pdf_backend()
    if backend is None:
        raise ResumeFormatError(
            "本机没有可用的 PDF 解析库（pypdf / PyMuPDF / pdfminer 都没装）。"
            "装一个即可：pip install pypdf；或粘贴文本 / 把 PDF 另存为 DOCX 上传。",
            suffix=".pdf",
        )
    try:
        if backend == "pypdf":
            from pypdf import PdfReader

            pages = [(page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages]
        elif backend == "fitz":
            import fitz

            with fitz.open(stream=data, filetype="pdf") as document:
                pages = [document.load_page(index).get_text() for index in range(document.page_count)]
        else:
            from pdfminer.high_level import extract_text as pdfminer_extract

            pages = [pdfminer_extract(io.BytesIO(data))]
    except Exception as error:  # 加密 / 损坏 / 库自身异常，都当作解析失败如实报出
        raise ResumeFormatError(
            f"PDF 解析失败（后端 {backend}）：{error}。加密或损坏的 PDF 请先另存一份，或粘贴文本。",
            code="RESUME_PDF_PARSE_FAILED",
            suffix=".pdf",
        ) from error

    lines = [line.strip() for page in pages for line in (page or "").splitlines() if line.strip()]
    if not lines:
        raise ResumeFormatError(
            "这个 PDF 里抽不出文字（多半是扫描件 / 图片版）：本机没有 OCR，不会假装识别出内容。"
            "请粘贴文本，或改用文字版 PDF / DOCX。",
            code="RESUME_EMPTY_TEXT",
            suffix=".pdf",
        )
    text = "\n".join(lines)
    suspicious = len(_MOJIBAKE_RE.findall(text))
    if suspicious / max(len(text), 1) > _MOJIBAKE_RATIO:
        raise ResumeFormatError(
            f"这个 PDF 的文字层读出来是乱码（后端 {backend}：字形没能映回 Unicode，"
            "常见于字体缺 ToUnicode 映射的 PDF）。与其把乱码当简历解析，不如明说："
            "请粘贴文本，或用 Word/WPS 另存一份 PDF / DOCX 再上传。",
            code="RESUME_PDF_TEXT_UNREADABLE",
            suffix=".pdf",
        )
    return text


_PDF_MAGIC = b"%PDF"
_IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"RIFF")


def suffix_of(filename: str) -> str:
    name = str(filename or "").strip()
    return ("." + name.rsplit(".", 1)[1].lower()) if "." in name else ""


def read_upload(filename: str, data: bytes) -> Tuple[str, str]:
    """按**内容**与扩展名决定怎么读。

    先嗅探魔数（改名的 PDF 也要被拦住），再按后缀分派；不支持的形态抛
    ``ResumeFormatError``（调用方回 415）并给出可执行的替代建议 —— 不假装解析成功。
    """
    if not data:
        raise ResumeFormatError("上传内容为空", code="RESUME_EMPTY_FILE")
    if len(data) > MAX_FILE_BYTES:
        raise ResumeFormatError(f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)}MB 上限", code="RESUME_FILE_TOO_LARGE")
    suffix = suffix_of(filename)
    if data.startswith(_PDF_MAGIC) or suffix == ".pdf":
        # 先嗅探魔数再看后缀：改名的 PDF（.txt 外壳）也走解析，而不是被当成纯文本读出乱码
        return extract_pdf_text(data), "pdf"
    if data.startswith(_IMAGE_MAGIC) or suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        raise ResumeFormatError(
            "图片简历暂不支持：本机没有 OCR 能力，不会假装识别出内容。请粘贴文本或上传 DOCX。",
            suffix=suffix or ".image",
        )
    if suffix == ".doc":
        raise ResumeFormatError(".doc 老格式不支持：请用 Word 另存为 .docx 再上传。", suffix=suffix)
    if suffix == ".docx":
        return extract_docx_text(data), "docx"
    if suffix in ("", ".txt", ".md", ".text"):
        return decode_text_bytes(data), "text"
    raise ResumeFormatError(f"不支持的文件类型 {suffix}：简历请上传 .docx / .txt，或直接粘贴文本。", suffix=suffix)


def parse_multipart_form(raw: bytes, content_type: str) -> Tuple[Dict[str, bytes], Dict[str, str]]:
    """解析 ``multipart/form-data``（stdlib，不落盘）。返回 ``(字段字节, 字段文件名)``。"""
    marker = "boundary="
    if marker not in str(content_type or ""):
        raise ResumeFormatError("上传请求不是 multipart/form-data（缺少 boundary）", code="RESUME_BAD_UPLOAD")
    boundary = content_type.split(marker, 1)[1].split(";")[0].strip().strip('"')
    if not boundary:
        raise ResumeFormatError("multipart 的 boundary 为空", code="RESUME_BAD_UPLOAD")
    delimiter = b"--" + boundary.encode("utf-8")
    fields: Dict[str, bytes] = {}
    filenames: Dict[str, str] = {}
    for chunk in raw.split(delimiter)[1:]:
        if chunk[:2] == b"--":  # 收尾分隔符
            break
        head, separator, body = chunk.partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]+)"', headers)
        if not name:
            continue
        if body.endswith(b"\r\n"):
            body = body[:-2]
        fields[name.group(1)] = body
        filename = re.search(r'filename="([^"]*)"', headers)
        if filename and filename.group(1):
            filenames[name.group(1)] = filename.group(1)
    if not fields:
        raise ResumeFormatError("multipart 里没有任何字段", code="RESUME_BAD_UPLOAD")
    return fields, filenames


def normalize_text(raw: str) -> str:
    """统一空白、去掉页眉页脚页码这类噪声行。**不做**改写或摘要。"""
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\xa0", " ").replace("\t", " ")
    lines: List[str] = []
    for line in text.split("\n"):
        stripped = " ".join(line.split())
        if not stripped or _PAGE_NOISE_RE.match(stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines)


# ---------------------------------------------------------------------------- 章节切分

def _section_of(line: str) -> Optional[str]:
    """判定一行是不是章节标题。

    简历里标题常与内容同行（``技能：Python、模型量化与部署``），所以先取冒号前的**头部**
    去比；同时排掉 ``项目名称：…`` 这类「看起来像标题、其实是内容」的行（它们要留给
    ``_EXPERIENCE_TITLE_RE`` 处理）。
    """
    head = re.split(r"[:：]", line, maxsplit=1)[0]
    if _NOT_HEADER_RE.search(head.strip()):
        return None
    for candidate_text in (head, line):
        normalized = re.sub(r"[\s:：\-—_]+", "", candidate_text)
        if not normalized or len(normalized) > 20:
            continue
        for key, aliases in SECTION_ALIASES:
            for alias in aliases:
                alias_norm = re.sub(r"[\s:：\-—_]+", "", alias)
                if not alias_norm:
                    continue
                if normalized == alias_norm or (
                    normalized.startswith(alias_norm) and len(normalized) <= len(alias_norm) + 8
                ):
                    return key
    return None


def _rest_after_title(line: str) -> str:
    for separator in ("：", ":"):
        if separator in line:
            head, _, tail = line.partition(separator)
            if _section_of(head) and tail.strip():
                return tail.strip()
    return ""


def split_sections(text: str) -> Dict[str, str]:
    """按章节别名表把正文分桶；``header`` 是标题之前的部分（通常含姓名与联系方式）。"""
    buckets: Dict[str, List[str]] = {"header": []}
    current = "header"
    for line in text.split("\n"):
        key = _section_of(line)
        if key:
            current = key
            buckets.setdefault(current, [])
            rest = _rest_after_title(line)
            if rest:
                buckets[current].append(rest)
            continue
        buckets.setdefault(current, []).append(line)
    return {key: "\n".join(value) for key, value in buckets.items()}


# ---------------------------------------------------------------------------- 抽取

def _evidence(field: str, value: str, text: str, span: Tuple[int, int]) -> Dict[str, Any]:
    """一条抽取的出处。``charRange`` 是**原文（归一化后）**里的字符区间。

    定位不到时如实给 ``[-1, -1]`` 与 ``located=False``，不假装有出处 ——
    这与知识库 chunk 的 ``charRange`` 可回溯是同一条纪律。
    """
    start, end = span
    located = 0 <= start < end <= len(text)
    return {
        "field": field,
        "value": value,
        "snippet": text[start:end][:200] if located else "",
        "charRange": [start, end] if located else [-1, -1],
        "located": located,
    }


def _span_of(value: str, text: str) -> Tuple[int, int]:
    position = text.find(value) if value else -1
    return (position, position + len(value)) if position >= 0 else (-1, -1)


def _evidence_for(field: str, value: str, text: str) -> Dict[str, Any]:
    """在整篇文本里定位 ``value``（第一次出现）作为出处。"""
    return _evidence(field, value, text, _span_of(value, text))


def _surface_for(node: Dict[str, Any], text: str) -> Tuple[str, Tuple[int, int]]:
    """节点在原文里的**字面形式**与位置（标签优先，其次别名；都找不到给 ``(-1,-1)``）。

    触发器与抽取都是按归一化名字命中的，所以原文里出现的可能是别名（如「模型量化」），
    证据必须回填**真正出现的那串字**，而不是节点标签。
    """
    names = [str(node.get("label") or "")] + [str(item) for item in (node.get("aliases") or [])]
    for name in names:
        if not name:
            continue
        position = text.find(name)
        if position >= 0:
            return name, (position, position + len(name))
    return str(node.get("label") or ""), (-1, -1)


def _first_value(pattern: re.Pattern, text: str, group: int = 1) -> Optional[str]:
    match = pattern.search(text)
    if not match:
        return None
    value = str(match.group(group) or "").strip()
    return value or None


def _search_scope(sections: Dict[str, str], keys: Tuple[str, ...]) -> str:
    parts = [sections.get(key, "") for key in keys]
    return "\n".join(part for part in parts if part)


def extract_skills(text: str, sections: Dict[str, str], store: GraphStore) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """技能两通道：图谱名词表全文命中 + 技能章节逐 token 校验。

    只有**图谱认识**的名词才算技能（返回带 nodeId 与 kind，能接着算能力域与岗位缺口）；
    认不出来的 token 单独列出，让用户自己决定要不要补进图谱 —— 不静默塞进画像。
    """
    hits: List[Dict[str, Any]] = []
    seen: set = set()

    def add(node: Dict[str, Any], evidence: Dict[str, Any]) -> None:
        if node["id"] in seen:
            return
        seen.add(node["id"])
        hits.append(
            {
                "skillId": str(node["id"]).split(":")[-1],
                "name": str(node.get("label") or ""),
                "nodeId": node["id"],
                "kind": node.get("kind"),
                "evidence": evidence,
            }
        )

    for node in store.nodes_in(text):
        if node.get("kind") != "skill":
            continue
        surface, span = _surface_for(node, text)
        add(node, _evidence("skills", surface, text, span))

    unknown: List[Dict[str, Any]] = []
    skill_section = sections.get("skills", "")
    for token in _SKILL_SPLIT_RE.split(skill_section):
        cleaned = _SKILL_PREFIX_RE.sub("", _SKILL_DECORATION_RE.sub("", token)).strip(" .。;；,，")
        if len(cleaned) < 2 or len(cleaned) > 40:
            continue
        matched = [node for node in store.nodes_in(cleaned) if node.get("kind") == "skill"]
        if matched:
            for node in matched:
                surface, span = _surface_for(node, text)
                if span == (-1, -1):
                    surface, span = cleaned, _span_of(cleaned, text)
                add(node, _evidence("skills", surface, text, span))
        elif cleaned not in {entry["token"] for entry in unknown}:
            unknown.append({"token": cleaned, "reason": "图谱里没有同名词条（未写入画像技能表）"})
    return hits, unknown


def _experiences(sections: Dict[str, str]) -> List[Dict[str, Any]]:
    blocks: List[str] = []
    for key in ("projects", "experience"):
        for block in re.split(r"\n(?=[^\n]{0,60}(?:项目|实习|工作|课题|研发|开发))", sections.get(key, "")):
            cleaned = block.strip()
            if cleaned:
                blocks.append(cleaned)
    entries: List[Dict[str, Any]] = []
    for index, block in enumerate(blocks[:5], start=1):
        title = ""
        titled = _EXPERIENCE_TITLE_RE.search(block)
        if titled:
            title = titled.group(1).strip()
        if not title:
            title = block.split("\n")[0].strip()
        title = title[:60]
        if not title:
            continue
        entries.append(
            {
                "experienceId": f"experience_resume_{index}",
                "type": "project",
                "title": title,
                "description": "；".join(part.strip() for part in block.split("\n")[1:4] if part.strip())[:200] or title,
            }
        )
    return entries


def _identity(sections: Dict[str, str], whole: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    value = _first_value(_GRADE_RE, whole)
    if value in ("应届毕业生", "应届生"):
        return IDENTITY_GRADUATE, _evidence_for("identity", value, whole)
    if value:
        return IDENTITY_STUDENT, _evidence_for("identity", value, whole)
    if (sections.get("experience") or "").strip():
        return IDENTITY_NEW_EMPLOYEE, None
    return "", None


def _major(sections: Dict[str, str], text: str) -> Optional[str]:
    """专业抽取三档（按可靠性排序），都带排除词保护。

    「自动化专业」→ 后缀式；「专业：自动化」→ 标签式；「浙江大学 自动化 本科」→
    学校名后面那个短词（但要排掉学历/年级/GPA，否则会把「大三」当专业 —— 实测踩过）。
    """
    scope = _search_scope(sections, ("education", "header")) or text
    suffix = _MAJOR_SUFFIX_RE.search(scope)
    if suffix:
        return suffix.group(1).strip()
    labeled = _MAJOR_RE.search(scope)
    if labeled:
        value = labeled.group(1).strip()
        if value and not _NON_MAJOR_RE.match(value):
            return value
    school = _SCHOOL_RE.search(scope)
    if school:
        for token in re.split(r"[\s|·/、,，]+", scope[school.end() :]):
            candidate = token.strip(" .。·-—")
            if 2 <= len(candidate) <= 12 and not re.search(r"\d", candidate) and not _NON_MAJOR_RE.match(candidate):
                return candidate
    return None


def extract_fields(text: str, sections: Dict[str, str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """结构化字段抽取。每个命中都带 ``charRange``（整篇文本里的真实区间），能指回原文。"""
    draft: Dict[str, Any] = {}
    evidence: List[Dict[str, Any]] = []
    scope = _search_scope(sections, ("education", "header", "summary")) or text

    school = _first_value(_SCHOOL_RE, scope)
    if school:
        draft["school"] = school
        evidence.append(_evidence_for("school", school, text))

    major = _major(sections, text)
    if major:
        draft["major"] = major
        evidence.append(_evidence_for("major", major, text))

    grade = _first_value(_GRADE_RE, scope)
    if grade:
        draft["grade"] = grade
        evidence.append(_evidence_for("grade", grade, text))

    graduation = _first_value(_GRADUATION_RE, scope)
    if graduation:
        draft["graduationYear"] = int(graduation)
        evidence.append(_evidence_for("graduationYear", graduation, text))

    location = _first_value(_LOCATION_RE, text)
    if location:
        draft["location"] = location
        evidence.append(_evidence_for("location", location, text))

    objective = _search_scope(sections, ("objective",))
    goal = _first_value(_GOAL_RE, objective or text)
    if not goal and objective.strip():
        # 「求职意向：X」这行会被当章节标题、把 X 放进 objective 桶里（标签本身不进桶），
        # 所以取不到标签时就拿该章节第一行当目标 —— 它本来就是用户写下的意向。
        goal = objective.strip().split("\n")[0].strip()[:60] or None
    if goal:
        draft["question"] = goal[:60]
        evidence.append(_evidence_for("question", goal[:60], text))

    identity, identity_evidence = _identity(sections, text)
    if identity:
        draft["identity"] = identity
        if identity_evidence:
            evidence.append(identity_evidence)
    return draft, evidence


# ---------------------------------------------------------------------------- LLM 兜底（只处理残差）

LLM_PROMPT_TEMPLATE = """下面是一份简历里**规则没能识别**的片段。请只提取片段中**明确写出**的信息。

硬性要求：
- 不要推断、不要补全、不要编造；片段里没有的字段给空数组；
- 只输出 JSON，不要解释、不要加 ``` 围栏；
- 结构：{{"directions": ["..."], "highlights": ["..."]}}；
- `directions` 是求职方向/目标岗位（最多 3 条，每条 ≤ 20 字）；
- `highlights` 是片段里明确写出的经历亮点（最多 3 条，每条 ≤ 40 字，必须能在片段里找到依据）。

片段：
{residue}
"""

MAX_LLM_ITEMS = 3
MAX_LLM_ITEM_CHARS = 40


def parse_llm_json(raw: str) -> Dict[str, Any]:
    """容错解析：去掉 ``` 围栏、截取第一个 ``{`` 到最后一个 ``}``。"""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型返回里找不到 JSON 对象")
    import json

    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("模型返回的 JSON 不是对象")
    return payload


def validate_llm_hints(payload: Dict[str, Any]) -> Dict[str, List[str]]:
    """逐字段夹取：类型不对就丢，超长就截断。**不做**任何补全。"""
    result: Dict[str, List[str]] = {}
    for key in ("directions", "highlights"):
        values = payload.get(key)
        if not isinstance(values, list):
            result[key] = []
            continue
        cleaned: List[str] = []
        for item in values:
            if not isinstance(item, (str, int, float)):
                continue
            text = str(item).strip()[:MAX_LLM_ITEM_CHARS]
            if text and text not in cleaned:
                cleaned.append(text)
        result[key] = cleaned[:MAX_LLM_ITEMS]
    return result


def build_residue(sections: Dict[str, str], unrecognized: List[Dict[str, Any]]) -> str:
    """残差 = 模型真正可能帮上忙的那部分：自我评价/求职意向原文 + 认不出的技能 token。"""
    parts: List[str] = []
    for key in ("summary", "objective"):
        body = (sections.get(key) or "").strip()
        if body:
            parts.append(body[:600])
    if unrecognized:
        parts.append("技能清单里未识别的词：" + "、".join(entry["token"] for entry in unrecognized[:20]))
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------- 主入口

def fingerprint(text: str) -> str:
    """同一份简历内容给同一个 id：重复上传不会产生重复候选（幂等）。"""
    return "resume_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def extract(
    *,
    raw_text: str,
    store: GraphStore,
    source: str = "text",
    filename: str = "",
    llm: Any = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """把一份简历文本解析成「画像草稿 + 记忆候选 + 证据」。

    纯函数式：不写库、不调模型（除非 ``use_llm`` 且残差非空且客户端已配置）。
    落库与授权由调用方（``CareerApi`` + ``MemoryStore``）负责。
    """
    text = normalize_text(raw_text)
    if not text:
        raise ResumeFormatError("没有解析出任何文字", code="RESUME_EMPTY_TEXT")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]

    sections = split_sections(text)
    skills, unrecognized = extract_skills(text, sections, store)
    draft, evidence = extract_fields(text, sections)
    experiences = _experiences(sections)

    if skills:
        draft["skills"] = [entry["name"] for entry in skills]
    if experiences:
        draft["experience"] = [entry["title"] for entry in experiences]
    directions = [str(node.get("label")) for node in store.nodes_in(text) if node.get("kind") == "occupation"]
    if directions:
        draft["directions"] = "、".join(dict.fromkeys(directions))
    draft["source"] = "resume"

    warnings: List[str] = []
    if unrecognized:
        warnings.append(
            f"{len(unrecognized)} 个技能词没有对应图谱词条，已列在 unrecognizedSkills 里（未写入画像）"
        )
    if not skills:
        warnings.append("没有识别到任何图谱已知技能：画像技能表为空，可手动补充")

    note: Dict[str, Any] = {"requested": bool(use_llm), "used": False, "model": None, "error": None}
    hints: Dict[str, List[str]] = {"directions": [], "highlights": []}
    residue = build_residue(sections, unrecognized)
    if use_llm and llm is not None and getattr(llm, "configured", False) and residue:
        note["model"] = getattr(getattr(llm, "config", None), "model", None)
        try:
            from .llm import LlmError

            raw = llm.chat(LLM_PROMPT_TEMPLATE.format(residue=residue), max_tokens=1024, timeout=120, attempts=2)
            hints = validate_llm_hints(parse_llm_json(raw))
            note["used"] = True
        except Exception as error:  # noqa: BLE001 —— 兜底失败绝不能打断解析
            code = getattr(error, "code", type(error).__name__)
            note["error"] = {"code": str(code), "message": str(error)[:300]}
            warnings.append(f"模型兜底未生效（{code}）：规则结果照常返回")
    elif use_llm and residue and (llm is None or not getattr(llm, "configured", False)):
        note["error"] = {"code": "LLM_NOT_CONFIGURED", "message": "未配置模型端点：本次只用规则抽取"}
    for direction in hints["directions"]:
        if direction and direction not in directions:
            warnings.append(f"模型兜底提到的方向「{direction}」未写入画像（需你确认后再补）")

    return {
        "extraction": {
            "module": MODULE,
            "source": source,
            "filename": filename,
            "charCount": len(text),
            "sections": sorted(key for key, value in sections.items() if value.strip()),
            "llm": note,
        },
        "resumeId": fingerprint(text),
        "profileDraft": draft,
        "skills": skills,
        "unrecognizedSkills": unrecognized,
        "experiences": experiences,
        "evidence": evidence,
        "llmHints": hints,
        "warnings": warnings,
    }


def memory_candidates(result: Dict[str, Any], limit: int = 20) -> List[Dict[str, Any]]:
    """把解析结果投影成**待确认**记忆候选（规则版，不调模型）。

    与「画像 sync」同一套语义：技能用 ``具备或正在学习：`` 前缀（与
    ``MemoryStore.skill_name_from_memory`` 的白名单一致），目标职业用 ``目标职业：``，
    专业用 ``专业：``。每条都带 ``anchor`` 便于回查。
    """
    draft = result.get("profileDraft") or {}
    entries: List[Dict[str, Any]] = []
    for skill in result.get("skills") or []:
        entries.append({"category": "skill", "content": f"具备或正在学习：{skill['name']}", "anchor": skill["name"]})
    # `directions` 在画像草稿里是「、」连接的字符串（对齐 `PUT /api/profile` 的入参口径），
    # 这里要按分隔符拆开 —— 直接 for 一个字符串会逐字符产出一堆垃圾候选（实测踩过）。
    raw_directions = draft.get("directions")
    if isinstance(raw_directions, str):
        directions = [part.strip() for part in re.split(r"[、,，;；/]+", raw_directions) if part.strip()]
    else:
        directions = [str(part).strip() for part in (raw_directions or []) if str(part).strip()]
    for direction in dict.fromkeys(directions):
        entries.append({"category": "career_target", "content": f"目标职业：{direction}", "anchor": direction})
    if draft.get("major"):
        entries.append({"category": "background", "content": f"专业：{draft['major']}", "anchor": "major"})
    if draft.get("location"):
        entries.append({"category": "preference", "content": f"期望地点：{draft['location']}", "anchor": "location"})
    if draft.get("question"):
        entries.append({"category": "goal", "content": str(draft["question"]), "anchor": "objective"})
    return entries[:limit]
