"""职业匹配（`/api/career-matches/*`）：把「已确认画像」与图谱里的职业算成一个可解释的排序。

**这个接口不需要外部数据源。** 契约里的 `CareerMatchItem` 只有 `occupationId` /
`skillGaps{skillId,targetLevel,importance}` / `tools` / `tasks` 这些**图谱本来就有的字段**，
没有任何岗位、公司、薪资字段 —— 所以它是「用户 ↔ 图谱职业」的适配度排序，
不是招聘网站的岗位搜索。

> 归因纠正（2026-09-30）：这三条路由长期回 501，README 写的理由是「岗位匹配需真实招聘数据源」。
> 实际上契约要的东西全在本仓库里（4 个职业、25 项技能、requires 边带 targetLevel+importance、
> 已确认画像）。缺的不是数据源，是没人去实现。所以先前的 501 归因是错的。

## 三条纪律

1. **每个分数都能追到依据**：四个维度各有算法与出处，`reasons` 里逐条写出来；
   拿不到依据的维度**记 0 并进 `needsValidation`**，不拿「看起来合理」的数字填空。
2. **权重是产品假设，不是语料结论**：图谱里没有任何一条边支撑「技能该占 50%」。
   所以权重写在 `WEIGHTS` 常量里、并在此声明为假设 —— 换权重不影响任何一条从图谱读出的事实。
3. **没有依据就说没有**：画像太空时明确回 `INSUFFICIENT_PROFILE`，不生成一份全是 0 分的
   「匹配结果」去糊弄用户。

## 与既有接口的分工

`GET /api/career/recommendations` 是**同一份图谱**上的轻量排序（只按技能覆盖度 + 一条理由），
给「推荐」用；本模块是**四维打分 + 逐条依据 + 差距/待验证问题**的完整版，给「职业匹配」页用。
两者都读同一份导出，算法不同、口径都写在各自模块里。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .knowledge import GraphStore, code_of, normalize

MODULE = "career-match/v1"

# 四个维度在总分里的权重。**这是产品假设，不是语料结论**（见模块 docstring §2）。
# 取值理由：技能是唯一有图谱边直接支撑的维度，权重最高；兴趣/经历来自用户自述，用于排序微调；
# 入门可行性只做轻微校正，避免把「差距小」直接等同于「适合」。
WEIGHTS: Dict[str, float] = {"skills": 0.50, "interest": 0.25, "experience": 0.15, "entryFeasibility": 0.10}
SCORE_KEYS: Tuple[str, ...] = ("interest", "skills", "experience", "entryFeasibility")

# 总分的分档（契约的 matchLevel）。
MATCH_LEVELS: Tuple[Tuple[int, str], ...] = ((60, "high"), (35, "medium"), (0, "exploratory"))

ITEM_LIMIT = 10


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def catalog_version(store: GraphStore) -> str:
    """图谱版本指纹。画像或图谱任一变化都要重算，所以它必须能比对。"""
    meta = store.meta or {}
    return f"{meta.get('kbVersion') or '?'}/{meta.get('graphVersion') or '?'}"


def _owned_terms(profile: Dict[str, Any]) -> Set[str]:
    owned = set()
    for item in profile.get("skills") or []:
        if isinstance(item, dict):
            name = normalize(item.get("name"))
        else:
            name = normalize(item)
        if name:
            owned.add(name)
    return owned


def _direction_text(profile: Dict[str, Any]) -> str:
    parts = [str(profile.get("currentGoal") or "")]
    parts.extend(str(x) for x in (profile.get("interests") or []))
    return " ".join(p for p in parts if p).strip()


def _experience_text(profile: Dict[str, Any]) -> str:
    parts: List[str] = []
    for item in profile.get("experiences") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("title") or ""))
            parts.append(str(item.get("description") or ""))
        else:
            parts.append(str(item))
    return " ".join(p for p in parts if p).strip()


def _domains_by_node(store: GraphStore) -> Dict[str, List[str]]:
    """node -> 它所属能力域的中文标签（只读既有 belongs_to 边，不新增结论）。"""
    index: Dict[str, List[str]] = {}
    for edge in store.edges_of_type("belongs_to"):
        node = store.node(edge.get("to", ""))
        label = (node or {}).get("label")
        if label:
            index.setdefault(edge.get("from", ""), []).append(label)
    return index


def _importance_level(importance: float) -> int:
    """边上的 importance 是 0..1 连续值，而契约的 UI 按「x/5」显示。

    这里做的是**展示刻度换算**（四舍五入到 1..5），不是重新估一个数：
    0.95→5、0.7→4、0.5→3。原始值仍可从图谱 requires 边逐条核对。
    """
    return max(1, min(5, int(round(float(importance or 0.0) * 5))))


def _match_level(score: int) -> str:
    for threshold, name in MATCH_LEVELS:
        if score >= threshold:
            return name
    return "exploratory"


def _confidence(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


class InsufficientProfile(ValueError):
    """画像太空，匹配没有意义（调用方回 INSUFFICIENT_PROFILE）。"""


def _score_occupation(
    store: GraphStore,
    node: Dict[str, Any],
    profile: Dict[str, Any],
    owned: Set[str],
    direction_nodes: Set[str],
    direction_domains: Set[str],
    experience_nodes: Set[str],
    domains_by_node: Dict[str, List[str]],
) -> Dict[str, Any]:
    required = store.required_skills(node["id"])
    advantages: List[str] = []
    gaps: List[Dict[str, Any]] = []
    evidence: List[str] = []

    total_importance = sum(float(r["importance"] or 0.0) for r in required)
    covered_importance = 0.0
    effort_total = 0.0
    effort_missing = 0.0

    for row in required:
        skill_node = store.node(row["skillNodeId"]) or {}
        name = skill_node.get("label") or row["skillId"]
        importance = float(row["importance"] or 0.0)
        level = int(row["targetLevel"] or 1)
        effort = importance * level
        effort_total += effort
        if store.owns_skill(skill_node, owned):
            covered_importance += importance
            advantages.append(name)
            evidence.append(f"已确认技能：{name}（命中图谱 {row['skillId']}）")
        else:
            effort_missing += effort
            gaps.append(
                {
                    "skillId": row["skillId"],
                    "name": name,
                    "targetLevel": level,
                    "importance": _importance_level(importance),
                }
            )

    scores: Dict[str, int] = {
        "skills": round(100 * covered_importance / total_importance) if total_importance else 0,
        "entryFeasibility": round(100 * (1 - effort_missing / effort_total)) if effort_total else 0,
        "interest": 0,
        "experience": 0,
    }

    # 兴趣：只用画像里真实写下的方向/目标，沿图谱名词表命中；命中不到就是 0（不猜）
    direction_text = _direction_text(profile)
    if direction_text:
        required_ids = {r["skillNodeId"] for r in required}
        occupation_domains = set(store.domain_labels_for(node["id"]))
        if node["id"] in direction_nodes:
            scores["interest"] = 100
            evidence.append(f"目标方向直接指向该职业：{direction_text}")
        elif required_ids & direction_nodes:
            scores["interest"] = 80
            evidence.append(f"目标方向命中了该职业的要求技能：{direction_text}")
        elif occupation_domains & direction_domains:
            shared = "、".join(sorted(occupation_domains & direction_domains))
            scores["interest"] = 60
            evidence.append(f"目标方向与该职业共享能力域：{shared}")

    # 经历：画像经历文本里能命中该职业多少要求技能（同样沿名词表命中，不做语义猜测）
    experience_text = _experience_text(profile)
    if experience_text and total_importance:
        covered = sum(
            float(r["importance"] or 0.0) for r in required if r["skillNodeId"] in experience_nodes
        )
        scores["experience"] = round(100 * covered / total_importance)
        if scores["experience"]:
            named = [
                (store.node(r["skillNodeId"]) or {}).get("label") or r["skillId"]
                for r in required
                if r["skillNodeId"] in experience_nodes
            ]
            evidence.append(f"经历中提到：{'、'.join(named)}")

    total_score = round(sum(scores[key] * WEIGHTS[key] for key in SCORE_KEYS))

    reasons: List[Dict[str, str]] = []
    if advantages:
        reasons.append(
            {
                "type": "confirmed_profile",
                "label": f"已确认技能命中 {len(advantages)} 项",
                "detail": f"命中：{'、'.join(advantages[:5])}" + ("…" if len(advantages) > 5 else ""),
                "source": "已确认画像（GET /api/profile）∩ 图谱 requires 边",
            }
        )
    if scores["experience"]:
        reasons.append(
            {
                "type": "transferable_experience",
                "label": "项目/实习经历佐证",
                "detail": f"经历维度得分 {scores['experience']}：经历文本命中了该职业的要求技能。",
                "source": "画像 experiences 字段 ∩ 图谱名词表",
            }
        )
    reasons.append(
        {
            "type": "catalog_inference",
            "label": "图谱要求与差距",
            "detail": (
                f"该职业要求 {len(required)} 项技能（按 requires 边 importance 加权），"
                f"已具备 {len(advantages)} 项、待补 {len(gaps)} 项；"
                f"入门可行性按「缺口重要性 × 目标等级」估为 {scores['entryFeasibility']}。"
            ),
            "source": "知识图谱 occupation --requires--> skill",
        }
    )

    needs_validation: List[str] = []
    if not direction_text:
        needs_validation.append("你想往哪个方向走？当前画像没有目标方向，兴趣维度按 0 计。")
    if not experience_text:
        needs_validation.append("有哪些项目或实习能佐证这些技能？当前经历字段为空，经历维度按 0 计。")
    if str(profile.get("status") or "") != "confirmed":
        needs_validation.append("画像还是草稿：确认后匹配分与差距会重算。")
    for gap in gaps[:3]:
        needs_validation.append(f"「{gap['name']}」你目前到什么程度？（图谱要求 {gap['targetLevel']} 级）")

    item_confidence_score = min(100, len(advantages) * 12 + (20 if scores["interest"] else 0) + (20 if scores["experience"] else 0))
    detail = store.occupation_summary(node)

    return {
        "occupationId": code_of(node["id"]),
        "occupationName": node.get("label", ""),
        # 导出没有英文职业名，如实留空，不臆造（与 occupation_summary 同口径）。
        "occupationNameEn": "",
        "shortName": node.get("label", ""),
        "description": node.get("description", ""),
        "matchScore": total_score,
        "matchLevel": _match_level(total_score),
        "confidence": _confidence(item_confidence_score),
        "confidenceScore": item_confidence_score,
        "scores": scores,
        "matchedEvidence": evidence,
        "skillAdvantages": advantages,
        "skillGaps": gaps,
        "reasons": reasons,
        "needsValidation": needs_validation,
        "tools": detail.get("tools", []),
        "tasks": store.occupation_detail(node).get("tasks", []),
    }


def generate_run(
    store: GraphStore,
    profile: Dict[str, Any],
    user_id: str,
    *,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """生成一份匹配 run。画像太空时抛 ``InsufficientProfile``。"""
    owned = _owned_terms(profile)
    direction_text = _direction_text(profile)
    if not owned and not direction_text:
        raise InsufficientProfile(
            "画像里既没有已确认技能、也没有目标方向，匹配没有依据。"
            "请先在画像页填写方向与技能（或上传简历）并确认后再生成。"
        )

    direction_nodes: Set[str] = set()
    direction_domains: Set[str] = set()
    domains_by_node = _domains_by_node(store)
    if direction_text:
        for hit in store.nodes_in(direction_text):
            direction_nodes.add(hit["id"])
            direction_domains.update(domains_by_node.get(hit["id"], []))

    experience_nodes: Set[str] = set()
    experience_text = _experience_text(profile)
    if experience_text:
        experience_nodes = {hit["id"] for hit in store.nodes_in(experience_text)}

    items = [
        _score_occupation(store, node, profile, owned, direction_nodes, direction_domains,
                          experience_nodes, domains_by_node)
        for node in store.nodes_of_kind("occupation")
    ]
    items.sort(key=lambda item: (-item["matchScore"], item["occupationId"]))
    items = items[:ITEM_LIMIT]
    for index, item in enumerate(items, start=1):
        item["rank"] = index

    run_confidence_score = min(
        100,
        min(40, 8 * len(owned))
        + (25 if direction_text else 0)
        + (20 if experience_text else 0)
        + (15 if str(profile.get("status") or "") == "confirmed" else 0),
    )

    return {
        "runId": f"match_{secrets.token_hex(8)}",
        "userId": user_id,
        "profileVersion": int(profile.get("profileVersion") or 0),
        "catalogVersion": catalog_version(store),
        "confidence": _confidence(run_confidence_score),
        "confidenceScore": run_confidence_score,
        "generatedAt": generated_at or _now(),
        "items": items,
    }


def is_stale(run: Optional[Dict[str, Any]], profile: Dict[str, Any], store: GraphStore) -> bool:
    """画像版本或图谱版本变了就算过期 —— 过期要重算，不能拿旧结果继续显示。"""
    if not run:
        return True
    return (
        int(run.get("profileVersion") or -1) != int(profile.get("profileVersion") or 0)
        or str(run.get("catalogVersion") or "") != catalog_version(store)
    )


def known_occupation_ids(store: GraphStore) -> Set[str]:
    return {code_of(node["id"]) for node in store.nodes_of_kind("occupation")}
