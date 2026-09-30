"""最小可跑后端（Python 标准库 http.server）。

提供前端已经接入、且能从真实图谱导出投影出来的接口：

* ``GET  /health``                         —— 数据源 / 版本 / 计数自检
* ``GET  /api/profile``                    —— 读取画像（未建立时返回种子画像）
* ``PUT  /api/profile``                    —— 写入并标准化画像
* ``POST /api/profile/confirm``            —— 确认画像
* ``GET  /api/career/recommendations``     —— 岗位推荐（**不使用通用响应包**，按契约直出）
* ``GET  /api/v1/occupations``             —— 职业目录列表（通用响应包）
* ``GET  /api/v1/occupations/<id>``        —— 职业详情
* ``GET  /api/v1/skills``                  —— 技能目录列表
* ``GET  /api/v1/catalog/stats``           —— 目录统计
* ``GET  /api/memories``                   —— 记忆列表（候选/已确认）
* ``POST /api/memories``                   —— 写一条记忆
* ``PATCH/DELETE /api/memories/<id>``      —— 改 / 删除即遗忘
* ``POST /api/memories/sync``              —— 已确认画像 → 待确认记忆
* ``POST /api/memories/<id>/triggers``     —— 重建该记忆的触发器（规则版）
* ``GET  /api/memories/context?query=``    —— 本次提问要注入的记忆（persona 常驻 + 本次想起）
* ``POST /api/chat``                       —— 对话（**注入已确认记忆**；模型失败降级为规则版）
* ``GET/POST /api/growth-records``         —— 成长记录；写入时把记录投影成**待确认**记忆候选
* ``POST /api/resumes/extract``            —— 简历（文本/DOCX）→ 画像草稿 + 待确认记忆候选

未实现的接口（认证、简历解析之外的推送、路径生成、岗位匹配等）统一返回 501，
明确区分「契约已声明但本轮未实现」与「未知路由 404」，不假装可用。

记忆库只消费 ``status='confirmed'`` 的那部分：候选记忆永远不进入上下文与推荐
（见 ``backend/memories.py`` 顶部注释与 ``记忆系统整合方案.md``）。
"""

from __future__ import annotations

import itertools
import json
import os
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .chat import ChatService, UnknownChatGeneratorError
from .career_path import InvalidCareerPathInput, OccupationNotFound
from .career_path import generate as generate_career_path
from .growth import normalize_record as normalize_growth_record
from .growth import record_text as growth_record_text
from .knowledge import GraphStore, code_of, normalize
from .memories import VALID_GROWTH_KINDS, VALID_STATUSES, MemoryStore, UnknownGeneratorError
from .resume import MAX_FILE_BYTES, ResumeFormatError
from .resume import SUPPORTED_SUFFIXES, UNSUPPORTED_SUFFIXES, pdf_backend
from .resume import extract as extract_resume
from .resume import memory_candidates as resume_memory_candidates
from .resume import parse_multipart_form, read_upload

IDENTITY_MAP = {
    "在校生": "student",
    "学生": "student",
    "student": "student",
    "应届生": "graduate",
    "毕业生": "graduate",
    "graduate": "graduate",
    "职场新人": "newEmployee",
    "新员工": "newEmployee",
    "newemployee": "newEmployee",
    "转型探索中": "careerChanger",
    "转行": "careerChanger",
    "careerchanger": "careerChanger",
}
VALID_IDENTITIES = ("student", "graduate", "newEmployee", "careerChanger")

_SPLIT_RE = re.compile(r"[,，、;；\n]+")

NOT_IMPLEMENTED = (
    "/api/auth/register",
    "/api/auth/login",
    "/api/career-matches/current",
    "/api/career-matches/generate",
    "/api/career-matches/select",
)

# 会话 Cookie（M1-2）。访客登录后由服务端下发，前端带着它就能拿到自己的画像与记忆；
# 没有它（或值不合法）一律回落 ``user_local``，保证本机单用户形态与既有测试不受影响。
SESSION_COOKIE = "career_session"
_SESSION_RE = re.compile(r"^user_[0-9a-f]{12}$")
CSRF_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def split_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in _SPLIT_RE.split(str(value)) if part.strip()]


def _matches_names(needles: List[str], names: List[str]) -> bool:
    """方向词与节点名/别名做双向归一化包含匹配。

    正向（名字 ⊂ 方向词）覆盖「MCU 开发与固件」这类长方向词；
    反向（方向词 ⊂ 名字）覆盖「边缘AI」这类短方向词命中的长标签/能力域名。
    两侧都先经 ``normalize`` 去掉空白与连接符，避免「边缘 AI」被空格拆散。
    """
    for name in names:
        normalized = normalize(name)
        if not normalized:
            continue
        if any(normalized in needle or needle in normalized for needle in needles):
            return True
    return False


class ProfileStore:
    """画像存储：默认进程内；接上 ``MemoryStore`` 后落同一个 ``career.db``（M1-1）。

    此前是纯内存，后端一重启画像就归零（简历解析白做）；接上记忆库后读写透传到
    ``profiles`` 表，进程重建仍读得到。**不传 ``memories`` 时保持旧的纯内存行为**
    —— 既有单测正是这么构造的，不必改。把前端提交的表单标准化成 ``UserProfile``。
    """

    def __init__(self, store: GraphStore, memories: Optional[Any] = None,
                 user_id: str = "user_local") -> None:
        self._store = store
        self._memories = memories
        # 默认用户（无会话时的回落）。多用户时由 server 按 Cookie 传具体 user_id。
        self._user_id = user_id
        self._lock = threading.RLock()
        # 没接记忆库（单测）时的进程内缓存，按 user_id 分桶
        self._cache: Dict[str, Dict[str, Any]] = {}

    def _seed(self, user_id: str) -> Dict[str, Any]:
        return {
            "userId": user_id,
            "profileVersion": 0,
            "status": "draft",
            "identity": "student",
            "school": None,
            "major": None,
            "grade": None,
            "graduationYear": None,
            "location": None,
            "careerStage": "",
            "currentGoal": None,
            "interests": [],
            "skills": [],
            "experiences": [],
            "candidateOccupationIds": [],
            "source": "manual",
            "updatedAt": None,
        }

    def _resolve(self, user_id: Optional[str]) -> str:
        return user_id or self._user_id

    def _previous(self, uid: str) -> Dict[str, Any]:
        """写之前的上一版画像：接库就读库，没接就读进程内缓存，都没有就用种子。"""
        if self._memories is not None:
            saved = self._memories.get_profile(uid)
            if saved:
                return saved
            return self._seed(uid)
        if uid not in self._cache:
            self._cache[uid] = self._seed(uid)
        return self._cache[uid]

    def _store_profile(self, uid: str, profile: Dict[str, Any]) -> None:
        if self._memories is not None:
            self._memories.save_profile(uid, profile)
        else:
            self._cache[uid] = profile

    def get(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        uid = self._resolve(user_id)
        with self._lock:
            return json.loads(json.dumps(self._previous(uid)))

    def save(self, payload: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
        uid = self._resolve(user_id)
        with self._lock:
            profile = self._normalize(payload, self._previous(uid))
            self._store_profile(uid, profile)
            return json.loads(json.dumps(profile))

    def confirm(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        uid = self._resolve(user_id)
        with self._lock:
            profile = self._previous(uid)
            profile["status"] = "confirmed"
            profile["updatedAt"] = _now()
            self._store_profile(uid, profile)
            return json.loads(json.dumps(profile))

    # ------------------------------------------------------------------ 标准化
    def _normalize(self, payload: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
        raw_identity = str(payload.get("identity", "")).strip()
        identity = IDENTITY_MAP.get(raw_identity.lower(), IDENTITY_MAP.get(raw_identity))
        if identity not in VALID_IDENTITIES:
            identity = previous.get("identity", "student")

        skills = [{"name": name, "level": "unknown"} for name in split_list(payload.get("skills"))]
        experiences = []
        for index, title in enumerate(split_list(payload.get("experience")), start=1):
            experiences.append(
                {
                    "experienceId": f"experience_local_{index}",
                    "type": "project",
                    "title": title,
                    "description": title,
                }
            )

        directions = split_list(payload.get("directions"))
        source = str(payload.get("source", "manual")).strip()
        if source not in ("manual", "resume", "chat"):
            source = "manual"

        profile = {
            "userId": previous.get("userId", "user_local"),
            "profileVersion": int(previous.get("profileVersion", 0)) + 1,
            "status": "draft",
            "identity": identity,
            "school": payload.get("school") or None,
            "major": payload.get("major") or None,
            "grade": payload.get("grade") or None,
            "graduationYear": None,
            "location": payload.get("location") or None,
            "careerStage": str(payload.get("careerStage", "") or ""),
            "currentGoal": payload.get("question") or None,
            "interests": directions,
            "skills": skills,
            "experiences": experiences,
            "candidateOccupationIds": self._candidate_occupations(directions),
            "source": source,
            "updatedAt": _now(),
        }
        return profile

    def _candidate_occupations(self, directions: List[str]) -> List[str]:
        needles = [n for n in (normalize(d) for d in directions) if n]
        if not needles:
            return []
        hits = []
        for node in self._store.nodes_of_kind("occupation"):
            names = [node.get("label", "")]
            names.extend(node.get("aliases", []) or [])
            # 再带上「该职业所需技能所属能力域」的真实名称：方向词常常是能力域
            # （如「边缘AI」），只沿既有 requires / belongs_to 边取标签，不新增结论。
            names.extend(self._store.domain_labels_for(node["id"]))
            if _matches_names(needles, names):
                hits.append(code_of(node["id"]))
        return hits


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CareerApi:
    """与 HTTP 传输解耦的请求处理器，便于单元测试直接调用。"""

    def __init__(self, store: Optional[GraphStore] = None, profiles: Optional[ProfileStore] = None,
                 memories: Optional[MemoryStore] = None, chats: Optional[ChatService] = None) -> None:
        self.store = store or GraphStore()
        # 记忆库的触发器概念锚点优先取图谱里已有的名词（label/alias 查表），
        # 所以这里把图的名词表作为词表传进去；不传也能跑，只是锚点退化成字面切分。
        # LLM 客户端与评测侧共用同一组配置（CAREER_LLM_* > DEEPEVAL_* > knowledge/eval/.env）；
        # 不传就由 MemoryStore 自己按环境解析，`/health` 会如实报出配没配上。
        self.memories = memories or MemoryStore(vocabulary=self.store.terms_in)
        # 画像落**同一个** sqlite（M1-1）：后端重启后画像仍在（此前是进程内内存）。
        # 显式传 ``profiles`` 的调用方（单测）保持旧行为，不受影响。
        self.profiles = profiles or ProfileStore(self.store, memories=self.memories)
        # 对话：注入的正是上面这库里的已确认记忆；模型客户端复用同一个（一处配置两处消费）
        self.chats = chats or ChatService(self.store, self.memories, self.profiles)
        self._seq = itertools.count(1)

    # ------------------------------------------------------------------ 记忆库小工具
    def user_id_from(self, cookies: Optional[Dict[str, str]] = None) -> str:
        """当前用户：带合法会话 Cookie 就用它，否则回落本机单用户 ``user_local``。

        值不合法（被人手改过、伪造）时不报错、只当没有会话 —— 回落路径与旧版一致，
        不会因为一个坏 Cookie 就让整条链路 500。
        """
        value = str((cookies or {}).get(SESSION_COOKIE) or "")
        return value if _SESSION_RE.match(value) else "user_local"

    def user_id(self) -> str:
        """无 Cookie 上下文的旧入口（内部与单测用）。"""
        return str(self.profiles.get().get("userId") or "user_local")

    def occupation_label(self, code: str) -> str:
        node = self.store.node(f"occupation:{code}")
        return str(node.get("label", code)) if node else code

    def request_id(self) -> str:
        return f"req_{next(self._seq):06d}"

    def envelope(self, data: Any) -> Dict[str, Any]:
        return {"requestId": self.request_id(), "data": data, "error": None}

    def error(self, code: str, message: str, status: int = 400, **extra: Any) -> Tuple[int, Dict[str, Any]]:
        error: Dict[str, Any] = {"code": code, "message": message}
        error.update(extra)
        return status, {"requestId": self.request_id(), "data": None, "error": error}

    # ------------------------------------------------------------------ 路由
    def handle(self, method: str, path: str, query: Dict[str, List[str]], body: Any,
               raw: Optional[bytes] = None, content_type: str = "",
               cookies: Optional[Dict[str, str]] = None,
               response: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        """路由入口。

        * ``raw`` / ``content_type`` 只有上传类接口（简历）用得到，默认 ``None``，
          所以既有调用方（单元测试与 JSON 请求）不需要改。
        * ``cookies`` 是本请求的 Cookie（M1-2 会话隔离用）；不传即回落 ``user_local``。
        * ``response`` 是本请求专属的出参槽（形如 ``{"setCookie": "..."}``）。刻意用
          每请求新建的 dict 而不是实例属性：ThreadingHTTPServer 每个请求一个线程，
          实例属性会被并发请求互相覆盖。
        """
        method = method.upper()
        self.store.refresh()
        user_id = self.user_id_from(cookies)

        if path == "/health" and method == "GET":
            # 把模型状态放进 /health：不配端点时必须能一眼看见（否则会以为触发器在走模型）
            # 简历能力同样放进来：PDF 是「装了库就能解析」的可选能力，不写死一句「不支持」。
            backend = pdf_backend()
            return 200, {
                **self.store.health(),
                "llm": self.memories.describe_llm(),
                "resume": {
                    "pdfSupported": backend is not None,
                    "pdfBackend": backend,
                    "supportedSuffixes": list(SUPPORTED_SUFFIXES),
                    "unsupportedSuffixes": list(UNSUPPORTED_SUFFIXES),
                    "note": (
                        f"PDF 走本机已安装的 {backend}（不是硬依赖）；一个都没装时 PDF 返回 415 并给安装建议。"
                        if backend
                        else "本机没有 PDF 解析库（pypdf / PyMuPDF / pdfminer 都没装），PDF 上传返回 415 并给安装建议。"
                    ),
                },
            }

        if path == "/api/career/recommendations" and method == "GET":
            # 该接口按契约不使用通用响应包（见 frontend-backend-page-contract.md §7）。
            # 记忆是「输入增强」：只补画像空缺，不覆盖显式画像；用了哪几条逐条回传。
            augmented, evidence = self.memories.augment_profile(self.profiles.get(user_id), user_id)
            payload = self.store.recommendations(augmented)
            for row in payload["recommendations"]:
                row["confirmed_memory"] = evidence
            payload["memory_hash"] = self.memories.confirmed_hash(user_id)
            return 200, payload

        if path == "/api/memories":
            if method == "GET":
                status = _first(query, "status") or None
                if status is not None and status not in VALID_STATUSES:
                    return self.error("INVALID_MEMORY_STATUS", f"未知的记忆状态：{status}", 400)
                items = self.memories.list_items(user_id, status)
                return 200, self.envelope(
                    {"items": items, "count": len(items), "memoryHash": self.memories.confirmed_hash(user_id)}
                )
            if method == "POST":
                if not isinstance(body, dict):
                    return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
                try:
                    item = self.memories.create(
                        user_id,
                        category=str(body.get("category", "custom")),
                        content=str(body.get("content", "")),
                        importance=int(body.get("importance", 50)),
                        status=str(body.get("status", "confirmed")),
                    )
                except (ValueError, TypeError) as error:
                    return self.error("INVALID_MEMORY", str(error), 400)
                return 201, self.envelope({"item": item})

        if path == "/api/memories/context" and method == "GET":
            query_text = _first(query, "query") or _first(query, "q")
            return 200, self.envelope(self.memories.build_context(user_id, query_text))

        if path == "/api/memories/sync" and method == "POST":
            items = self.memories.sync_profile_candidates(
                user_id, self.profiles.get(user_id), self.occupation_label
            )
            return 200, self.envelope({"items": items, "count": len(items)})

        if path.startswith("/api/memories/") and path != "/api/memories/":
            rest = path[len("/api/memories/") :].strip("/")
            if rest.endswith("/triggers") and method == "POST":
                memory_id = rest[: -len("/triggers")].strip("/")
                # `generator`：auto（默认，配了模型就用模型）/ llm / rule-based
                generator = (_first(query, "generator") or "auto").strip()
                try:
                    triggers = self.memories.generate_triggers(user_id, memory_id, generator=generator)
                except LookupError:
                    return self.error("MEMORY_NOT_FOUND", f"未找到记忆：{memory_id}", 404)
                except UnknownGeneratorError as error:
                    return self.error("INVALID_GENERATOR", str(error), 400)
                except ValueError as error:
                    return self.error("MEMORY_NOT_CONFIRMED", str(error), 400)
                return 200, self.envelope(
                    {
                        "item": self.memories.get_item(user_id, memory_id, with_triggers=True),
                        "triggers": triggers,
                        "count": len(triggers),
                        # 这次到底走了模型还是规则版、失败原因是什么 —— 一并回传，不许静默
                        "generator": self.memories.last_trigger_note,
                    }
                )
            if method == "PATCH":
                if not isinstance(body, dict):
                    return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
                try:
                    updated = self.memories.update(user_id, rest, body)
                except (ValueError, TypeError) as error:
                    return self.error("INVALID_MEMORY", str(error), 400)
                if updated is None:
                    return self.error("MEMORY_NOT_FOUND", f"未找到记忆：{rest}", 404)
                # 带上触发器：确认那一刻生成的触发器要能被管理面板直接看到
                return 200, self.envelope({"item": self.memories.get_item(user_id, rest, with_triggers=True)})
            if method == "DELETE":
                if not self.memories.delete(user_id, rest):
                    return self.error("MEMORY_NOT_FOUND", f"未找到记忆：{rest}", 404)
                return 200, self.envelope(
                    {"deleted": rest, "memoryHash": self.memories.confirmed_hash(user_id)}
                )

        if path == "/api/chat" and method == "POST":
            # 对话：注入已确认记忆 + 图谱事实。`generator` 与触发器接口同一套取值
            # （auto 有模型就用 / llm 强制要模型 / rule-based 强制规则版）。
            # **模型不可用或调用失败都不报错**：降级成规则版答案，并在 llm.error 里说明原因。
            if not isinstance(body, dict):
                return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
            generator = (_first(query, "generator") or "auto").strip()
            try:
                payload = self.chats.reply(
                    user_id,
                    body.get("message"),
                    body.get("conversationId"),
                    generator=generator,
                )
            except UnknownChatGeneratorError as error:  # 注意顺序：它是 ValueError 的子类
                return self.error("INVALID_GENERATOR", str(error), 400)
            except ValueError as error:
                return self.error("INVALID_CHAT_MESSAGE", str(error), 400)
            return 200, self.envelope(payload)

        if path == "/api/growth-records":
            # 成长记录：这是记忆库第二条写入通道（第一条是画像 sync）。
            # 写入时**同一事务**落记录 + 待确认候选；候选只由图谱已知名词派生（规则版，不等模型）。
            if method == "GET":
                kind = _first(query, "kind") or None
                if kind is not None and kind not in VALID_GROWTH_KINDS:
                    return self.error("INVALID_GROWTH_KIND", f"未知的记录类别：{kind}", 400)
                raw_limit = _first(query, "limit")
                limit = int(raw_limit) if raw_limit.isdigit() else None
                try:
                    items = self.memories.list_growth_records(user_id, kind, limit)
                except ValueError as error:
                    return self.error("INVALID_GROWTH_KIND", str(error), 400)
                return 200, self.envelope(
                    {"items": items, "count": len(items), "memoryHash": self.memories.confirmed_hash(user_id)}
                )
            if method == "POST":
                if not isinstance(body, dict):
                    return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
                try:
                    record = normalize_growth_record(body)
                except ValueError as error:
                    return self.error("INVALID_GROWTH_RECORD", str(error), 400)
                nodes = self.store.nodes_in(growth_record_text(record))
                try:
                    payload = self.memories.create_growth_record(user_id, record, nodes)
                except ValueError as error:
                    return self.error("INVALID_GROWTH_RECORD", str(error), 400)
                return 201, self.envelope(payload)

        if path == "/api/growth-records/confirm" and method == "POST":
            # 候选 → 记录 → 证据 → 事件，一个事务（M1-3）。重复 confirm 幂等。
            if not isinstance(body, dict):
                return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
            record_id = str(body.get("recordId") or "").strip()
            if not record_id:
                return self.error("INVALID_BODY", "缺少 recordId", 400)
            raw_ids = body.get("memoryIds")
            if not isinstance(raw_ids, list) or not raw_ids:
                return self.error("INVALID_BODY", "memoryIds 必须是非空数组", 400)
            try:
                result = self.memories.confirm_growth_candidates(
                    user_id, record_id, [str(item) for item in raw_ids]
                )
            except LookupError as error:
                return self.error("GROWTH_RECORD_NOT_FOUND", str(error), 404)
            except ValueError as error:
                return self.error("INVALID_GROWTH_CONFIRM", str(error), 400)
            return 200, self.envelope(result)

        if path.startswith("/api/growth-records/") and path != "/api/growth-records/":
            record_id = path[len("/api/growth-records/") :].strip("/")
            if method == "GET":
                record = self.memories.get_growth_record(user_id, record_id)
                if record is None:
                    return self.error("GROWTH_RECORD_NOT_FOUND", f"未找到成长记录：{record_id}", 404)
                return 200, self.envelope({"record": record})
            if method == "DELETE":
                result = self.memories.delete_growth_record(user_id, record_id)
                if result is None:
                    return self.error("GROWTH_RECORD_NOT_FOUND", f"未找到成长记录：{record_id}", 404)
                return 200, self.envelope(result)

        if path == "/api/resumes/extract" and method == "POST":
            # 简历解析：multipart 上传（file）或 JSON {"text": "..."} 两条入口。
            # 只产出**画像草稿**与**待确认候选** —— 不写正式画像，用户在前端复核后再 PUT。
            # `?generator=rule-based` 可强制不调模型（与记忆触发器同一套取值）。
            generator = (_first(query, "generator") or "auto").strip()
            if generator not in ("auto", "llm", "rule-based"):
                return self.error("INVALID_GENERATOR", f"未知的生成器：{generator}；可选 auto, llm, rule-based", 400)
            return self._extract_resume(user_id, raw, content_type, body, generator)

        if path == "/api/auth/guest" and method == "POST":
            # 访客会话（M1-2）：下发 HttpOnly Cookie，之后的请求带着它就能拿到自己的
            # 画像/记忆/成长记录。**不落任何账号密码**，因此 register/login 仍是 501。
            display_name = ""
            if isinstance(body, dict):
                display_name = str(body.get("displayName") or "").strip()[:40]
            session_user = f"user_{uuid.uuid4().hex[:12]}"
            created = _now()
            if response is not None:
                response["setCookie"] = (
                    f"{SESSION_COOKIE}={session_user}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
                )
            return 201, self.envelope(
                {
                    "user": {
                        "userId": session_user,
                        "displayName": display_name or "体验用户",
                        "isGuest": True,
                        "createdAt": created,
                    }
                }
            )

        if path == "/api/profile":
            if method == "GET":
                return 200, self.envelope({"profile": self.profiles.get(user_id)})
            if method == "PUT":
                if not isinstance(body, dict):
                    return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
                return 200, self.envelope({"profile": self.profiles.save(body, user_id)})

        if path == "/api/profile/confirm" and method == "POST":
            return 200, self.envelope({"profile": self.profiles.confirm(user_id)})

        if path == "/api/v1/occupations" and method == "GET":
            keyword = _first(query, "keyword")
            return 200, self.envelope({"items": self.store.list_occupations(keyword)})

        if path.startswith("/api/v1/occupations/") and method == "GET":
            occupation_id = path[len("/api/v1/occupations/") :].strip()
            node = self.store.node(f"occupation:{occupation_id}")
            if node is None:
                return self.error("OCCUPATION_NOT_FOUND", f"未找到职业：{occupation_id}", 404)
            return 200, self.envelope({"occupation": self.store.occupation_detail(node)})

        if path == "/api/v1/skills" and method == "GET":
            keyword = _first(query, "keyword")
            return 200, self.envelope({"items": self.store.list_skills(keyword)})

        if path == "/api/v1/catalog/stats" and method == "GET":
            return 200, self.envelope(self.store.catalog_stats())

        if path == "/api/v1/career-path/generate" and method == "POST":
            # 路径引擎（M1-4）：差距与顺序由图谱 requires / prerequisite 边算出，模型不参与。
            # 输入没给 current_skills 时用**已确认/草稿画像**里的技能补（M1-6），
            # 并逐条回传用了什么，避免「个性化」只依赖前端硬编码。
            if not isinstance(body, dict):
                return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
            request_payload = dict(body)
            profile = self.profiles.get(user_id)
            request_payload["profile_used"] = bool(profile.get("skills"))
            try:
                result = generate_career_path(self.store, request_payload, profile=profile)
            except OccupationNotFound as error:
                return self.error("OCCUPATION_NOT_FOUND", str(error), 404)
            except InvalidCareerPathInput as error:
                return self.error("INVALID_CAREER_PATH_INPUT", str(error), 400)
            return 201, self.envelope(result)

        if path in NOT_IMPLEMENTED:
            return self.error(
                "NOT_IMPLEMENTED",
                f"{path} 属于契约中声明、但本轮最小后端未实现的接口。",
                501,
            )

        return self.error("NOT_FOUND", f"未知路由：{method} {path}", 404)

    # ------------------------------------------------------------------ 简历解析
    def _extract_resume(self, user_id: str, raw: Optional[bytes], content_type: str, body: Any,
                        generator: str) -> Tuple[int, Dict[str, Any]]:
        """解析上传/粘贴的简历，返回画像草稿 + 待确认候选。

        形态不支持（PDF / 图片 / .doc）一律 **415 + 可执行建议**，不假装解析成功；
        抽取出的内容只落 ``candidate``，授权闸门与画像 sync / 成长记录完全一致。
        """
        blob = raw or b""
        if len(blob) > MAX_FILE_BYTES:
            return self.error("RESUME_FILE_TOO_LARGE", f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)}MB 上限", 413)
        filename = ""
        use_llm = generator != "rule-based"
        try:
            if blob and "multipart/form-data" in str(content_type or ""):
                fields, filenames = parse_multipart_form(blob, content_type)
                filename = filenames.get("file", "")
                upload = fields.get("file") or fields.get("text") or b""
                if not upload:
                    return self.error("RESUME_BAD_UPLOAD", "multipart 里没有 file 或 text 字段", 400)
                text, source = read_upload(filename, upload)
                flag = fields.get("useLlm")
                if flag is not None:
                    use_llm = use_llm and flag.strip().lower() not in (b"0", b"false", b"off")
            elif isinstance(body, dict) and str(body.get("text") or "").strip():
                text, source = str(body["text"]), "text"
                filename = str(body.get("filename") or "pasted-resume.txt")
                if "useLlm" in body:
                    use_llm = use_llm and bool(body.get("useLlm"))
            else:
                return self.error(
                    "INVALID_BODY", '请上传 multipart 的 file 字段，或提交 JSON {"text": "..."}', 400
                )
        except ResumeFormatError as error:
            # 「这份上传内容我处理不了」一律 415 —— 包括乱码/扫描件/不支持的后缀。
            # 400 留给「请求本身不合法」（空请求体、坏 generator 之类），两者不能混。
            upload_codes = {
                "RESUME_FORMAT_UNSUPPORTED",
                "RESUME_ENCODING_UNSUPPORTED",
                "RESUME_EMPTY_TEXT",
                "RESUME_PDF_PARSE_FAILED",
                "RESUME_PDF_TEXT_UNREADABLE",
            }
            status = 415 if error.code in upload_codes else 400
            return self.error(error.code, str(error), status, suffix=error.suffix)

        try:
            result = extract_resume(
                raw_text=text, store=self.store, source=source, filename=filename,
                llm=self.memories.llm, use_llm=use_llm,
            )
        except ResumeFormatError as error:
            return self.error(error.code, str(error), 400)

        candidates = self.memories.sync_resume_candidates(
            user_id, result["resumeId"], resume_memory_candidates(result)
        )
        # 画像证据（M1-1）：每条抽取的出处落库，回答「学校=浙江大学」是从简历哪一段来的。
        # 定位不到的（located=False）不写：没有出处的证据等于没有证据。
        written_evidence = 0
        for item in result.get("evidence", []):
            if not item.get("located"):
                continue
            start, end = item.get("charRange", [-1, -1])
            self.memories.add_profile_evidence(
                user_id,
                source_type="resume",
                source_id=str(result["resumeId"]),
                field=str(item.get("field") or ""),
                value=str(item.get("value") or ""),
                locator=f"chars[{start},{end}]",
            )
            written_evidence += 1
        payload = dict(result)
        payload.update(
            {
                "memoryCandidates": candidates,
                "candidateCount": len(candidates),
                "evidenceCount": written_evidence,
                "memoryHash": self.memories.confirmed_hash(user_id),
                # 契约字段：原文件不留存（不落盘、不入库）
                "originalFileRetained": False,
            }
        )
        return 200, self.envelope(payload)


def _first(query: Dict[str, List[str]], key: str) -> str:
    values = query.get(key)
    return values[0] if values else ""


class CareerRequestHandler(BaseHTTPRequestHandler):
    server_version = "CareerNavigatorBackend/0.1.0"
    protocol_version = "HTTP/1.1"

    @property
    def api(self) -> CareerApi:
        return self.server.api  # type: ignore[attr-defined]

    def do_OPTIONS(self) -> None:  # noqa: N802 (stdlib 命名)
        self.send_response(204)
        self._write_cors()
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        # 原始字节也要给到 API 层：简历上传是 multipart，不能先按 JSON 解一遍
        raw = self._read_raw()
        response: Dict[str, Any] = {}
        status, payload = self.api.handle(
            method,
            parsed.path,
            query,
            self._parse_json(raw),
            raw=raw,
            content_type=self.headers.get("Content-Type") or "",
            cookies=self._parse_cookies(),
            response=response,
        )
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._write_cors()
        set_cookie = response.get("setCookie")
        if set_cookie:
            self.send_header("Set-Cookie", str(set_cookie))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _parse_cookies(self) -> Dict[str, str]:
        """解析 Cookie 头。不合法/没有就返回空表（API 层会回落 ``user_local``）。"""
        raw = self.headers.get("Cookie") or ""
        cookies: Dict[str, str] = {}
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name:
                cookies[name.strip()] = value.strip()
        return cookies

    def _read_raw(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    @staticmethod
    def _parse_json(raw: bytes) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _write_cors(self) -> None:
        origin = self.headers.get("Origin")
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        if origin:
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def log_message(self, fmt: str, *args: Any) -> None:  # 精简访问日志
        print(f"[backend] {self.address_string()} {fmt % args}", flush=True)


def create_server(host: str = "127.0.0.1", port: Optional[int] = None, store: Optional[GraphStore] = None,
                  memories: Optional[MemoryStore] = None) -> ThreadingHTTPServer:
    if port is None:
        port = int(os.environ.get("BACKEND_PORT") or os.environ.get("PORT") or 8000)
    httpd = ThreadingHTTPServer((host, port), CareerRequestHandler)
    httpd.daemon_threads = True
    httpd.api = CareerApi(store=store, memories=memories)  # type: ignore[attr-defined]
    return httpd


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="最小可跑职业导航后端（仅标准库）")
    parser.add_argument("--host", default=os.environ.get("BACKEND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BACKEND_PORT") or os.environ.get("PORT") or 8000))
    parser.add_argument("--export", default=None, help="覆盖知识图谱导出路径（等价于 CAREER_GRAPH_EXPORT）")
    args = parser.parse_args(argv)

    if args.export:
        os.environ["CAREER_GRAPH_EXPORT"] = str(Path(args.export))

    store = GraphStore()
    httpd = create_server(args.host, args.port, store=store)
    health = store.health()
    print(
        f"[backend] dataSource={health['dataSource']} kbVersion={health['kbVersion']} "
        f"graphVersion={health['graphVersion']} nodes={health['counts'].get('nodes')} "
        f"edges={health['counts'].get('edges')}",
        flush=True,
    )
    print(f"[backend] memoryDb={httpd.api.memories.path}", flush=True)
    llm = httpd.api.memories.describe_llm()
    if llm["configured"]:
        print(f"[backend] llm={llm['model']} @ {llm['host']}（来源 {llm['source']}）", flush=True)
    else:
        print("[backend] llm=未配置（记忆触发器走规则版；重排/裁判档不可用）", flush=True)
    print(f"[backend] listening on http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[backend] interrupted, shutting down", flush=True)
    finally:
        httpd.server_close()
    return 0
