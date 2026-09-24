import type { ApiResponse } from "../../types/contracts/common";
import type { CareerRecommendationsResponse } from "../../types/contracts/career-recommendation";
import { apiUrl } from "./http";

export class CareerRecommendationApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
    this.name = "CareerRecommendationApiError";
  }
}

export async function getCareerRecommendations(): Promise<CareerRecommendationsResponse> {
  const response = await fetch(apiUrl("/api/career/recommendations"), {
    cache: "no-store",
    credentials: "include",
  });
  const body = await response.json() as CareerRecommendationsResponse | ApiResponse<never>;
  if (!response.ok) {
    const error = "error" in body ? body.error : undefined;
    throw new CareerRecommendationApiError(
      response.status,
      error?.code ?? "CAREER_RECOMMENDATIONS_FAILED",
      error?.message ?? "职业推荐读取失败",
    );
  }
  if (!("recommendations" in body) || !Array.isArray(body.recommendations)) {
    throw new CareerRecommendationApiError(502, "INVALID_RESPONSE", "职业推荐返回格式不正确");
  }
  return body;
}
