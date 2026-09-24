import type { ApiResponse } from "../../types/contracts/common";
import type { DemoUser } from "../../types/contracts/auth";
import type { UserProfile } from "../../types/contracts/profile";
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
