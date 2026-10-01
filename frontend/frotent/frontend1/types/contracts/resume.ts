/**
 * 简历存档契约（供「简历面试」选取）。
 *
 * 与队友 `career-ai-system` 同名契约相比**故意更小**：本项目没有简历评分（`overallScore` /
 * `scoreDetail` / `suggestions` 那套是队友的岗位匹配打分），所以这里不编造这些字段。
 * `analysis` 是解析快照的**形状说明**，不是我方新增的结论。
 */
export type ResumeAnalyzeStatus = "COMPLETED" | "FAILED" | "PROCESSING";

export type ResumeAnalysisSnapshot = {
  /** 解析结果的原始形状（extract() 的返回），字段随版本变化，故只约束用到的几个。 */
  profileDraft?: {
    school?: string | null;
    major?: string | null;
    directions?: string | string[];
    experience?: string[];
  };
  skills?: Array<{ name?: string } | string>;
  experiences?: Array<{ title?: string }>;
  extraction?: { module?: string; source?: string; filename?: string; charCount?: number };
  /** 队友那版有面试重点；本项目解析结果里没有这个字段，保留为可选以免前端假读。 */
  interviewFocus?: string[];
  [key: string]: unknown;
};

export type ResumeRecord = {
  resumeId: string;
  filename: string;
  analyzeStatus: ResumeAnalyzeStatus;
  analyzeError: string | null;
  charCount: number;
  createdAt: string;
  analysis: ResumeAnalysisSnapshot | null;
};
