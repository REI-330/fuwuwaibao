"""跨岗位沟通训练：32 个职业题库，单选作答 + 维度加权报告。

移植自队友项目 `career-ai-system` 的 ``backend/app/api/cross_role.py`` 与
``backend/app/services/cross_role.py``（FastAPI + SQLAlchemy async），**按本项目纪律重写**：

* **同步 + 标准库 ``sqlite3``**，表建在同一个 ``career.db``；
* **不需要模型**：题目与「推荐处理方式 / 为什么 / 要避免什么」全部来自题库，
  这正是它适合做「互动式场景训练」的原因（离线、可复算、不靠端点）；
* **选项顺序每题随机**（``random.SystemRandom``），避免记住「答案永远是 B」；
* **题库来源与免责声明原样保留**：题库文件顶部有 ``source`` 与 ``disclaimer``，
  训练结果一律标注「用于训练反馈，不作为正式人才测评结论」。

题库文件（``backend/data/cross_role_questionnaires.json``，1.05 MB / 32 角色 / 320 题）
来自队友项目的同一份文件，未做内容改动；键名从 ``cross-role-questionnaires.json``
改成下划线只是为了跟本仓库的文件命名一致。
"""

from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import threading
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .memories import DEFAULT_DB_ENV

MODULE = "cross-role/v1"

QUESTION_BANK_PATH = Path(__file__).resolve().parent / "data" / "cross_role_questionnaires.json"
EXPECTED_ROLES = 32

DIMENSION_NAMES = {
    "delivery": "项目推进",
    "trust": "团队信任",
    "alignment": "信息同步",
    "riskControl": "风险控制",
}

# 题库只按职业编号分组；这里把编号映射成「岗位族」，用于前端分类筛选。
ROLE_GROUPS = {
    "AI002": "产品、设计与业务", "AI007": "产品、设计与业务", "AI008": "产品、设计与业务",
    "AI010": "产品、设计与业务", "AI027": "产品、设计与业务", "AI028": "产品、设计与业务",
    "AI029": "产品、设计与业务", "AI030": "产品、设计与业务",
    "AI004": "软件研发与交付", "AI009": "软件研发与交付", "AI012": "软件研发与交付",
    "AI017": "软件研发与交付", "AI018": "软件研发与交付", "AI019": "软件研发与交付",
    "AI020": "软件研发与交付",
    "AI001": "AI 与数据", "AI003": "AI 与数据", "AI005": "AI 与数据", "AI006": "AI 与数据",
    "AI013": "AI 与数据", "AI014": "AI 与数据", "AI015": "AI 与数据",
    "AI011": "安全、运维与数据库", "AI021": "安全、运维与数据库", "AI022": "安全、运维与数据库",
    "AI023": "安全、运维与数据库", "AI024": "安全、运维与数据库", "AI025": "安全、运维与数据库",
    "AI026": "安全、运维与数据库",
    "AI016": "硬件与自动化", "AI031": "硬件与自动化", "AI032": "硬件与自动化",
}

MODES = ("practice", "assessment")

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cross_role_sessions (
    session_id              TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    request_id              TEXT,
    role_id                 TEXT NOT NULL,
    role_name               TEXT NOT NULL,
    mode                    TEXT NOT NULL DEFAULT 'practice',
    total_questions         INTEGER NOT NULL DEFAULT 10,
    current_question_index  INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'IN_PROGRESS',
    overall_score           INTEGER,
    report_json             TEXT,
    created_at              TEXT NOT NULL,
    completed_at            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cross_role_user_request
    ON cross_role_sessions(user_id, request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cross_role_user_created
    ON cross_role_sessions(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS cross_role_questions (
    question_id           TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL,
    source_question_id    TEXT NOT NULL,
    position              INTEGER NOT NULL,
    question              TEXT NOT NULL,
    options_json          TEXT NOT NULL,
    correct_option_id     TEXT NOT NULL,
    recommended_approach  TEXT NOT NULL,
    explanation           TEXT NOT NULL,
    pitfall_advice        TEXT NOT NULL,
    dimensions_json       TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES cross_role_sessions(session_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cross_role_question_position
    ON cross_role_questions(session_id, position);

CREATE TABLE IF NOT EXISTS cross_role_answers (
    answer_id           TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    question_id         TEXT NOT NULL,
    selected_option_id  TEXT NOT NULL,
    is_correct          INTEGER NOT NULL DEFAULT 0,
    answered_at         TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES cross_role_sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES cross_role_questions(question_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cross_role_answer_question
    ON cross_role_answers(session_id, question_id);
"""


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _strip_option_letters(text: str) -> str:
    """题库里的「选项 A」在随机打乱顺序后已经对不上，统一改成「错误做法」。"""
    return re.sub(r"选项 [A-D]", "错误做法", text or "")


# ---------------------------------------------------------------------------- 题库


@lru_cache(maxsize=1)
def load_question_bank() -> Dict[str, Any]:
    with QUESTION_BANK_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    roles = payload.get("roles")
    if not isinstance(roles, list) or len(roles) != EXPECTED_ROLES:
        raise ValueError(f"跨岗位沟通题库不完整（期望 {EXPECTED_ROLES} 个角色）")
    return payload


def list_roles() -> List[Dict[str, Any]]:
    return [{
        "roleId": role["roleId"],
        "name": role["roleName"],
        "description": role["description"],
        "category": ROLE_GROUPS.get(role["roleId"], "其他"),
        "questionCount": len(role["questions"]),
    } for role in load_question_bank()["roles"]]


def get_role(role_id: str) -> Optional[Dict[str, Any]]:
    return next((item for item in load_question_bank()["roles"] if item["roleId"] == role_id), None)


def bank_metadata() -> Dict[str, Any]:
    payload = load_question_bank()
    return {
        "schemaVersion": payload.get("schemaVersion"),
        "source": payload.get("source"),
        "disclaimer": payload.get("disclaimer"),
        "roleCount": len(payload["roles"]),
        "questionCount": sum(len(role["questions"]) for role in payload["roles"]),
    }


# ---------------------------------------------------------------------------- 存储


class CrossRoleStore:
    """跨岗位训练的读写。与记忆库同一个 sqlite 文件，一把可重入锁管连接。"""

    def __init__(self, path: Optional[str] = None, now: Optional[Callable[[], str]] = None) -> None:
        self.path = path or os.environ.get(DEFAULT_DB_ENV) or str(Path(__file__).resolve().parent / "career.db")
        self._now = now or _now
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- 行 → dict

    def _question_rows(self, session_id: str) -> List[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM cross_role_questions WHERE session_id = ? ORDER BY position", (session_id,)
        ).fetchall()

    def _answer_rows(self, session_id: str) -> Dict[str, sqlite3.Row]:
        rows = self._conn.execute(
            "SELECT * FROM cross_role_answers WHERE session_id = ?", (session_id,)
        ).fetchall()
        return {row["question_id"]: row for row in rows}

    def _session_row(self, user_id: str, session_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM cross_role_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()

    def get_owned_session(self, user_id: str, session_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._session_row(user_id, session_id)

    def _feedback(self, question: sqlite3.Row, answer: sqlite3.Row) -> Dict[str, Any]:
        return {
            "isCorrect": bool(answer["is_correct"]),
            "correctOptionId": question["correct_option_id"],
            "recommendedApproach": question["recommended_approach"],
            "explanation": question["explanation"],
            "pitfallAdvice": _strip_option_letters(question["pitfall_advice"]),
            "dimensions": json.loads(question["dimensions_json"]),
        }

    def session_to_dict(self, session_row: sqlite3.Row) -> Dict[str, Any]:
        session_id = session_row["session_id"]
        questions = self._question_rows(session_id)
        answers = self._answer_rows(session_id)
        completed = session_row["status"] == "COMPLETED"
        items: List[Dict[str, Any]] = []
        for question in questions:
            answer = answers.get(question["question_id"])
            # 练习模式：作答后立刻揭晓；测评模式：交卷后才揭晓
            can_reveal = bool(answer) and (completed or session_row["mode"] == "practice")
            items.append({
                "questionId": question["question_id"],
                "position": question["position"],
                "question": question["question"],
                "options": json.loads(question["options_json"]),
                "selectedOptionId": answer["selected_option_id"] if answer else None,
                "answeredAt": answer["answered_at"] if answer else None,
                "feedback": self._feedback(question, answer) if answer and can_reveal else None,
            })
        return {
            "sessionId": session_id,
            "roleId": session_row["role_id"],
            "roleName": session_row["role_name"],
            "mode": session_row["mode"],
            "status": session_row["status"],
            "totalQuestions": session_row["total_questions"],
            "answeredQuestions": len(answers),
            "currentQuestionIndex": session_row["current_question_index"],
            "overallScore": session_row["overall_score"],
            "createdAt": session_row["created_at"],
            "completedAt": session_row["completed_at"],
            "questions": items,
        }

    # ---------------------------------------------------------------- 会话

    def create_session(self, *, user_id: str, role_id: str, mode: str = "practice",
                       request_id: Optional[str] = None) -> Dict[str, Any]:
        mode = mode if mode in MODES else "practice"
        with self._lock:
            if request_id:
                existing = self._conn.execute(
                    "SELECT * FROM cross_role_sessions WHERE user_id = ? AND request_id = ?",
                    (user_id, request_id),
                ).fetchone()
                if existing:
                    return self.session_to_dict(existing)
            role = get_role(role_id)
            if not role:
                raise ValueError("ROLE_NOT_FOUND")
            session_id = str(uuid.uuid4())
            created_at = self._now()
            self._conn.execute(
                """INSERT INTO cross_role_sessions
                   (session_id, user_id, request_id, role_id, role_name, mode, total_questions,
                    current_question_index, status, created_at)
                   VALUES (?,?,?,?,?,?,?,0,'IN_PROGRESS',?)""",
                (session_id, user_id, request_id, role_id, role["roleName"], mode,
                 len(role["questions"]), created_at),
            )
            randomizer = random.SystemRandom()
            for source in role["questions"]:
                option_rows: List[Dict[str, str]] = []
                correct_option_id = ""
                for option in source["options"]:
                    option_id = uuid.uuid4().hex
                    option_rows.append({"optionId": option_id, "text": option["text"]})
                    if option["sourceId"] == source["correctSourceId"]:
                        correct_option_id = option_id
                randomizer.shuffle(option_rows)
                self._conn.execute(
                    """INSERT INTO cross_role_questions
                       (question_id, session_id, source_question_id, position, question, options_json,
                        correct_option_id, recommended_approach, explanation, pitfall_advice, dimensions_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), session_id, source["questionId"], source["position"],
                     source["question"], json.dumps(option_rows, ensure_ascii=False),
                     correct_option_id, source["recommendedApproach"], source["explanation"],
                     source["pitfallAdvice"], json.dumps(source["dimensions"], ensure_ascii=False)),
                )
            self._conn.commit()
            return self.session_to_dict(self._session_row(user_id, session_id))

    def save_answer(self, session_row: sqlite3.Row, question_id: str, option_id: str) -> Optional[Dict[str, Any]]:
        session_id = session_row["session_id"]
        with self._lock:
            question = self._conn.execute(
                "SELECT * FROM cross_role_questions WHERE session_id = ? AND question_id = ?",
                (session_id, question_id),
            ).fetchone()
            if not question:
                return None
            valid_option_ids = {item["optionId"] for item in json.loads(question["options_json"])}
            if option_id not in valid_option_ids:
                raise ValueError("OPTION_NOT_FOUND")
            existing = self._conn.execute(
                "SELECT answer_id FROM cross_role_answers WHERE session_id = ? AND question_id = ?",
                (session_id, question_id),
            ).fetchone()
            timestamp = self._now()
            is_correct = int(option_id == question["correct_option_id"])
            if existing:
                self._conn.execute(
                    "UPDATE cross_role_answers SET selected_option_id = ?, is_correct = ?, updated_at = ? WHERE answer_id = ?",
                    (option_id, is_correct, timestamp, existing["answer_id"]),
                )
            else:
                self._conn.execute(
                    """INSERT INTO cross_role_answers
                       (answer_id, session_id, question_id, selected_option_id, is_correct, answered_at, updated_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), session_id, question_id, option_id, is_correct, timestamp, timestamp),
                )
            self._conn.execute(
                "UPDATE cross_role_sessions SET current_question_index = ? WHERE session_id = ?",
                (min(session_row["total_questions"] - 1,
                     max(session_row["current_question_index"], question["position"])), session_id),
            )
            self._conn.commit()
            return self.session_to_dict(self._session_row(session_row["user_id"], session_id))

    def _build_report(self, session_row: sqlite3.Row, questions: List[sqlite3.Row],
                      answers: Dict[str, sqlite3.Row]) -> Dict[str, Any]:
        correct_count = sum(1 for answer in answers.values() if answer["is_correct"])
        dimension_weighted = {key: 0.0 for key in DIMENSION_NAMES}
        dimension_totals = {key: 0.0 for key in DIMENSION_NAMES}
        details: List[Dict[str, Any]] = []
        for question in questions:
            answer = answers.get(question["question_id"])
            dimensions = json.loads(question["dimensions_json"])
            for key in DIMENSION_NAMES:
                weight = float(dimensions.get(key, 0) or 0)
                dimension_totals[key] += weight
                # 答错不是 0 分：这道题在这个维度上仍有「部分表现」，按 0.35 计入（与队友口径一致）
                if answer:
                    dimension_weighted[key] += weight * (1.0 if answer["is_correct"] else 0.35)
            details.append({
                "questionId": question["question_id"],
                "position": question["position"],
                "question": question["question"],
                "options": json.loads(question["options_json"]),
                "selectedOptionId": answer["selected_option_id"] if answer else None,
                "correctOptionId": question["correct_option_id"],
                "isCorrect": bool(answer and answer["is_correct"]),
                "recommendedApproach": question["recommended_approach"],
                "explanation": question["explanation"],
                "pitfallAdvice": _strip_option_letters(question["pitfall_advice"]),
            })
        dimension_scores = [{
            "key": key,
            "name": name,
            "score": round(dimension_weighted[key] / dimension_totals[key] * 100) if dimension_totals[key] else 0,
        } for key, name in DIMENSION_NAMES.items()]
        ranked = sorted(dimension_scores, key=lambda item: item["score"], reverse=True)
        total = session_row["total_questions"] or 0
        overall = round(correct_count / total * 100) if total else 0
        return {
            "sessionId": session_row["session_id"],
            "roleId": session_row["role_id"],
            "roleName": session_row["role_name"],
            "mode": session_row["mode"],
            "totalQuestions": total,
            "answeredQuestions": len(answers),
            "correctAnswers": correct_count,
            "overallScore": overall,
            "dimensions": dimension_scores,
            "strengths": [f"{item['name']}是本次相对稳定的协作表现。" for item in ranked[:2]],
            "improvements": [f"优先练习{item['name']}：复盘错误选项，并把推荐表达应用到真实协作中。" for item in ranked[-2:]],
            "questionDetails": details,
            "disclaimer": "本结果用于训练反馈，不作为正式人才测评结论。",
        }

    def complete_session(self, session_row: sqlite3.Row) -> Dict[str, Any]:
        session_id = session_row["session_id"]
        with self._lock:
            if session_row["report_json"]:
                return json.loads(session_row["report_json"])
            questions = self._question_rows(session_id)
            answers = self._answer_rows(session_id)
            report = self._build_report(session_row, questions, answers)
            self._conn.execute(
                """UPDATE cross_role_sessions
                   SET status = 'COMPLETED', overall_score = ?, completed_at = ?, report_json = ?
                   WHERE session_id = ?""",
                (report["overallScore"], self._now(), json.dumps(report, ensure_ascii=False), session_id),
            )
            self._conn.commit()
            return report

    def read_report(self, session_row: sqlite3.Row) -> Optional[Dict[str, Any]]:
        with self._lock:
            return json.loads(session_row["report_json"]) if session_row["report_json"] else None

    def list_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM cross_role_sessions WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
            return [{
                "sessionId": row["session_id"],
                "roleId": row["role_id"],
                "roleName": row["role_name"],
                "mode": row["mode"],
                "status": row["status"],
                "totalQuestions": row["total_questions"],
                "currentQuestionIndex": row["current_question_index"],
                "overallScore": row["overall_score"],
                "createdAt": row["created_at"],
                "completedAt": row["completed_at"],
            } for row in rows]

    def delete_session(self, session_row: sqlite3.Row) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM cross_role_sessions WHERE session_id = ?", (session_row["session_id"],)
            )
            self._conn.commit()
