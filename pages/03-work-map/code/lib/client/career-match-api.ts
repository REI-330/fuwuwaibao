import type { ApiResponse } from "../../types/contracts/common";
import type { CareerMatchRun, CareerTarget } from "../../types/contracts/career-match";
import { apiUrl } from "./http";

export class CareerMatchApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
    this.name = "CareerMatchApiError";
  }
}

async function read<T>(response: Response) {
  const body = await response.json() as ApiResponse<T>;
  if (!response.ok || !body.data) {
    throw new CareerMatchApiError(response.status, body.error?.code ?? "CAREER_MATCH_REQUEST_FAILED", body.error?.message ?? "职业匹配请求失败");
  }
  return body.data;
}

export async function getCareerMatches() {
  return (await read<{ run: CareerMatchRun }>(await fetch(apiUrl("/api/career-matches/current"), { cache: "no-store", credentials: "include" }))).run;
}

export async function generateCareerMatches() {
  return (await read<{ run: CareerMatchRun }>(await fetch(apiUrl("/api/career-matches/generate"), { method: "POST", credentials: "include" }))).run;
}

export async function selectCareerTarget(occupationId: string) {
  return (await read<{ target: CareerTarget }>(await fetch(apiUrl("/api/career-matches/select"), {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ occupationId }),
  }))).target;
}
