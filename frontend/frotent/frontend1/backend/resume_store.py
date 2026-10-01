"""简历存档：把 ``POST /api/resumes/extract`` 的结果落库，供模拟面试选取「简历面试」。

为什么需要它：解析本身是纯函数（``backend/resume.py::extract`` 不写库），``resumeId``
是内容指纹；但模拟面试要按 ``resumeId`` 取回**当时**的解析结果，所以得有一张表。
表建在同一个 ``career.db``（``CAREER_MEMORY_DB`` 可覆盖）。

纪律（与简历解析本身一致）：

* **只存解析结果，不存原文件**：``resume_text`` 存的是已抽取的文本（解析与二次解析都用它），
  文件字节不落盘；响应里仍如实写明「原文件不留存」。
* **认不出就不写**：``analyze_status`` 只会有 ``COMPLETED``（当前解析要么成功要么报错返回 415），
  不留一个「假装在处理中」的中间态。
* **同一份内容幂等**：``UNIQUE(user_id, file_hash)`` —— 重复上传同一份简历不会产生第二行。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .memories import DEFAULT_DB_ENV, now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resumes (
    resume_id          TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    original_filename  TEXT NOT NULL,
    content_type       TEXT NOT NULL,
    file_size          INTEGER NOT NULL,
    file_hash          TEXT NOT NULL,
    resume_text        TEXT NOT NULL,
    analyze_status     TEXT NOT NULL DEFAULT 'COMPLETED',
    analyze_error      TEXT,
    analysis_json      TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_resumes_user_hash ON resumes(user_id, file_hash);
CREATE INDEX IF NOT EXISTS idx_resumes_user_created ON resumes(user_id, created_at DESC);
"""


class ResumeStore:
    """简历存档的读写。与 ``MemoryStore`` / ``InterviewStore`` 同一个 sqlite 文件。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.environ.get(DEFAULT_DB_ENV) or str(Path(__file__).resolve().parent / "career.db")
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _summary(row: sqlite3.Row) -> Dict[str, Any]:
        analysis = json.loads(row["analysis_json"] or "{}")
        return {
            "resumeId": row["resume_id"],
            "filename": row["original_filename"],
            "analyzeStatus": row["analyze_status"],
            "analyzeError": row["analyze_error"],
            "charCount": len(row["resume_text"] or ""),
            "createdAt": row["created_at"],
            # 给前端下拉用的最小画像摘要：只报**算得出来的**字段，没有 overallScore 就不编一个
            "analysis": analysis,
        }

    def save(self, user_id: str, result: Dict[str, Any], *, filename: str, content_type: str,
             file_size: int, text: str) -> Dict[str, Any]:
        """落库并返回摘要。同一 ``(user_id, file_hash)`` 只保留一行（重复上传幂等）。"""
        resume_id = str(result.get("resumeId") or "").strip()
        if not resume_id:
            raise ValueError("解析结果缺少 resumeId")
        import hashlib

        file_hash = hashlib.sha1((text or "").encode("utf-8")).hexdigest()
        now = now_iso()
        with self._lock:
            existing = self._conn.execute(
                "SELECT resume_id, created_at FROM resumes WHERE user_id = ? AND file_hash = ?",
                (user_id, file_hash),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE resumes SET analysis_json = ?, analyze_status = 'COMPLETED', updated_at = ? WHERE resume_id = ?",
                    (json.dumps(result, ensure_ascii=False), now, existing["resume_id"]),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM resumes WHERE resume_id = ?", (existing["resume_id"],)
                ).fetchone()
                return self._summary(row)
            self._conn.execute(
                """INSERT INTO resumes
                   (resume_id, user_id, original_filename, content_type, file_size, file_hash,
                    resume_text, analyze_status, analysis_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,'COMPLETED',?,?,?)""",
                (resume_id, user_id, filename or "pasted-resume.txt", content_type or "text/plain",
                 int(file_size), file_hash, text or "", json.dumps(result, ensure_ascii=False), now, now),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM resumes WHERE resume_id = ?", (resume_id,)).fetchone()
            return self._summary(row)

    def list(self, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM resumes WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
            return [self._summary(row) for row in rows]

    def get(self, user_id: str, resume_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM resumes WHERE user_id = ? AND resume_id = ?", (user_id, resume_id)
            ).fetchone()
            return self._summary(row) if row else None

    def interview_context(self, user_id: str, resume_id: str) -> Optional[Dict[str, Any]]:
        """给模拟面试用的三件套：``(resume_id, filename, analysis)``；找不到返回 ``None``。"""
        record = self.get(user_id, resume_id)
        if not record:
            return None
        return {
            "resumeId": record["resumeId"],
            "filename": record["filename"],
            "analysis": interview_analysis(record["analysis"]),
        }


def interview_analysis(result: Dict[str, Any]) -> Dict[str, Any]:
    """把本项目 ``extract()`` 的结果整形成出题 prompt / 规则模板认得的形状。

    队友那版的 ``ResumeAnalysis.analysis_json`` 形如
    ``{"profile": {"projects": [...], "skills": [...]}, "interviewFocus": [...]}``。
    本项目的解析结果是 ``profileDraft`` + ``skills`` + ``experiences`` + ``unrecognizedSkills``，
    这里**只做字段映射**，不新增任何结论；本项目没有 ``interviewFocus`` 这个概念，
    就如实留空（出题模板会跳过它）。
    """
    draft = result.get("profileDraft") or {}
    skills = [str(entry.get("name")) for entry in (result.get("skills") or []) if entry.get("name")]
    projects = [str(entry.get("title")) for entry in (result.get("experiences") or []) if entry.get("title")]
    if not projects:
        raw = draft.get("experience")
        if isinstance(raw, list):
            projects = [str(item) for item in raw if str(item).strip()]
    return {
        "profile": {
            "projects": projects,
            "skills": skills,
            "major": draft.get("major"),
            "school": draft.get("school"),
        },
        "interviewFocus": [],
    }
