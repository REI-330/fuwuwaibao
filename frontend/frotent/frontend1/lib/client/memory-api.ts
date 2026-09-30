/**
 * 记忆库请求层。
 *
 * 契约对应后端 `backend/server.py` 的 `/api/memories*` 路由；
 * 这些路由都走通用响应包 `{requestId, data, error}`，所以统一在这里解包。
 *
 * 只有 `confirmed` 的记忆会进入对话注入与推荐增强 —— 这也是这里
 * `listMemories()` 默认不传 status（拿到全部，界面自己分区）的原因：
 * 管理面板必须能看见候选，用户才有机会拒绝。
 */
import type { ApiResponse } from "../../types/contracts/common";
import type {
  MemoryContextPayload,
  MemoryItem,
  MemoryListPayload,
  MemoryStatus,
  MemorySyncPayload,
  MemoryTrigger,
  MemoryWritePayload,
} from "../../types/contracts/memory";
import { apiUrl } from "./http";

export class MemoryApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
    this.name = "MemoryApiError";
  }
}

async function read<T>(response: Response): Promise<T> {
  const body = await response.json() as ApiResponse<T>;
  if (!response.ok || body.data === null || body.data === undefined) {
    throw new MemoryApiError(
      response.status,
      body.error?.code ?? "MEMORY_REQUEST_FAILED",
      body.error?.message ?? "记忆库请求失败",
    );
  }
  return body.data;
}

export async function listMemories(status?: MemoryStatus): Promise<MemoryListPayload> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return read<MemoryListPayload>(await fetch(apiUrl(`/api/memories${query}`), {
    cache: "no-store",
    credentials: "include",
  }));
}

/** 本次提问会注入哪些记忆（persona 常驻 + 本次想起），供透明预览。 */
export async function previewMemoryContext(query: string): Promise<MemoryContextPayload> {
  return read<MemoryContextPayload>(await fetch(
    apiUrl(`/api/memories/context?query=${encodeURIComponent(query)}`),
    { cache: "no-store", credentials: "include" },
  ));
}

export async function createMemory(payload: MemoryWritePayload): Promise<MemoryItem> {
  return (await read<{ item: MemoryItem }>(await fetch(apiUrl("/api/memories"), {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  }))).item;
}

/** 确认或改内容。确认那一刻后端会为它生成触发器。 */
export async function updateMemory(
  memoryId: string,
  changes: Partial<MemoryWritePayload>,
): Promise<MemoryItem> {
  return (await read<{ item: MemoryItem }>(await fetch(apiUrl(`/api/memories/${encodeURIComponent(memoryId)}`), {
    method: "PATCH",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(changes),
  }))).item;
}

/** 删除即遗忘：触发器级联清理，推荐侧据此重算。 */
export async function deleteMemory(memoryId: string): Promise<{ deleted: string; memoryHash: string }> {
  return read<{ deleted: string; memoryHash: string }>(await fetch(
    apiUrl(`/api/memories/${encodeURIComponent(memoryId)}`),
    { method: "DELETE", credentials: "include" },
  ));
}

/** 已确认画像 → 待确认记忆。画像没确认时后端返回空数组（不做任何写入）。 */
export async function syncProfileMemories(): Promise<MemorySyncPayload> {
  return read<MemorySyncPayload>(await fetch(apiUrl("/api/memories/sync"), {
    method: "POST",
    credentials: "include",
  }));
}

export type MemoryTriggerGeneration = {
  item: MemoryItem;
  triggers: MemoryTrigger[];
  count: number;
  /** 这次到底走了模型还是规则版、失败原因是什么 —— 不许静默。 */
  generator: {
    requested: "auto" | "rule-based" | "llm";
    used: string;
    model: string | null;
    error: { code: string; message: string } | null;
    count?: number;
  };
};

/**
 * 重建触发器。
 *
 * `generator`：`auto`（缺省，配好模型就用模型）/ `llm`（强制模型，失败自动降级）/ `rule-based`。
 * 模型版单次实测约 10–40 s（推理模型先出思维链），所以这个调用要能被用户主动触发、
 * 且调用方应给出"生成中"的反馈；写路径（确认记忆）不会走这里。
 */
export async function generateMemoryTriggers(
  memoryId: string,
  generator: "auto" | "rule-based" | "llm" = "auto",
): Promise<MemoryTriggerGeneration> {
  return read<MemoryTriggerGeneration>(await fetch(
    apiUrl(`/api/memories/${encodeURIComponent(memoryId)}/triggers?generator=${generator}`),
    {
      method: "POST",
      credentials: "include",
      // 模型版单次 10–40 s：给足超时，否则前端会先把请求掐断、看着像"生成失败"
      signal: AbortSignal.timeout(180000),
    },
  ));
}
