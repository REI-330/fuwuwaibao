import type { ApiResponse } from "../../types/contracts/common";
import type {
  CrossRole,
  CrossRoleMode,
  CrossRoleReport,
  CrossRoleSession,
  CrossRoleSessionSummary,
} from "../../types/contracts/cross-role";
import { apiUrl } from "./http";

export class CrossRoleApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
    this.name = "CrossRoleApiError";
  }
}

/** 跨岗位沟通训练接口客户端（移植自队友 `career-ai-system`，去掉本项目不存在的 401 重试）。 */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { credentials: "include", cache: "no-store", ...init });
  const body = await response.json() as ApiResponse<T>;
  if (!response.ok || !body.data) {
    throw new CrossRoleApiError(
      response.status,
      body.error?.code ?? "CROSS_ROLE_REQUEST_FAILED",
      body.error?.message ?? "跨岗位沟通请求失败",
    );
  }
  return body.data;
}

export async function getCrossRoleRoles(): Promise<CrossRole[]> {
  return (await call<{ items: CrossRole[] }>("/api/v1/cross-role/roles")).items;
}

export async function listCrossRoleSessions(): Promise<CrossRoleSessionSummary[]> {
  return (await call<{ items: CrossRoleSessionSummary[] }>("/api/v1/cross-role/sessions")).items;
}

export async function createCrossRoleSession(roleId: string, mode: CrossRoleMode): Promise<CrossRoleSession> {
  return (await call<{ session: CrossRoleSession }>("/api/v1/cross-role/sessions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ roleId, mode, requestId: crypto.randomUUID() }),
  })).session;
}

export async function getCrossRoleSession(sessionId: string): Promise<CrossRoleSession> {
  return (await call<{ session: CrossRoleSession }>(`/api/v1/cross-role/sessions/${encodeURIComponent(sessionId)}`)).session;
}

export async function submitCrossRoleAnswer(sessionId: string, questionId: string, optionId: string): Promise<CrossRoleSession> {
  return (await call<{ session: CrossRoleSession }>(`/api/v1/cross-role/sessions/${encodeURIComponent(sessionId)}/answers`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ questionId, optionId }),
  })).session;
}

export async function completeCrossRoleSession(sessionId: string): Promise<CrossRoleReport> {
  return (await call<{ report: CrossRoleReport }>(`/api/v1/cross-role/sessions/${encodeURIComponent(sessionId)}/complete`, {
    method: "POST",
  })).report;
}

export async function getCrossRoleReport(sessionId: string): Promise<CrossRoleReport> {
  return (await call<{ report: CrossRoleReport }>(`/api/v1/cross-role/sessions/${encodeURIComponent(sessionId)}/report`)).report;
}

export async function deleteCrossRoleSession(sessionId: string): Promise<void> {
  await call<{ deleted: boolean }>(`/api/v1/cross-role/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}
