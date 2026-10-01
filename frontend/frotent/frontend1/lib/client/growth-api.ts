/**
 * 成长记录请求层。
 *
 * 契约对应后端 `backend/server.py` 的 `/api/growth-records*`（通用响应包 `{requestId,data,error}`）。
 * 写入是**异步派生**：后端在同一事务里落记录 + 待确认记忆候选，候选本身就是记忆库的
 * `candidate`，所以用户是在记忆管理面板（`/growth` 的「记忆库」）里确认或拒绝它们。
 */
import type { ApiResponse } from "../../types/contracts/common";
import type {
  GrowthDeletePayload,
  GrowthListPayload,
  GrowthRecord,
  GrowthWriteInput,
  GrowthWritePayload,
} from "../../types/contracts/growth";
import { apiUrl } from "./http";

export class GrowthApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
    this.name = "GrowthApiError";
  }
}

async function read<T>(response: Response): Promise<T> {
  const body = await response.json() as ApiResponse<T>;
  if (!response.ok || body.data === null || body.data === undefined) {
    throw new GrowthApiError(
      response.status,
      body.error?.code ?? "GROWTH_REQUEST_FAILED",
      body.error?.message ?? "成长记录请求失败",
    );
  }
  return body.data;
}

/**
 * 读一页成长记录。
 *
 * 分页键是**位移**（`cursor`），不是记录 id：后端排序里带了 `record_id` 兜底，
 * 所以同一时刻写多条也不会错位。不传 `limit` 时后端保持旧行为（一次给全部）。
 */
export async function listGrowthRecords(
  params: { kind?: string; limit?: number; cursor?: string } = {},
): Promise<GrowthListPayload> {
  const query = new URLSearchParams();
  if (params.kind) query.set("kind", params.kind);
  if (params.limit) query.set("limit", String(params.limit));
  if (params.cursor) query.set("cursor", params.cursor);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return read<GrowthListPayload>(await fetch(apiUrl(`/api/growth-records${suffix}`), {
    cache: "no-store",
    credentials: "include",
  }));
}

/** 写入一条成长记录；同一 `recordId` 重复提交是幂等的（不会产生重复记录或重复候选）。 */
export async function writeGrowthRecord(input: GrowthWriteInput): Promise<GrowthWritePayload> {
  return read<GrowthWritePayload>(await fetch(apiUrl("/api/growth-records"), {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function getGrowthRecord(recordId: string): Promise<{ record: GrowthRecord }> {
  return read<{ record: GrowthRecord }>(await fetch(
    apiUrl(`/api/growth-records/${encodeURIComponent(recordId)}`),
    { cache: "no-store", credentials: "include" },
  ));
}

/** 删记录：连它派生出的**未确认**候选一起清掉；已确认的记忆归用户，不动。 */
export async function deleteGrowthRecord(recordId: string): Promise<GrowthDeletePayload> {
  return read<GrowthDeletePayload>(await fetch(
    apiUrl(`/api/growth-records/${encodeURIComponent(recordId)}`),
    { method: "DELETE", credentials: "include" },
  ));
}
