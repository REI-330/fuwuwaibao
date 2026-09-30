"""职业路径引擎（M1-4）：由图谱真实边算出成长路径，零三方依赖。

设计纪律（与 `项目架构与技术文档.md` §6/§7.2 一致）：

1. **差距与顺序是算出来的，不是写出来的**：阶段、先修顺序、缺口全部来自
   ``occupation --requires--> skill`` 与 ``skill --prerequisite--> skill`` 两张真实边表，
   做拓扑排序；模型不参与（``career_path`` 里没有任何 LLM 调用）。
2. **同输入两次输出完全相同**：所有排序都有确定的次级键（skill_id / task_id），
   不生成随机 id、不读时钟来决定结构。唯一带时间的字段是 ``generated_at``，
   可由调用方注入（测试传固定时钟即得逐字节相同的结果）。
3. **图谱没有的字段如实留空，不臆造**：``target_job_en`` 在导出里没有对应字段，
   就返回空串；任务与工具的对应关系图谱没记，就在 ``warnings`` 里说明工具的归属
   只是职业级，而不是编一条“这个任务用这个工具”。

这个模块不碰 HTTP，也不碰 sqlite：只吃 ``GraphStore``。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .knowledge import GraphStore, code_of, normalize

RULES_VERSION = "rules-1.0.0"
METRICS_VERSION = "metrics-1.0.0"

STAGES: Tuple[str, str, str] = ("junior", "intermediate", "advanced")
STAGE_NAMES = {"junior": "入门基础", "intermediate": "独立实践", "advanced": "高级发展"}
STAGE_PERIOD = {"junior": "0—1年", "intermediate": "1—3年", "advanced": "3—5年"}

# 先修深度 → 基础阶段。用**结构**定阶段而不是目标等级：目标等级表示「这个职业需要多高」，
# 不是「这个技能有多基础」。若按目标等级定，一条高等级的公共先修会把它的所有后继
# 一起顶到最高阶段，整条路径会塌进一个阶段（实测过）。
STAGE_BY_DEPTH = {0: "junior", 1: "intermediate", 2: "advanced"}
STAGE_ORDER = {stage: index for index, stage in enumerate(STAGES)}

MIN_LEVEL = 0
MAX_LEVEL = 5
# 每提升 1 级所需投入（小时）。这是本引擎的**明示假设**，不是从语料推出来的：
# 图谱没有学时数据，所以它写在常量里并在 `warnings` 里说明。
HOURS_PER_LEVEL = 24
DEFAULT_WEEKLY_HOURS = 10
# 每阶段建议的最长周期（周）。同样是本引擎的明示假设。
RECOMMENDED_MAX_WEEKS = {"junior": 26, "intermediate": 52, "advanced": 78}
GRADE_BANDS = ((90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "E"))


class OccupationNotFound(LookupError):
    """目标职业在图谱里不存在。与「参数非法」分开：一个该 404，一个该 400。"""


class InvalidCareerPathInput(ValueError):
    """请求体本身不合法（缺 target_job、current_level 越界等）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- 图谱访问


class _Graph:
    """把图谱里本引擎用得到的边包成窄接口（只读）。"""

    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self._prereq_of: Dict[str, List[str]] = {}
        self._trains: Dict[str, List[Dict[str, Any]]] = {}
        for edge in store.edges_of_type("prerequisite"):
            self._prereq_of.setdefault(edge["to"], []).append(edge["from"])
        for edge in store.edges_of_type("trains"):
            self._trains.setdefault(edge["to"], []).append(edge)
        for values in self._prereq_of.values():
            values.sort()
        for values in self._trains.values():
            values.sort(key=lambda e: e["from"])

    def label(self, node_id: str) -> str:
        node = self.store.node(node_id) or {}
        return str(node.get("label") or code_of(node_id))

    def aliases(self, node_id: str) -> List[str]:
        node = self.store.node(node_id) or {}
        return [str(item) for item in (node.get("aliases") or [])]

    def prerequisites(self, skill_node_id: str) -> List[str]:
        return list(self._prereq_of.get(skill_node_id, []))

    def tasks_for(self, skill_node_id: str) -> List[Dict[str, Any]]:
        return list(self._trains.get(skill_node_id, []))

    def tools_for_occupation(self, occupation_node_id: str) -> List[str]:
        rows = [e for e in self.store.edges_of_type("uses") if e.get("from") == occupation_node_id]
        return [self.label(e["to"]) for e in sorted(rows, key=lambda e: e.get("to", ""))]

    def stage_goals(self, occupation_node_id: str) -> Dict[int, Dict[str, Any]]:
        """learning_unit 边（position 1..3）→ 该阶段的真实目标与出处。"""
        rows = [e for e in self.store.edges_of_type("learning_unit") if e.get("from") == occupation_node_id]
        out: Dict[int, Dict[str, Any]] = {}
        for edge in rows:
            node = self.store.node(edge["to"]) or {}
            out[int(edge.get("position") or 0)] = {
                "goal": str(node.get("description") or ""),
                "unit": str(node.get("label") or ""),
                "sourceRefs": [str(ref) for ref in (edge.get("sourceRefs") or [])],
            }
        return out


def resolve_occupation(store: GraphStore, target: Any) -> str:
    """把 target_job（id / ``occupation:AI001`` / 中文名 / 别名）解析成职业节点 id。"""
    raw = str(target or "").strip()
    if not raw:
        raise InvalidCareerPathInput("target_job 不能为空")

    if raw.startswith("occupation:") and store.node(raw):
        return raw
    prefixed = f"occupation:{raw}"
    if store.node(prefixed):
        return prefixed
    upper = raw.upper()
    if store.node(f"occupation:{upper}"):
        return f"occupation:{upper}"

    # 名称 / 别名：先精确，再按图谱名词表命中（`nodes_in` 已按名字长度排序）
    for node in store.nodes_of_kind("occupation"):
        names = [str(node.get("label") or "")] + [str(a) for a in (node.get("aliases") or [])]
        if raw in names:
            return node["id"]
    needle = normalize(raw)
    for node in store.nodes_in(raw):
        if node.get("kind") == "occupation":
            return node["id"]
    for node in store.nodes_of_kind("occupation"):
        names = [normalize(str(node.get("label") or ""))] + [normalize(str(a)) for a in (node.get("aliases") or [])]
        if any(needle and (needle in name or name in needle) for name in names if name):
            return node["id"]
    raise OccupationNotFound(f"未找到职业：{raw}")


# --------------------------------------------------------------------------- 先修计算


def _depths(graph: _Graph, nodes: Sequence[str]) -> Dict[str, int]:
    """每个技能的前置深度：没有前置 = 0，否则 1 + max(前置深度)。

    用带染色标记的迭代 DFS：图谱理论上可无环，但**真出现环也要能返回**，
    而不是递归爆栈 —— 环由 ``hard_checks.prerequisite_cycle`` 如实报出。
    """
    scope = set(nodes)
    depth: Dict[str, int] = {}
    visiting: set = set()

    def visit(start: str) -> int:
        stack: List[Tuple[str, bool]] = [(start, False)]
        while stack:
            node, expanded = stack.pop()
            if node in depth:
                continue
            if expanded:
                parents = [p for p in graph.prerequisites(node) if p in scope]
                depth[node] = 0 if not parents else 1 + max(depth.get(p, 0) for p in parents)
                visiting.discard(node)
                continue
            if node in visiting:  # 环：当作深度 0 截断，环本身另行报出
                depth[node] = 0
                continue
            visiting.add(node)
            stack.append((node, True))
            for parent in graph.prerequisites(node):
                if parent in scope and parent not in depth:
                    stack.append((parent, False))
        return depth.get(start, 0)

    for node in sorted(scope):
        if node not in depth:
            visit(node)
    return depth


def _has_cycle(graph: _Graph, nodes: Sequence[str]) -> bool:
    """技能先修图里是否存在环（标准三色标记）。"""
    scope = set(nodes)
    color: Dict[str, int] = {}

    def walk(start: str) -> bool:
        stack: List[Tuple[str, int]] = [(start, 0)]
        while stack:
            node, index = stack.pop()
            if index == 0:
                if color.get(node) == 1:
                    return True
                if color.get(node) == 2:
                    continue
                color[node] = 1
            parents = [p for p in graph.prerequisites(node) if p in scope]
            if index < len(parents):
                stack.append((node, index + 1))
                stack.append((parents[index], 0))
            else:
                color[node] = 2
        return False

    for node in sorted(scope):
        if color.get(node, 0) == 0 and walk(node):
            return True
    return False


def _level_inputs(payload: Dict[str, Any], graph: _Graph) -> Tuple[Dict[str, int], List[str]]:
    """解析 ``current_skills``（请求里显式给的等级）。返回 (skill_node_id → level, warnings)。"""
    levels: Dict[str, int] = {}
    warnings: List[str] = []
    rows = payload.get("current_skills")
    if rows is None:
        return levels, warnings
    if not isinstance(rows, list):
        raise InvalidCareerPathInput("current_skills 必须是数组")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InvalidCareerPathInput(f"current_skills[{index}] 必须是对象")
        node_id = _resolve_skill(graph, row.get("skill_id") or row.get("skill_name"))
        if node_id is None:
            # 认不出来不算错误：如实说出来，别静默丢掉（用户以为算进去了）
            warnings.append(f"current_skills[{index}] 未匹配到图谱技能，已忽略")
            continue
        raw_level = row.get("current_level")
        try:
            level = int(raw_level)
        except (TypeError, ValueError):
            raise InvalidCareerPathInput(f"current_skills[{index}].current_level 必须是 0–{MAX_LEVEL} 的整数")
        if not MIN_LEVEL <= level <= MAX_LEVEL:
            raise InvalidCareerPathInput(f"current_skills[{index}].current_level 超出 0–{MAX_LEVEL}")
        levels[node_id] = max(levels.get(node_id, MIN_LEVEL), level)
    return levels, warnings


def _resolve_skill(graph: _Graph, raw: Any) -> Optional[str]:
    value = str(raw or "").strip()
    if not value:
        return None
    store = graph.store
    if value.startswith("skill:") and store.node(value):
        return value
    if store.node(f"skill:{value}"):
        return f"skill:{value}"
    if store.node(f"skill:{value.upper()}"):
        return f"skill:{value.upper()}"
    needle = normalize(value)
    for node in store.nodes_of_kind("skill"):
        names = [str(node.get("label") or "")] + [str(a) for a in (node.get("aliases") or [])]
        if value in names or any(needle == normalize(name) for name in names if name):
            return node["id"]
    return None


def _levels_from_profile(profile: Optional[Dict[str, Any]], graph: _Graph) -> Tuple[Dict[str, int], List[str]]:
    """画像里的技能当参考等级。

    画像的 ``skills[].level`` 是 ``"unknown"``（见 ``ProfileStore._normalize``）——
    它只说明「这个人声称具备」，并不给等级。这里按 **1 级**计入，并在 warnings 里明说，
    不假装它是实测等级。
    """
    levels: Dict[str, int] = {}
    matched: List[str] = []
    if not isinstance(profile, dict):
        return levels, matched
    for row in profile.get("skills") or []:
        if not isinstance(row, dict):
            continue
        node_id = _resolve_skill(graph, row.get("name"))
        if node_id:
            levels[node_id] = max(levels.get(node_id, MIN_LEVEL), 1)
            matched.append(graph.label(node_id))
    return levels, matched


# --------------------------------------------------------------------------- 主流程


def generate(store: GraphStore, payload: Any, profile: Optional[Dict[str, Any]] = None,
             now: Optional[Any] = None) -> Dict[str, Any]:
    """生成一条职业路径。返回 ``GeneratedCareerPath``（字段与前端类型契约同名）。"""
    if not isinstance(payload, dict):
        raise InvalidCareerPathInput("请求体必须是 JSON 对象")
    graph = _Graph(store)
    occupation_node = resolve_occupation(store, payload.get("target_job"))

    raw_weekly = payload.get("weekly_hours")
    if raw_weekly is None:
        weekly_hours = DEFAULT_WEEKLY_HOURS
    else:
        try:
            weekly_hours = int(raw_weekly)
        except (TypeError, ValueError):
            raise InvalidCareerPathInput("weekly_hours 必须是正整数")
        if weekly_hours <= 0:
            raise InvalidCareerPathInput("weekly_hours 必须是正整数")

    required = store.required_skills(occupation_node)
    required_ids = [row["skillNodeId"] for row in required]
    prereq_closure: set = set()
    frontier = list(required_ids)
    while frontier:
        current = frontier.pop()
        for parent in graph.prerequisites(current):
            if parent not in prereq_closure and parent not in required_ids:
                prereq_closure.add(parent)
                frontier.append(parent)

    scope = sorted(set(required_ids) | prereq_closure)
    warnings: List[str] = []

    request_levels, level_warnings = _level_inputs(payload, graph)
    warnings.extend(level_warnings)
    profile_levels, profile_matched = _levels_from_profile(profile, graph)
    if profile_matched:
        warnings.append(
            "画像里的技能未标等级，按入门（1 级）计入：" + "、".join(sorted(set(profile_matched)))
        )

    levels: Dict[str, int] = dict(profile_levels)
    levels.update(request_levels)  # 请求里显式给的等级优先于画像推断

    depth = _depths(graph, scope)
    required_set = set(required_ids)
    meta = {row["skillNodeId"]: row for row in required}

    # 谁卡住了别人（决定了「优先学习」的判定）
    blocks: set = set()
    for node_id in scope:
        if levels.get(node_id, 0) < _target_level(meta, node_id):
            for child in scope:
                if node_id in graph.prerequisites(child) and node_id != child:
                    blocks.add(node_id)

    skills: List[Dict[str, Any]] = []
    for node_id in scope:
        target_level = _target_level(meta, node_id)
        current_level = max(MIN_LEVEL, min(MAX_LEVEL, int(levels.get(node_id, 0))))
        gap = max(0, target_level - current_level)
        prerequisite_only = node_id not in required_set
        if gap == 0:
            status = "satisfied"
        elif current_level > 0:
            status = "improve"
        elif node_id in blocks or prerequisite_only:
            status = "priority_learning"
        else:
            status = "learning"
        base_stage = STAGE_BY_DEPTH[min(depth.get(node_id, 0), 2)]
        importance = float(meta.get(node_id, {}).get("importance", 0.0) or 0.0)
        skills.append(
            {
                "skill_id": code_of(node_id),
                "name_zh": graph.label(node_id),
                "current_level": current_level,
                "target_level": target_level,
                "gap": gap,
                "importance": round(importance, 4),
                "status": status,
                "prerequisite_ids": [code_of(p) for p in graph.prerequisites(node_id)],
                "priority_score": round(importance * 10 + gap * 2 + depth.get(node_id, 0), 2),
                "prerequisite_depth": depth.get(node_id, 0),
                "default_stage": base_stage,
                "prerequisite_only": prerequisite_only,
                "primary_learning": status in ("learning", "priority_learning"),
                "_node_id": node_id,
                "_base_stage": base_stage,
            }
        )

    # 阶段不得早于任何先修：按深度升序推进，逐级取 max
    by_node = {item["_node_id"]: item for item in skills}
    for item in sorted(skills, key=lambda s: (s["prerequisite_depth"], s["skill_id"])):
        floor = item["_base_stage"]
        for parent in graph.prerequisites(item["_node_id"]):
            if parent in by_node:
                floor = _later_stage(floor, by_node[parent]["default_stage"])
        if STAGE_ORDER[floor] > STAGE_ORDER[item["_base_stage"]]:
            item["default_stage"] = floor
            item["stage_adjustment_reason"] = "prerequisite_promotion"

    skills.sort(key=lambda s: (-s["priority_score"], s["skill_id"]))

    goals = graph.stage_goals(occupation_node)
    occupation_tools = graph.tools_for_occupation(occupation_node)
    if occupation_tools:
        warnings.append(
            "图谱未记录「任务 ↔ 工具」的对应，下面任务里的工具按职业级给出：" + "、".join(occupation_tools)
        )

    stages: List[Dict[str, Any]] = []
    for position, stage in enumerate(STAGES, start=1):
        stage_skills = [s for s in skills if s["default_stage"] == stage]
        unmet = [s for s in stage_skills if s["gap"] > 0]
        tasks, task_skill_ids = _tasks_for_stage(graph, stage_skills, occupation_tools)
        hours = sum(s["gap"] for s in unmet) * HOURS_PER_LEVEL
        weeks = -(-hours // weekly_hours) if hours else 0  # 上取整
        goal_row = goals.get(position) or {}
        stages.append(
            {
                "stage": stage,
                "period": STAGE_PERIOD[stage],
                "goal": goal_row.get("goal") or f"{STAGE_NAMES[stage]}：{graph.label(occupation_node)}的核心能力",
                "skills": [{
                    key: value for key, value in skill.items() if not key.startswith("_")
                } for skill in stage_skills],
                "tasks": tasks,
                "estimated_hours": hours,
                "estimated_weeks": int(weeks),
                "satisfied_ratio": round(
                    (len(stage_skills) - len(unmet)) / len(stage_skills), 4) if stage_skills else 1.0,
                "compressed": not unmet,
                "stage_skipped": not unmet,
                "_task_skill_ids": task_skill_ids,
                "_source_refs": goal_row.get("sourceRefs") or [],
            }
        )

    covered_importance = sum(s["importance"] for s in skills if s["gap"] == 0 and not s["prerequisite_only"])
    total_importance = sum(float(row["importance"] or 0.0) for row in required) or 0.0
    match_score = round(covered_importance / total_importance, 4) if total_importance else 0.0

    gap_summary = {
        "total_target_skills": len(required_ids),
        "satisfied_count": len([s for s in skills if s["gap"] == 0 and not s["prerequisite_only"]]),
        "improve_count": len([s for s in skills if s["status"] == "improve"]),
        "learning_count": len([s for s in skills if s["status"] == "learning"]),
        "priority_learning_count": len([s for s in skills if s["status"] == "priority_learning"]),
    }

    evaluation, extra_warnings = _evaluate(graph, payload, skills, stages, scope, match_score)
    warnings.extend(extra_warnings)
    if match_score == 0:
        in_scope = set(scope)
        known = [item for item in list(request_levels) + list(profile_levels) if item in in_scope]
        if known:
            # 画像/请求里确实有该职业的技能，只是等级还不够 —— 不能说「未提供」
            warnings.append("已有技能的水平仍低于目标等级，匹配度按 0 计。")
        elif request_levels or profile_levels:
            warnings.append("提供的当前技能都不在该职业的技能范围内，匹配度按 0 计。")
        else:
            warnings.append("未提供任何当前技能：匹配度按 0 计，路径按从零开始编排。")
    # 契约里有 evaluation.warnings：与顶层 warnings 同源，前端两处都能读到。
    evaluation["warnings"] = list(warnings)

    return {
        "occupation_id": code_of(occupation_node),
        "target_job": graph.label(occupation_node),
        # 导出里没有英文名的对应字段：如实留空，不猜（前端契约允许空串）
        "target_job_en": "",
        "profile_summary": _profile_summary(payload, profile, graph, occupation_node),
        "match_score": match_score,
        "match_type": "profile_coverage" if (request_levels or profile_levels) else "no_profile",
        "weekly_hours": weekly_hours,
        "skill_gap_summary": gap_summary,
        "path": [
            {key: value for key, value in stage.items() if not key.startswith("_")} for stage in stages
        ],
        "evaluation": evaluation,
        "generated_at": (now or _now_iso)(),
        "rules_version": RULES_VERSION,
        "metrics_version": METRICS_VERSION,
        "warnings": warnings,
    }


def _target_level(meta: Dict[str, Dict[str, Any]], node_id: str) -> int:
    raw = meta.get(node_id, {}).get("targetLevel")
    if raw is None:
        # 只作为先修出现的技能没有目标等级：按入门（1 级）计，够用且可解释
        return 1
    return max(MIN_LEVEL, min(MAX_LEVEL, int(raw)))


def _later_stage(left: str, right: str) -> str:
    return left if STAGE_ORDER[left] >= STAGE_ORDER[right] else right


def _tasks_for_stage(graph: _Graph, stage_skills: Sequence[Dict[str, Any]],
                     tools: Sequence[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """该阶段技能的实训任务（``task --trains--> skill`` 反向取）。"""
    out: List[Dict[str, Any]] = []
    skill_ids: List[str] = []
    seen: set = set()
    for skill in stage_skills:
        if skill["gap"] == 0 and skill["prerequisite_only"]:
            continue
        for edge in graph.tasks_for(skill["_node_id"]):
            task_id = edge["from"]
            skill_ids.append(skill["skill_id"])
            if task_id in seen:
                continue
            seen.add(task_id)
            node = graph.store.node(task_id) or {}
            deliverable = str(edge.get("deliverable") or "")
            out.append(
                {
                    "task": str(node.get("label") or code_of(task_id)),
                    "required_skill_ids": [skill["skill_id"]],
                    "tools": list(tools),
                    "deliverable": deliverable,
                    "evidence": [{"type": "demo", "description": deliverable}] if deliverable else [],
                    "subtasks": [str(point) for point in (edge.get("assessmentPoints") or [])],
                    "source_refs": [str(ref) for ref in (edge.get("sourceRefs") or [])],
                    "_task_id": task_id,
                }
            )
    out.sort(key=lambda item: item["_task_id"])
    return [{k: v for k, v in task.items() if not k.startswith("_")} for task in out], skill_ids


def _profile_summary(payload: Dict[str, Any], profile: Optional[Dict[str, Any]],
                     graph: _Graph, occupation_node: str) -> str:
    parts: List[str] = []
    if isinstance(profile, dict):
        for key, label in (("school", "学校"), ("major", "专业"), ("grade", "年级")):
            value = profile.get(key)
            if value:
                parts.append(f"{label}{value}")
        skills = [str(s.get("name")) for s in (profile.get("skills") or []) if isinstance(s, dict) and s.get("name")]
        if skills:
            parts.append("画像技能 " + "、".join(skills))
    for key, label in (("education", "学历"), ("major", "专业"), ("experience_years", "工作年限")):
        value = payload.get(key)
        if value not in (None, ""):
            parts.append(f"{label}{value}")
    if payload.get("career_goal"):
        parts.append(f"职业目标：{payload['career_goal']}")
    prefix = f"目标职业：{graph.label(occupation_node)}"
    return prefix if not parts else f"{prefix}；" + "；".join(parts)


def _evaluate(graph: _Graph, payload: Dict[str, Any], skills: Sequence[Dict[str, Any]],
              stages: Sequence[Dict[str, Any]], scope: Sequence[str],
              match_score: float) -> Tuple[Dict[str, Any], List[str]]:
    """六维指标 + 六项硬校验 + 工作量提示。全部由上面算出的结构自检得出。"""
    warnings: List[str] = []

    # --- 硬校验（True = 违规）
    cycle = _has_cycle(graph, scope)
    missing_skill_id = any(not graph.store.node(f"skill:{s['skill_id']}") for s in skills)
    invalid_level = any(
        not (MIN_LEVEL <= s["current_level"] <= MAX_LEVEL and MIN_LEVEL <= s["target_level"] <= MAX_LEVEL)
        for s in skills
    )
    invalid_stage = any(s["default_stage"] not in STAGE_ORDER for s in skills)
    invalid_gap = any(s["gap"] != max(0, s["target_level"] - s["current_level"]) for s in skills)
    index = {s["skill_id"]: s for s in skills}
    prerequisite_order = any(
        STAGE_ORDER[index[child]["default_stage"]] < STAGE_ORDER[index[parent]["default_stage"]]
        for child in index
        for parent in index[child]["prerequisite_ids"]
        if parent in index
    )
    hard_checks = {
        "prerequisite_cycle": bool(cycle),
        "missing_skill_id": bool(missing_skill_id),
        "invalid_level": bool(invalid_level),
        "invalid_stage": bool(invalid_stage),
        "invalid_gap": bool(invalid_gap),
        "prerequisite_order": bool(prerequisite_order),
    }

    # --- 六维指标（0–100）
    pairs = [(child, parent) for child in index for parent in index[child]["prerequisite_ids"] if parent in index]
    respected = [1 for child, parent in pairs
                 if STAGE_ORDER[index[child]["default_stage"]] >= STAGE_ORDER[index[parent]["default_stage"]]]
    prerequisite_reasonableness = 0 if cycle else round(100 * len(respected) / len(pairs)) if pairs else 100

    unmet = [s for s in skills if s["gap"] > 0]
    trained = {sid for stage in stages for sid in stage["_task_skill_ids"]}
    gap_coverage = round(100 * len([s for s in unmet if s["skill_id"] in trained]) / len(unmet)) if unmet else 100

    live_stages = [stage for stage in stages if not stage["stage_skipped"]]
    stage_alignment = (
        round(100 * len([stage for stage in live_stages if stage["tasks"]]) / len(live_stages))
        if live_stages else 100
    )

    personalization = 0
    if payload.get("current_skills"):
        personalization += 40
    if isinstance(payload.get("_profile"), dict) or payload.get("profile_used"):
        personalization += 30
    if payload.get("weekly_hours") is not None:
        personalization += 15
    if payload.get("career_goal"):
        personalization += 15
    if not unmet:
        personalization = max(personalization, 60)  # 已全部满足时不因缺输入判低分
    personalization = min(100, personalization)

    task_skill_alignment = (
        round(100 * len([s for s in unmet if s["skill_id"] in trained]) / len(unmet)) if unmet else 100
    )

    workload_stages: Dict[str, Dict[str, Any]] = {}
    worst = "normal"
    for stage in stages:
        required_weeks = stage["estimated_weeks"]
        maximum = RECOMMENDED_MAX_WEEKS[stage["stage"]]
        if required_weeks <= maximum:
            status = "normal"
        elif required_weeks <= maximum * 1.5:
            status = "warning"
            worst = "warning" if worst == "normal" else worst
        else:
            status = "critical"
            worst = "critical"
        workload_stages[stage["stage"]] = {
            "required_weeks": required_weeks,
            "recommended_max_weeks": maximum,
            "status": status,
        }
    executability = {"normal": 100, "warning": 70, "critical": 40}[worst]

    metrics = {
        "prerequisite_reasonableness": prerequisite_reasonableness,
        "gap_coverage": gap_coverage,
        "stage_alignment": stage_alignment,
        "personalization": personalization,
        "task_skill_alignment": task_skill_alignment,
        "executability": executability,
    }
    weights = {
        "prerequisite_reasonableness": 0.2,
        "gap_coverage": 0.25,
        "stage_alignment": 0.15,
        "personalization": 0.1,
        "task_skill_alignment": 0.15,
        "executability": 0.15,
    }
    overall = round(sum(metrics[key] * weights[key] for key in metrics))
    grade = next(name for floor, name in GRADE_BANDS if overall >= floor)
    path_valid = not any(hard_checks.values())

    if not unmet:
        warnings.append("该职业要求的技能在现有画像下已全部满足，路径只保留能力验证节点。")
    if gap_coverage < 100 and unmet:
        warnings.append("有缺口技能暂无对应的实训任务（图谱里没有这条 trains 边），按自学安排。")
    if worst != "normal":
        warnings.append(f"按每周投入计算，工作量达到「{worst}」级别，建议放长周期或降低每周目标。")

    suggestions: List[str] = []
    priority = [s for s in skills if s["status"] == "priority_learning"]
    if priority:
        suggestions.append("先补先修链上的技能：" + "、".join(s["name_zh"] for s in priority[:3]))
    deep = [s for s in skills if s["prerequisite_depth"] >= 2 and s["gap"] > 0]
    if deep:
        suggestions.append("以下技能前置较深，建议排在对应阶段的后半段：" + "、".join(s["name_zh"] for s in deep[:3]))
    if not suggestions:
        suggestions.append("路径结构完整，可按阶段任务开始行动。")

    evaluation = {
        "path_valid": path_valid,
        "overall_score": overall,
        "grade": grade,
        "metrics": metrics,
        "hard_checks": hard_checks,
        "workload": {"status": worst, "stages": workload_stages},
        "warnings": [],
        "suggestions": suggestions,
    }
    return evaluation, warnings
