"""知识图谱数据层。

只读第 11 步导出的真实交付物 ``knowledge/exports/career-graph.json``
（``meta.status == "pipeline-export"``，kbVersion ``2026.09.15``），
把它投影成前端契约需要的目录 / 推荐结构。

设计约束（与 knowledge/pipeline 一致）：
* 只用 Python 标准库，零第三方依赖。
* 不新增任何图谱结论：只做字段映射，以及沿既有边（requires / belongs_to /
  prerequisite / learning_unit / trains / emerging_in / uses）的邻域查询。
* 所有导出文件里的确定性字段（x/y、weight=importance、annotatedBy）原样透传或按需读取，
  不在这里重算布局，也不修改任何标注。
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

EXPORT_ENV = "CAREER_GRAPH_EXPORT"
DEFAULT_RELATIVE = "knowledge/exports/career-graph.json"

OCCUPATION = "occupation"
SKILL = "skill"
KNOWLEDGE = "knowledge"
TASK = "task"
TREND = "trend"
CREDENTIAL = "credential"
DOMAIN = "domain"
TOOL = "tool"

_SEPARATOR_RE = re.compile(r"[\s_\-]+")


def code_of(node_id: str) -> str:
    """``occupation:AI001`` -> ``AI001``；无冒号时原样返回。"""
    return node_id.split(":", 1)[1] if ":" in node_id else node_id


def stage_of(node_id: str) -> str:
    """``knowledge:AI001:stage1`` -> ``stage1``。"""
    return node_id.rsplit(":", 1)[-1] if ":" in node_id else node_id


def normalize(text: Any) -> str:
    """技能/别名匹配用的宽松归一化：去掉空白与常见连接符并转小写。"""
    if text is None:
        return ""
    return _SEPARATOR_RE.sub("", str(text)).strip().lower()


def default_export_path() -> Path:
    """定位导出文件：``CAREER_GRAPH_EXPORT`` 覆盖 > 仓库内向上查找默认路径。"""
    override = os.environ.get(EXPORT_ENV)
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_absolute() else (Path.cwd() / candidate)

    here = Path(__file__).resolve()
    for base in here.parents:
        candidate = base / DEFAULT_RELATIVE
        if candidate.is_file():
            return candidate
    # 未找到时返回一个最可能的路径，让报错信息可读。
    for base in here.parents:
        if (base / "knowledge").is_dir():
            return base / DEFAULT_RELATIVE
    return here.parent / "career-graph.json"


class GraphStore:
    """加载导出文件并提供只读查询；按文件 mtime 自动热更新。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else default_export_path()
        self._lock = threading.RLock()
        self._mtime: Optional[float] = None
        self._raw: Dict[str, Any] = {}
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._by_type: Dict[str, List[Dict[str, Any]]] = {}
        self.refresh(force=True)

    # ------------------------------------------------------------------ 加载
    def refresh(self, force: bool = False) -> None:
        with self._lock:
            if not self.path.is_file():
                raise FileNotFoundError(f"未找到知识图谱导出文件：{self.path}")
            mtime = self.path.stat().st_mtime
            if not force and self._raw and mtime == self._mtime:
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            nodes = {n["id"]: n for n in raw.get("nodes", []) if "id" in n}
            by_type: Dict[str, List[Dict[str, Any]]] = {}
            for edge in raw.get("edges", []):
                by_type.setdefault(edge.get("type", ""), []).append(edge)
            self._raw = raw
            self._nodes = nodes
            self._by_type = by_type
            self._mtime = mtime

    # ------------------------------------------------------------- 基础访问器
    @property
    def meta(self) -> Dict[str, Any]:
        return self._raw.get("meta", {})

    @property
    def counts(self) -> Dict[str, Any]:
        return self.meta.get("counts", {})

    def node(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self._nodes.get(node_id)

    def nodes_of_kind(self, kind: str) -> List[Dict[str, Any]]:
        rows = [n for n in self._nodes.values() if n.get("kind") == kind]
        rows.sort(key=lambda n: n.get("id", ""))
        return rows

    def edges_of_type(self, edge_type: str) -> List[Dict[str, Any]]:
        return list(self._by_type.get(edge_type, []))

    def _label(self, node_id: str) -> str:
        node = self._nodes.get(node_id)
        return node.get("label", code_of(node_id)) if node else code_of(node_id)

    def _out(self, node_id: str, edge_type: str) -> List[Dict[str, Any]]:
        return [e for e in self._by_type.get(edge_type, []) if e.get("from") == node_id]

    def _in(self, node_id: str, edge_type: str) -> List[Dict[str, Any]]:
        return [e for e in self._by_type.get(edge_type, []) if e.get("to") == node_id]

    # ----------------------------------------------------------------- 派生视图
    def required_skills(self, occupation_id: str) -> List[Dict[str, Any]]:
        """occupation --requires--> skill，按 importance 降序（weight 规则同源）。"""
        rows = []
        for edge in self._out(occupation_id, "requires"):
            rows.append(
                {
                    "skillId": code_of(edge["to"]),
                    "skillNodeId": edge["to"],
                    "importance": float(edge.get("importance", 0.0) or 0.0),
                    "targetLevel": edge.get("targetLevel"),
                }
            )
        rows.sort(key=lambda r: (-r["importance"], r["skillId"]))
        return rows

    def domain_labels_for(self, occupation_id: str) -> List[str]:
        """``occupation --requires--> skill --belongs_to--> domain`` 的能力域名（去重）。

        只沿既有边读取真实标签，用于把用户口语化的方向词（如「边缘AI」）映射到职业。
        """
        labels: List[str] = []
        for row in self.required_skills(occupation_id):
            for edge in self._out(row["skillNodeId"], "belongs_to"):
                label = self._label(edge["to"])
                if label and label not in labels:
                    labels.append(label)
        return labels

    def _skill_aliases(self, skill_node: Dict[str, Any]) -> List[str]:
        names = [skill_node.get("label", "")]
        names.extend(skill_node.get("aliases", []) or [])
        return [n for n in names if n]

    def owns_skill(self, skill_node: Dict[str, Any], owned: Iterable[str]) -> bool:
        owned_set = owned if isinstance(owned, set) else set(owned)
        return any(normalize(name) in owned_set for name in self._skill_aliases(skill_node))

    def _future_signal(self, occupation_id: str, required: List[Dict[str, Any]]) -> str:
        """occupation -> skill -> emerging_in -> trend：取该职业所需技能上涌现的第一个趋势。"""
        skill_ids = {r["skillNodeId"] for r in required}
        for edge in self._by_type.get("emerging_in", []):
            if edge.get("to") in skill_ids:
                trend = self._nodes.get(edge.get("from"), {})
                label = trend.get("label")
                if label:
                    return label
        return ""

    def _tasks_for(self, required: List[Dict[str, Any]]) -> List[str]:
        """训练了该职业所需技能的任务清单（task --trains--> skill）。"""
        skill_ids = {r["skillNodeId"] for r in required}
        rows: List[str] = []
        for edge in self._by_type.get("trains", []):
            if edge.get("to") in skill_ids:
                label = self._label(edge.get("from"))
                if label and label not in rows:
                    rows.append(label)
        return rows

    # -------------------------------------------------------------- 契约投影
    def occupation_summary(self, node: Dict[str, Any]) -> Dict[str, Any]:
        required = self.required_skills(node["id"])
        tools = [self._label(t) for t in (e["to"] for e in self._out(node["id"], "uses"))]
        stage_count = len(self._out(node["id"], "learning_unit"))
        return {
            "occupationId": code_of(node["id"]),
            "targetJob": node.get("label", ""),
            # L1 导出没有英文职业名：如实留空，不臆造（见设计 §9.4）。
            "targetJobEn": "",
            "shortName": node.get("label", ""),
            "descriptionZh": node.get("description", ""),
            "coreSkills": [self._label(r["skillNodeId"]) for r in required[:5]],
            "tools": tools,
            "skillCount": len(required),
            "stageCount": stage_count,
        }

    def occupation_detail(self, node: Dict[str, Any]) -> Dict[str, Any]:
        detail = dict(self.occupation_summary(node))
        required = self.required_skills(node["id"])

        skills = []
        for row in required:
            skill_node = self._nodes.get(row["skillNodeId"], {})
            domains = [self._nodes.get(e["to"], {}) for e in self._out(row["skillNodeId"], "belongs_to")]
            domain = domains[0] if domains else {}
            level = row["targetLevel"]
            skills.append(
                {
                    "skillId": row["skillId"],
                    "name": skill_node.get("label", row["skillId"]),
                    "nameZh": skill_node.get("label", row["skillId"]),
                    "category": code_of(domain.get("id", "")) if domain else "",
                    "categoryZh": domain.get("label", "") if domain else "",
                    "targetLevel": level if level is not None else 0,
                    "levelName": f"{level} 级" if level is not None else "",
                    # 导出没有阶段/级别命名，如实留空，由前端按需回退。
                    "stage": "",
                    "stageLabel": "",
                    "importance": row["importance"],
                    "prerequisites": [code_of(e["from"]) for e in self._in(row["skillNodeId"], "prerequisite")],
                }
            )

        stages = []
        units = self._out(node["id"], "learning_unit")
        units.sort(key=lambda e: (e.get("position", 0), e.get("to", "")))
        for edge in units:
            knowledge_node = self._nodes.get(edge["to"], {})
            stages.append(
                {
                    "stage": stage_of(edge["to"]),
                    "stageOrder": edge.get("position", 0),
                    "period": "",
                    "goal": knowledge_node.get("description", ""),
                    "gaps": [],
                    "unit": knowledge_node.get("label", ""),
                    "hours": 0,
                    "task": "",
                    "deliverable": "",
                    "evidence": "",
                }
            )

        detail.update(
            {
                "futureSignalZh": self._future_signal(node["id"], required),
                "tasks": self._tasks_for(required),
                "aliases": list(node.get("aliases", []) or []),
                "skills": skills,
                "stages": stages,
            }
        )
        return detail

    def skill_summary(self, node: Dict[str, Any]) -> Dict[str, Any]:
        domains = [self._nodes.get(e["to"], {}) for e in self._out(node["id"], "belongs_to")]
        domain = domains[0] if domains else {}
        label = node.get("label", "")
        return {
            "skillId": code_of(node["id"]),
            "name": label,
            "nameZh": label,
            "category": code_of(domain.get("id", "")) if domain else "",
            "categoryZh": domain.get("label", "") if domain else "",
            "descriptionZh": node.get("description", ""),
            "prerequisites": [code_of(e["from"]) for e in self._in(node["id"], "prerequisite")],
        }

    def list_occupations(self, keyword: str = "") -> List[Dict[str, Any]]:
        rows = [self.occupation_summary(n) for n in self.nodes_of_kind(OCCUPATION)]
        return self._filter(rows, keyword, ("occupationId", "targetJob", "shortName", "descriptionZh"))

    def list_skills(self, keyword: str = "") -> List[Dict[str, Any]]:
        rows = [self.skill_summary(n) for n in self.nodes_of_kind(SKILL)]
        return self._filter(rows, keyword, ("skillId", "name", "nameZh", "descriptionZh", "categoryZh"))

    @staticmethod
    def _filter(rows: List[Dict[str, Any]], keyword: str, fields: Iterable[str]) -> List[Dict[str, Any]]:
        needle = (keyword or "").strip().lower()
        if not needle:
            return rows
        out = []
        for row in rows:
            haystack = " ".join(str(row.get(f, "")) for f in fields).lower()
            if needle in haystack:
                out.append(row)
        return out

    def catalog_stats(self) -> Dict[str, Any]:
        return {
            "occupations": len(self.nodes_of_kind(OCCUPATION)),
            "skills": len(self.nodes_of_kind(SKILL)),
            "prerequisites": len(self.edges_of_type("prerequisite")),
            "occupationSkills": len(self.edges_of_type("requires")),
            "stages": len(self.edges_of_type("learning_unit")),
            # 契约 union 为 "sqlite" | "json-seed"；本后端读的是 JSON 导出，取后者。
            "source": "json-seed",
        }

    def recommendations(self, profile: Optional[Dict[str, Any]] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        """按画像技能覆盖度给 4 个职业排序；沿 requires 边算缺口，沿 emerging_in 取趋势。"""
        owned = {normalize(s.get("name")) for s in (profile or {}).get("skills", []) if isinstance(s, dict)}
        owned.discard("")

        rows = []
        for node in self.nodes_of_kind(OCCUPATION):
            required = self.required_skills(node["id"])
            total = sum(r["importance"] for r in required)
            covered = 0.0
            matched = 0
            gaps: List[str] = []
            for row in required:
                skill_node = self._nodes.get(row["skillNodeId"], {})
                if self.owns_skill(skill_node, owned):
                    covered += row["importance"]
                    matched += 1
                else:
                    gaps.append(skill_node.get("label", row["skillId"]))
            score = round(100 * covered / total) if total else 0
            rows.append(
                {
                    "occupation_id": code_of(node["id"]),
                    "occupation_name": node.get("label", ""),
                    "match_score": score,
                    "reason": self._recommend_reason(bool(owned), matched, len(gaps)),
                    "core_skills": [self._label(r["skillNodeId"]) for r in required[:3]],
                    "skill_gaps": gaps,
                    "salary_range": "暂未提供",
                    "future_signal": self._future_signal(node["id"], required),
                    "career_path": f"/path?occupation={code_of(node['id'])}",
                }
            )

        rows.sort(key=lambda r: (-r["match_score"], r["occupation_id"]))
        if limit is not None:
            rows = rows[:limit]
        return {"recommendations": rows}

    @staticmethod
    def _recommend_reason(has_profile: bool, matched: int, gap_count: int) -> str:
        if not has_profile:
            return "尚未建立画像：先按岗位要求技能列出，建立画像后即可计算匹配度与技能差距。"
        if matched:
            return f"画像技能命中 {matched} 项岗位要求技能；仍需补齐 {gap_count} 项。"
        return "画像尚未命中该岗位要求技能，建议从核心技能开始补齐。"

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "service": "career-navigator-backend",
            "transport": "http",
            # 用 POSIX 形式暴露（跨平台一致，便于前端/运维自检）。
            "dataSource": self.path.as_posix(),
            "kbVersion": self.meta.get("kbVersion"),
            "graphVersion": self.meta.get("graphVersion"),
            "generatedAt": self.meta.get("generatedAt"),
            "exportStatus": self.meta.get("status"),
            "counts": self.counts,
        }
