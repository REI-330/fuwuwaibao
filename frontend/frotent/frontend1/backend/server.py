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
* ``POST /api/resumes/extract``            —— 简历（文本/DOCX/PDF）→ 画像草稿 + 待确认记忆候选
* ``GET  /api/resumes``                    —— 简历存档列表（供「简历面试」选取）
* ``GET  /api/v1/interview-skills``        —— 模拟面试可选岗位（来自职业目录）+ 自定义
* ``GET/POST /api/v1/interviews``          —— 模拟面试列表 / 新建（``requestId`` 幂等）
* ``GET/POST/DELETE /api/v1/interviews/<id>[/answers|/complete|/report]`` —— 作答与报告
* ``GET  /api/v1/cross-role/roles``        —— 跨岗位训练岗位（32 个 / 320 题）
* ``GET/POST /api/v1/cross-role/sessions`` —— 跨岗位训练列表 / 新建（``requestId`` 幂等）
* ``GET/POST/DELETE /api/v1/cross-role/sessions/<id>[/answers|/complete|/report]``
* ``GET  /api/tasks``                      —— 任务实践：从**路径引擎**派生的任务清单（不臆造）
* ``GET  /api/tasks/<taskId>``             —— 任务详情 + 历史提交
* ``POST /api/tasks/<taskId>/runs``        —— 提交行动（落成长记录 + **待确认**候选，``requestId`` 幂等）
* ``POST /api/task-runs/<runId>/evaluate`` —— 评估反馈（不落已确认能力）

未实现的接口统一返回 501（当前只剩账号密码相关的 `/api/auth/{register,login}` —— 本项目不存账号密码），
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
from urllib.parse import parse_qs, quote, urlparse

from .chat import ChatService, ChatStore, UnknownChatGeneratorError
from .career_path import InvalidCareerPathInput, OccupationNotFound
from .career_path import generate as generate_career_path
from .growth import normalize_record as normalize_growth_record
from .growth import record_text as growth_record_text
from .knowledge import GraphStore, code_of, normalize
from .memories import VALID_GROWTH_KINDS, VALID_STATUSES, MemoryStore, UnknownGeneratorError
from .resume import MAX_FILE_BYTES, ResumeFormatError
from .resume import SUPPORTED_SUFFIXES, UNSUPPORTED_SUFFIXES, pdf_backend
from .career_match import InsufficientProfile, generate_run, is_stale, known_occupation_ids
from .cross_role import MODES as CROSS_ROLE_MODES
from .cross_role import CrossRoleStore
from .cross_role import bank_metadata as cross_role_bank_metadata
from .cross_role import list_roles as list_cross_role_roles
from .interviews import DIFFICULTIES, MAX_ANSWER_CHARS, MAX_JD_CHARS, InterviewStore
from .resume import extract as extract_resume
from .resume_store import ResumeStore
from .tasks import DISCLAIMER as TASK_DISCLAIMER
from .tasks import TaskStore, derive_tasks
from .tasks import MAX_ACTION_CHARS as TASK_MAX_ACTION_CHARS
from .tasks import MAX_ATTACHMENT_BYTES as TASK_MAX_ATTACHMENT_BYTES
from .tasks import MAX_ATTACHMENTS as TASK_MAX_ATTACHMENTS
from .tasks import MAX_ATTACHMENTS_PER_TASK as TASK_MAX_ATTACHMENTS_PER_TASK
from .tasks import MAX_SUBMISSION_CHARS as TASK_MAX_SUBMISSION_CHARS
from .tasks import STATUS_AVAILABLE, STATUS_COMPLETED, STATUS_PLANNED
from .tasks import ATTACHMENT_EVIDENCE_SCOPE as TASK_ATTACHMENT_EVIDENCE_SCOPE
from .tasks import ATTACHMENT_NOTES as TASK_ATTACHMENT_NOTES
from .tasks import MAX_TASK_NOTE_CHARS as TASK_MAX_NOTE_CHARS
from .tasks import apply_task_overrides, classify_attachment, parse_task_id as parse_task_id_ref, resolve_attachment_ids
from .resume import memory_candidates as resume_memory_candidates
from .resume import parse_multipart_form, read_upload

# 成长记录分页：一次最多取多少条（前端默认 20）。不传 limit 时保持旧行为（一次给全部）。
GROWTH_MAX_PAGE = 200

# 画像历史快照分页：同口径。画像版本不会堆得像记忆那么多，50 足够。
PROFILE_HISTORY_MAX_PAGE = 50

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
        # 画像历史快照的进程内副本（仅在没接记忆库时用；接了库以库为准）
        self._history: Dict[str, List[Dict[str, Any]]] = {}

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
        """写当前态 + 留一份历史快照。快照是**只增不改**的：写失败也不该把当前态一起回滚掉，
        所以两支各自独立。"""
        if self._memories is not None:
            self._memories.save_profile(uid, profile)
            self._memories.save_profile_snapshot(uid, profile)
        else:
            self._cache[uid] = profile
            history = self._history.setdefault(uid, [])
            version = int(profile.get("profileVersion") or 0)
            status = str(profile.get("status") or "draft")
            snapshot_id = f"psnap_local_{version}_{status}"
            entry = {
                "snapshotId": snapshot_id,
                "profileVersion": version,
                "status": status,
                "capturedAt": str(profile.get("updatedAt") or ""),
                "summary": {
                    "identity": profile.get("identity"),
                    "school": profile.get("school"),
                    "major": profile.get("major"),
                    "currentGoal": profile.get("currentGoal"),
                    "skills": [item.get("name") for item in (profile.get("skills") or []) if isinstance(item, dict)],
                },
                "profile": json.loads(json.dumps(profile)),
            }
            history[:] = [item for item in history if item["snapshotId"] != snapshot_id]
            history.append(entry)

    def history(self, user_id: Optional[str] = None, limit: Optional[int] = None,
                offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
        """画像历史快照：**最新在前**。返回 ``(items, total)``；没接库时用进程内副本。"""
        uid = self._resolve(user_id)
        with self._lock:
            if self._memories is not None:
                items = self._memories.list_profile_snapshots(uid, limit, offset)
                total = self._memories.count_profile_snapshots(uid)
                return items, total
            ordered = list(reversed(self._history.get(uid, [])))
            total = len(ordered)
            window = ordered[offset:] if limit is None else ordered[offset:offset + limit]
            return [self._snapshot_summary(item) for item in window], total

    def snapshot(self, user_id: Optional[str], snapshot_id: str) -> Optional[Dict[str, Any]]:
        """取某一时刻的完整画像；不存在返回 ``None``。"""
        uid = self._resolve(user_id)
        with self._lock:
            if self._memories is not None:
                return self._memories.get_profile_snapshot(uid, snapshot_id)
            for item in self._history.get(uid, []):
                if item["snapshotId"] == snapshot_id:
                    return json.loads(json.dumps(item["profile"]))
            return None

    @staticmethod
    def _snapshot_summary(item: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in item.items() if key != "profile"}

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
                 memories: Optional[MemoryStore] = None, chats: Optional[ChatService] = None,
                 interviews: Optional[InterviewStore] = None,
                 cross_role: Optional[CrossRoleStore] = None,
                 resumes: Optional[ResumeStore] = None,
                 tasks: Optional[TaskStore] = None) -> None:
        self.store = store or GraphStore()
        # 记忆库的触发器概念锚点优先取图谱里已有的名词（label/alias 查表），
        # 所以这里把图的名词表作为词表传进去；不传也能跑，只是锚点退化成字面切分。
        # LLM 客户端与评测侧共用同一组配置（CAREER_LLM_* > DEEPEVAL_* > knowledge/eval/.env）；
        # 不传就由 MemoryStore 自己按环境解析，`/health` 会如实报出配没配上。
        self.memories = memories or MemoryStore(vocabulary=self.store.terms_in)
        # 画像落**同一个** sqlite（M1-1）：后端重启后画像仍在（此前是进程内内存）。
        # 显式传 ``profiles`` 的调用方（单测）保持旧行为，不受影响。
        self.profiles = profiles or ProfileStore(self.store, memories=self.memories)
        # 对话：注入的正是上面这库里的已确认记忆；模型客户端复用同一个（一处配置两处消费）。
        # 会话与消息落同一个 sqlite（2026-10-01 起）—— 后端重启后还能找回聊过什么。
        self.chats = chats or ChatService(self.store, self.memories, self.profiles, sessions=ChatStore())
        # 模拟面试 / 跨岗位训练 / 简历存档：与记忆库同一形态（本地 + SQLite + 同一把锁的读法），
        # 默认落同一个 career.db。模拟面试复用记忆库那个 LlmClient —— 一处配置两处消费，
        # `/health` 报的模型状态就是它；没配端点时出题/评分自动走规则版。
        self.interviews = interviews or InterviewStore(llm=self.memories.llm)
        self.cross_role = cross_role or CrossRoleStore()
        self.resumes = resumes or ResumeStore()
        # 任务实践：同样复用记忆库那个 LlmClient，并复用记忆库写「成长记录 + 待确认候选」那条通道。
        self.tasks = tasks or TaskStore(llm=self.memories.llm)
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
                # 会话是否落库：不落库时「重启后聊天记录还在不在」就是个能一眼看见的事实
                "chat": {"sessionsPersisted": self._chat_store() is not None},
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

        if path == "/api/chat/sessions" and method == "GET":
            return self._list_chat_sessions(user_id, query)

        if path.startswith("/api/chat/sessions/") and path != "/api/chat/sessions/" and method in ("GET", "DELETE"):
            return self._chat_session_action(user_id, method, path)

        if path == "/api/growth-records":
            # 成长记录：这是记忆库第二条写入通道（第一条是画像 sync）。
            # 写入时**同一事务**落记录 + 待确认候选；候选只由图谱已知名词派生（规则版，不等模型）。
            if method == "GET":
                kind = _first(query, "kind") or None
                if kind is not None and kind not in VALID_GROWTH_KINDS:
                    return self.error("INVALID_GROWTH_KIND", f"未知的记录类别：{kind}", 400)
                raw_limit = _first(query, "limit")
                # `cursor` 就是「已经看过多少条」的位移（分页键用 offset：排序里带了 record_id 兜底，
                # 同一时刻多条也不会错位）。不传 limit 时保持旧行为：一次给全部。
                raw_cursor = _first(query, "cursor") or _first(query, "offset")
                if raw_limit and not raw_limit.isdigit():
                    return self.error("INVALID_GROWTH_PAGE", f"limit 必须是整数：{raw_limit}", 400)
                if raw_cursor and not raw_cursor.isdigit():
                    return self.error("INVALID_GROWTH_PAGE", f"cursor 必须是整数位移：{raw_cursor}", 400)
                limit = min(int(raw_limit), GROWTH_MAX_PAGE) if raw_limit else None
                offset = int(raw_cursor) if raw_cursor else 0
                try:
                    items = self.memories.list_growth_records(user_id, kind, limit, offset)
                    total = self.memories.count_growth_records(user_id, kind)
                except ValueError as error:
                    return self.error("INVALID_GROWTH_KIND", str(error), 400)
                next_offset = offset + len(items)
                has_more = bool(limit) and next_offset < total
                return 200, self.envelope(
                    {
                        "items": items,
                        "count": len(items),
                        "total": total,
                        "limit": limit,
                        "offset": offset,
                        "nextCursor": str(next_offset) if has_more else None,
                        "hasMore": has_more,
                        "memoryHash": self.memories.confirmed_hash(user_id),
                    }
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

        if path == "/api/profile/history" and method == "GET":
            # 画像历史快照（2026-10-01 第七轮）：回答「上周那一刻的档案长什么样」。
            # 位移分页，口径与成长记录一致；快照只增不改，读接口不参与任何写入。
            raw_limit = _first(query, "limit")
            raw_cursor = _first(query, "cursor") or _first(query, "offset")
            if raw_limit and not raw_limit.isdigit():
                return self.error("INVALID_PROFILE_PAGE", f"limit 必须是整数：{raw_limit}", 400)
            if raw_cursor and not raw_cursor.isdigit():
                return self.error("INVALID_PROFILE_PAGE", f"cursor 必须是整数位移：{raw_cursor}", 400)
            limit = min(int(raw_limit), PROFILE_HISTORY_MAX_PAGE) if raw_limit else None
            offset = int(raw_cursor) if raw_cursor else 0
            items, total = self.profiles.history(user_id, limit, offset)
            next_offset = offset + len(items)
            has_more = bool(limit) and next_offset < total
            return 200, self.envelope(
                {
                    "items": items,
                    "count": len(items),
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "nextCursor": str(next_offset) if has_more else None,
                    "hasMore": has_more,
                }
            )

        if path.startswith("/api/profile/history/") and path != "/api/profile/history/":
            snapshot_id = path[len("/api/profile/history/") :].strip("/")
            if method == "GET":
                snapshot = self.profiles.snapshot(user_id, snapshot_id)
                if snapshot is None:
                    # 不存在 / 属于别人 —— 都回 404，不泄露「这个 id 存在但不是你的」
                    return self.error("PROFILE_SNAPSHOT_NOT_FOUND", f"未找到画像快照：{snapshot_id}", 404)
                return 200, self.envelope({"snapshotId": snapshot_id, "profile": snapshot})

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

        # ------------------------------------------------------------------ 职业匹配
        # 契约：types/contracts/career-match.ts。**不需要外部数据源** —— 排序依据全部来自
        # 图谱（requires 边带 importance / targetLevel）与已确认画像；算法与假设见
        # backend/career_match.py 的模块 docstring。
        if path == "/api/career-matches/current" and method == "GET":
            run = self.memories.get_match_run(user_id)
            if not run:
                # 前端对这两个码有专门处理：NOT_FOUND 直接去生成，STALE 重新生成
                return self.error("CAREER_MATCH_NOT_FOUND", "还没有生成过职业匹配结果。", 404)
            if is_stale(run, self.profiles.get(user_id), self.store):
                return self.error(
                    "CAREER_MATCH_STALE",
                    "画像或知识图谱已更新，旧结果不再适用，请重新生成。",
                    409,
                )
            return 200, self.envelope({"run": run})

        if path == "/api/career-matches/generate" and method == "POST":
            profile = self.profiles.get(user_id)
            try:
                run = generate_run(self.store, profile, user_id)
            except InsufficientProfile as error:
                # 画像太空时明确拒答，而不是生成一份全是 0 分的「匹配结果」
                return self.error("INSUFFICIENT_PROFILE", str(error), 409)
            self.memories.save_match_run(user_id, run)
            return 201, self.envelope({"run": run})

        if path == "/api/career-matches/select" and method == "POST":
            if not isinstance(body, dict):
                return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
            occupation_id = str(body.get("occupationId") or "").strip()
            if not occupation_id:
                return self.error("INVALID_BODY", "缺少 occupationId", 400)
            if occupation_id not in known_occupation_ids(self.store):
                return self.error("OCCUPATION_NOT_FOUND", f"图谱里没有职业 {occupation_id}", 404)
            run = self.memories.get_match_run(user_id) or {}
            self.memories.save_match_target(user_id, occupation_id, str(run.get("runId") or ""))
            return 200, self.envelope({"target": self.memories.get_match_target(user_id)})

        # ------------------------------------------------------------------ 模拟面试
        # 契约：types/contracts/interview.ts（移植自队友 career-ai-system，按本项目纪律重写）。
        # 用户一律来自会话 Cookie（无 Cookie 回落 user_local），所以这里没有 401 分支 ——
        # 本机单用户形态下不存在「未登录」，这一点与队友那版（真账号体系）不同。
        if path == "/api/v1/interview-skills" and method == "GET":
            occupation = self.store.list_occupations("")
            roles = [{
                "roleId": item["occupationId"],
                "name": item["targetJob"],
                "description": item.get("descriptionZh", ""),
                "coreSkills": item.get("coreSkills", []),
            } for item in occupation]
            roles.insert(0, {
                "roleId": "custom",
                "name": "自定义岗位",
                "description": "输入目标岗位名称和 JD，生成针对性问题。",
                "coreSkills": [],
            })
            return 200, self.envelope({"items": roles, "count": len(roles), "source": "graph"})

        if path == "/api/v1/interviews":
            if method == "GET":
                items = self.interviews.list_interviews(user_id)
                return 200, self.envelope({"items": items, "count": len(items)})
            if method == "POST":
                return self._create_interview(user_id, body)

        if path.startswith("/api/v1/interviews/") and method in ("GET", "POST", "DELETE"):
            return self._interview_action(user_id, method, path, body)

        # ------------------------------------------------------------------ 跨岗位沟通训练
        if path == "/api/v1/cross-role/roles" and method == "GET":
            items = list_cross_role_roles()
            return 200, self.envelope({**cross_role_bank_metadata(), "items": items, "count": len(items)})

        if path == "/api/v1/cross-role/sessions":
            if method == "GET":
                items = self.cross_role.list_sessions(user_id)
                return 200, self.envelope({"items": items, "count": len(items)})
            if method == "POST":
                return self._create_cross_role_session(user_id, body)

        if path.startswith("/api/v1/cross-role/sessions/") and method in ("GET", "POST", "DELETE"):
            return self._cross_role_action(user_id, method, path, body)

        # ------------------------------------------------------------------ 简历存档
        # 解析本身是纯函数（不落库），这里查的是「解析过的简历」，供模拟面试选取。
        if path == "/api/resumes" and method == "GET":
            items = self.resumes.list(user_id)
            return 200, self.envelope({"items": items, "count": len(items)})

        if path.startswith("/api/resumes/") and path != "/api/resumes/" and method == "GET":
            resume_id = path[len("/api/resumes/") :].strip("/")
            record = self.resumes.get(user_id, resume_id)
            if record is None:
                return self.error("RESUME_NOT_FOUND", f"未找到简历：{resume_id}", 404)
            return 200, self.envelope({"resume": record})

        # ------------------------------------------------------------------ 任务实践
        # 任务全部由路径引擎从图谱 `task --trains--> skill` 边派生（不臆造）；提交只产出
        # **待确认**候选（复用记忆库那条「成长记录 + 候选」通道），评估只给反馈、不落已确认能力。
        if path == "/api/tasks" and method == "GET":
            return self._list_tasks(user_id, query)

        if path == "/api/tasks/reorder" and method == "POST":
            return self._reorder_tasks(user_id, body)

        if path.startswith("/api/attachments/") and path != "/api/attachments/" and method in ("GET", "DELETE"):
            return self._attachment_action(user_id, method, path, response)

        if path.startswith("/api/task-runs/") and method == "POST":
            return self._evaluate_task_run(user_id, path)

        if path.startswith("/api/tasks/") and path != "/api/tasks/" and method in ("GET", "POST", "PATCH"):
            return self._task_action(user_id, method, path, body, raw=raw, content_type=content_type)

        if path in NOT_IMPLEMENTED:
            return self.error(
                "NOT_IMPLEMENTED",
                f"{path} 属于契约中声明、但本轮最小后端未实现的接口。",
                501,
            )

        return self.error("NOT_FOUND", f"未知路由：{method} {path}", 404)

    # ------------------------------------------------------------------ 对话会话（路由实现）
    def _chat_store(self) -> Optional[ChatStore]:
        """会话库。单测可能只构造 `ChatService`（不挂库），此时如实说「没有挂」。"""
        return getattr(self.chats, "sessions", None)

    def _list_chat_sessions(self, user_id: str, query: Dict[str, List[str]]) -> Tuple[int, Dict[str, Any]]:
        store = self._chat_store()
        if store is None:
            return 200, self.envelope({
                "items": [], "count": 0, "persisted": False,
                "note": "这次运行没有挂会话库（只有单测会这样），所以没有可列的历史会话",
            })
        raw_limit = _first(query, "limit")
        limit = int(raw_limit) if raw_limit.isdigit() else 50
        items = store.list_sessions(user_id, limit)
        return 200, self.envelope({"items": items, "count": len(items), "persisted": True})

    def _chat_session_action(self, user_id: str, method: str, path: str) -> Tuple[int, Dict[str, Any]]:
        store = self._chat_store()
        rest = path[len("/api/chat/sessions/") :].strip("/")
        session_id = [part for part in rest.split("/") if part]
        session_id_str = session_id[0] if session_id else ""
        if store is None or not session_id_str:
            return self.error("CHAT_SESSION_NOT_FOUND", f"没有找到这段对话：{rest}", 404)
        session = store.get_session(user_id, session_id_str)
        if session is None:
            # 别人的会话对我就是「不存在」
            return self.error("CHAT_SESSION_NOT_FOUND", f"没有找到这段对话：{session_id_str}", 404)
        if method == "GET":
            return 200, self.envelope({
                "session": session,
                "messages": store.messages(user_id, session_id_str),
            })
        return 200, self.envelope(store.delete_session(user_id, session_id_str))

    # ------------------------------------------------------------------ 任务实践（路由实现）
    def _task_landscape(self, user_id: str, query: Dict[str, List[str]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
        """算出这个用户当前的任务清单（**含被隐藏的**，调用方自己按 `hidden` 过滤），并说明「用的是哪个职业、从哪来」。

        职业优先级：`?occupation=` → 画像候选 → 目录第一个。用了哪一个会回传
        （`occupationSource`），不做静默回落。
        """
        requested = (_first(query, "occupation") or "").strip()
        profile = self.profiles.get(user_id) or {}
        occupation_id, source = requested, "query"
        if not occupation_id:
            candidates = [str(item) for item in (profile.get("candidateOccupationIds") or []) if str(item).strip()]
            if candidates:
                occupation_id, source = candidates[0], "profile"
        if not occupation_id:
            catalog = self.store.list_occupations("")
            if not catalog:
                raise OccupationNotFound("图谱里没有任何职业，无法派生任务")
            occupation_id, source = str(catalog[0]["occupationId"]), "catalog"
        path_result = generate_career_path(self.store, {"target_job": occupation_id}, profile=profile)
        overrides = self.tasks.task_overrides(user_id)
        dismissed = {task_id for task_id, item in overrides.items() if item.get("hidden")}
        tasks = derive_tasks(path_result, self.tasks.submitted_task_ids(user_id), dismissed)
        return path_result, apply_task_overrides(tasks, overrides, include_hidden=True), source

    def _list_tasks(self, user_id: str, query: Dict[str, List[str]]) -> Tuple[int, Dict[str, Any]]:
        status_filter = (_first(query, "status") or "").strip()
        if status_filter and status_filter not in (STATUS_PLANNED, STATUS_AVAILABLE, STATUS_COMPLETED):
            return self.error("INVALID_STATUS", f"未知的任务状态：{status_filter}", 400)
        include_hidden = (_first(query, "includeHidden") or "").strip() in ("1", "true", "yes")
        try:
            path_result, all_tasks, source = self._task_landscape(user_id, query)
        except OccupationNotFound as error:
            return self.error("OCCUPATION_NOT_FOUND", str(error), 404)
        except InvalidCareerPathInput as error:
            return self.error("INVALID_CAREER_PATH_INPUT", str(error), 400)
        visible = [task for task in all_tasks if not task["hidden"]]
        # `counts` 只数**看得见的**任务：否则界面会出现「有 1 条可做」却一条都点不出
        counts = {status: sum(1 for task in visible if task["status"] == status)
                  for status in (STATUS_AVAILABLE, STATUS_PLANNED, STATUS_COMPLETED)}
        shown = all_tasks if include_hidden else visible
        items = [task for task in shown if not status_filter or task["status"] == status_filter]
        return 200, self.envelope({
            "items": items,
            "count": len(items),
            "occupation": {"occupationId": path_result["occupation_id"], "targetJob": path_result["target_job"]},
            "occupationSource": source,
            "counts": counts,
            "includeHidden": include_hidden,
            "hiddenCount": sum(1 for task in all_tasks if task["hidden"]),
            # 图谱没有任务级难度/学时：这一条如实说出来，别让前端以为只是没取到
            "notes": [
                "任务与交付要求来自图谱的 task→skill 边；难度与任务级学时图谱未标注，返回 null",
                "estimatedHours 是**阶段级**投入（技能等级差 × 24 小时），不是这一条任务的耗时",
                "note / hidden / position 是你自己的视图覆盖层（PATCH /api/tasks/<id>），任务本身仍来自图谱",
            ],
            "disclaimer": TASK_DISCLAIMER,
        })

    def _find_task(self, user_id: str, task_id: str) -> Optional[Dict[str, Any]]:
        """按 taskId 找回任务（**含被隐藏的**：详情页仍然要能打开）。找不到返回 ``None``。"""
        try:
            occupation_id, _, _ = parse_task_id_ref(task_id)
        except ValueError:
            return None
        try:
            path_result = generate_career_path(
                self.store, {"target_job": occupation_id}, profile=self.profiles.get(user_id)
            )
        except (OccupationNotFound, InvalidCareerPathInput):
            return None
        overrides = self.tasks.task_overrides(user_id)
        dismissed = {known for known, item in overrides.items() if item.get("hidden")}
        tasks = derive_tasks(path_result, self.tasks.submitted_task_ids(user_id), dismissed)
        decorated = apply_task_overrides(tasks, overrides, include_hidden=True)
        return next((task for task in decorated if task["taskId"] == task_id), None)

    def _task_action(self, user_id: str, method: str, path: str, body: Any,
                     raw: Optional[bytes] = None, content_type: str = "") -> Tuple[int, Dict[str, Any]]:
        rest = path[len("/api/tasks/") :].strip("/")
        parts = [part for part in rest.split("/") if part]
        task_id = parts[0] if parts else ""
        action = parts[1] if len(parts) > 1 else ""
        if not task_id:
            return self.error("NOT_FOUND", f"未知路由：{method} {path}", 404)

        if not action and method == "GET":
            task = self._find_task(user_id, task_id)
            if task is None:
                return self.error("TASK_NOT_FOUND", f"没有找到任务：{task_id}", 404)
            runs = self.tasks.list_runs(user_id, task_id)
            attachments = self.tasks.list_attachments(user_id, task_id)
            return 200, self.envelope({
                "task": task,
                "runs": runs,
                "attachments": attachments,
                "attachmentCount": len(attachments),
                "runCount": len(runs),
                "latestFeedback": next((run["feedback"] for run in runs if run["feedback"]), None),
                "attachmentNotes": TASK_ATTACHMENT_NOTES,
                "disclaimer": TASK_DISCLAIMER,
            })

        if not action and method == "PATCH":
            return self._patch_task(user_id, task_id, body)

        if action == "runs" and method == "POST":
            return self._submit_task_run(user_id, task_id, body)

        if action == "attachments" and method == "POST":
            return self._upload_task_attachment(user_id, task_id, raw, content_type)

        return self.error("NOT_FOUND", f"未知路由：{method} {path}", 404)

    def _patch_task(self, user_id: str, task_id: str, body: Any) -> Tuple[int, Dict[str, Any]]:
        """改的是**你自己的视图**（备注 / 隐藏 / 顺序），不是任务本身。

        任务标题、交付要求、执行步骤、要求能力全部来自图谱的 `task --trains--> skill` 边 ——
        这里一个都不改，所以也没有「把任务改成自定义」的入口。
        `{"reset": true}` 可以把这条任务恢复成默认（连备注与顺序一起清掉）。
        """
        if not isinstance(body, dict):
            return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
        task = self._find_task(user_id, task_id)
        if task is None:
            return self.error("TASK_NOT_FOUND", f"没有找到任务：{task_id}", 404)

        if body.get("reset") is True:
            self.tasks.clear_task_override(user_id, task_id)
            return 200, self.envelope({
                "task": self._find_task(user_id, task_id),
                "override": None,
                "reset": True,
                "disclaimer": TASK_DISCLAIMER,
            })

        hidden = body.get("hidden")
        if hidden is not None and not isinstance(hidden, bool):
            return self.error("INVALID_BODY", "hidden 必须是布尔值", 422)
        note = body.get("note")
        if note is not None:
            note = str(note)
            if len(note) > TASK_MAX_NOTE_CHARS:
                return self.error("INVALID_BODY", f"备注超过 {TASK_MAX_NOTE_CHARS} 字", 422)
        position = body.get("position")
        clear_position = "position" in body and position is None
        if position is not None and (not isinstance(position, int) or isinstance(position, bool) or position < 0):
            return self.error("INVALID_BODY", "position 必须是非负整数（要清除顺序就传 null）", 422)

        override = self.tasks.set_task_override(
            user_id, task_id, hidden=hidden, note=note,
            position=position if isinstance(position, int) else None,
            clear_position=clear_position,
        )
        return 200, self.envelope({
            "task": self._find_task(user_id, task_id),
            "override": override,
            "reset": False,
            "notes": ["只改了你的视图：任务内容仍是图谱派生的那一份"],
            "disclaimer": TASK_DISCLAIMER,
        })

    def _reorder_tasks(self, user_id: str, body: Any) -> Tuple[int, Dict[str, Any]]:
        """重排任务：写的是 `position`，**不动任务内容**。

        只接受「你当前任务清单里真实存在」的 taskId —— 不认得的直接 422 点名，
        否则会出现给别人的任务、或已不存在的任务排序这种查不出处的情况。
        """
        if not isinstance(body, dict):
            return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
        raw_ids = body.get("taskIds")
        if not isinstance(raw_ids, list) or not raw_ids:
            return self.error("INVALID_BODY", "taskIds 必须是非空数组", 422)
        ordered = [str(item).strip() for item in raw_ids if str(item).strip()]
        if not ordered:
            return self.error("INVALID_BODY", "taskIds 里没有有效的任务 id", 422)
        scope: Dict[str, List[str]] = {}
        occupation = str(body.get("occupation") or "").strip()
        if occupation:
            scope["occupation"] = [occupation]
        try:
            _, tasks, _ = self._task_landscape(user_id, scope)
        except OccupationNotFound as error:
            return self.error("OCCUPATION_NOT_FOUND", str(error), 404)
        except InvalidCareerPathInput as error:
            return self.error("INVALID_CAREER_PATH_INPUT", str(error), 400)
        known = {task["taskId"] for task in tasks}
        unknown = [task_id for task_id in ordered if task_id not in known]
        if unknown:
            return self.error(
                "UNKNOWN_TASK",
                "这些 taskId 不在你当前的任务清单里：" + "、".join(unknown[:3]),
                422,
                unknownTaskIds=unknown,
            )
        updated = self.tasks.set_task_positions(user_id, ordered)
        _, after, source = self._task_landscape(user_id, scope)
        return 200, self.envelope({
            "ordered": [task["taskId"] for task in after],
            "updated": updated,
            "occupationSource": source,
            "notes": ["顺序只影响你自己的清单显示，不改变任务内容与完成状态"],
            "disclaimer": TASK_DISCLAIMER,
        })

    def _attachment_action(self, user_id: str, method: str, path: str,
                           response: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        """附件三件事：看元数据 / 下原始字节 / 删除。归属不符一律按「不存在」处理。"""
        rest = path[len("/api/attachments/") :].strip("/")
        parts = [part for part in rest.split("/") if part]
        attachment_id = parts[0] if parts else ""
        suffix = parts[1] if len(parts) > 1 else ""
        if not attachment_id:
            return self.error("NOT_FOUND", f"未知路由：{method} {path}", 404)

        if suffix == "content" and method == "GET":
            blob = self.tasks.attachment_content(user_id, attachment_id)
            if blob is None:
                return self.error("ATTACHMENT_NOT_FOUND", f"没有找到这个附件：{attachment_id}", 404)
            if response is not None:
                # 原始字节走 response 的 rawBody 通道：这条响应不是 JSON，不能先 json.dumps
                response["rawBody"] = {
                    "content": blob["content"],
                    "contentType": blob["contentType"],
                    "contentDisposition": (
                        "attachment; filename*=UTF-8''" + quote(blob["filename"], safe="")
                    ),
                }
            return 200, self.envelope({
                "attachmentId": attachment_id,
                "filename": blob["filename"],
                "byteSize": len(blob["content"]),
            })

        if suffix and suffix != "content":
            return self.error("NOT_FOUND", f"未知路由：{method} {path}", 404)

        if method == "GET":
            record = self.tasks.get_attachment(user_id, attachment_id)
            if record is None:
                return self.error("ATTACHMENT_NOT_FOUND", f"没有找到这个附件：{attachment_id}", 404)
            # 刻意**不**回传原始字节：元数据走 JSON，字节走 /content
            return 200, self.envelope({"attachment": record, "note": TASK_ATTACHMENT_EVIDENCE_SCOPE})

        if method == "DELETE":
            referenced = self.tasks.runs_referencing_attachment(user_id, attachment_id)
            if referenced:
                # 引用可追溯是这条链路的前提：还挂在运行记录上的附件不能删，
                # 否则 `attachmentIds` 里就留下查不到出处的 id。
                return self.error(
                    "ATTACHMENT_IN_USE",
                    f"这个附件还被 {len(referenced)} 次提交引用着，不能删：{'、'.join(referenced[:3])}",
                    409,
                    referencedRunIds=referenced,
                )
            removed = self.tasks.delete_attachment(user_id, attachment_id)
            if removed is None:
                return self.error("ATTACHMENT_NOT_FOUND", f"没有找到这个附件：{attachment_id}", 404)
            return 200, self.envelope({
                "deleted": removed["attachmentId"],
                "taskId": removed["taskId"],
                "attachmentCount": removed["taskAttachmentCount"],
                "sha256": removed["sha256"],
            })

        return self.error("NOT_FOUND", f"未知路由：{method} {path}", 404)

    def _upload_task_attachment(self, user_id: str, task_id: str, raw: Optional[bytes],
                                content_type: str) -> Tuple[int, Dict[str, Any]]:
        """任务附件上传（multipart）。

        与简历解析的差别写在这里，免得后来人以为两处不一致是 bug：
        **附件存得下就存**（图片/压缩包/xlsx 照样收），只是「有没有抽出文字」如实回传；
        拒绝只发生在「请求不合法」或「超限」—— 不拿 415 挡掉正常的交付物。
        """
        task = self._find_task(user_id, task_id)
        if task is None:
            return self.error("TASK_NOT_FOUND", f"没有找到任务：{task_id}", 404)

        blob = raw or b""
        if not blob:
            return self.error("ATTACHMENT_BAD_UPLOAD", "请求体是空的：请用 multipart/form-data 上传 file 字段", 400)
        if "multipart/form-data" not in str(content_type or ""):
            return self.error(
                "ATTACHMENT_BAD_UPLOAD",
                "附件上传必须是 multipart/form-data（字段名 file）；JSON 只能提交文本成果",
                400,
            )
        try:
            fields, filenames = parse_multipart_form(blob, content_type)
        except ResumeFormatError as error:
            return self.error("ATTACHMENT_BAD_UPLOAD", str(error), 400)
        upload = fields.get("file") or b""
        if not upload:
            return self.error("ATTACHMENT_BAD_UPLOAD", "multipart 里没有 file 字段", 400)
        if len(upload) > TASK_MAX_ATTACHMENT_BYTES:
            return self.error(
                "ATTACHMENT_TOO_LARGE",
                f"附件超过 {TASK_MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB 上限",
                413,
            )
        filename = str(filenames.get("file") or fields.get("filename", b"").decode("utf-8", "replace") or "attachment").strip()
        filename = filename.replace("\\", "/").split("/")[-1][:200] or "attachment"

        existing_count = self.tasks.count_attachments(user_id, task_id)
        known = {item["attachmentId"]: item for item in self.tasks.list_attachments(user_id, task_id)}
        # 幂等：同一个文件重复上传不算新增，先看是不是已经存过（否则会被数量上限误挡）
        same = next((item for item in known.values()
                     if item["filename"] == filename and item["byteSize"] == len(upload)), None)
        if same is None and existing_count >= TASK_MAX_ATTACHMENTS_PER_TASK:
            return self.error(
                "ATTACHMENT_LIMIT",
                f"这条任务下最多存 {TASK_MAX_ATTACHMENTS_PER_TASK} 个附件，请先删掉不用的",
                422,
            )

        classified = classify_attachment(filename, upload)
        record = self.tasks.add_attachment(
            user_id=user_id,
            task_id=task_id,
            filename=filename,
            content_type=str(content_type).split(";")[0].strip(),
            data=upload,
            kind=classified["kind"],
            preview=classified["preview"],
            text_extracted=classified["textExtracted"],
            note=classified["note"],
            preview_truncated=classified["previewTruncated"],
        )
        attachments = self.tasks.list_attachments(user_id, task_id)
        return 201, self.envelope({
            "attachment": record,
            "attachments": attachments,
            "attachmentCount": len(attachments),
            "created": bool(record.get("created")),
            "limits": {
                "maxBytes": TASK_MAX_ATTACHMENT_BYTES,
                "maxPerTask": TASK_MAX_ATTACHMENTS_PER_TASK,
                "maxPerRun": TASK_MAX_ATTACHMENTS,
            },
            "attachmentNotes": TASK_ATTACHMENT_NOTES,
            "disclaimer": TASK_DISCLAIMER,
        })

    def _submit_task_run(self, user_id: str, task_id: str, body: Any) -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict):
            return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
        submission = str(body.get("submission") or "").strip()
        if not submission:
            return self.error("INVALID_BODY", "submission（文本成果）不能为空", 422)
        if len(submission) > TASK_MAX_SUBMISSION_CHARS:
            return self.error("INVALID_BODY", f"submission 超过 {TASK_MAX_SUBMISSION_CHARS} 字", 422)
        action_text = str(body.get("action") or "").strip()
        if len(action_text) > TASK_MAX_ACTION_CHARS:
            return self.error("INVALID_BODY", f"action 超过 {TASK_MAX_ACTION_CHARS} 字", 422)
        raw_attachments = body.get("attachmentIds") or []
        if not isinstance(raw_attachments, list) or len(raw_attachments) > TASK_MAX_ATTACHMENTS:
            return self.error("INVALID_BODY", f"attachmentIds 必须是最多 {TASK_MAX_ATTACHMENTS} 项的数组", 422)
        attachments = [str(item) for item in raw_attachments if str(item).strip()]

        # `requestId` 必须**先查**：否则重试会先写出一条成长记录再被幂等挡住，白留一条记录。
        request_id = str(body.get("requestId") or "").strip() or None
        if request_id:
            existing = self.tasks.find_by_request(user_id, request_id)
            if existing:
                return 201, self.envelope({
                    "run": existing,
                    "growthRecord": self.memories.get_growth_record(user_id, existing["growthRecordId"] or ""),
                    "candidates": [],
                    "candidateNote": {"reason": "duplicate_request",
                                      "message": "同一 requestId 已提交过：按幂等处理，没有重复写记录与候选"},
                    "created": False,
                    "disclaimer": TASK_DISCLAIMER,
                })

        task = self._find_task(user_id, task_id)
        if task is None:
            return self.error("TASK_NOT_FOUND", f"没有找到任务：{task_id}", 404)

        # 附件不再是「随便什么字符串」：必须是这个人在**这条任务**下真上传过的。
        # 未知 id / 别人的 id / 挂在别的任务下的 id 都回 422 并点名，不静默忽略 ——
        # 静默忽略会让运行记录里的 attachmentIds 变成查不到出处的引用。
        known = self.tasks.attachment_index(user_id, attachments)
        usable, unknown = resolve_attachment_ids(user_id, task_id, attachments, known)
        if unknown:
            return self.error(
                "UNKNOWN_ATTACHMENT",
                "这些 attachmentIds 不是这条任务下你上传的附件，请先上传再引用：" + "、".join(unknown),
                422,
                unknownAttachmentIds=unknown,
            )
        attachments = usable

        run_id = TaskStore.new_run_id()
        record = {
            "kind": "任务行动",
            "title": f"完成实践任务：{task['title']}"[:200],
            "before": "",
            "after": submission[:1000],
            "explanation": (
                f"来源：{task['sourcePath']['targetJob']} · {task['sourcePath']['stageName']}阶段。"
                + (f"行动说明：{action_text[:200]}" if action_text else "")
            ),
            "source": "职场模拟",
            "recordId": run_id,
        }
        # 复用既有通道：一条成长记录 + 由图谱名词派生的**待确认**候选（同一事务、按 recordId 幂等）
        try:
            stored = self.memories.create_growth_record(
                user_id, record, self.store.nodes_in(growth_record_text(normalize_growth_record(record)))
            )
        except ValueError as error:
            return self.error("INVALID_GROWTH_RECORD", str(error), 400)

        run = self.tasks.create_run(
            run_id=run_id,
            user_id=user_id,
            task=task,
            action=action_text,
            submission=submission,
            attachment_ids=attachments,
            growth_record_id=str(stored["record"]["id"]),
            candidate_ids=[str(item["id"]) for item in stored["candidates"]],
            request_id=request_id,
        )
        return 201, self.envelope({
            "run": run,
            "growthRecord": stored["record"],
            "candidates": stored["candidates"],
            "candidateNote": stored["note"],
            "created": True,
            "disclaimer": TASK_DISCLAIMER,
        })

    def _evaluate_task_run(self, user_id: str, path: str) -> Tuple[int, Dict[str, Any]]:
        rest = path[len("/api/task-runs/") :].strip("/")
        parts = [part for part in rest.split("/") if part]
        run_id = parts[0] if parts else ""
        action = parts[1] if len(parts) > 1 else ""
        if not run_id or action != "evaluate":
            return self.error("NOT_FOUND", f"未知路由：POST {path}", 404)
        run = self.tasks.own_run(user_id, run_id)
        if run is None:
            return self.error("TASK_RUN_NOT_FOUND", f"没有找到这次提交：{run_id}", 404)
        task = self._find_task(user_id, run["taskId"])
        if task is None:
            # 路径变了、这条任务已经不在了：用运行记录里存下的最小信息兜底，
            # 至少还能给反馈，而不是报 404 把用户的历史提交变成孤儿。
            task = {
                "taskId": run["taskId"],
                "title": run["title"],
                "deliverable": run["deliverable"],
                "requiredSkills": [],
                "steps": [],
                "evidenceTargets": [],
                "sourcePath": {"stageName": run["stage"], "stageGoal": "", "occupationId": run["occupationId"]},
            }
        report = self.tasks.evaluate(task, run)
        return 200, self.envelope({
            "report": report,
            "run": self.tasks.get_run(user_id, run_id),
            "taskAvailable": self._find_task(user_id, run["taskId"]) is not None,
        })

    # ------------------------------------------------------------------ 模拟面试（路由实现）
    def _create_interview(self, user_id: str, body: Any) -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict):
            return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
        role_id = str(body.get("roleId") or "").strip()
        if not role_id:
            return self.error("INVALID_BODY", "缺少 roleId", 400)
        role_name = str(body.get("roleName") or "").strip()
        if role_id == "custom":
            if not role_name:
                return self.error("ROLE_NAME_REQUIRED", "自定义面试需要填写岗位名称", 422)
        else:
            node = self.store.node(f"occupation:{role_id}")
            if node is None:
                return self.error("ROLE_NOT_FOUND", "没有找到这个面试岗位", 404)
            role_name = str(node.get("label") or role_id)

        difficulty = str(body.get("difficulty") or "mid")
        if difficulty not in DIFFICULTIES:
            return self.error("INVALID_DIFFICULTY", f"未知难度：{difficulty}；可选 {', '.join(DIFFICULTIES)}", 422)
        try:
            question_count = int(body.get("questionCount", 5))
        except (TypeError, ValueError):
            return self.error("INVALID_BODY", "questionCount 必须是整数", 422)
        if not 3 <= question_count <= 10:
            return self.error("INVALID_BODY", "questionCount 必须在 3—10 之间", 422)
        jd_text = str(body.get("jdText") or "").strip()
        if len(jd_text) > MAX_JD_CHARS:
            return self.error("INVALID_BODY", f"jdText 超过 {MAX_JD_CHARS} 字", 422)

        resume_id = str(body.get("resumeId") or "").strip() or None
        resume_context = None
        if resume_id:
            resume_context = self.resumes.interview_context(user_id, resume_id)
            if not resume_context:
                return self.error("RESUME_NOT_FOUND", "没有找到可用于面试的简历", 404)

        session = self.interviews.create_interview(
            user_id=user_id,
            role_id=role_id,
            role_name=role_name,
            difficulty=difficulty,
            question_count=question_count,
            jd_text=jd_text or None,
            resume_id=resume_id,
            resume_filename=resume_context["filename"] if resume_context else None,
            resume_text=None,
            resume_analysis=resume_context["analysis"] if resume_context else None,
            request_id=str(body.get("requestId") or "").strip() or None,
        )
        return 201, self.envelope({"session": session})

    def _interview_action(self, user_id: str, method: str, path: str, body: Any) -> Tuple[int, Dict[str, Any]]:
        rest = path[len("/api/v1/interviews/") :].strip("/")
        parts = [part for part in rest.split("/") if part]
        session_id = parts[0] if parts else ""
        action = parts[1] if len(parts) > 1 else ""
        if not session_id:
            return self.error("NOT_FOUND", f"未知路由：{method} {path}", 404)
        session = self.interviews.get_owned_session(user_id, session_id)
        if session is None:
            return self.error("INTERVIEW_NOT_FOUND", "面试记录不存在", 404)

        if not action and method == "GET":
            return 200, self.envelope({"session": self.interviews.session_to_dict(session)})
        if not action and method == "DELETE":
            self.interviews.delete_interview(session)
            return 200, self.envelope({"deleted": True})
        if action == "answers" and method == "POST":
            if not isinstance(body, dict):
                return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
            question_id = str(body.get("questionId") or "").strip()
            answer = str(body.get("answer") or "").strip()
            if not question_id or not answer:
                return self.error("INVALID_BODY", "questionId 与 answer 都不能为空", 422)
            if len(answer) > MAX_ANSWER_CHARS:
                return self.error("INVALID_BODY", f"answer 超过 {MAX_ANSWER_CHARS} 字", 422)
            if session["status"] in ("COMPLETED", "EVALUATED"):
                return self.error("INTERVIEW_FINISHED", "面试已经交卷，不能继续修改答案", 409)
            updated = self.interviews.save_answer(session, question_id, answer)
            if updated is None:
                return self.error("QUESTION_NOT_FOUND", "题目不属于当前面试", 404)
            return 200, self.envelope({"session": updated})
        if action == "complete" and method == "POST":
            return 200, self.envelope({"report": self.interviews.complete_interview(user_id, session)})
        if action == "report" and method == "GET":
            report = self.interviews.read_report(session_id)
            if report is None:
                return self.error("REPORT_NOT_READY", "面试尚未完成评估", 409)
            return 200, self.envelope({"report": report})
        return self.error("NOT_FOUND", f"未知路由：{method} {path}", 404)

    # ------------------------------------------------------------------ 跨岗位训练（路由实现）
    def _create_cross_role_session(self, user_id: str, body: Any) -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict):
            return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
        role_id = str(body.get("roleId") or "").strip()
        if not role_id:
            return self.error("INVALID_BODY", "缺少 roleId", 400)
        mode = str(body.get("mode") or "practice")
        if mode not in CROSS_ROLE_MODES:
            return self.error("INVALID_MODE", f"未知模式：{mode}；可选 {', '.join(CROSS_ROLE_MODES)}", 422)
        try:
            session = self.cross_role.create_session(
                user_id=user_id, role_id=role_id, mode=mode,
                request_id=str(body.get("requestId") or "").strip() or None,
            )
        except ValueError as error:
            if str(error) == "ROLE_NOT_FOUND":
                return self.error("ROLE_NOT_FOUND", "没有找到这个训练岗位", 404)
            raise
        return 201, self.envelope({"session": session})

    def _cross_role_action(self, user_id: str, method: str, path: str, body: Any) -> Tuple[int, Dict[str, Any]]:
        rest = path[len("/api/v1/cross-role/sessions/") :].strip("/")
        parts = [part for part in rest.split("/") if part]
        session_id = parts[0] if parts else ""
        action = parts[1] if len(parts) > 1 else ""
        if not session_id:
            return self.error("NOT_FOUND", f"未知路由：{method} {path}", 404)
        session = self.cross_role.get_owned_session(user_id, session_id)
        if session is None:
            return self.error("SESSION_NOT_FOUND", "训练记录不存在", 404)

        if not action and method == "GET":
            return 200, self.envelope({"session": self.cross_role.session_to_dict(session)})
        if not action and method == "DELETE":
            self.cross_role.delete_session(session)
            return 200, self.envelope({"deleted": True})
        if action == "answers" and method == "POST":
            if not isinstance(body, dict):
                return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
            question_id = str(body.get("questionId") or "").strip()
            option_id = str(body.get("optionId") or "").strip()
            if not question_id or not option_id:
                return self.error("INVALID_BODY", "questionId 与 optionId 都不能为空", 422)
            if session["status"] == "COMPLETED":
                return self.error("SESSION_FINISHED", "训练已经完成，不能继续修改答案", 409)
            try:
                updated = self.cross_role.save_answer(session, question_id, option_id)
            except ValueError as error:
                if str(error) == "OPTION_NOT_FOUND":
                    return self.error("OPTION_NOT_FOUND", "所选答案不属于这道题", 422)
                raise
            if updated is None:
                return self.error("QUESTION_NOT_FOUND", "题目不属于当前训练", 404)
            return 200, self.envelope({"session": updated})
        if action == "complete" and method == "POST":
            return 200, self.envelope({"report": self.cross_role.complete_session(session)})
        if action == "report" and method == "GET":
            report = self.cross_role.read_report(session)
            if report is None:
                return self.error("REPORT_NOT_READY", "请先完成训练", 409)
            return 200, self.envelope({"report": report})
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
        # 存档（供模拟面试按 resumeId 取回本次解析结果）。**只存解析结果，不存文件字节**：
        # `originalFileRetained` 仍然是 false，这一条不改口径。
        try:
            resume_summary = self.resumes.save(
                user_id,
                result,
                filename=filename,
                content_type=content_type or "text/plain",
                file_size=len(blob) or len(text.encode("utf-8")),
                text=text,
            )
        except ValueError:
            resume_summary = None
        payload.update(
            {
                "resume": resume_summary,
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
        raws = response.get("rawBody")
        if raws is not None:
            # 非 JSON 响应（附件原字节下载）：不经 json.dumps
            data = bytes(raws.get("content") or b"")
            self.send_response(status)
            self._write_cors()
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
            self.send_header("Content-Type", str(raws.get("contentType") or "application/octet-stream"))
            if raws.get("contentDisposition"):
                self.send_header("Content-Disposition", str(raws["contentDisposition"]))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
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
