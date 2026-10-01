/**
 * 任务实践契约（`/api/tasks*`）。
 *
 * 任务**不是**人造的演示数据：它来自路径引擎在**图谱 `task --trains--> skill` 边**上算出的条目，
 * 标题、交付要求、执行步骤（图谱的考核点）、证据要求、出处 `sourceRefs` 都是现成数据。
 * 图谱没有的字段（`difficulty` / 任务级 `estimatedHours`）如实为 `null`，并列在 `unavailableFields` 里。
 */
export type TaskStatus = "planned" | "available" | "completed";

export type TaskSkill = {
  skillId: string;
  name: string;
  status: string;
  gap: number;
  targetLevel: number;
};

export type TaskSourcePath = {
  occupationId: string;
  targetJob: string;
  stage: string;
  stageName: string;
  period: string;
  stageGoal: string;
  stageOrder: number;
};

export type PracticeTask = {
  taskId: string;
  title: string;
  sourcePath: TaskSourcePath;
  requiredSkills: TaskSkill[];
  tools: string[];
  steps: string[];
  deliverable: string;
  evidenceTargets: Array<{ type: string; description: string }>;
  sourceRefs: string[];
  difficulty: number | null;
  estimatedHours: number | null;
  stageEstimatedHours: number;
  stageEstimatedWeeks: number;
  unavailableFields: string[];
  status: TaskStatus;
};

export type TaskListResponse = {
  items: PracticeTask[];
  count: number;
  occupation: { occupationId: string; targetJob: string };
  occupationSource: "query" | "profile" | "catalog";
  counts: Record<TaskStatus, number>;
  notes: string[];
  disclaimer: string;
};

export type TaskFeedback = {
  runId: string;
  taskId: string;
  provider: "llm" | "fallback";
  score: number;
  coverage: number;
  summary: string;
  strengths: string[];
  improvements: string[];
  observedAbilities: Array<{ skillId?: string; name: string; evidence: string }>;
  needsVerification: Array<{ kind: string; name: string; why: string }>;
  disclaimer: string;
};

export type TaskRun = {
  runId: string;
  taskId: string;
  occupationId: string;
  stage: string;
  title: string;
  deliverable: string;
  action: string;
  submission: string;
  attachmentIds: string[];
  status: "SUBMITTED" | "EVALUATED";
  growthRecordId: string | null;
  candidateIds: string[];
  submittedAt: string;
  evaluatedAt: string | null;
  feedback: TaskFeedback | null;
  provider: string | null;
};

export type TaskDetailResponse = {
  task: PracticeTask;
  runs: TaskRun[];
  runCount: number;
  latestFeedback: TaskFeedback | null;
  disclaimer: string;
};

export type SubmitTaskRunInput = {
  action?: string;
  submission: string;
  attachmentIds?: string[];
  requestId: string;
};

export type SubmitTaskRunResponse = {
  run: TaskRun;
  growthRecord: { id: string; kind: string; title: string; candidates?: unknown[] } | null;
  candidates: Array<{ id: string; category: string; content: string; status: string }>;
  candidateNote: { reason: string; message: string };
  created: boolean;
  disclaimer: string;
};

export type EvaluateTaskRunResponse = {
  report: TaskFeedback;
  run: TaskRun | null;
  taskAvailable: boolean;
};
