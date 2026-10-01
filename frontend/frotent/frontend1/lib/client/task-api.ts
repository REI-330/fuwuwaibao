import type { ApiResponse } from "../../types/contracts/common";
import type {
  EvaluateTaskRunResponse,
  PracticeTask,
  SubmitTaskRunInput,
  SubmitTaskRunResponse,
  TaskDetailResponse,
  TaskListResponse,
  TaskStatus,
  UploadTaskAttachmentResponse,
} from "../../types/contracts/task";
import { apiUrl } from "./http";

export class TaskApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
    this.name = "TaskApiError";
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { credentials: "include", cache: "no-store", ...init });
  const body = await response.json() as ApiResponse<T>;
  if (!response.ok || !body.data) {
    throw new TaskApiError(response.status, body.error?.code ?? "TASK_REQUEST_FAILED",
      body.error?.message ?? "任务请求失败");
  }
  return body.data;
}

export async function listTasks(params: { status?: TaskStatus; occupation?: string } = {}): Promise<TaskListResponse> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.occupation) query.set("occupation", params.occupation);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return call<TaskListResponse>(`/api/tasks${suffix}`);
}

export async function getTask(taskId: string): Promise<TaskDetailResponse> {
  return call<TaskDetailResponse>(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export async function submitTaskRun(taskId: string, input: SubmitTaskRunInput): Promise<SubmitTaskRunResponse> {
  return call<SubmitTaskRunResponse>(`/api/tasks/${encodeURIComponent(taskId)}/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
}

/**
 * 上传任务附件（multipart，字段名 `file`）。
 *
 * 刻意**不**用 `call()`：那个 helper 会给请求体设 JSON 头，而 multipart 的
 * `content-type` 必须带上浏览器生成的 boundary，手写会直接把后端解析弄坏。
 * `Content-Type` 一个字都别设，交给 `FormData` 自己带。
 */
export async function uploadTaskAttachment(taskId: string, file: File): Promise<UploadTaskAttachmentResponse> {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch(apiUrl(`/api/tasks/${encodeURIComponent(taskId)}/attachments`), {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    body: form,
  });
  const body = await response.json() as ApiResponse<UploadTaskAttachmentResponse>;
  if (!response.ok || !body.data) {
    throw new TaskApiError(response.status, body.error?.code ?? "ATTACHMENT_UPLOAD_FAILED",
      body.error?.message ?? "附件上传失败");
  }
  return body.data;
}

export async function evaluateTaskRun(runId: string): Promise<EvaluateTaskRunResponse> {
  return call<EvaluateTaskRunResponse>(`/api/task-runs/${encodeURIComponent(runId)}/evaluate`, { method: "POST" });
}

/** 任务清单里「现在就能做」的那条（没有就返回 null）——前端各处统一用它做提示。 */
export function firstAvailableTask(tasks: PracticeTask[]): PracticeTask | null {
  return tasks.find(task => task.status === "available") ?? null;
}
