import type { ApiResponse } from "../../types/contracts/common";

/**
 * API 基础地址。
 * 开发时可用 NEXT_PUBLIC_API_BASE_URL 显式指定后端地址。未指定时沿用当前
 * 页面的协议与主机名，避免用户用 127.0.0.1 打开前端却把会话 Cookie 发到
 * localhost（或反过来）导致跨站 Cookie 被浏览器拦截；服务端渲染时回退到
 * http://localhost:8000。
 */
const configuredApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
const runtimeApiBaseUrl = typeof window !== "undefined" && window.location.hostname
  ? `${window.location.protocol}//${window.location.hostname}:8000`
  : "http://localhost:8000";
const API_BASE_URL = (configuredApiBaseUrl || runtimeApiBaseUrl).replace(/\/$/, "");

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
