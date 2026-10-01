/**
 * 模拟面试契约。移植自队友 `career-ai-system` 的 `types/contracts/interview.ts`。
 *
 * 唯一的口径改动：`questionSource` 取 `llm` / `fallback`（队友那版写死 `tbox` ——
 * 百宝箱；本项目出题走 `backend/llm.py`，没有百宝箱依赖，于是不沿用那个词）。
 */
export type InterviewDifficulty = "junior" | "mid" | "senior";
export type InterviewStatus = "IN_PROGRESS" | "COMPLETED" | "EVALUATED";
export type InterviewEvaluationStatus = "NOT_STARTED" | "PROCESSING" | "COMPLETED" | "FAILED";
export type InterviewQuestionSource = "llm" | "fallback";

export type InterviewRole = {
  roleId: string;
  name: string;
  description: string;
  coreSkills: string[];
};

export type InterviewQuestion = {
  questionId: string;
  position: number;
  question: string;
  category: string;
  answer: string;
  answeredAt: string | null;
};

export type InterviewSession = {
  sessionId: string;
  roleId: string;
  roleName: string;
  difficulty: InterviewDifficulty;
  totalQuestions: number;
  currentQuestionIndex: number;
  status: InterviewStatus;
  evaluationStatus: InterviewEvaluationStatus;
  evaluationError: string | null;
  questionSource: InterviewQuestionSource;
  questionMode: "resume" | "general";
  resumeId: string | null;
  resumeFilename: string | null;
  createdAt: string;
  completedAt: string | null;
  questions: InterviewQuestion[];
};

export type InterviewSessionSummary = Omit<InterviewSession, "questions" | "evaluationError"> & {
  overallScore: number | null;
};

export type InterviewQuestionEvaluation = {
  questionId: string;
  position: number;
  question: string;
  category: string;
  answer: string;
  score: number;
  feedback: string;
  referenceAnswer: string;
  keyPoints: string[];
};

export type InterviewReport = {
  sessionId: string;
  roleName: string;
  difficulty: InterviewDifficulty;
  totalQuestions: number;
  answeredQuestions: number;
  overallScore: number;
  categoryScores: Array<{ category: string; score: number; questionCount: number }>;
  questionDetails: InterviewQuestionEvaluation[];
  overallFeedback: string;
  strengths: string[];
  improvements: string[];
};

export type CreateInterviewInput = {
  roleId: string;
  roleName?: string;
  difficulty: InterviewDifficulty;
  questionCount: number;
  jdText?: string;
  resumeId?: string;
  requestId: string;
};
