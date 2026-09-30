"""写时触发器的**模型版**生成（以及规则版共用的解析/校验）。

来源与归属说明（按开源约定标注）：prompt 文本与「严格 JSON + 容错解析 + 逐字段夹取」
这套做法来自队友项目 `career-ai-system` 的 `backend/app/services/memory_triggers.py`
（其设计又参考腾讯 T-Mem，EMNLP 2026，MIT）。本文件是**按本项目约束的重写**：
不引入 json_repair / rapidfuzz，改用 `re` + 白名单校验；模型调用走 `backend/llm.py`。

三条纪律与规则版一致（`backend/memories.py` 顶部有完整说明）：
1. 只有 `confirmed` 记忆才生成触发器；
2. 生成失败**不阻断**记忆使用 —— 降级成规则版并在 `generated_by` 里如实标注；
3. 触发器只扩充候选集，永远不进回答证据链。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .llm import LlmClient, LlmError

# 概念锚点是**排序权重**，不是概率。这一条与规则版共用同一份措辞。
CONCEPT_MAX_LENGTH = 60
BRIDGE_MAX_LENGTH = 120
PATTERN_MAX_LENGTH = 80
PATTERN_COUNT = 3
DEFAULT_TRIGGER_COUNT = 3
# 推理模型会把额度吃在思维链上：实测同一条触发器 prompt 的 reasoning_tokens 在 1794–2048 之间浮动，
# 给 2048 会"刚好不够"（正文回空串）。给 4096 留一倍余量；真不够时 llm.chat 还会自动翻倍重试。
LLM_MAX_TOKENS = 4096

# 这份 prompt 直接取自队友实现的 `_TRIGGER_PROMPT_TEMPLATE`（只把 {count} 与 {content} 保留为占位符）。
# 它值钱的地方在于三条质量规则都是**反例式**的：禁复述原句、禁过于宽泛的标签、禁弱关联场景。
TRIGGER_PROMPT_TEMPLATE = """你是职业记忆检索触发器专家。为下面这条用户已确认的记忆生成 {count} 个触发器。
每个触发器是以下两者之一：
- 概念锚点：比记忆高 1-2 级抽象的名词短语（例如“正在学 Python 数据分析”→“数据岗位技能”）；
- 强关联场景：一提及就很可能想起这条记忆的具体场景或情境。
禁止：复述原句、过于宽泛的标签（如“工作”“学习”）、弱关联场景。

每条触发器必须包含：
- bridge：用不超过 12 个字说明为什么该触发器对这条记忆成立（格式：<记忆中的线索> → <一步推断>）；
- confidence：0.0-1.0，估计“用户提到该触发器时，能否可靠想起这条记忆”；
- activation_patterns：3 条“用户将来可能怎么问”的问句句式（简体中文）。

记忆内容：{content}

返回严格 JSON（不要输出任何其他文字）：
{{"triggers":[{{"concept":"短名词短语","bridge":"线索→推断","confidence":0.0,"activation_patterns":["问句1","问句2","问句3"]}}]}}"""


def parse_json_robust(text: str) -> Optional[Any]:
    """从模型回复里取第一个 JSON 对象；取不到返回 None。

    比队友那版多一层：`re.search` 用**贪婪**匹配到最后一个 `}`，
    因为模型常在 JSON 后面再补一段说明文字（实测 deepseek-v4.1-flash 会）。
    """
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def normalize_concept(text: str) -> str:
    """概念归一化：只用于去重，不改写要展示给用户的原文。"""
    value = (text or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value.strip(" .,?!;:\"'`()[]{}<>-—_")


def validate_triggers(data: Any) -> List[Dict[str, Any]]:
    """把模型输出夹成我们敢落库的形状。

    逐条都要过：概念非空且去重、confidence 夹到 0–1、句式最多 3 条且各 ≤80 字、
    各字段截断。**任何一条不合格就丢弃那一条**，而不是整批重试 —— 重试更贵且不一定更好。
    """
    if not isinstance(data, dict):
        return []
    raw = data.get("triggers")
    if not isinstance(raw, list) or not raw:
        return []
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        concept = str(entry.get("concept") or "").strip()
        if not concept:
            continue
        key = normalize_concept(concept)
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            confidence = float(entry.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        patterns = entry.get("activation_patterns")
        if not isinstance(patterns, list):
            patterns = []
        out.append(
            {
                "concept": concept[:CONCEPT_MAX_LENGTH],
                "bridge": str(entry.get("bridge") or "").strip()[:BRIDGE_MAX_LENGTH],
                "confidence": max(0.0, min(1.0, confidence)),
                "activationPatterns": [
                    str(pattern).strip()[:PATTERN_MAX_LENGTH] for pattern in patterns if str(pattern).strip()
                ][:PATTERN_COUNT],
            }
        )
    return out


def build_prompt(content: str, count: int = DEFAULT_TRIGGER_COUNT) -> str:
    # 只取前 500 字：记忆内容上限 1000，但触发器只需要核心事实，长尾反而会诱导模型复述原句
    return TRIGGER_PROMPT_TEMPLATE.format(content=str(content or "")[:500], count=count)


def generate_triggers_with_llm(
    llm: LlmClient,
    content: str,
    *,
    count: int = DEFAULT_TRIGGER_COUNT,
    max_tokens: int = LLM_MAX_TOKENS,
    timeout: int = 180,
    attempts: int = 2,
) -> List[Dict[str, Any]]:
    """调用模型生成触发器；失败抛 `LlmError`（由调用方决定降级）。

    注意 `attempts=2` 而不是 3：单次调用实测约 5–20 s（思维链），调用方是同步写请求，
    重试预算要留给「降级成规则版」这件事本身。
    """
    reply = llm.chat(build_prompt(content, count), max_tokens=max_tokens, timeout=timeout, attempts=attempts)
    parsed = parse_json_robust(reply)
    triggers = validate_triggers(parsed)
    if not triggers:
        # 模型答了但内容不可用：这跟「调用失败」是两件事，得让上层能区分
        raise LlmError(
            "LLM_TRIGGERS_UNUSABLE",
            "模型返回了内容，但没有一条触发器通过校验（概念为空/重复/结构不符）",
            {"replyHead": (reply or "")[:300]},
        )
    return triggers
