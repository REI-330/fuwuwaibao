import type { ApiResponse } from "../../types/contracts/common";
import type { ResumeRecord } from "../../types/contracts/resume";
import { apiUrl } from "./http";

export class ResumeApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
    this.name = "ResumeApiError";
  }
}

/**
 * 简历存档查询。**只读** —— 上传/解析仍走本项目既有的 `POST /api/resumes/extract`
 * （见 `app/(entry)/onboarding/page.tsx`）；这里只把「解析过的简历」列出来给「简历面试」选。
 */
async function call<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path), { credentials: "include", cache: "no-store" });
  const body = await response.json() as ApiResponse<T>;
  if (!response.ok || !body.data) {
    throw new ResumeApiError(response.status, body.error?.code ?? "RESUME_REQUEST_FAILED",
      body.error?.message ?? "简历请求失败");
  }
  return body.data;
}

export async function listResumes(): Promise<ResumeRecord[]> {
  return (await call<{ items: ResumeRecord[] }>("/api/resumes")).items;
}

export async function getResume(resumeId: string): Promise<ResumeRecord> {
  return (await call<{ resume: ResumeRecord }>(`/api/resumes/${encodeURIComponent(resumeId)}`)).resume;
}
