import type { ApiResponse } from "../../types/contracts/common";

/**
 * API 基础地址。
 * 开发时设为 http://localhost:8000 即可让前端直接调用 FastAPI 后端。
 * 本地默认连接 FastAPI 的 8000 端口；部署时通过环境变量覆盖。
 */
const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

export function apiUrl(urlOrPath: string): string {
  if (urlOrPath.startsWith("http")) return urlOrPath;
  return `${API_BASE_URL}${urlOrPath.startsWith("/") ? urlOrPath : `/${urlOrPath}`}`;
}

export async function requestJson<T>(urlOrPath: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(urlOrPath), { credentials: "include", ...init });
  const payload = await response.json() as ApiResponse<T>;

  if (!response.ok || !payload.data) {
    throw new Error(payload.error?.message ?? "请求失败");
  }

  return payload.data;
}
