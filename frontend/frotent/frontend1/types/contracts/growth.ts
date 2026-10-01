/**
 * 成长记录契约（前端视角）。
 *
 * 与后端 `backend/growth.py` + `backend/server.py` 的 `/api/growth-records*` 一一对应。
 * 两条边界写在类型里，而不只是文档里：
 * 1. 写一条记录**只**产出 `candidate` 记忆 —— 确认权在用户手上，与记忆库的授权闸门一致；
 * 2. 记录里认不出图谱已知的职业/技能名时宁可不写候选，并用 `note.reason` 说明原因
 *    （不把自由文本塞进记忆库去污染召回与匹配算式）。
 */

import type { MemoryCategory, MemoryStatus } from "./memory";

export type GrowthKind = "能力变化" | "任务行动" | "职业方向" | "路径调整";

export type GrowthWriteInput = {
  kind: GrowthKind;
  title: string;
  before?: string;
  after?: string;
  explanation?: string;
  source?: string;
  occurredAt?: string;
  /** 客户端稳定 id（前端 `crypto.randomUUID()`）：带上它，重复提交同一条记录是幂等的 */
  recordId?: string;
};

export type GrowthCandidate = {
  id: string;
  category: MemoryCategory;
  content: string;
  status: MemoryStatus;
  sourceType: string;
  /** 形如 `<recordId>:<category>:<index>`，能指回是哪条记录的哪一个锚点 */
  sourceId: string;
  importance: number;
};

export type GrowthRecord = {
  id: string;
  userId: string;
  kind: GrowthKind;
  title: string;
  before: string;
  after: string;
  explanation: string;
  source: string;
  occurredAt: string;
  createdAt: string;
  candidates: GrowthCandidate[];
};

export type GrowthNote = {
  /** `ok` 有候选；`record_exists` 幂等；`no_known_term` 认不出名词（不写）；`action_only` 只落了行动记录 */
  reason: string;
  message: string;
};

export type GrowthWritePayload = {
  record: GrowthRecord;
  candidates: GrowthCandidate[];
  created: boolean;
  note: GrowthNote;
};

export type GrowthDeletePayload = {
  deleted: string;
  removedMemoryIds: string[];
  keptMemoryIds: string[];
  memoryHash: string;
};

export type GrowthListPayload = {
  items: GrowthRecord[];
  count: number;
  /** 满足筛选条件的总条数（不是这一页的条数）—— `hasMore` 用它算出来的 */
  total: number;
  limit: number | null;
  offset: number;
  /** 下一页的位移；没有下一页时是 `null` */
  nextCursor: string | null;
  hasMore: boolean;
  memoryHash: string;
};
