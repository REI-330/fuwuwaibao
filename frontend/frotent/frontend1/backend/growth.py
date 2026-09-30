"""成长记录 → 待确认记忆候选（纯函数，无三方依赖）。

**这是记忆系统整合里最后一条写入通道。** 队友实现走的是
``memory_feedback.py::sync_feedback_skill_candidates``：从「面试报告 / 跨岗位报告里
分数 ≥70 的维度」提炼技能候选，重要度按分数分档（≥85→70、≥75→60、其余 50），
``source_id = f"{session_id}:{dimension}"`` 用来躲开 ``UNIQUE(user_id, source_type, source_id)``。
本仓库没有 ``interview_sessions`` / ``cross_role_sessions`` 那三张表，所以来源换成
**前端成长档案页真实存在的四类记录**（能力变化 / 任务行动 / 职业方向 / 路径调整），
分档规则照抄、其余按本项目约束重写。

三条与写路径一致的纪律：

1. **只产出候选**（``status='candidate'``）：记录写了不等于记忆生效，仍要用户在管理面板确认。
2. **固定规则版**：候选只由图谱里已有的名词表（``GraphStore.nodes_in``）派生，不调模型 ——
   写一条记录不该卡 20 秒（与 ``MemoryStore.generate_triggers`` 调用处同一条理由）。
3. **认不出来就不写**：文本里没有图谱已知的职业/技能名时，宁可不生成候选并说明原因，
   也不把自由文本塞进记忆库污染召回与匹配算式。唯一例外是「任务行动」——
   它是用户真的做过的事，落一条 ``background`` 候选（``已完成行动：…``）有据可依。

内容前缀必须与 ``backend/memories.py`` 的 ``SKILL_PREFIXES`` 白名单一致，
否则 ``MemoryStore.skill_name_from_memory`` 抽不出技能名（有用例钉住这层一致性）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# 四类记录来自前端 `app/(product)/growth-records/page.tsx` 的分类口径
VALID_KINDS = ("能力变化", "任务行动", "职业方向", "路径调整")

# 内容前缀：前两个必须出现在 memories.SKILL_PREFIXES 里（有用例核对）
SKILL_PREFIX = "具备或正在学习："
PLAN_PREFIX = "计划学习："
OCCUPATION_PREFIX = "目标职业："
ACTION_PREFIX = "已完成行动："

# 「任务/能力变化」才是在证明**已有**技能；「职业方向/路径调整」里的技能名是打算学
OWNED_KINDS = ("能力变化", "任务行动")

# 队友那版的档位是「报告维度得分」：≥85→70、≥75→60、其余 50。
# 这里没有分数，只有类别，所以用类别替代得分并如实说明——不假装它是同一个量。
IMPORTANCE_BY_KIND = {"能力变化": 70, "任务行动": 50, "职业方向": 70, "路径调整": 60}
DEFAULT_IMPORTANCE = 50
# 命中图谱名词的候选能真的进匹配算式与召回，比纯文字记录更有价值
GRAPH_BONUS = 10

MAX_TITLE_LENGTH = 200
MAX_TEXT_LENGTH = 1000
SOURCE_TYPE = "growth_record"


def importance_for(kind: str, anchored: bool) -> int:
    base = IMPORTANCE_BY_KIND.get(str(kind), DEFAULT_IMPORTANCE)
    return max(0, min(100, base + (GRAPH_BONUS if anchored else 0)))


def normalize_record(payload: Any) -> Dict[str, Any]:
    """校验并标准化一条记录。字段与前端成长档案的 `ArchiveItem` 对齐（多余字段忽略）。"""
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    kind = str(payload.get("kind") or "").strip() or "任务行动"
    if kind not in VALID_KINDS:
        raise ValueError(f"未知的记录类别：{kind}；可选 {', '.join(VALID_KINDS)}")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("记录标题不能为空")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"记录标题超过 {MAX_TITLE_LENGTH} 字")

    def text_of(key: str) -> str:
        value = str(payload.get(key) or "").strip()
        if len(value) > MAX_TEXT_LENGTH:
            raise ValueError(f"字段 {key} 超过 {MAX_TEXT_LENGTH} 字")
        return value

    return {
        "kind": kind,
        "title": title,
        "before": text_of("before"),
        "after": text_of("after"),
        "explanation": text_of("explanation"),
        "source": text_of("source") or "用户记录",
        "occurredAt": str(payload.get("occurredAt") or "").strip(),
        # 客户端自己的 id（前端 `crypto.randomUUID()`）：带上就能按它幂等，重复提交不产生重复行
        "recordId": str(payload.get("recordId") or "").strip()[:64],
    }


def record_text(record: Dict[str, Any]) -> str:
    """参与名词匹配的全文。顺序固定，便于复盘「为什么认出了这个技能」。"""
    parts = [record.get("title", ""), record.get("before", ""), record.get("after", ""),
             record.get("explanation", "")]
    return "\n".join(part for part in parts if part)


def derive_candidates(record: Dict[str, Any], nodes: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """这条记录应该产出哪些**候选**记忆。

    `nodes` 由调用方用 ``GraphStore.nodes_in(record_text(record))`` 给出（本模块不碰图），
    顺序即匹配到的名词长度序；职业与技能各去重，技能最多 3 条。
    """
    kind = str(record.get("kind") or "任务行动")
    candidates: List[Dict[str, Any]] = []
    seen: set = set()

    occupations: List[str] = []
    skills: List[str] = []
    for node in nodes or []:
        label = str(node.get("label") or "").strip()
        if not label:
            continue
        if node.get("kind") == "occupation" and label not in occupations:
            occupations.append(label)
        elif node.get("kind") == "skill" and label not in skills:
            skills.append(label)

    for label in occupations[:1]:
        content = f"{OCCUPATION_PREFIX}{label}"
        if content in seen:
            continue
        seen.add(content)
        candidates.append({"category": "career_target", "content": content, "anchored": True, "anchor": label})

    for label in skills[:3]:
        prefix = SKILL_PREFIX if kind in OWNED_KINDS else PLAN_PREFIX
        content = f"{prefix}{label}"
        if content in seen:
            continue
        seen.add(content)
        candidates.append({"category": "skill", "content": content, "anchored": True, "anchor": label})

    if not candidates and kind == "任务行动":
        content = f"{ACTION_PREFIX}{str(record.get('title') or '')[:60]}"
        candidates.append({"category": "background", "content": content, "anchored": False, "anchor": ""})

    for index, entry in enumerate(candidates):
        entry["importance"] = importance_for(kind, entry["anchored"])
        # 同一记录重复写入时靠 `source_id` 幂等（UNIQUE(user_id, source_type, source_id)）
        entry["sourceId"] = f"{record.get('recordId', '')}:{entry['category']}:{index}"
    return candidates
