/**
 * 记忆库契约（前端视角）。
 *
 * 与后端 `backend/memories.py` + `backend/server.py` 的路由一一对应；
 * 设计来源见 `记忆系统整合方案.md`（移植自队友实现的机制，按本项目约束重写）。
 *
 * 两条边界写在类型上，而不是只写在文档里：
 * 1. `status` 只有 `candidate | confirmed`，消费链路只读 `confirmed`；
 * 2. `candidate` 的记忆 `triggers` 恒为空数组 —— 没确认就不会有触发器。
 */

export type MemoryCategory = "goal" | "preference" | "skill" | "background" | "career_target" | "custom";

export type MemoryStatus = "candidate" | "confirmed";

export type MemoryTrigger = {
  triggerId: string;
  memoryId: string;
  level: number;
  concept: string;
  bridge: string;
  activationPatterns: string[];
  /**
   * 排序权重，**不是**模型自评的召回概率。
   * 规则版（`generatedBy === "rule-based"`）时是固定常量；模型版时是模型的自评置信度。
   */
  confidence: number;
  /**
   * `rule-based` = 确定性派生；`llm` = 模型生成；
   * `rule-based-fallback` = 本来要模型但失败了、退回规则版（出错原因不隐藏）。
   */
  generatedBy: "rule-based" | "llm" | "rule-based-fallback" | string;
  /** 模型版才有：生成它的模型名。规则版为空串。 */
  generatedByModel: string;
  createdAt: string;
};

export type MemoryItem = {
  id: string;
  userId: string;
  category: MemoryCategory;
  content: string;
  status: MemoryStatus;
  importance: number;
  sourceType: "profile" | "manual";
  sourceId: string;
  metadata: Record<string, unknown>;
  queryPatterns: string[];
  triggersPending: boolean;
  createdAt: string;
  updatedAt: string;
  confirmedAt: string | null;
  /** 只有列表接口会带；创建/改动的响应里看不到。 */
  triggers?: MemoryTrigger[];
};

export type MemoryListPayload = {
  items: MemoryItem[];
  count: number;
  /** 已确认记忆集合的指纹；变了就说明推荐需要重算。 */
  memoryHash: string;
};

export type MemoryContextEntry = {
  memoryId: string;
  category: MemoryCategory;
  content: string;
  importance: number;
  recallScore: number;
  finalScore: number;
  /** 命中通道：`trigger:<概念>` 或 `word`。 */
  channel: string;
};

export type MemoryContextPayload = {
  /** 常驻块：目标职业 / 当前目标 / 背景 / 偏好。 */
  persona: MemoryItem[];
  /** 本次想起：触发器命中或词面命中的其它记忆。 */
  recalled: MemoryContextEntry[];
  merged: Array<{
    memoryId: string;
    section: "persona" | "recalled";
    category: MemoryCategory;
    content: string;
    recallScore?: number;
    channel?: string;
  }>;
  memoryIds: string[];
  memoryHash: string;
  /** 可直接展示给用户的注入预览（【常驻】/【本次想起】两段）。 */
  summaryText: string;
  count: number;
};

export type MemorySyncPayload = {
  items: MemoryItem[];
  count: number;
};

export type MemoryWritePayload = {
  category: MemoryCategory;
  content: string;
  importance?: number;
  status?: MemoryStatus;
};
