import type { ApiResponse } from "../../types/contracts/common";
import type { DemoUser } from "../../types/contracts/auth";
import type { ProfileHistoryPayload, UserProfile } from "../../types/contracts/profile";
import { apiUrl } from "./http";

export class ProfileApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
    this.name = "ProfileApiError";
  }
}

async function read<T>(response: Response) {
  const body = await response.json() as ApiResponse<T>;
  if (!response.ok || !body.data) {
    throw new ProfileApiError(response.status, body.error?.code ?? "PROFILE_REQUEST_FAILED", body.error?.message ?? "用户画像请求失败");
  }
  return body.data;
}

export async function createGuest(displayName = "体验用户") {
  return (await read<{ user: DemoUser }>(await fetch(apiUrl("/api/auth/guest"), {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ displayName }),
  }))).user;
}

async function saveProfile(payload: Record<string, unknown>) {
  return read<{ profile: UserProfile }>(await fetch(apiUrl("/api/profile"), {
    method: "PUT",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function getProfile() {
  return (await read<{ profile: UserProfile }>(await fetch(apiUrl("/api/profile"), {
    cache: "no-store",
    credentials: "include",
  }))).profile;
}

export async function saveAndConfirmProfile(payload: Record<string, unknown>) {
  try {
    await saveProfile(payload);
  } catch (error) {
    if (!(error instanceof ProfileApiError) || !["UNAUTHORIZED", "INVALID_SESSION"].includes(error.code)) throw error;
    await createGuest();
    await saveProfile(payload);
  }
  return (await read<{ profile: UserProfile }>(await fetch(apiUrl("/api/profile/confirm"), {
    method: "POST",
    credentials: "include",
  }))).profile;
}

/**
 * 画像历史快照列表（最新在前）。分页键是**位移**（`cursor`），与成长记录同一口径。
 *
 * 这是**只读**接口：看历史不会产生新快照，也不会改当前画像。
 */
export async function listProfileHistory(
  params: { limit?: number; cursor?: string } = {},
): Promise<ProfileHistoryPayload> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.cursor) query.set("cursor", params.cursor);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return read<ProfileHistoryPayload>(await fetch(apiUrl(`/api/profile/history${suffix}`), {
    cache: "no-store",
    credentials: "include",
  }));
}

/** 取某一时刻的完整画像。未知 / 不属于自己的 snapshotId 会抛 `PROFILE_SNAPSHOT_NOT_FOUND`（404）。 */
export async function getProfileSnapshot(snapshotId: string) {
  return read<{ snapshotId: string; profile: UserProfile }>(await fetch(
    apiUrl(`/api/profile/history/${encodeURIComponent(snapshotId)}`),
    { cache: "no-store", credentials: "include" },
  ));
}
