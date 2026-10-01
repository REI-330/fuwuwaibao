"""对话（``POST /api/chat``）：把「已确认记忆」真正注入一轮问答。

这是记忆系统整合里最后一块「消费端」—— 在此之前记忆只有**预览**（`GET /api/memories/context`），
没有任何一条真实对话用到它。本文件把三样东西拼成一次回答：

1. **已确认记忆**（`MemoryStore.build_context` 的产物：persona 常驻 + 本次想起）；
2. **图谱事实**（`GraphStore` 沿既有边算出的要求技能 / 缺口 / 能力域，不新增结论）；
3. **模型**（`backend/llm.py`；没配端点或调用失败时降级为规则版，**绝不报错给前端**）。

两条纪律来自队友实现的设计（`_prepare_query`），这里是它们的落点，并被测试钉住：

* **无记忆时不改写提问**：检索用的 query 就是用户原话（只去掉首尾空白、不做扩写或改写），
  注入块缺席时提示词就是「固定系统前缀 + 图谱事实 + 用户原文」。空记忆与空上下文都只是
  「块不存在」，不会插入空标题或占位符 —— 否则查询会被静默污染，事后无法复盘。
* **注入是固定可审计前缀**：正文只由模块级常量模板与上述产物拼接；响应里回传
  `injected.memoryIds / memoryHash / prefixBytes / promptTemplate`，
  外部可以复核「这次到底把什么喂给了模型」，也能看出模型有没有被用上
  （`provider` / `llm.used` / 降级时的 `llm.error`）。

规则版兜底不是摆设：它是**没有模型也能用**的那条路 —— 答案只由图谱事实与已确认记忆拼出来，
并且**如实标注**自己是规则版（`provider=rule-based`），不许假装成模型生成。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .knowledge import OCCUPATION, SKILL, GraphStore, code_of, normalize
from .llm import DEFAULT_MAX_TOKENS, LlmClient, LlmError
from .memories import DEFAULT_DB_ENV, MemoryStore

PROMPT_TEMPLATE = "career-chat/v1"

# 固定系统前缀：模块级常量，不含任何用户数据，逐字节可复现。
SYSTEM_PREFIX = (
    "你是「向新」，一个中文 AI 职业导航与终身学习伙伴。\n"
    "回答要求：用中文、口语化、不超过 200 字；只依据下面给出的【用户画像】【已知记忆】【图谱事实】；\n"
    "缺信息时明确说还需要用户补充什么；不要编造图谱里不存在的职业名或技能名。\n"
)

PROFILE_TITLE = "【用户画像】"
MEMORY_TITLE = "【已知记忆】（全部来自用户已确认的记忆库，用户可随时删除）"
GRAPH_TITLE = "【图谱事实】（沿既有边查询得到，不新增结论）"
HISTORY_TITLE = "【前几轮对话】"
USER_TITLE = "【用户这次说】"

# 生成器取值：与记忆触发器接口（`POST /api/memories/<id>/triggers?generator=`）同一套词汇，
# 便于用同一条命令解释「这次为什么走了规则版」。
GENERATORS = ("auto", "llm", "rule-based")
PROVIDER_RULE = "rule-based"
PROVIDER_MODEL = "llm"
RULE_MARKER = "（规则版回答 · 本轮未接模型，只依据图谱事实与已确认记忆）"

# 一次对话留在上下文里的轮数（1 轮 = 用户 + 助手各一条）
HISTORY_TURNS = 6
MAX_CONVERSATIONS = 200

# 会话列表一次最多回多少条
MAX_SESSION_PAGE = 100

# 会话与消息**落库**（与记忆库同一个 sqlite）。此前它们只在 `ChatService._conversations`
# 这个进程内字典里 —— 后端一重启，聊过什么就没了。
_CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user
    ON chat_sessions(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id   TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    role         TEXT NOT NULL,
    text         TEXT NOT NULL,
    provider     TEXT,
    injected_json TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, created_at);
"""


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ChatStore:
    """会话与消息的读写。归属校验一律走 `user_id`：别人的会话当作不存在。"""

    def __init__(self, path: Optional[str] = None, now: Optional[Any] = None) -> None:
        self.path = path or os.environ.get(DEFAULT_DB_ENV) or str(Path(__file__).resolve().parent / "career.db")
        self._now = now or _now
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_CHAT_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> Dict[str, Any]:
        try:
            injected = json.loads(row["injected_json"]) if row["injected_json"] else None
        except ValueError:
            injected = None
        return {
            "messageId": row["message_id"],
            "sessionId": row["session_id"],
            "role": row["role"],
            "text": row["text"],
            "provider": row["provider"],
            "injected": injected,
            "createdAt": row["created_at"],
        }

    def ensure_session(self, user_id: str, session_id: str, title: str = "") -> Dict[str, Any]:
        """建会话（存在就只更新 updated_at；标题只在空的时候补一次，不覆盖用户聊出来的上下文）。"""
        now = self._now()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chat_sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO chat_sessions (session_id, user_id, title, created_at, updated_at) VALUES (?,?,?,?,?)",
                    (session_id, user_id, str(title or "")[:120], now, now),
                )
            else:
                self._conn.execute(
                    "UPDATE chat_sessions SET updated_at = ?, title = CASE WHEN title = '' THEN ? ELSE title END"
                    " WHERE session_id = ? AND user_id = ?",
                    (now, str(title or "")[:120], session_id, user_id),
                )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM chat_sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id)
            ).fetchone()
        return {
            "sessionId": row["session_id"],
            "title": row["title"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def append(self, user_id: str, session_id: str, role: str, text: str,
               provider: Optional[str] = None, injected: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        message_id = f"chatmsg_{uuid.uuid4().hex[:12]}"
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO chat_messages (message_id, session_id, user_id, role, text, provider,"
                " injected_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (message_id, session_id, user_id, role, text, provider,
                 json.dumps(injected, ensure_ascii=False) if injected else None, now),
            )
            self._conn.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ? AND user_id = ?",
                (now, session_id, user_id),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM chat_messages WHERE message_id = ?", (message_id,)).fetchone()
        return self._row_to_message(row)

    def messages(self, user_id: str, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        # 用 `rowid`（插入顺序）兜底：`created_at` 只到秒，同一轮的两条消息时间戳会完全相同，
        # 拿随机 message_id 当兜底键会让「用户 → 助手」的顺序随机翻转。
        if limit:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM chat_messages WHERE user_id = ? AND session_id = ?"
                    " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (user_id, session_id, max(1, int(limit))),
                ).fetchall()
            return [self._row_to_message(row) for row in reversed(rows)]
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chat_messages WHERE user_id = ? AND session_id = ? ORDER BY created_at, rowid",
                (user_id, session_id),
            ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def list_sessions(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.session_id"
                " AND m.user_id = s.user_id) AS message_count"
                " FROM chat_sessions s WHERE s.user_id = ? ORDER BY s.updated_at DESC LIMIT ?",
                (user_id, max(1, min(int(limit), MAX_SESSION_PAGE))),
            ).fetchall()
        return [{
            "sessionId": row["session_id"],
            "title": row["title"],
            "messageCount": int(row["message_count"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        } for row in rows]

    def get_session(self, user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chat_sessions WHERE user_id = ? AND session_id = ?", (user_id, session_id)
            ).fetchone()
        if row is None:
            return None
        return {"sessionId": row["session_id"], "title": row["title"],
                "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    def delete_session(self, user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chat_sessions WHERE user_id = ? AND session_id = ?", (user_id, session_id)
            ).fetchone()
            if row is None:
                return None
            cursor = self._conn.execute(
                "DELETE FROM chat_messages WHERE user_id = ? AND session_id = ?", (user_id, session_id)
            )
            removed = cursor.rowcount
            self._conn.execute(
                "DELETE FROM chat_sessions WHERE user_id = ? AND session_id = ?", (user_id, session_id)
            )
            self._conn.commit()
        return {"deleted": session_id, "removedMessages": removed}



class UnknownChatGeneratorError(ValueError):
    """`generator` 取值非法。与「模型不可用」分开：一个是调用方写错参数，一个是环境不允许。"""


def compose_prompt(
    *,
    profile_text: str = "",
    memory_text: str = "",
    graph_text: str = "",
    history_text: str = "",
    message: str,
) -> str:
    """按固定模板拼接提示词。**没有内容的块整块缺席**（不插空标题、不留占位符）。"""
    chunks: List[str] = [SYSTEM_PREFIX]
    for title, body in (
        (PROFILE_TITLE, profile_text),
        (MEMORY_TITLE, memory_text),
        (GRAPH_TITLE, graph_text),
        (HISTORY_TITLE, history_text),
    ):
        text = (body or "").strip()
        if text:
            chunks.append(f"{title}\n{text}\n")
    chunks.append(f"{USER_TITLE}\n{message.strip()}")
    return "\n".join(chunks)


def profile_text(profile: Optional[Dict[str, Any]]) -> str:
    """画像块：只写有值的字段，空画像时整块缺席。"""
    data = profile or {}
    lines: List[str] = []
    identity = str(data.get("identity") or "").strip()
    if identity:
        lines.append(f"- 身份：{identity}")
    for key, label in (("major", "专业"), ("grade", "年级"), ("location", "期望地点")):
        value = str(data.get(key) or "").strip()
        if value:
            lines.append(f"- {label}：{value}")
    goal = str(data.get("currentGoal") or "").strip()
    if goal:
        lines.append(f"- 当前目标：{goal}")
    interests = [str(item).strip() for item in (data.get("interests") or []) if str(item).strip()]
    if interests:
        lines.append(f"- 感兴趣的方向：{'、'.join(interests)}")
    skills = [str(s.get("name") or "").strip() for s in (data.get("skills") or []) if isinstance(s, dict)]
    skills = [name for name in skills if name]
    if skills:
        lines.append(f"- 已掌握技能：{'、'.join(skills)}")
    if data.get("status") == "draft":
        lines.append("- （画像尚未确认：以下仅按用户当次填写内容作答）")
    return "\n".join(lines)


class ChatService:
    """一轮问答的组装与兜底。线程安全：一把可重入锁管住会话表。"""

    def __init__(
        self,
        store: GraphStore,
        memories: MemoryStore,
        profiles: Any,
        client: Optional[LlmClient] = None,
        *,
        sessions: Optional["ChatStore"] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = 120,
        attempts: int = 2,
        temperature: float = 0.3,
    ) -> None:
        self.store = store
        self.memories = memories
        self.profiles = profiles
        # 默认复用记忆库那只客户端：产品侧只有一处配置（`CAREER_LLM_*` > `DEEPEVAL_*` > .env）
        self.client = client if client is not None else memories.llm
        # 会话落库（传了就用库，没传就是旧的进程内行为 —— 单测仍可只测组装逻辑）
        self.sessions = sessions
        self.max_tokens = max_tokens
        # 对话是交互式调用：超时与重试都给紧一点，失败就降级出规则版答案，不把用户挂在页面上
        self.timeout = timeout
        self.attempts = attempts
        self.temperature = temperature
        self._lock = threading.RLock()
        self._conversations: Dict[str, List[Dict[str, str]]] = {}
        self._history_user = ""
        self.last_note: Dict[str, Any] = {}

    # ------------------------------------------------------------------ 会话表
    def history(self, conversation_id: str, user_id: str = "") -> List[Dict[str, str]]:
        """最近几轮对话。传了会话库就以库为准（后端重启后还在），否则用进程内那份。"""
        if self.sessions is not None:
            return [
                {"role": row["role"], "text": row["text"]}
                for row in self.sessions.messages(user_id, conversation_id, limit=HISTORY_TURNS * 2)
            ]
        with self._lock:
            return list(self._conversations.get(conversation_id, []))

    def _append(self, conversation_id: str, role: str, text: str, user_id: str = "",
                provider: Optional[str] = None, injected: Optional[Dict[str, Any]] = None) -> None:
        if self.sessions is not None and user_id:
            self.sessions.append(user_id, conversation_id, role, text, provider=provider, injected=injected)
            return
        with self._lock:
            turns = self._conversations.setdefault(conversation_id, [])
            turns.append({"role": role, "text": text})
            # 只留最近 HISTORY_TURNS 轮；会话数也要有上限，否则长跑进程会一直吃内存
            overflow = len(turns) - HISTORY_TURNS * 2
            if overflow > 0:
                del turns[:overflow]
            while len(self._conversations) > MAX_CONVERSATIONS:
                oldest = next(iter(self._conversations))
                if oldest == conversation_id:
                    break
                self._conversations.pop(oldest, None)

    def _history_text(self, conversation_id: str) -> str:
        lines = []
        for turn in self.history(conversation_id, self._history_user):
            speaker = "用户" if turn["role"] == "user" else "向新"
            lines.append(f"{speaker}：{turn['text']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 主流程
    def reply(self, user_id: str, message: Any, conversation_id: Optional[str] = None,
              generator: str = "auto") -> Dict[str, Any]:
        """组装并回答一轮。`generator`：auto（有模型就用）/ llm / rule-based。"""
        text = str(message or "").strip()
        if not text:
            raise ValueError("消息不能为空")
        if len(text) > 2000:
            raise ValueError("消息过长（上限 2000 字）")
        generator = str(generator or "auto").strip() or "auto"
        if generator not in GENERATORS:
            raise UnknownChatGeneratorError(f"未知的生成器：{generator}；可选 {', '.join(GENERATORS)}")

        conversation = str(conversation_id or "").strip() or f"conversation_{uuid.uuid4().hex[:12]}"
        # 会话先落库（第一条用户消息当标题），这样刷新/重启之后还能找回这段对话
        self._history_user = user_id
        if self.sessions is not None:
            self.sessions.ensure_session(user_id, conversation, title=text)

        # ① 召回：检索 query 就是用户原话，逐字节不改写
        context = self.memories.build_context(user_id, text)
        # ② 画像（记忆只补空缺，不覆盖显式画像 —— 与推荐接口同一个增强函数）
        profile = self.profile_of(user_id)
        rows = self.store.recommendations(profile).get("recommendations", [])
        graph_text = self.graph_facts(text, profile, rows)
        memory_text = str(context.get("summaryText") or "")

        prompt = compose_prompt(
            profile_text=profile_text(profile),
            memory_text=memory_text,
            graph_text=graph_text,
            history_text=self._history_text(conversation),
            message=text,
        )

        use_model = generator != PROVIDER_RULE and self.client.configured
        started = time.time()
        note: Dict[str, Any] = {
            "requested": generator,
            "used": PROVIDER_RULE,
            "provider": "llm" if use_model else None,
            "model": None,
            "error": None,
            "elapsedMs": 0,
        }
        answer = ""
        if use_model:
            note["model"] = getattr(self.client.config, "model", None)
            try:
                answer = self.client.chat(
                    prompt,
                    max_tokens=self.max_tokens,
                    timeout=self.timeout,
                    attempts=self.attempts,
                    temperature=self.temperature,
                )
                note["used"] = PROVIDER_MODEL
            except LlmError as error:
                # 失败不抛给前端：降级为规则版，并如实回传错误码与说明
                note["error"] = {"code": error.code, "message": str(error), "detail": error.detail}
            except Exception as error:  # noqa: BLE001 —— 兜底：任何异常都不许把对话打断
                note["error"] = {"code": "LLM_UNEXPECTED", "message": f"{type(error).__name__}: {error}"}
        if not answer:
            # 模型没被用上（没配端点 / 显式要规则版 / 调用失败）都走同一条兜底路径
            answer = self._rule_reply(text, profile, context, rows)
            note["used"] = PROVIDER_RULE
        note["elapsedMs"] = int((time.time() - started) * 1000)

        self._append(conversation, "user", text, user_id)
        injected_payload = {
            "query": text,  # 检索用的原始提问（只去掉首尾空白，不做任何改写/扩写）
            "count": context.get("count", 0),
            "memoryIds": context.get("memoryIds", []),
            "memoryHash": context.get("memoryHash", ""),
            "persona": context.get("persona", []),
            "recalled": context.get("recalled", []),
            "summaryText": memory_text,
            "prefixBytes": len(prompt.encode("utf-8")),
            "memoryBlockBytes": len(memory_text.encode("utf-8")),
            "promptTemplate": PROMPT_TEMPLATE,
        }
        self._append(conversation, "assistant", answer, user_id, provider=note["used"], injected=injected_payload)
        self.last_note = note

        return {
            "message": answer,
            "conversationId": conversation,
            "provider": note["used"],
            "injected": injected_payload,
            "llm": note,
            "graph": {"recommended": rows[0]["occupation_id"] if rows else None},
        }

    def profile_of(self, user_id: str) -> Dict[str, Any]:
        """当前画像：记忆是输入增强，所以这里取的是「画像 + 已确认记忆补的空缺」。"""
        base = self.profiles.get(user_id) if hasattr(self.profiles, "get") else {}
        augmented, _evidence = self.memories.augment_profile(base, user_id)
        return augmented

    # ------------------------------------------------------------------ 图谱事实
    def graph_facts(self, message: str, profile: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
        """把「提问里出现的图谱节点」与「画像对应的岗位缺口」写成一段可读事实。"""
        lines: List[str] = []
        owned = {normalize(s.get("name")) for s in (profile.get("skills") or []) if isinstance(s, dict)}
        owned.discard("")
        for node in self.store.nodes_in(message)[:2]:
            kind = node.get("kind")
            label = str(node.get("label") or code_of(node["id"]))
            if kind == OCCUPATION:
                required = self.store.required_skills(node["id"])
                gaps = [
                    self._label(row["skillNodeId"])
                    for row in required
                    if not self.store.owns_skill(self.store.node(row["skillNodeId"]) or {}, owned)
                ]
                lines.append(
                    f"- {label}（{code_of(node['id'])}）要求 {len(required)} 项技能；"
                    f"用户已覆盖 {len(required) - len(gaps)} 项，缺口：{'、'.join(gaps[:5]) or '无'}"
                )
            elif kind == SKILL:
                domain = self._domain_of(node["id"])
                owners = [
                    self._label(edge["from"])
                    for edge in self.store.edges_of_type("requires")
                    if edge.get("to") == node["id"]
                ][:4]
                lines.append(
                    f"- 技能 {label}（{code_of(node['id'])}）：能力域 {domain or '未标注'}；"
                    f"需要它的职业：{'、'.join(owners) or '未标注'}"
                )
        if rows:
            top = rows[0]
            lines.append(
                f"- 按当前画像，图谱里最接近的职业是 {top['occupation_name']}"
                f"（匹配度 {top['match_score']}%），缺口：{'、'.join((top.get('skill_gaps') or [])[:5]) or '无'}"
            )
        elif not lines:
            lines.append("- 用户还没有可用画像，图谱里也认不出提问中的职业或技能名。")
        return "\n".join(lines)

    def _label(self, node_id: str) -> str:
        node = self.store.node(node_id)
        return str(node.get("label") or code_of(node_id)) if node else code_of(node_id)

    def _domain_of(self, skill_node_id: str) -> str:
        for edge in self.store.edges_of_type("belongs_to"):
            if edge.get("from") == skill_node_id:
                return self._label(edge["to"])
        return ""

    # ------------------------------------------------------------------ 规则版兜底
    def _rule_reply(self, message: str, profile: Dict[str, Any], context: Dict[str, Any],
                    rows: List[Dict[str, Any]]) -> str:
        """没有模型也要给得出**有依据**的答案：图谱事实 + 已确认记忆。"""
        lines: List[str] = [RULE_MARKER]
        owned = [str(s.get("name")) for s in (profile.get("skills") or []) if isinstance(s, dict) and s.get("name")]
        mentioned = self.store.nodes_in(message)
        occupations = [node for node in mentioned if node.get("kind") == OCCUPATION]
        skills = [node for node in mentioned if node.get("kind") == SKILL]

        if occupations:
            node = occupations[0]
            required = self.store.required_skills(node["id"])
            owned_norm = {normalize(name) for name in owned}
            gaps = [self._label(row["skillNodeId"]) for row in required
                    if not self.store.owns_skill(self.store.node(row["skillNodeId"]) or {}, owned_norm)]
            label = str(node.get("label") or code_of(node["id"]))
            lines.append(
                f"「{label}」在图谱里有 {len(required)} 项要求技能，你已经覆盖 "
                f"{len(required) - len(gaps)} 项。"
            )
            if gaps:
                lines.append(f"还差：{'、'.join(gaps[:4])}。建议先补「{gaps[0]}」——它是缺口里权重最高的一项。")
            else:
                lines.append("要求技能你已经全覆盖，可以开始按岗位任务做作品了。")
        elif skills:
            node = skills[0]
            label = str(node.get("label") or code_of(node["id"]))
            domain = self._domain_of(node["id"])
            owners = [self._label(edge["from"]) for edge in self.store.edges_of_type("requires")
                      if edge.get("to") == node["id"]][:3]
            lines.append(f"「{label}」是画像/图谱里的技能（能力域：{domain or '未标注'}）。")
            if owners:
                lines.append(f"需要它的职业有：{'、'.join(owners)}。")
        elif owned or str(profile.get("currentGoal") or "").strip():
            if rows and rows[0].get("match_score"):
                top = rows[0]
                gaps = top.get("skill_gaps") or []
                lines.append(f"按你现在的画像，图谱里最接近的是「{top['occupation_name']}」（匹配度 {top['match_score']}%）。")
                if gaps:
                    lines.append(f"缺口：{'、'.join(gaps[:3])}。先把第一项补上，匹配度会立刻动。")
            elif rows:
                labels = [str(row.get("occupation_name") or "") for row in rows[:3]]
                lines.append("你的画像还没命中任何岗位的要求技能，所以匹配度都是 0。")
                lines.append(
                    f"把技能名换成图谱里的标准名（例如「模型量化与部署」）就能算出来；"
                    f"当前图谱里的职业有：{'、'.join(labels)}。"
                )
            else:
                lines.append("图谱里还没有可比的职业节点，暂时算不出匹配度。")
        else:
            lines.append("我还不知道你的背景和技能，所以只能先按图谱回答。")
            lines.append("告诉我：专业/年级、正在学的技能、想去的方向，我就能算出匹配度和技能缺口。")

        # 已确认记忆：persona 常驻 + 本次想起，两条上限，避免把气泡刷满
        remembered = [entry.get("content", "") for entry in (context.get("recalled") or [])]
        remembered += [item.get("content", "") for item in (context.get("persona") or [])]
        remembered = [item for item in dict.fromkeys(remembered) if item][:2]
        if remembered:
            lines.append(f"我记得你确认过：{'、'.join(remembered)}。")
        return "\n".join(lines)
