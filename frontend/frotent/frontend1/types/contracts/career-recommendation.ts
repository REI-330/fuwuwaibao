/** 推荐用到了哪条已确认记忆（记忆只做输入增强，逐条可审计）。 */
export type MemoryEvidence = {
  memoryId: string;
  category: string;
  content: string;
  /** 这条记忆被用在哪个输入位上：skills / currentGoal / interests。 */
  usedFor: string;
};

export type CareerRecommendation = {
  occupation_id: string;
  occupation_name: string;
  match_score: number;
  reason: string;
  core_skills: string[];
  skill_gaps: string[];
  salary_range: string;
  future_signal: string;
  career_path: string;
  /** 后端补的字段：本次用了哪些已确认记忆（无记忆时为 []）。 */
  confirmed_memory?: MemoryEvidence[];
};

export type CareerRecommendationsResponse = {
  recommendations: CareerRecommendation[];
  /** 已确认记忆集合的指纹；变化即表示推荐结果需要重算（无记忆时为空串）。 */
  memory_hash?: string;
};
