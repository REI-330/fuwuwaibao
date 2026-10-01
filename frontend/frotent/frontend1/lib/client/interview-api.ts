import type { ApiResponse } from "../../types/contracts/common";
import type {
  CreateInterviewInput,
  InterviewReport,
  InterviewRole,
  InterviewSession,
  InterviewSessionSummary,
} from "../../types/contracts/interview";
import { apiUrl } from "./http";

export class InterviewApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
    this.name = "InterviewApiError";
  }
}

/**
 * 模拟面试接口客户端（移植自队友 `career-ai-system` 的 `lib/client/interview-api.ts`）。
 *
 * 与队友那版的差异：**去掉了 401 → 自动建访客会话的重试**。本项目后端没有账号体系，
 * 无 Cookie 时回落 `user_local`，不存在 401；留着重试只会掩盖真实的报错。
 */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { credentials: "include", cache: "no-store", ...init });
  const body = await response.json() as ApiResponse<T>;
  if (!response.ok || !body.data) {
    throw new InterviewApiError(
      response.status,
      body.error?.code ?? "INTERVIEW_REQUEST_FAILED",
      body.error?.message ?? "模拟面试请求失败",
    );
  }
  return body.data;
}

export async function getInterviewRoles(): Promise<InterviewRole[]> {
  return (await call<{ items: InterviewRole[] }>("/api/v1/interview-skills")).items;
}

export async function listInterviewSessions(): Promise<InterviewSessionSummary[]> {
  return (await call<{ items: InterviewSessionSummary[] }>("/api/v1/interviews")).items;
}

export async function createInterview(input: CreateInterviewInput): Promise<InterviewSession> {
  return (await call<{ session: InterviewSession }>("/api/v1/interviews", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  })).session;
}

export async function getInterview(sessionId: string): Promise<InterviewSession> {
  return (await call<{ session: InterviewSession }>(`/api/v1/interviews/${encodeURIComponent(sessionId)}`)).session;
}

export async function submitInterviewAnswer(sessionId: string, questionId: string, answer: string): Promise<InterviewSession> {
  return (await call<{ session: InterviewSession }>(`/api/v1/interviews/${encodeURIComponent(sessionId)}/answers`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ questionId, answer }),
  })).session;
}

export async function completeInterview(sessionId: string): Promise<InterviewReport> {
  return (await call<{ report: InterviewReport }>(`/api/v1/interviews/${encodeURIComponent(sessionId)}/complete`, {
    method: "POST",
  })).report;
}

export async function getInterviewReport(sessionId: string): Promise<InterviewReport> {
  return (await call<{ report: InterviewReport }>(`/api/v1/interviews/${encodeURIComponent(sessionId)}/report`)).report;
}

export async function deleteInterview(sessionId: string): Promise<void> {
  await call<{ deleted: boolean }>(`/api/v1/interviews/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}
