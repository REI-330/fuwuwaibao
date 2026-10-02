"""用户记忆库：stdlib + SQLite，零三方依赖。

**这是「整合另一位开发者的记忆系统」的落地件，来源与边界如实写在这里：**

* 机制概念（写时触发器 / persona 常驻 + 联想召回 / candidate 授权闸门 / memoryHash）
  参考腾讯 T-Mem（EMNLP 2026，MIT）以及队友实现 `career-ai-system` 的
  `backend/app/services/memory_*.py`（取舍见 `项目架构与技术文档.md`）。
* 实现是**按本项目约束重写**的：stdlib `sqlite3`，没有 FastAPI / SQLAlchemy / aiosqlite /
  rapidfuzz / json_repair，也没有外部模型调用。队友那版 5 个 service 全部以 `AsyncSession`
  为入口，无法直接搬运（详见整合方案里的依赖清单）。
* 触发器一期是**规则版**（`generated_by='rule-based'`）：概念锚点与提问句式都从记忆原文
  确定性派生，不调模型。**概念锚点优先取图谱里已有的名词**（`GraphStore.terms_in` 提供的
  label/alias 查表）—— 这样「轻量级推理引擎集成」的锚点就是真实技能名，而不是切出来的
  「量级」这类字片段。文本里没有已知名词时才退回字面切分。
  接上 LLM 后按同一张表、同一套字段替换即可，消费侧不用改。

三条不可违反的边界：

1. **只消费 `status='confirmed'`**：`candidate` 只停留在管理界面，永远不进入上下文与算法。
2. **未确认不覆盖已确认**：同一来源重复写入时，已确认的那条内容不被静默改写。
3. **删除即遗忘**：删记忆会级联清掉它的触发器，`memoryHash` 随之改变，推荐自动重算。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .growth import SOURCE_TYPE as GROWTH_SOURCE_TYPE
from .growth import VALID_KINDS as VALID_GROWTH_KINDS
from .growth import derive_candidates, normalize_record
from .llm import LlmClient, LlmError, default_client
from .memory_triggers import generate_triggers_with_llm

TRIGGER_GENERATORS = ("auto", "rule-based", "llm")
# 降级标记：`generated_by` 要能区分「本来就是规则版」与「模型失败了才退回规则版」，
# 否则事后无法统计模型生成的成功率。
GENERATED_BY_RULE = "rule-based"
GENERATED_BY_RULE_FALLBACK = "rule-based-fallback"
GENERATED_BY_LLM = "llm"
# 写路径（确认/改内容）固定用规则版，理由见 `MemoryStore.generate_triggers` 调用处注释
WRITE_PATH_GENERATOR = GENERATED_BY_RULE


class UnknownGeneratorError(ValueError):
    """生成器取值非法。与「记忆未确认」分开：一个是调用方写错了参数，一个是状态不允许。"""

VALID_CATEGORIES = ("goal", "preference", "skill", "background", "career_target", "custom")
VALID_STATUSES = ("candidate", "confirmed")
CATEGORY_LABELS = {
    "goal": "当前目标",
    "preference": "偏好",
    "skill": "技能",
    "background": "背景",
    "career_target": "目标职业",
    "custom": "自定义",
}
PERSONA_CATEGORIES = ("career_target", "goal", "background", "preference")

DEFAULT_IMPORTANCE = 50
MAX_CONTENT_LENGTH = 1000
PERSONA_LIMIT = 3
RECALL_LIMIT = 8
# 与队友那版一致的轻量加权（替代 T-Mem 的 RRF，因为这里没有多路检索器）
W_IMPORTANCE = 0.4
W_RECALL = 0.4
W_RECENCY = 0.2
# 词面召回门限：低于它只算噪声（一个共同 2-gram 不应该把无关记忆捞进来）
WORD_HIT_FLOOR = 0.25
# 规则版触发器的固定置信度：它是排序用的权重，不是模型自评的概率，不要在对外材料里当概率讲
RULE_TRIGGER_CONFIDENCE = 0.6
SKILL_PREFIX = "具备或正在学习："
# 从记忆内容里抽技能名时认的前缀白名单（形状与队友实现的 `_SKILL_PREFIXES` 一致）
SKILL_PREFIXES = (
    SKILL_PREFIX,
    "掌握：",
    "技能：",
    "具备：",
    "了解：",
    "目标职业：",
    "感兴趣的方向：",
    # 成长记录还在「打算学」阶段时用的前缀（见 backend/growth.py）；
    # 白名单必须认它，否则抽出来的「技能名」会是整句「计划学习：模型量化与部署」。
    "计划学习：",
)

DEFAULT_DB_ENV = "CAREER_MEMORY_DB"

_STOPWORDS = frozenset(
    {
        "我们",
        "你们",
        "他们",
        "这个",
        "那个",
        "什么",
        "怎么",
        "可以",
        "需要",
        "觉得",
        "现在",
        "以后",
        "以前",
        "已经",
        "还是",
        "就是",
        "因为",
        "所以",
        "如果",
        "但是",
    }
)

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS memory_items (
    memory_id           TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    category            TEXT NOT NULL,
    content             TEXT NOT NULL,
    status              TEXT NOT NULL,
    importance          INTEGER NOT NULL,
    source_type         TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    query_patterns_json TEXT NOT NULL DEFAULT '[]',
    triggers_pending    INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    confirmed_at        TEXT,
    UNIQUE (user_id, source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_items_user_status
    ON memory_items (user_id, status, updated_at);

CREATE TABLE IF NOT EXISTS memory_triggers (
    trigger_id                TEXT PRIMARY KEY,
    memory_id                 TEXT NOT NULL REFERENCES memory_items (memory_id) ON DELETE CASCADE,
    level                     INTEGER NOT NULL DEFAULT 1,
    concept                   TEXT NOT NULL,
    bridge                    TEXT NOT NULL DEFAULT '',
    activation_patterns_json  TEXT NOT NULL DEFAULT '[]',
    confidence                REAL NOT NULL,
    generated_by              TEXT NOT NULL DEFAULT 'rule-based',
    generated_by_model        TEXT NOT NULL DEFAULT '',
    created_at                TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_triggers_memory ON memory_triggers (memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_triggers_concept ON memory_triggers (concept);

-- 成长记录（来源：前端成长档案页）。候选记忆通过 source_type/source_id 指回这里，
-- 不用外键：记忆可以被单独确认/删除，生命周期与记录解耦（删除记录只清掉未确认的候选）。
CREATE TABLE IF NOT EXISTS growth_records (
    record_id        TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    kind             TEXT NOT NULL,
    title            TEXT NOT NULL,
    before_text      TEXT NOT NULL DEFAULT '',
    after_text       TEXT NOT NULL DEFAULT '',
    explanation      TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL DEFAULT '',
    occurred_at      TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    metadata_json    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_growth_records_user
    ON growth_records (user_id, occurred_at);

-- 用户画像（M1-1）。此前 ProfileStore 是进程内内存，重启即丢；落这张表后
-- 「简历解析 → 草稿 → 确认画像」整条链在进程重启后仍可复现。
-- 整份画像按 JSON 存（字段由 server.ProfileStore._normalize 定义），
-- 另抽 status / version / updated_at 三列出来做查询与自检。
CREATE TABLE IF NOT EXISTS profiles (
    user_id         TEXT PRIMARY KEY,
    profile_json    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft',
    profile_version INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);

-- 画像证据（M1-1/M1-3）：一条证据说明「画像里这个结论是从哪来的」。
-- source_type 复用记忆库的口径（resume / growth_record / manual / chat）。
CREATE TABLE IF NOT EXISTS profile_evidence (
    evidence_id   TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    source_id     TEXT NOT NULL DEFAULT '',
    field         TEXT NOT NULL,
    value         TEXT NOT NULL DEFAULT '',
    locator       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_profile_evidence_user
    ON profile_evidence (user_id, created_at);

-- 成长事件（M1-3）：确认一条候选后追加的不可变流水，用来回答「这条画像/记忆
-- 是哪一次确认改的」。刻意不做外键：记忆与记录都可以被单独删除，事件是历史。
CREATE TABLE IF NOT EXISTS growth_events (
    event_id     TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    record_id    TEXT NOT NULL DEFAULT '',
    memory_id    TEXT NOT NULL DEFAULT '',
    action       TEXT NOT NULL,
    detail_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_growth_events_user
    ON growth_events (user_id, created_at);

-- 画像历史快照：每次画像写入 / 确认都留一份**不可变**副本，用于「回看上周那一刻的档案」。
-- 与 profiles 分开：profiles 是**可变的最新态**（每次 upsert 覆盖），profile_snapshots 是**只增不改**的历史。
-- snapshot_id 由 (user_id, profile_version, status) 派生 → 同一次写入 / 重复 confirm 幂等，不堆重复行。
CREATE TABLE IF NOT EXISTS profile_snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    profile_json    TEXT NOT NULL,
    profile_version INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'draft',
    captured_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_profile_snapshots_user
    ON profile_snapshots (user_id, captured_at);

-- 职业匹配结果：每个用户只保留**最新一份** run（契约的 /current 读的就是它）。
-- 整份 run 按 JSON 存；另抽 run_id / profile_version / catalog_version 三列出来做「过期判定」——
-- 画像或图谱变过就必须重算，而不是把旧结果端给用户（上层据此回 CAREER_MATCH_STALE）。
CREATE TABLE IF NOT EXISTS career_match_runs (
    user_id         TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    run_json        TEXT NOT NULL,
    profile_version INTEGER NOT NULL DEFAULT 0,
    catalog_version TEXT NOT NULL DEFAULT '',
    generated_at    TEXT NOT NULL
);

-- 用户选定的目标职业。与 run 分表：换目标不该强迫重新生成整份匹配。
CREATE TABLE IF NOT EXISTS career_match_targets (
    user_id       TEXT PRIMARY KEY,
    occupation_id TEXT NOT NULL,
    run_id        TEXT NOT NULL DEFAULT '',
    selected_at   TEXT NOT NULL
);
"""

# 幂等迁移：`CREATE TABLE IF NOT EXISTS` 不会给**已存在**的表补列，
# 所以每次打开库都要对一遍 PRAGMA，缺哪列补哪列。老库不重建、数据不丢。
# （队友项目里对应的是 `data_loader.py` 的幂等建表 + `ADD COLUMN` 迁移，同一件事。）
_MIGRATIONS: Tuple[Tuple[str, str, str], ...] = (
    ("memory_triggers", "generated_by_model", "ALTER TABLE memory_triggers ADD COLUMN generated_by_model TEXT NOT NULL DEFAULT ''"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_dict(value: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _json_list(value: str) -> List[str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def normalize_text(text: Any) -> str:
    """比对与召回用的归一化：小写、去掉空白与中英标点。"""
    value = str(text or "").lower()
    value = re.sub(r"[\s，。！？、；：“”‘’（）【】《》.,!?;:(){}<>\[\]\"'`]+", "", value)
    return value


def concept_key(concept: str) -> str:
    return normalize_text(concept)


def _recency_score(updated_at: str) -> float:
    """时间衰减：今天 1.0，30 天前 0.5，再往前压到 0.2 地板。"""
    try:
        updated = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.5
    days = max(0.0, (datetime.now(timezone.utc) - updated).total_seconds() / 86400.0)
    if days <= 0:
        return 1.0
    return max(0.2, 1.0 - 0.5 * (days / 30.0))


def _word_hit(query_norm: str, *texts: str) -> float:
    """查询词在候选文本里的覆盖率（0..1）。

    中文按整段 + 2 字滑窗，英文数字按 ≥2 字符的词 —— 这样「机器视觉岗位怎么样」
    能命中记忆里的「机器视觉工程师」，而不是只靠整句包含。
    """
    if not query_norm:
        return 0.0
    haystack = normalize_text(" ".join(text for text in texts if text))
    if not haystack:
        return 0.0
    if query_norm in haystack:
        return 1.0
    tokens: set[str] = set()
    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", query_norm):
        tokens.add(segment)
        tokens.update(segment[index : index + 2] for index in range(len(segment) - 1))
    tokens.update(re.findall(r"[a-z0-9]{2,}", query_norm))
    tokens.discard("")
    if not tokens:
        return 0.0
    hits = sum(1 for token in tokens if token in haystack)
    return hits / len(tokens)


# --------------------------------------------------------------------------- 触发器（规则版）


def derive_concepts(content: str, category: str, vocabulary: Optional[Callable[[str], List[str]]] = None,
                    limit: int = 3) -> List[str]:
    """从记忆原文派生概念锚点（确定性，不调模型）。

    优先用**图谱里已经存在的名词**：`vocabulary(text)` 返回这段文本里出现的节点名/别名
    （由 `GraphStore.terms_in` 提供）。这样「具备或正在学习：轻量级推理引擎集成」的锚点
    就是真实的技能名，而不是切出来的「量级」这种字片段 —— 联想召回与界面显示同时受益。

    文本里没有已知名词时才退回字面切分：中文段 2–6 字整段直接用、更长的取前几个 2 字窗口；
    英文/数字 ≥2 字符的词原样保留（`Python`、`SK215`、`pytest`）。
    """
    value = str(content or "").strip()
    if "：" in value:
        value = value.split("：", 1)[1]

    concepts: List[str] = []
    if vocabulary is not None:
        try:
            for term in vocabulary(value):
                if term and term not in concepts:
                    concepts.append(term)
        except Exception:  # noqa: BLE001 —— 词表不可用不能拖垮写入
            concepts = []
    if concepts:
        return concepts[:limit]

    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        if segment in _STOPWORDS:
            continue
        if len(segment) <= 6:
            candidates = [segment]
        else:
            candidates = [segment[index : index + 2] for index in range(0, min(len(segment) - 1, 4))]
        for candidate in candidates:
            if candidate in _STOPWORDS or candidate in concepts:
                continue
            concepts.append(candidate)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+.#_\-]{1,}", value):
        if token in concepts:
            continue
        concepts.append(token)
    if not concepts:
        # 兜底：类别标签本身，保证「这条记忆能被想起来」这件事不依赖于文本长度
        concepts.append(CATEGORY_LABELS.get(category, category))
    return concepts[:limit]


def build_triggers(content: str, category: str, created_at: str,
                   vocabulary: Optional[Callable[[str], List[str]]] = None) -> List[Dict[str, Any]]:
    """规则版触发器：概念锚点 + 3 条未来提问句式 + 固定置信度。

    与队友那版的差别（必须如实对外说）：它用 TBox LLM 生成 concept/bridge/confidence，
    这里全部是确定性派生，因此**没有语义泛化能力** —— 只有词面与 2-gram 层面的联想。
    """
    triggers = []
    for concept in derive_concepts(content, category, vocabulary):
        triggers.append(
            {
                "triggerId": f"trigger_{uuid.uuid4().hex[:12]}",
                "level": 1,
                "concept": concept,
                "bridge": f"{CATEGORY_LABELS.get(category, category)} → {concept}（由记忆原文确定性派生）",
                "activationPatterns": [
                    f"我{concept}进展怎么样",
                    f"还记得我之前说过{concept}吗",
                    f"{concept}接下来怎么安排",
                ],
                "confidence": RULE_TRIGGER_CONFIDENCE,
                "generatedBy": GENERATED_BY_RULE,
                "createdAt": created_at,
            }
        )
    return triggers


# --------------------------------------------------------------------------- 记忆库


class MemoryStore:
    """记忆的读写与召回。线程安全：一把可重入锁管住同一个 sqlite 连接。

    `path` 传 `:memory:` 时用内存库（测试用）；缺省落 `backend/career.db`，
    可用环境变量 `CAREER_MEMORY_DB` 覆盖（本项目决策 1「本地跑 + SQLite 单文件」）。
    """

    def __init__(self, path: Optional[str] = None, now: Optional[Callable[[], str]] = None,
                 vocabulary: Optional[Callable[[str], List[str]]] = None,
                 llm: Optional[LlmClient] = None) -> None:
        self.path = path or os.environ.get(DEFAULT_DB_ENV) or str(Path(__file__).resolve().parent / "career.db")
        self._now = now or now_iso
        # 已知名词表（图谱 labels/aliases）。传入时触发器概念锚点取真实概念名，否则退化为字面切分。
        self._vocabulary = vocabulary
        # 模型客户端。传 None 时按环境/`knowledge/eval/.env` 自动解析配置；
        # 解析不到（或没配全）时 `auto` 会走规则版，且 `/health` 会把「没配端点」说出来。
        self._llm = llm if llm is not None else default_client()
        self.last_trigger_note: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """幂等补列：老库（没有 generated_by_model）也能直接用。"""
        for table, column, ddl in _MIGRATIONS:
            existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if not existing:
                continue  # 表刚由 _SCHEMA 建好，列是全的
            if column not in existing:
                self._conn.execute(ddl)

    @property
    def llm(self) -> LlmClient:
        return self._llm

    def describe_llm(self) -> Dict[str, Any]:
        """给 `/health` 用：只报 host 与模型名，不含密钥。"""
        return self._llm.describe()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ 内部：行 → dict
    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["memory_id"],
            "userId": row["user_id"],
            "category": row["category"],
            "content": row["content"],
            "status": row["status"],
            "importance": row["importance"],
            "sourceType": row["source_type"],
            "sourceId": row["source_id"],
            "metadata": _json_dict(row["metadata_json"]),
            "queryPatterns": _json_list(row["query_patterns_json"]),
            "triggersPending": bool(row["triggers_pending"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "confirmedAt": row["confirmed_at"],
        }

    @staticmethod
    def _row_to_trigger(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "triggerId": row["trigger_id"],
            "memoryId": row["memory_id"],
            "level": row["level"],
            "concept": row["concept"],
            "bridge": row["bridge"],
            "activationPatterns": _json_list(row["activation_patterns_json"]),
            "confidence": row["confidence"],
            "generatedBy": row["generated_by"],
            "generatedByModel": row["generated_by_model"] if "generated_by_model" in row.keys() else "",
            "createdAt": row["created_at"],
        }

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    # ------------------------------------------------------------------ 查询
    def list_items(self, user_id: str, status: Optional[str] = None, with_triggers: bool = True) -> List[Dict[str, Any]]:
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"未知的记忆状态：{status}")
        sql = "SELECT * FROM memory_items WHERE user_id = ?"
        params: List[Any] = [user_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY importance DESC, updated_at DESC, memory_id"
        items = [self._row_to_item(row) for row in self._rows(sql, params)]
        if not with_triggers or not items:
            return items
        by_memory: Dict[str, List[Dict[str, Any]]] = {}
        for trigger in self.triggers_for([item["id"] for item in items]):
            by_memory.setdefault(trigger["memoryId"], []).append(trigger)
        for item in items:
            item["triggers"] = by_memory.get(item["id"], [])
        return items

    def get_item(self, user_id: str, memory_id: str, with_triggers: bool = False) -> Optional[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM memory_items WHERE user_id = ? AND memory_id = ?", [user_id, memory_id])
        if not rows:
            return None
        item = self._row_to_item(rows[0])
        if with_triggers:
            item["triggers"] = self.triggers_for([memory_id])
        return item

    def confirmed_items(self, user_id: str) -> List[Dict[str, Any]]:
        return self.list_items(user_id, status="confirmed", with_triggers=False)

    def triggers_for(self, memory_ids: Iterable[str]) -> List[Dict[str, Any]]:
        ids = [memory_id for memory_id in memory_ids if memory_id]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        sql = f"SELECT * FROM memory_triggers WHERE memory_id IN ({placeholders}) ORDER BY confidence DESC, trigger_id"
        return [self._row_to_trigger(row) for row in self._rows(sql, ids)]

    def confirmed_hash(self, user_id: str) -> str:
        """已确认记忆集合的指纹；没有记忆时返回空串。

        推荐/路径用它与自己上一轮的 hash 比对，判断「记忆变过、结果要重算」。
        """
        rows = self._rows(
            "SELECT category, content, updated_at FROM memory_items WHERE user_id = ? AND status = 'confirmed'",
            [user_id],
        )
        if not rows:
            return ""
        digest = hashlib.sha1()
        for row in sorted(rows, key=lambda item: (item["category"], item["content"])):
            digest.update(f"{row['category']}|{row['content']}|{row['updated_at']}\n".encode("utf-8"))
        return digest.hexdigest()

    # -------------------------------------------------------------- 画像持久化（M1-1）
    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """读某用户的画像；没有返回 ``None``（由 ``ProfileStore`` 决定回落到种子画像）。"""
        rows = self._rows("SELECT profile_json FROM profiles WHERE user_id = ?", [user_id])
        if not rows:
            return None
        try:
            data = json.loads(rows[0]["profile_json"])
        except (json.JSONDecodeError, TypeError):
            # 库被人手改坏了也不抛：当作没有画像，让上层用种子画像继续，别让整个后端起不来
            return None
        return data if isinstance(data, dict) else None

    def save_profile(self, user_id: str, profile: Dict[str, Any]) -> None:
        """整份画像按 JSON upsert。status / version / updated_at 另抽三列，便于查询与自检。"""
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO profiles (user_id, profile_json, status, profile_version, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                " profile_json = excluded.profile_json,"
                " status = excluded.status,"
                " profile_version = excluded.profile_version,"
                " updated_at = excluded.updated_at",
                (
                    user_id,
                    json.dumps(profile, ensure_ascii=False),
                    str(profile.get("status") or "draft"),
                    int(profile.get("profileVersion") or 0),
                    str(profile.get("updatedAt") or now),
                ),
            )
            self._conn.commit()

    # ------------------------------------------------ 画像历史快照（2026-10-01 第七轮）
    @staticmethod
    def _snapshot_id(user_id: str, version: int, status: str) -> str:
        """快照 id 由 (user, version, status) 派生 —— 这是**写入动作的指纹**，不是内容指纹。

        为什么不用内容：``confirm`` 只改 status 与 updatedAt（version 不变），若拿整份 JSON 算指纹，
        重复 confirm 会因为 updatedAt 变了而每次都产生新快照，幂等就没了。用 (version, status) 才
        对得上「同一个版本 + 同一个状态 = 同一次快照」这个语义。
        """
        digest = hashlib.sha1(f"{user_id}|{int(version)}|{status}".encode("utf-8")).hexdigest()[:16]
        return f"psnap_{digest}"

    def save_profile_snapshot(self, user_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        """留一份画像历史快照（只增不改）。同一 (version, status) 重复写是幂等的，返回同一 id。"""
        version = int(profile.get("profileVersion") or 0)
        status = str(profile.get("status") or "draft")
        snapshot_id = self._snapshot_id(user_id, version, status)
        payload = json.dumps(profile, ensure_ascii=False)
        captured = str(profile.get("updatedAt") or self._now())
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO profile_snapshots"
                " (snapshot_id, user_id, profile_json, profile_version, status, captured_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot_id, user_id, payload, version, status, captured),
            )
            self._conn.commit()
        return {"snapshotId": snapshot_id, "profileVersion": version, "status": status, "capturedAt": captured}

    def count_profile_snapshots(self, user_id: str) -> int:
        rows = self._rows("SELECT COUNT(*) AS total FROM profile_snapshots WHERE user_id = ?", [user_id])
        return int(rows[0]["total"]) if rows else 0

    def list_profile_snapshots(self, user_id: str, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """按时间倒序列出快照（最新的在前）；``rowid`` 兜底保证同一秒的两条也不错位。"""
        sql = (
            "SELECT snapshot_id, profile_json, profile_version, status, captured_at, rowid AS seq"
            " FROM profile_snapshots WHERE user_id = ?"
            " ORDER BY captured_at DESC, seq DESC"
        )
        params: List[Any] = [user_id]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(int(offset))
        return [self._row_to_snapshot(row) for row in self._rows(sql, params)]

    def get_profile_snapshot(self, user_id: str, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """取某一时刻的**完整**画像；不存在或不属于该用户都返回 ``None``（由路由回 404，不区分）。"""
        rows = self._rows(
            "SELECT profile_json FROM profile_snapshots WHERE user_id = ? AND snapshot_id = ?",
            [user_id, snapshot_id],
        )
        if not rows:
            return None
        data = _json_dict(rows[0]["profile_json"])
        return data or None

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> Dict[str, Any]:
        """列表项：给足「一眼看出这是哪一版」，但不塞整份画像（详情走 get 那支）。"""
        profile = _json_dict(row["profile_json"])
        skills = [item.get("name") for item in (profile.get("skills") or []) if isinstance(item, dict)]
        return {
            "snapshotId": row["snapshot_id"],
            "profileVersion": row["profile_version"],
            "status": row["status"],
            "capturedAt": row["captured_at"],
            "summary": {
                "identity": profile.get("identity"),
                "school": profile.get("school"),
                "major": profile.get("major"),
                "currentGoal": profile.get("currentGoal"),
                "skills": [name for name in skills if name],
            },
        }

    # ------------------------------------------------- 职业匹配持久化（职业匹配页）
    def save_match_run(self, user_id: str, run: Dict[str, Any]) -> None:
        """整份 run 按 JSON upsert；另抽三列做过期判定（见 _SCHEMA 注释）。"""
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO career_match_runs (user_id, run_id, run_json, profile_version, catalog_version, generated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                " run_id = excluded.run_id,"
                " run_json = excluded.run_json,"
                " profile_version = excluded.profile_version,"
                " catalog_version = excluded.catalog_version,"
                " generated_at = excluded.generated_at",
                (
                    user_id,
                    str(run.get("runId") or ""),
                    json.dumps(run, ensure_ascii=False),
                    int(run.get("profileVersion") or 0),
                    str(run.get("catalogVersion") or ""),
                    str(run.get("generatedAt") or now),
                ),
            )
            self._conn.commit()

    def get_match_run(self, user_id: str) -> Optional[Dict[str, Any]]:
        """读该用户最新一份 run；没有或存档损坏都返回 ``None``（上层据此让前端重新生成）。"""
        rows = self._rows("SELECT run_json FROM career_match_runs WHERE user_id = ?", [user_id])
        if not rows:
            return None
        try:
            data = json.loads(rows[0]["run_json"])
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def save_match_target(self, user_id: str, occupation_id: str, run_id: str = "") -> None:
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO career_match_targets (user_id, occupation_id, run_id, selected_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                " occupation_id = excluded.occupation_id,"
                " run_id = excluded.run_id,"
                " selected_at = excluded.selected_at",
                (user_id, str(occupation_id), str(run_id or ""), now),
            )
            self._conn.commit()

    def get_match_target(self, user_id: str) -> Optional[Dict[str, Any]]:
        rows = self._rows(
            "SELECT occupation_id, run_id, selected_at FROM career_match_targets WHERE user_id = ?",
            [user_id],
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "userId": user_id,
            "occupationId": row["occupation_id"],
            "matchRunId": row["run_id"],
            "selectedAt": row["selected_at"],
        }

    # ------------------------------------------------------- 画像证据 / 成长事件（M1-3）
    def add_profile_evidence(self, user_id: str, *, source_type: str, source_id: str, field: str,
                             value: str, locator: str = "", commit: bool = True) -> Dict[str, Any]:
        """记一条「画像结论从哪来」。幂等键取 (user, source_type, source_id, field)：
        同一份简历重复解析不会堆出重复证据。`commit=False` 时交给调用方在同一事务里提交。"""
        rows = self._rows(
            "SELECT evidence_id FROM profile_evidence WHERE user_id = ? AND source_type = ?"
            " AND source_id = ? AND field = ?",
            [user_id, source_type, source_id, field],
        )
        if rows:
            return self._row_to_evidence(
                self._rows("SELECT * FROM profile_evidence WHERE evidence_id = ?", [rows[0]["evidence_id"]])[0]
            )
        evidence_id = f"evidence_{uuid.uuid4().hex[:12]}"
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO profile_evidence (evidence_id, user_id, source_type, source_id, field, value,"
                " locator, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (evidence_id, user_id, source_type, source_id, field, value, locator, now),
            )
            if commit:
                self._conn.commit()
        return {"id": evidence_id, "userId": user_id, "sourceType": source_type, "sourceId": source_id,
                "field": field, "value": value, "locator": locator, "createdAt": now}

    @staticmethod
    def _row_to_evidence(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["evidence_id"],
            "userId": row["user_id"],
            "sourceType": row["source_type"],
            "sourceId": row["source_id"],
            "field": row["field"],
            "value": row["value"],
            "locator": row["locator"],
            "createdAt": row["created_at"],
        }

    def list_profile_evidence(self, user_id: str) -> List[Dict[str, Any]]:
        return [self._row_to_evidence(row) for row in self._rows(
            "SELECT * FROM profile_evidence WHERE user_id = ? ORDER BY created_at, evidence_id", [user_id]
        )]

    def add_growth_event(self, user_id: str, *, action: str, record_id: str = "", memory_id: str = "",
                         detail: Optional[Dict[str, Any]] = None, commit: bool = True) -> Dict[str, Any]:
        """追加一条不可变事件流水。`event_id` 由内容指纹决定，同一动作重复调用不产生重复行。"""
        detail_json = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha1(f"{user_id}|{action}|{record_id}|{memory_id}|{detail_json}".encode("utf-8")).hexdigest()
        event_id = f"event_{digest[:12]}"
        rows = self._rows("SELECT * FROM growth_events WHERE event_id = ?", [event_id])
        if rows:
            return self._row_to_event(rows[0])
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO growth_events (event_id, user_id, record_id, memory_id, action,"
                " detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, user_id, record_id, memory_id, action, detail_json, now),
            )
            if commit:
                self._conn.commit()
        return {"id": event_id, "userId": user_id, "recordId": record_id, "memoryId": memory_id,
                "action": action, "detail": detail or {}, "createdAt": now}

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["event_id"],
            "userId": row["user_id"],
            "recordId": row["record_id"],
            "memoryId": row["memory_id"],
            "action": row["action"],
            "detail": _json_dict(row["detail_json"]),
            "createdAt": row["created_at"],
        }

    def list_growth_events(self, user_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM growth_events WHERE user_id = ? ORDER BY created_at, event_id"
        params: List[Any] = [user_id]
        if limit:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        return [self._row_to_event(row) for row in self._rows(sql, params)]

    # ------------------------------------------------------------------ 写入
    def _insert(self, *, user_id: str, category: str, content: str, importance: int, status: str,
                source_type: str, source_id: str, metadata: Dict[str, Any],
                query_patterns: Optional[List[str]] = None, commit: bool = True) -> str:
        """写一条记忆。`commit=False` 时只执行不提交，交给调用方在一个事务里提交
        （成长记录的「记录 + 候选」要求原子落库：半截写入会让记录看起来有候选却查不到）。"""
        now = self._now()
        memory_id = f"memory_{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO memory_items (memory_id, user_id, category, content, status, importance, source_type,"
                " source_id, metadata_json, query_patterns_json, triggers_pending, created_at, updated_at, confirmed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    user_id,
                    category,
                    content,
                    status,
                    importance,
                    source_type,
                    source_id,
                    json.dumps(metadata, ensure_ascii=False),
                    json.dumps(query_patterns or [], ensure_ascii=False),
                    0 if status == "confirmed" else 1,
                    now,
                    now,
                    now if status == "confirmed" else None,
                ),
            )
            if commit:
                self._conn.commit()
        if status == "confirmed":
            # 写路径用规则版：确认记忆是一次 HTTP 写请求，不能同步等模型（单次实测 5–20 s）。
            # 模型版由 `POST /api/memories/{id}/triggers` 显式触发（用户点「重建触发器」时）。
            self.generate_triggers(user_id, memory_id, generator=WRITE_PATH_GENERATOR)
        return memory_id

    def create(self, user_id: str, category: str, content: str, importance: int = DEFAULT_IMPORTANCE,
               status: str = "confirmed") -> Dict[str, Any]:
        """用户手写一条记忆。`status='candidate'` 用于把 AI 观察先停在待确认区。"""
        category = str(category or "").strip()
        if category not in VALID_CATEGORIES:
            raise ValueError(f"未知的记忆类别：{category}")
        if status not in VALID_STATUSES:
            raise ValueError(f"未知的记忆状态：{status}")
        text = str(content or "").strip()
        if not text:
            raise ValueError("记忆内容不能为空")
        if len(text) > MAX_CONTENT_LENGTH:
            raise ValueError(f"记忆内容超过 {MAX_CONTENT_LENGTH} 字")
        importance = max(0, min(100, int(importance)))
        memory_id = self._insert(
            user_id=user_id,
            category=category,
            content=text,
            importance=importance,
            status=status,
            source_type="manual",
            source_id=uuid.uuid4().hex,
            metadata={},
        )
        return self.get_item(user_id, memory_id) or {}

    def sync_profile_candidates(self, user_id: str, profile: Optional[Dict[str, Any]],
                                occupation_label: Optional[Callable[[str], str]] = None) -> List[Dict[str, Any]]:
        """把**已确认**画像投影成待确认记忆；画像没确认就什么都不写。

        与队友实现一致：only from an explicitly confirmed profile —— 未经用户确认的信息
        不允许进入 AI（赛题伦理边界，也是本项目不可违反的设计约束 1）。
        """
        if not profile or profile.get("status") != "confirmed":
            return []
        candidates: List[Tuple[str, str, str]] = []
        goal = str(profile.get("currentGoal") or "").strip()
        if goal:
            candidates.append(("goal", goal, "current_goal"))
        location = str(profile.get("location") or "").strip()
        if location:
            candidates.append(("preference", f"期望地点：{location}", "location"))
        major = str(profile.get("major") or "").strip()
        if major:
            candidates.append(("background", f"专业：{major}", "major"))
        for index, interest in enumerate(profile.get("interests") or []):
            text = str(interest).strip()
            if text:
                candidates.append(("preference", f"感兴趣的方向：{text}", f"interest:{index}"))
        for index, skill in enumerate(profile.get("skills") or []):
            name = skill.get("name") if isinstance(skill, dict) else skill
            text = str(name or "").strip()
            if text:
                candidates.append(("skill", f"{SKILL_PREFIX}{text}", f"skill:{index}"))
        occupation_ids = list(profile.get("candidateOccupationIds") or [])
        if occupation_ids:
            first = str(occupation_ids[0])
            label = occupation_label(first) if occupation_label else first
            candidates.append(("career_target", f"目标职业：{label}", "career_target"))

        written: List[Dict[str, Any]] = []
        for category, content, source_id in candidates:
            memory_id = self._upsert_profile_candidate(user_id, category, content, source_id, profile)
            item = self.get_item(user_id, memory_id)
            if item:
                written.append(item)
        return written

    def _upsert_profile_candidate(self, user_id: str, category: str, content: str, source_id: str,
                                  profile: Dict[str, Any]) -> str:
        """同一来源重复同步时更新候选；**已确认的内容不被静默覆盖**。"""
        rows = self._rows(
            "SELECT * FROM memory_items WHERE user_id = ? AND source_type = 'profile' AND source_id = ?",
            [user_id, source_id],
        )
        metadata = {"profileVersion": profile.get("profileVersion")}
        if rows:
            row = rows[0]
            if row["content"] == content or row["status"] == "confirmed":
                return row["memory_id"]
            now = self._now()
            with self._lock:
                self._conn.execute(
                    "UPDATE memory_items SET content = ?, category = ?, status = 'candidate', importance = ?,"
                    " metadata_json = ?, triggers_pending = 1, updated_at = ?, confirmed_at = NULL"
                    " WHERE memory_id = ?",
                    (
                        content,
                        category,
                        DEFAULT_IMPORTANCE,
                        json.dumps(metadata, ensure_ascii=False),
                        now,
                        row["memory_id"],
                    ),
                )
                self._conn.execute("DELETE FROM memory_triggers WHERE memory_id = ?", [row["memory_id"]])
                self._conn.commit()
            return row["memory_id"]
        return self._insert(
            user_id=user_id,
            category=category,
            content=content,
            importance=DEFAULT_IMPORTANCE,
            status="candidate",
            source_type="profile",
            source_id=source_id,
            metadata=metadata,
        )

    def sync_resume_candidates(self, user_id: str, resume_id: str,
                               entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """简历解析结果 → **待确认**记忆候选（``source_type='resume'``）。

        与画像 sync 同一套语义：只写 ``candidate``，用户确认后才进上下文与推荐；
        同一份简历（``resumeId`` 是内容指纹）重复上传不会产生重复候选。
        """
        written: List[Dict[str, Any]] = []
        for index, entry in enumerate(entries or []):
            category = str(entry.get("category") or "").strip()
            content = str(entry.get("content") or "").strip()[:MAX_CONTENT_LENGTH]
            if category not in VALID_CATEGORIES or not content:
                continue
            source_id = f"{resume_id}:{category}:{index}"
            duplicate = self._rows(
                "SELECT memory_id FROM memory_items WHERE user_id = ? AND source_type = 'resume' AND source_id = ?",
                [user_id, source_id],
            )
            if duplicate:
                continue
            memory_id = self._insert(
                user_id=user_id,
                category=category,
                content=content,
                importance=DEFAULT_IMPORTANCE,
                status="candidate",
                source_type="resume",
                source_id=source_id,
                metadata={"resumeId": resume_id, "anchor": str(entry.get("anchor") or "")},
            )
            item = self.get_item(user_id, memory_id)
            if item:
                written.append(item)
        return written

    def update(self, user_id: str, memory_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        row = self.get_item(user_id, memory_id)
        if row is None:
            return None
        fields: Dict[str, Any] = {}
        if "category" in changes:
            category = str(changes["category"] or "").strip()
            if category not in VALID_CATEGORIES:
                raise ValueError(f"未知的记忆类别：{category}")
            fields["category"] = category
        if "content" in changes:
            content = str(changes["content"] or "").strip()
            if not content or len(content) > MAX_CONTENT_LENGTH:
                raise ValueError("记忆内容为空或超过长度上限")
            fields["content"] = content
        if "importance" in changes:
            fields["importance"] = max(0, min(100, int(changes["importance"])))
        status_changed_to: Optional[str] = None
        if "status" in changes:
            status = str(changes["status"] or "").strip()
            if status not in VALID_STATUSES:
                raise ValueError(f"未知的记忆状态：{status}")
            fields["status"] = status
            fields["confirmed_at"] = self._now() if status == "confirmed" else None
            status_changed_to = status
        if not fields:
            return row
        fields["updated_at"] = self._now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE memory_items SET {assignments} WHERE user_id = ? AND memory_id = ?",
                (*fields.values(), user_id, memory_id),
            )
            self._conn.commit()
        if status_changed_to == "candidate":
            with self._lock:
                self._conn.execute("DELETE FROM memory_triggers WHERE memory_id = ?", [memory_id])
                self._conn.commit()
        elif status_changed_to == "confirmed":
            self.generate_triggers(user_id, memory_id, generator=WRITE_PATH_GENERATOR)
        elif "content" in fields:
            # 内容改了就重生成触发器，避免触发器还在描述旧内容
            self.generate_triggers(user_id, memory_id, generator=WRITE_PATH_GENERATOR)
        return self.get_item(user_id, memory_id)

    def delete(self, user_id: str, memory_id: str) -> bool:
        """删除即遗忘：触发器由外键 CASCADE 一并清掉。"""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM memory_items WHERE user_id = ? AND memory_id = ?", (user_id, memory_id)
            )
            self._conn.commit()
        return bool(cursor.rowcount)

    # ------------------------------------------------------------------ 触发器
    def generate_triggers(self, user_id: str, memory_id: str, generator: str = "auto") -> List[Dict[str, Any]]:
        """为一条已确认记忆生成（或重建）触发器；未确认时抛 ValueError。

        `generator` 三档：
        * `auto`（缺省）：配好 LLM 就调模型，否则走规则版；
        * `llm`：强制要模型结果，失败就**降级成规则版**并把 `generated_by` 记成
          `rule-based-fallback`（不抛异常 —— 触发器生成失败不该阻断记忆的使用）；
        * `rule-based`：只要确定性结果（离线/演示/对可复现性有要求的场合）。

        无论走哪条路，都会在 `self.last_trigger_note` 里留下这次到底发生了什么，
        供接口回传与 `/health` 排查 —— 「模型没生效」必须是能被看见的。
        """
        if generator not in TRIGGER_GENERATORS:
            raise UnknownGeneratorError(
                f"未知的触发器生成方式：{generator}（可选 {', '.join(TRIGGER_GENERATORS)}）"
            )
        item = self.get_item(user_id, memory_id)
        if item is None:
            raise LookupError(f"未找到记忆：{memory_id}")
        if item["status"] != "confirmed":
            raise ValueError("只有已确认的记忆才生成触发器（候选记忆不进入任何消费链路）")

        requested = generator
        if generator == "auto":
            generator = GENERATED_BY_LLM if self._llm.configured else GENERATED_BY_RULE

        note: Dict[str, Any] = {
            "requested": requested,
            "used": generator,
            "model": self._llm.config.model if self._llm.config else None,
            "error": None,
        }
        entries: Optional[List[Dict[str, Any]]] = None
        generated_by = GENERATED_BY_RULE
        model_name = ""

        if generator == GENERATED_BY_LLM:
            try:
                raw = generate_triggers_with_llm(self._llm, item["content"])
                entries = [
                    {
                        "triggerId": f"trigger_{uuid.uuid4().hex[:12]}",
                        "level": 1,
                        "concept": entry["concept"],
                        "bridge": entry["bridge"],
                        "activationPatterns": entry["activationPatterns"],
                        "confidence": entry["confidence"],
                        "generatedBy": GENERATED_BY_LLM,
                        "createdAt": self._now(),
                    }
                    for entry in raw
                ]
                generated_by = GENERATED_BY_LLM
                model_name = self._llm.config.model if self._llm.config else ""
            except LlmError as error:
                # 关键：**不抛**。记忆的基本可用性（persona + 词面召回）不依赖触发器，
                # 但失败必须留在 note 里，且 generated_by 要标成 fallback 以便事后统计。
                note["error"] = {"code": error.code, "message": str(error), "detail": error.detail}
                note["used"] = GENERATED_BY_RULE_FALLBACK
                generated_by = GENERATED_BY_RULE_FALLBACK

        if entries is None:
            entries = build_triggers(item["content"], item["category"], self._now(), self._vocabulary)
            for entry in entries:
                entry["generatedBy"] = generated_by

        with self._lock:
            self._conn.execute("DELETE FROM memory_triggers WHERE memory_id = ?", [memory_id])
            for entry in entries:
                self._conn.execute(
                    "INSERT INTO memory_triggers (trigger_id, memory_id, level, concept, bridge,"
                    " activation_patterns_json, confidence, generated_by, generated_by_model, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        entry["triggerId"],
                        memory_id,
                        entry["level"],
                        entry["concept"],
                        entry["bridge"],
                        json.dumps(entry["activationPatterns"], ensure_ascii=False),
                        entry["confidence"],
                        entry["generatedBy"],
                        model_name,
                        entry["createdAt"],
                    ),
                )
            self._conn.execute(
                "UPDATE memory_items SET triggers_pending = 0, updated_at = ? WHERE memory_id = ?",
                (self._now(), memory_id),
            )
            self._conn.commit()
        note["count"] = len(entries)
        self.last_trigger_note = note
        return self.triggers_for([memory_id])

    # ------------------------------------------------------------------ 召回
    @staticmethod
    def _recall_score(item: Dict[str, Any], triggers: List[Dict[str, Any]], query_norm: str) -> Tuple[float, str]:
        """一条已确认记忆对当前提问的相关度（0..1）与命中通道。"""
        if not query_norm:
            return 0.0, ""
        best = 0.0
        channel = ""
        for trigger in triggers:
            concept_norm = normalize_text(trigger["concept"])
            if not concept_norm:
                continue
            for pattern in trigger.get("activationPatterns", []):
                pattern_norm = normalize_text(pattern)
                if pattern_norm and (query_norm in pattern_norm or pattern_norm in query_norm):
                    score = min(1.0, trigger["confidence"] * 0.9 + 0.1)
                    if score > best:
                        best, channel = score, f"trigger:{trigger['concept'][:20]}"
                    break
            if query_norm in concept_norm or concept_norm in query_norm:
                score = min(1.0, trigger["confidence"] * 0.85 + 0.15)
                if score > best:
                    best, channel = score, f"trigger:{trigger['concept'][:20]}"
        word = _word_hit(query_norm, item["content"], *item.get("queryPatterns", []))
        if word >= WORD_HIT_FLOOR and word > best:
            best, channel = word, "word"
        return best, channel

    def build_context(self, user_id: str, query: str = "") -> Dict[str, Any]:
        """一次提问要注入的记忆上下文：persona 常驻 + 本次想起（可预览、可审计）。"""
        items = self.confirmed_items(user_id)
        if not items:
            return {
                "persona": [],
                "recalled": [],
                "merged": [],
                "memoryIds": [],
                "memoryHash": "",
                "summaryText": "",
                "count": 0,
            }
        triggers_by_memory: Dict[str, List[Dict[str, Any]]] = {}
        for trigger in self.triggers_for([item["id"] for item in items]):
            triggers_by_memory.setdefault(trigger["memoryId"], []).append(trigger)

        query_norm = normalize_text(query)
        persona = [item for item in items if item["category"] in PERSONA_CATEGORIES][:PERSONA_LIMIT]

        scored: List[Tuple[float, str, Dict[str, Any]]] = []
        for item in items:
            score, channel = self._recall_score(item, triggers_by_memory.get(item["id"], []), query_norm)
            if score > 0:
                scored.append((score, channel, item))
        scored.sort(key=lambda entry: (-entry[0], -entry[2]["importance"]))

        persona_ids = {item["id"] for item in persona}
        recalled: List[Dict[str, Any]] = []
        for score, channel, item in scored:
            if item["id"] in persona_ids or any(entry["memoryId"] == item["id"] for entry in recalled):
                continue
            if len(recalled) >= RECALL_LIMIT:
                break
            final = round(
                (item["importance"] / 100.0) * W_IMPORTANCE
                + score * W_RECALL
                + _recency_score(item["updatedAt"]) * W_RECENCY,
                4,
            )
            recalled.append(
                {
                    "memoryId": item["id"],
                    "category": item["category"],
                    "content": item["content"],
                    "importance": item["importance"],
                    "recallScore": round(score, 4),
                    "finalScore": final,
                    "channel": channel,
                }
            )

        merged = [{"memoryId": item["id"], "section": "persona", "category": item["category"], "content": item["content"]}
                  for item in persona]
        merged.extend(
            {
                "memoryId": entry["memoryId"],
                "section": "recalled",
                "category": entry["category"],
                "content": entry["content"],
                "recallScore": entry["recallScore"],
                "channel": entry["channel"],
            }
            for entry in recalled
        )

        persona_lines = [f"- {CATEGORY_LABELS.get(item['category'], item['category'])}：{item['content'][:60]}"
                         for item in persona]
        recalled_lines = [
            f"- {CATEGORY_LABELS.get(entry['category'], entry['category'])}：{entry['content'][:60]}"
            f"（相关度 {entry['recallScore']}，{entry['channel']}）"
            for entry in recalled
        ]
        blocks = []
        if persona_lines:
            blocks.append("【常驻】\n" + "\n".join(persona_lines))
        if recalled_lines:
            blocks.append("【本次想起】\n" + "\n".join(recalled_lines))
        return {
            "persona": persona,
            "recalled": recalled,
            "merged": merged,
            "memoryIds": [entry["memoryId"] for entry in merged],
            "memoryHash": self.confirmed_hash(user_id),
            "summaryText": "\n".join(blocks),
            "count": len(merged),
        }

    # ------------------------------------------------------------------ 消费：增强画像
    @staticmethod
    def skill_name_from_memory(content: str) -> str:
        """剥掉写入时加的前缀，取出「技能名」本身。

        只认白名单前缀（形状与队友实现的 `_SKILL_PREFIXES` 一致），**不是**「见到冒号就切」：
        后者会把用户手写的 `Python：异步编程` 这种内容静默截成 `异步编程`，
        变成一个在图谱里根本不存在、却会进匹配算式的技能名。
        """
        text = str(content or "").strip()
        for prefix in SKILL_PREFIXES:
            if text.startswith(prefix):
                return text[len(prefix) :].strip()
        return text[:60]

    def augment_profile(self, profile: Optional[Dict[str, Any]], user_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """已确认记忆补画像空缺（只补空缺，**绝不覆盖显式画像**），并返回用了哪些记忆。

        与队友实现的分工一致：推荐/路径仍是确定性引擎，记忆只做输入增强，且每条都能指回 id。
        """
        base = dict(profile or {})
        memories = self.confirmed_items(user_id)
        evidence: List[Dict[str, Any]] = []
        if not memories:
            return base, evidence

        skills = [dict(skill) if isinstance(skill, dict) else {"name": str(skill), "level": "unknown"}
                  for skill in (base.get("skills") or [])]
        owned = {normalize_text(skill.get("name")) for skill in skills}
        interests = list(base.get("interests") or [])
        interests_norm = {normalize_text(item) for item in interests}

        for item in memories:
            if item["category"] == "skill":
                name = self.skill_name_from_memory(item["content"])
                if not name or normalize_text(name) in owned:
                    continue
                skills.append({"name": name, "level": "unknown"})
                owned.add(normalize_text(name))
                evidence.append({"memoryId": item["id"], "category": item["category"], "content": item["content"],
                                 "usedFor": "skills"})
            elif item["category"] == "goal" and not str(base.get("currentGoal") or "").strip():
                base["currentGoal"] = item["content"]
                evidence.append({"memoryId": item["id"], "category": item["category"], "content": item["content"],
                                 "usedFor": "currentGoal"})
            elif item["category"] == "career_target":
                name = self.skill_name_from_memory(item["content"])
                if name and normalize_text(name) not in interests_norm:
                    interests.append(name)
                    interests_norm.add(normalize_text(name))
                    evidence.append({"memoryId": item["id"], "category": item["category"], "content": item["content"],
                                     "usedFor": "interests"})
            # preference / background 不进确定性引擎：它们会影响解释文案，但不改变匹配算式。
        base["skills"] = skills
        base["interests"] = interests
        return base, evidence

    # ------------------------------------------------------------------ 成长记录（消费侧写入通道）
    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["record_id"],
            "userId": row["user_id"],
            "kind": row["kind"],
            "title": row["title"],
            "before": row["before_text"],
            "after": row["after_text"],
            "explanation": row["explanation"],
            "source": row["source"],
            "occurredAt": row["occurred_at"],
            "createdAt": row["created_at"],
            "metadata": _json_dict(row["metadata_json"]),
        }

    def candidates_for_record(self, user_id: str, record_id: str) -> List[Dict[str, Any]]:
        """这条记录派生出的记忆（候选与已确认都在，界面自己分区）。"""
        rows = self._rows(
            "SELECT * FROM memory_items WHERE user_id = ? AND source_type = ? AND source_id LIKE ?"
            " ORDER BY created_at, memory_id",
            [user_id, GROWTH_SOURCE_TYPE, f"{record_id}:%"],
        )
        return [self._row_to_item(row) for row in rows]

    def list_growth_records(self, user_id: str, kind: Optional[str] = None,
                            limit: Optional[int] = None, offset: Optional[int] = None) -> List[Dict[str, Any]]:
        if kind is not None and kind not in VALID_GROWTH_KINDS:
            raise ValueError(f"未知的记录类别：{kind}")
        sql = "SELECT * FROM growth_records WHERE user_id = ?"
        params: List[Any] = [user_id]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        # 排序里带 record_id 兜底，所以 OFFSET 分页是稳定的（不会因为同一时刻多条而错位）
        sql += " ORDER BY occurred_at DESC, created_at DESC, record_id"
        if limit:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
            if offset:
                sql += " OFFSET ?"
                params.append(max(0, int(offset)))
        records = []
        for row in self._rows(sql, params):
            record = self._row_to_record(row)
            record["candidates"] = self.candidates_for_record(user_id, record["id"])
            records.append(record)
        return records

    def count_growth_records(self, user_id: str, kind: Optional[str] = None) -> int:
        """总条数（分页要用来算 hasMore / nextCursor）。"""
        if kind is not None and kind not in VALID_GROWTH_KINDS:
            raise ValueError(f"未知的记录类别：{kind}")
        sql = "SELECT COUNT(*) AS total FROM growth_records WHERE user_id = ?"
        params: List[Any] = [user_id]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        rows = self._rows(sql, params)
        return int(rows[0]["total"]) if rows else 0

    def get_growth_record(self, user_id: str, record_id: str) -> Optional[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM growth_records WHERE user_id = ? AND record_id = ?", [user_id, record_id])
        if not rows:
            return None
        record = self._row_to_record(rows[0])
        record["candidates"] = self.candidates_for_record(user_id, record_id)
        return record

    def create_growth_record(self, user_id: str, payload: Any,
                             nodes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """写一条成长记录，并把它投影成**待确认**记忆候选（同一事务里落记录 + 候选）。

        `nodes` 由调用方给出（`GraphStore.nodes_in(growth.record_text(record))`）——
        记忆库不碰图谱，只认「这段文本里有哪些已知名词」这个结果。
        客户端带 `recordId` 时按它幂等：重复提交同一条记录不会产生重复行或重复候选。
        """
        record = normalize_record(payload)
        record_id = str(record.pop("recordId", "") or "") or f"growth_{uuid.uuid4().hex[:12]}"
        existing = self.get_growth_record(user_id, record_id)
        if existing is not None:
            return {"record": existing, "candidates": existing["candidates"], "created": False, "note": {
                "reason": "record_exists",
                "message": "同一 recordId 已存在：按幂等处理，不重复写记录与候选",
            }}

        record["recordId"] = record_id
        occurred_at = str(record.get("occurredAt") or "") or self._now()
        now = self._now()
        candidates = derive_candidates(record, nodes)
        written_ids: List[str] = []
        # 记录 + 候选**一个事务**落库：半截写入会让「记录存在但候选查不到」无法自查
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO growth_records (record_id, user_id, kind, title, before_text, after_text,"
                    " explanation, source, occurred_at, created_at, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record_id,
                        user_id,
                        record["kind"],
                        record["title"],
                        record["before"],
                        record["after"],
                        record["explanation"],
                        record["source"],
                        occurred_at,
                        now,
                        json.dumps({"candidateCount": len(candidates)}, ensure_ascii=False),
                    ),
                )
                for entry in candidates:
                    duplicate = self._conn.execute(
                        "SELECT memory_id FROM memory_items WHERE user_id = ? AND source_type = ? AND source_id = ?",
                        (user_id, GROWTH_SOURCE_TYPE, entry["sourceId"]),
                    ).fetchall()
                    if duplicate:
                        continue
                    written_ids.append(
                        self._insert(
                            user_id=user_id,
                            category=entry["category"],
                            content=entry["content"],
                            importance=entry["importance"],
                            status="candidate",
                            source_type=GROWTH_SOURCE_TYPE,
                            source_id=entry["sourceId"],
                            metadata={"recordId": record_id, "kind": record["kind"], "anchor": entry["anchor"]},
                            commit=False,
                        )
                    )
                self._conn.commit()
            except Exception:
                # 必须显式回滚：sqlite3 默认在下次 commit 时会把未提交的语句一起提交上去，
                # 那种「记录写进去了、候选没写」的半截状态最难看懂。
                self._conn.rollback()
                raise

        written = [item for item in (self.get_item(user_id, memory_id) for memory_id in written_ids) if item]

        note = (
            {"reason": "ok", "message": f"从记录里认出了 {len(written)} 条候选记忆，等你确认后才会进入对话与推荐"}
            if written
            else {
                "reason": "no_known_term" if record["kind"] != "任务行动" else "action_only",
                "message": "记录里没有图谱已知的职业/技能名，没有生成候选 —— 不把自由文本塞进记忆库",
            }
        )
        return {"record": self.get_growth_record(user_id, record_id), "candidates": written,
                "created": True, "note": note}

    def delete_growth_record(self, user_id: str, record_id: str) -> Optional[Dict[str, Any]]:
        """删记录：连它派生出的**未确认**候选一起清掉；已确认的记忆归用户，不动。"""
        if self.get_growth_record(user_id, record_id) is None:
            return None
        kept: List[str] = []
        removed: List[str] = []
        for item in self.candidates_for_record(user_id, record_id):
            if item["status"] == "confirmed":
                kept.append(item["id"])
                continue
            if self.delete(user_id, item["id"]):
                removed.append(item["id"])
        with self._lock:
            self._conn.execute("DELETE FROM growth_records WHERE user_id = ? AND record_id = ?", [user_id, record_id])
            self._conn.commit()
        return {
            "deleted": record_id,
            "removedMemoryIds": removed,
            "keptMemoryIds": kept,
            "memoryHash": self.confirmed_hash(user_id),
        }

    def confirm_growth_candidates(self, user_id: str, record_id: str,
                                  memory_ids: Iterable[str]) -> Dict[str, Any]:
        """把某条成长记录派生出的候选确认为正式记忆（M1-3）。

        验收口径「候选 → 记录 → 证据 → 事件一个事务写入；重复 confirm 不产生重复行」：

        * 三张表的写入包在**一个事务**里（半截写入会造出「记忆确认了但证据没有」）；
        * 越权保护：只接受 ``source_type='growth_record'`` 且属于这条记录的候选，
          传了不相干的 id 直接拒绝，不静默跳过（静默跳过会让人以为确认成功了）；
        * 证据与事件都按内容幂等 —— 重复 confirm 只回 ``alreadyConfirmedMemoryIds``，
          不新增行。
        """
        record = self.get_growth_record(user_id, record_id)
        if record is None:
            raise LookupError(f"未找到成长记录：{record_id}")

        wanted = [str(mid).strip() for mid in memory_ids if str(mid).strip()]
        if not wanted:
            raise ValueError("memoryIds 不能为空")
        by_id = {item["id"]: item for item in self.candidates_for_record(user_id, record_id)}
        unknown = [mid for mid in wanted if mid not in by_id]
        if unknown:
            raise ValueError(f"以下记忆不属于这条记录或不存在：{'、'.join(unknown)}")

        now = self._now()
        confirmed: List[str] = []
        already: List[str] = []
        evidence: List[Dict[str, Any]] = []
        events: List[Dict[str, Any]] = []
        with self._lock:
            try:
                for memory_id in wanted:
                    item = by_id[memory_id]
                    if item["status"] == "confirmed":
                        already.append(memory_id)
                    else:
                        self._conn.execute(
                            "UPDATE memory_items SET status = 'confirmed', confirmed_at = ?, updated_at = ?"
                            " WHERE user_id = ? AND memory_id = ?",
                            (now, now, user_id, memory_id),
                        )
                        confirmed.append(memory_id)
                    evidence.append(self.add_profile_evidence(
                        user_id, source_type=GROWTH_SOURCE_TYPE, source_id=f"{record_id}:{memory_id}",
                        field="memory", value=item["content"], locator=record_id, commit=False,
                    ))
                    events.append(self.add_growth_event(
                        user_id, action="confirm_candidate", record_id=record_id, memory_id=memory_id,
                        detail={"category": item["category"], "content": item["content"]}, commit=False,
                    ))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        return {
            "recordId": record_id,
            "confirmedMemoryIds": confirmed,
            "alreadyConfirmedMemoryIds": already,
            "evidence": evidence,
            "events": events,
            "memoryHash": self.confirmed_hash(user_id),
        }
