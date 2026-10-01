"""任务实践链路：路径里的实训任务 → 提交行动 → 评估反馈。

这是 `PAGE_FUNCTION_MAP.md` §9 一直写着、但直到 2026-10-01 才做的那条：
**`/path` 安排的任务 → 在 `/actions` 里真正做掉 → 落成长记录与待确认候选**。

三条纪律（与全项目一致）：

1. **任务不臆造**：任务全部来自路径引擎（`backend/career_path.py`）在**图谱 `task --trains--> skill`
   边上**算出来的条目 —— 标题、交付要求、执行步骤（图谱的 `assessmentPoints`）、证据要求、
   出处 `sourceRefs` 都是现成数据。图谱没有的字段（例如**任务级难度**）如实返回 `null`，
   不编一个。
2. **候选不落已确认**：一次提交只能产出**待确认**候选（`backend/growth.py` 那套：
   命中图谱名词才写、认不出就不写）。确认动作留在既有的记忆面板，不在这里代签。
3. **评估是「反馈」不是「结论」**：规则版永远可用；模型版只是更好的措辞，且模型声称的
   「观察到的能力」必须**引用了提交原文里真实存在的片段**，否则降级进 `needsVerification`。

`taskId` 的形式是 `task_<职业号>_<阶段>_<序号>`：路径输出里**没有**暴露图谱任务节点 id
（`career_path._tasks_for_stage` 把它剥掉了，且有测试钉住那个键集），所以这里用路径内的
**稳定位置**做标识，并把任务标题/交付要求一起存进运行记录 —— 路径以后变了，历史记录也不会变形。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .career_path import STAGE_NAMES, STAGE_PERIOD, STAGES
from .llm import LlmClient
from .memories import DEFAULT_DB_ENV
from .resume import parse_llm_json

MODULE = "task-run/v1"

MAX_ACTION_CHARS = 2000
MAX_SUBMISSION_CHARS = 8000
MAX_ATTACHMENTS = 20

STAGE_ORDER = {stage: index for index, stage in enumerate(STAGES)}

# 状态口径（写死在这里，前端与文档照这个说）：
#   completed —— 这条任务至少提交过一次
#   available —— 没有提交，且属于「第一个还没做完的阶段」：现在就能做
#   planned   —— 没有提交，且位于更后面的阶段
STATUS_COMPLETED = "completed"
STATUS_AVAILABLE = "available"
STATUS_PLANNED = "planned"
VALID_STATUSES = (STATUS_PLANNED, STATUS_AVAILABLE, STATUS_COMPLETED)

_TASK_ID_RE = re.compile(r"^task_([A-Za-z0-9\-]+)_([a-z]+)_(\d+)$")

DISCLAIMER = "单次表现不构成已掌握能力；候选观察需你在记忆面板确认后才会进入对话与推荐。"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_runs (
    run_id              TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    request_id          TEXT,
    task_id             TEXT NOT NULL,
    occupation_id       TEXT NOT NULL,
    stage               TEXT NOT NULL,
    title               TEXT NOT NULL,
    deliverable         TEXT NOT NULL DEFAULT '',
    action              TEXT NOT NULL,
    submission          TEXT NOT NULL,
    attachment_ids_json TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'SUBMITTED',
    growth_record_id    TEXT,
    candidate_ids_json  TEXT NOT NULL DEFAULT '[]',
    feedback_json       TEXT,
    provider            TEXT,
    submitted_at        TEXT NOT NULL,
    evaluated_at        TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runs_request
    ON task_runs(user_id, request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_task_runs_user_task
    ON task_runs(user_id, task_id, submitted_at DESC);
"""


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_task_id(occupation_id: str, stage: str, index: int) -> str:
    return f"task_{occupation_id}_{stage}_{index}"


def parse_task_id(task_id: str) -> Tuple[str, str, int]:
    """拆 `task_<职业号>_<阶段>_<序号>`。不合法抛 ``ValueError``。"""
    match = _TASK_ID_RE.match(str(task_id or "").strip())
    if not match:
        raise ValueError(f"任务 ID 形状不对：{task_id}")
    occupation_id, stage, index = match.group(1), match.group(2), int(match.group(3))
    if stage not in STAGE_ORDER:
        raise ValueError(f"未知阶段：{stage}")
    return occupation_id, stage, index


# ---------------------------------------------------------------------------- 任务派生


def derive_tasks(path_result: Dict[str, Any], submitted_task_ids: Optional[set] = None) -> List[Dict[str, Any]]:
    """把路径引擎的输出摊平成任务清单（纯函数，便于单测）。

    `submitted_task_ids` 是「该用户已经提交过的 taskId」集合，用来算状态；
    不传就全部按「没提交过」处理。
    """
    submitted = submitted_task_ids or set()
    occupation_id = str(path_result.get("occupation_id") or "")
    target_job = str(path_result.get("target_job") or "")
    stages = [stage for stage in (path_result.get("path") or []) if isinstance(stage, dict)]

    # 先摊平，再统一判状态：需要知道「每条任务有没有提交」才能定 available/planned
    flattened: List[Dict[str, Any]] = []
    for stage in stages:
        stage_key = str(stage.get("stage") or "")
        skills_by_id = {
            str(skill.get("skill_id")): skill
            for skill in (stage.get("skills") or []) if isinstance(skill, dict)
        }
        tasks = [task for task in (stage.get("tasks") or []) if isinstance(task, dict)]
        for index, task in enumerate(tasks):
            task_id = build_task_id(occupation_id, stage_key, index)
            required = [skills_by_id[skill_id] for skill_id in (task.get("required_skill_ids") or [])
                        if skill_id in skills_by_id]
            flattened.append({
                "taskId": task_id,
                "title": str(task.get("task") or ""),
                "sourcePath": {
                    "occupationId": occupation_id,
                    "targetJob": target_job,
                    "stage": stage_key,
                    "stageName": STAGE_NAMES.get(stage_key, stage_key),
                    "period": str(stage.get("period") or STAGE_PERIOD.get(stage_key, "")),
                    "stageGoal": str(stage.get("goal") or ""),
                    "stageOrder": STAGE_ORDER.get(stage_key, 0) + 1,
                },
                "requiredSkills": [{
                    "skillId": str(skill.get("skill_id") or ""),
                    "name": str(skill.get("name_zh") or ""),
                    "status": str(skill.get("status") or ""),
                    "gap": int(skill.get("gap") or 0),
                    "targetLevel": int(skill.get("target_level") or 0),
                } for skill in required],
                "tools": [str(item) for item in (task.get("tools") or [])],
                "steps": [str(item) for item in (task.get("subtasks") or [])],
                "deliverable": str(task.get("deliverable") or ""),
                "evidenceTargets": [
                    {"type": str(item.get("type") or ""), "description": str(item.get("description") or "")}
                    for item in (task.get("evidence") or []) if isinstance(item, dict)
                ],
                "sourceRefs": [str(item) for item in (task.get("source_refs") or [])],
                # 图谱没有任务级难度与任务级学时：如实留空，不编。
                # 学时只有**阶段级** `estimated_hours`（由技能等级差 × HOURS_PER_LEVEL 算出）。
                "difficulty": None,
                "estimatedHours": None,
                "stageEstimatedHours": int(stage.get("estimated_hours") or 0),
                "stageEstimatedWeeks": int(stage.get("estimated_weeks") or 0),
                "unavailableFields": ["difficulty", "estimatedHours"],
                "_submitted": task_id in submitted,
            })

    # 第一个「有任务且没全做完」的阶段 = 当前可做阶段
    available_stage = ""
    for stage in stages:
        stage_key = str(stage.get("stage") or "")
        stage_tasks = [item for item in flattened if item["sourcePath"]["stage"] == stage_key]
        if stage_tasks and not all(item["_submitted"] for item in stage_tasks):
            available_stage = stage_key
            break

    for item in flattened:
        if item.pop("_submitted"):
            item["status"] = STATUS_COMPLETED
        elif item["sourcePath"]["stage"] == available_stage:
            item["status"] = STATUS_AVAILABLE
        else:
            item["status"] = STATUS_PLANNED
    return flattened


# ---------------------------------------------------------------------------- 规则版评估


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?；;：:（）()【】\[\]]", "", text or "").lower()


def _fallback_evaluation(task: Dict[str, Any], action: str, submission: str) -> Dict[str, Any]:
    """不需要模型就能给的反馈：覆盖了多少要求技能、结构如何、还缺什么证据。"""
    text = f"{action}\n{submission}"
    normalized = _normalize(text)
    observed: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    for skill in task.get("requiredSkills") or []:
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        if _normalize(name) and _normalize(name) in normalized:
            snippet = _snippet_around(text, name)
            observed.append({"skillId": skill.get("skillId"), "name": name, "evidence": snippet})
        else:
            missing.append({"kind": "skill", "name": name,
                            "why": "提交内容里没有提到这项要求能力，规则版无法判断你是否用到它"})

    needs: List[Dict[str, Any]] = list(missing)
    for target in task.get("evidenceTargets") or []:
        description = str(target.get("description") or "").strip()
        if description:
            needs.append({"kind": "evidence", "name": description,
                          "why": "需要可核验的交付物；规则版只能看到你的文字描述，交付物本身要另外举证"})
    if len(_normalize(submission)) < 40:
        needs.append({"kind": "detail", "name": "提交内容过短",
                      "why": "不足 40 个有效字符：说清背景、判断、做法与结果，才谈得上可评估"})
    if task.get("steps"):
        needs.append({"kind": "steps", "name": f"执行步骤共 {len(task['steps'])} 条",
                      "why": "规则版不对照步骤逐条判定；请确认每一步是否真的做过并有痕迹"})

    coverage = len(observed) / len(task.get("requiredSkills") or [1]) if task.get("requiredSkills") else 0.0
    length_score = min(50, len(_normalize(submission)) // 4)
    structure_bonus = 10 if any(token in submission for token in ("首先", "其次", "最后", "第一", "第二", "然后")) else 0
    evidence_bonus = 10 if any(token in submission for token in ("例如", "数据", "指标", "截图", "结果", "实测")) else 0
    score = min(95, round(length_score + structure_bonus + evidence_bonus + coverage * 30))

    strengths: List[str] = []
    improvements: List[str] = []
    if observed:
        strengths.append(f"提交里能看到对 {len(observed)} 项要求能力的实际使用：{ '、'.join(item['name'] for item in observed[:3]) }。")
    if structure_bonus:
        strengths.append("叙述有先后结构，便于他人复核。")
    if evidence_bonus:
        strengths.append("提到了可核验的结果（数据/指标/实测），比只说「做过了」更有说服力。")
    if missing:
        improvements.append(f"优先补上没提到的能力：{ '、'.join(item['name'] for item in missing[:3]) }。")
    if not evidence_bonus:
        improvements.append("补一句可核验的结果（数字、指标或交付物链接），让这次行动有据可查。")
    if task.get("steps"):
        improvements.append("对照「执行步骤」逐条自查，把没做的部分说清楚 —— 比含糊带过更有价值。")
    return {
        "runId": "",
        "taskId": task.get("taskId"),
        "provider": "fallback",
        "score": score,
        "coverage": round(coverage, 4),
        "summary": "本次提交已经记录了你的行动。下面的观察只是**候选**，还不是已掌握的能力。" ,
        "strengths": strengths or ["愿意把行动写下来并暴露不确定的部分。"],
        "improvements": improvements or ["继续保持并补充可核验的结果。"],
        "observedAbilities": observed,
        "needsVerification": needs,
        "disclaimer": DISCLAIMER,
    }


def _snippet_around(text: str, needle: str, width: int = 40) -> str:
    """截取原文里出现该能力名的那一段，作为「引用出处」。

    只做切片与首尾去空白 —— 保留下来的仍然是**逐字子串**（换成把换行替换成空格就
    不再是子串了，前端与用例都拿它做 includes 校验）。
    """
    position = text.find(needle)
    if position < 0:
        return ""
    start = max(0, position - width)
    end = min(len(text), position + len(needle) + width)
    return text[start:end].strip()


def _evaluation_prompt(task: Dict[str, Any], action: str, submission: str) -> str:
    prompt = {
        "task": "评估一次职场实践任务的提交，并返回结构化反馈",
        "taskTitle": task.get("title"),
        "stage": task.get("sourcePath", {}).get("stageName"),
        "goal": task.get("sourcePath", {}).get("stageGoal"),
        "requiredSkills": [skill.get("name") for skill in (task.get("requiredSkills") or [])],
        "steps": task.get("steps"),
        "deliverable": task.get("deliverable"),
        "userAction": action,
        "userSubmission": submission,
        "rules": [
            "只能依据用户提交的内容判断，不要补造他做过但没写的事",
            "observedAbilities[].name 必须**逐字**取自 requiredSkills 列表：不要改写、不要新增、不要翻译",
            "observedAbilities 的每一条必须 quote「行动说明 / 文本成果」里**逐字存在**的一段原文作为 evidence",
            "提交里找不到证据的能力**不要**写进 observedAbilities，放进 needsVerification",
            "不要给出「已掌握」这类结论性评价：这只是一次练习",
            "只返回 JSON 对象，不返回 Markdown",
        ],
        "schema": {
            "summary": "string",
            "strengths": ["string"],
            "improvements": ["string"],
            "observedAbilities": [{"name": "string", "evidence": "string"}],
            "needsVerification": [{"kind": "skill|evidence|detail", "name": "string", "why": "string"}],
        },
    }
    return json.dumps(prompt, ensure_ascii=False)


def _merge_model_evaluation(task: Dict[str, Any], source_text: str, payload: Dict[str, Any],
                            base: Dict[str, Any]) -> Dict[str, Any]:
    """把模型输出并进规则版报告。

    模型声称的 `observedAbilities` 必须过两道闸才被接受：
    ① `name` 必须是这条任务要求的能力之一；② `evidence` 必须在**用户自己写的原文**
    （行动说明 + 文本成果）里逐字存在。
    过不了的降级进 `needsVerification` —— 这一条是「不把模型的话当证据」的落点。
    """
    required = {str(skill.get("name") or ""): skill for skill in (task.get("requiredSkills") or [])}
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for item in payload.get("observedAbilities") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if name not in required:
            rejected.append({"kind": "skill", "name": name or "（未命名）",
                             "why": "模型提到它，但它不是这条任务要求的能力，未采纳"})
            continue
        if not evidence or evidence not in source_text:
            rejected.append({"kind": "skill", "name": name,
                             "why": "模型没有引用原文里逐字存在的片段，未采纳为观察结果"})
            continue
        accepted.append({"skillId": required[name].get("skillId"), "name": name, "evidence": evidence})

    def _strings(key: str, fallback: List[str]) -> List[str]:
        values = [str(item).strip() for item in payload.get(key) or [] if str(item).strip()]
        return values[:5] or fallback

    # 模型给的观察放在前面（它的措辞更好），规则版观察按名字去重续在后面 ——
    # **不是替换**：规则版那几条同样满足「名字属于要求能力 + 出处逐字可查」，
    # 模型没提到它们不等于没发生，丢掉反而更差。
    merged = list(accepted)
    seen = {item["name"] for item in merged}
    for item in base["observedAbilities"]:
        if item["name"] not in seen:
            merged.append(item)
            seen.add(item["name"])

    return {
        **base,
        "provider": "llm",
        "summary": str(payload.get("summary") or base["summary"]).strip()[:600],
        "strengths": _strings("strengths", base["strengths"]),
        "improvements": _strings("improvements", base["improvements"]),
        "observedAbilities": merged,
        "needsVerification": [*rejected, *base["needsVerification"]][:12],
        "coverage": round(len(merged) / len(required), 4) if required else 0.0,
    }


# ---------------------------------------------------------------------------- 存储


class TaskStore:
    """任务运行记录的读写。与记忆库同一个 `career.db`，一把可重入锁管连接。"""

    def __init__(self, path: Optional[str] = None, llm: Optional[LlmClient] = None,
                 now: Optional[Callable[[], str]] = None) -> None:
        self.path = path or os.environ.get(DEFAULT_DB_ENV) or str(Path(__file__).resolve().parent / "career.db")
        self._now = now or _now
        self._llm = llm
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

    @staticmethod
    def _row_to_run(row: sqlite3.Row, with_feedback: bool = True) -> Dict[str, Any]:
        out = {
            "runId": row["run_id"],
            "taskId": row["task_id"],
            "occupationId": row["occupation_id"],
            "stage": row["stage"],
            "title": row["title"],
            "deliverable": row["deliverable"],
            "action": row["action"],
            "submission": row["submission"],
            "attachmentIds": json.loads(row["attachment_ids_json"] or "[]"),
            "status": row["status"],
            "growthRecordId": row["growth_record_id"],
            "candidateIds": json.loads(row["candidate_ids_json"] or "[]"),
            "submittedAt": row["submitted_at"],
            "evaluatedAt": row["evaluated_at"],
        }
        if with_feedback:
            out["feedback"] = json.loads(row["feedback_json"]) if row["feedback_json"] else None
            out["provider"] = row["provider"]
        return out

    def get_run(self, user_id: str, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task_runs WHERE user_id = ? AND run_id = ?", (user_id, run_id)
            ).fetchone()
            return self._row_to_run(row) if row else None

    def own_run(self, user_id: str, run_id: str) -> Optional[Dict[str, Any]]:
        """归属校验：不是自己的运行记录一律当作不存在。"""
        return self.get_run(user_id, run_id)

    def list_runs(self, user_id: str, task_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if task_id:
                rows = self._conn.execute(
                    "SELECT * FROM task_runs WHERE user_id = ? AND task_id = ? ORDER BY submitted_at DESC",
                    (user_id, task_id),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM task_runs WHERE user_id = ? ORDER BY submitted_at DESC", (user_id,)
                ).fetchall()
            return [self._row_to_run(row) for row in rows]

    def find_by_request(self, user_id: str, request_id: str) -> Optional[Dict[str, Any]]:
        """按 `requestId` 找已有运行。**必须在写任何东西之前查**，否则重试会多出一条成长记录。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task_runs WHERE user_id = ? AND request_id = ?", (user_id, request_id)
            ).fetchone()
            return self._row_to_run(row) if row else None

    @staticmethod
    def new_run_id() -> str:
        """先拿到 runId，再拿它当成长记录的 recordId —— 两边用同一个 id 才能互相指认。"""
        return f"run_{uuid.uuid4().hex[:12]}"

    def submitted_task_ids(self, user_id: str) -> set:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT task_id FROM task_runs WHERE user_id = ?", (user_id,)
            ).fetchall()
            return {row["task_id"] for row in rows}

    def create_run(self, *, run_id: str, user_id: str, task: Dict[str, Any], action: str, submission: str,
                   attachment_ids: List[str], growth_record_id: str, candidate_ids: List[str],
                   request_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            if request_id:
                existing = self._conn.execute(
                    "SELECT * FROM task_runs WHERE user_id = ? AND request_id = ?", (user_id, request_id)
                ).fetchone()
                if existing:
                    return self._row_to_run(existing)
            self._conn.execute(
                """INSERT INTO task_runs
                   (run_id, user_id, request_id, task_id, occupation_id, stage, title, deliverable,
                    action, submission, attachment_ids_json, status, growth_record_id,
                    candidate_ids_json, submitted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?, 'SUBMITTED', ?,?,?)""",
                (run_id, user_id, request_id, task["taskId"], task["sourcePath"]["occupationId"],
                 task["sourcePath"]["stage"], task["title"], task.get("deliverable") or "",
                 action, submission, json.dumps(attachment_ids, ensure_ascii=False),
                 growth_record_id, json.dumps(candidate_ids, ensure_ascii=False), self._now()),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM task_runs WHERE run_id = ?", (run_id,)).fetchone()
            return self._row_to_run(row)

    def evaluate(self, task: Dict[str, Any], run: Dict[str, Any]) -> Dict[str, Any]:
        """产出反馈。已评估过就直接返回存档的那一份（幂等）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT feedback_json FROM task_runs WHERE run_id = ?", (run["runId"],)
            ).fetchone()
            if row and row["feedback_json"]:
                return json.loads(row["feedback_json"])

        base = _fallback_evaluation(task, run["action"], run["submission"])
        base["runId"] = run["runId"]
        report = base
        if self._llm is not None and getattr(self._llm, "configured", False):
            try:
                raw = self._llm.chat(
                    _evaluation_prompt(task, run["action"], run["submission"]),
                    max_tokens=3000, timeout=180, attempts=2, backoff=(4.0, 20.0),
                )
                payload = parse_llm_json(raw)
                if not isinstance(payload, dict):
                    raise ValueError("模型返回的 JSON 不是对象")
                report = _merge_model_evaluation(task, f"{run['action']}\n{run['submission']}", payload, base)
            except Exception:  # noqa: BLE001 —— 兜底失败绝不能打断评估
                report = base
        with self._lock:
            self._conn.execute(
                "UPDATE task_runs SET feedback_json = ?, provider = ?, status = 'EVALUATED', evaluated_at = ? WHERE run_id = ?",
                (json.dumps(report, ensure_ascii=False), report["provider"], self._now(), run["runId"]),
            )
            self._conn.commit()
        return report
