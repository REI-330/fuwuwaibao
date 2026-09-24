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

未实现的接口（简历解析、聊天、路径生成、成长记录等）统一返回 501，
明确区分「契约已声明但本轮未实现」与「未知路由 404」，不假装可用。
"""

from __future__ import annotations

import itertools
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .knowledge import GraphStore, code_of, normalize

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
    "/api/auth/guest",
    "/api/auth/register",
    "/api/auth/login",
    "/api/resumes/extract",
    "/api/chat",
    "/api/v1/career-path/generate",
    "/api/career-matches/current",
    "/api/career-matches/generate",
    "/api/career-matches/select",
    "/api/growth-records",
)


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
    """进程内画像存储（无数据库），把前端提交的表单标准化成 ``UserProfile``。"""

    def __init__(self, store: GraphStore) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._profile = self._seed()

    def _seed(self) -> Dict[str, Any]:
        return {
            "userId": "user_local",
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

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._profile))

    def save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            previous = self._profile
            profile = self._normalize(payload, previous)
            self._profile = profile
            return json.loads(json.dumps(profile))

    def confirm(self) -> Dict[str, Any]:
        with self._lock:
            self._profile["status"] = "confirmed"
            self._profile["updatedAt"] = _now()
            return json.loads(json.dumps(self._profile))

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

    def __init__(self, store: Optional[GraphStore] = None, profiles: Optional[ProfileStore] = None) -> None:
        self.store = store or GraphStore()
        self.profiles = profiles or ProfileStore(self.store)
        self._seq = itertools.count(1)

    def request_id(self) -> str:
        return f"req_{next(self._seq):06d}"

    def envelope(self, data: Any) -> Dict[str, Any]:
        return {"requestId": self.request_id(), "data": data, "error": None}

    def error(self, code: str, message: str, status: int = 400, **extra: Any) -> Tuple[int, Dict[str, Any]]:
        error: Dict[str, Any] = {"code": code, "message": message}
        error.update(extra)
        return status, {"requestId": self.request_id(), "data": None, "error": error}

    # ------------------------------------------------------------------ 路由
    def handle(self, method: str, path: str, query: Dict[str, List[str]], body: Any) -> Tuple[int, Dict[str, Any]]:
        method = method.upper()
        self.store.refresh()

        if path == "/health" and method == "GET":
            return 200, self.store.health()

        if path == "/api/career/recommendations" and method == "GET":
            # 该接口按契约不使用通用响应包（见 frontend-backend-page-contract.md §7）。
            return 200, self.store.recommendations(self.profiles.get())

        if path == "/api/profile":
            if method == "GET":
                return 200, self.envelope({"profile": self.profiles.get()})
            if method == "PUT":
                if not isinstance(body, dict):
                    return self.error("INVALID_BODY", "请求体必须是 JSON 对象", 400)
                return 200, self.envelope({"profile": self.profiles.save(body)})

        if path == "/api/profile/confirm" and method == "POST":
            return 200, self.envelope({"profile": self.profiles.confirm()})

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

        if path in NOT_IMPLEMENTED:
            return self.error(
                "NOT_IMPLEMENTED",
                f"{path} 属于契约中声明、但本轮最小后端未实现的接口。",
                501,
            )

        return self.error("NOT_FOUND", f"未知路由：{method} {path}", 404)


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
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        body = self._read_body()
        status, payload = self.api.handle(method, parsed.path, query, body)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._write_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return None
        raw = self.rfile.read(length)
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


def create_server(host: str = "127.0.0.1", port: Optional[int] = None, store: Optional[GraphStore] = None) -> ThreadingHTTPServer:
    if port is None:
        port = int(os.environ.get("BACKEND_PORT") or os.environ.get("PORT") or 8000)
    httpd = ThreadingHTTPServer((host, port), CareerRequestHandler)
    httpd.daemon_threads = True
    httpd.api = CareerApi(store=store)  # type: ignore[attr-defined]
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
    print(f"[backend] listening on http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[backend] interrupted, shutting down", flush=True)
    finally:
        httpd.server_close()
    return 0
