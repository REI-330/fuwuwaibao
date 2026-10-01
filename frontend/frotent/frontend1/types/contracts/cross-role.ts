/** 跨岗位沟通训练契约。移植自队友 `career-ai-system` 的 `types/contracts/cross-role.ts`（未改口径）。 */
export type CrossRoleMode = "practice" | "assessment";
export type CrossRoleStatus = "IN_PROGRESS" | "COMPLETED";

export type CrossRole = {
  roleId: string;
  name: string;
  description: string;
  category: string;
  questionCount: number;
};

export type CrossRoleOption = {
  optionId: string;
  text: string;
};

export type CrossRoleFeedback = {
  isCorrect: boolean;
  correctOptionId: string;
  recommendedApproach: string;
  explanation: string;
  pitfallAdvice: string;
  dimensions: Record<string, number>;
};

export type CrossRoleQuestion = {
  questionId: string;
  position: number;
  question: string;
  options: CrossRoleOption[];
  selectedOptionId: string | null;
  answeredAt: string | null;
  feedback: CrossRoleFeedback | null;
};

export type CrossRoleSession = {
  sessionId: string;
  roleId: string;
  roleName: string;
  mode: CrossRoleMode;
  status: CrossRoleStatus;
  totalQuestions: number;
  answeredQuestions: number;
  currentQuestionIndex: number;
  overallScore: number | null;
  createdAt: string;
  completedAt: string | null;
  questions: CrossRoleQuestion[];
};

export type CrossRoleSessionSummary = Omit<CrossRoleSession, "questions" | "answeredQuestions">;

export type CrossRoleDimension = {
  key: string;
  name: string;
  score: number;
};

export type CrossRoleQuestionResult = {
  questionId: string;
  position: number;
  question: string;
  options: CrossRoleOption[];
  selectedOptionId: string | null;
  correctOptionId: string;
  isCorrect: boolean;
  recommendedApproach: string;
  explanation: string;
  pitfallAdvice: string;
};

export type CrossRoleReport = {
  sessionId: string;
  roleId: string;
  roleName: string;
  mode: CrossRoleMode;
  totalQuestions: number;
  answeredQuestions: number;
  correctAnswers: number;
  overallScore: number;
  dimensions: CrossRoleDimension[];
  strengths: string[];
  improvements: string[];
  questionDetails: CrossRoleQuestionResult[];
  disclaimer: string;
};
