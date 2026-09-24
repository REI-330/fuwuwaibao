export type ApiError = {
  code: string;
  message: string;
  missingFields?: string[];
  nextQuestions?: string[];
  details?: Record<string, unknown>;
};

export type ApiResponse<T> = {
  requestId: string;
  data: T | null;
  error?: ApiError;
};
