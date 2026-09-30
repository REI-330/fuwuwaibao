export type CareerStage = "junior" | "intermediate" | "advanced";

export type CurrentSkillInput = {
  skill_id?: string;
  skill_name?: string;
  current_level: number;
};

export type CareerPathGenerateInput = {
  target_job: string;
  current_skills?: CurrentSkillInput[];
  experience_years?: number;
  education?: string;
  major?: string;
  weekly_hours?: number;
  career_goal?: string;
};

export type CareerPathSkill = {
  skill_id: string;
  name_zh: string;
  current_level: number;
  target_level: number;
  gap: number;
  importance: number;
  status: "satisfied" | "improve" | "learning" | "priority_learning";
  prerequisite_ids: string[];
  priority_score: number;
  prerequisite_depth: number;
  default_stage: CareerStage;
  prerequisite_only: boolean;
  primary_learning: boolean;
  stage_adjustment_reason?: "prerequisite_promotion";
};

export type CareerPathEvidence = {
  type:
    | "git_repository"
    | "demo"
    | "model_weights"
    | "experiment_report"
    | "dashboard"
    | "prd"
    | "design_prototype"
    | "api"
    | "deployment_url"
    | "test_report"
    | "paper"
    | "patent"
    | "presentation"
    | "video";
  description: string;
};

export type CareerPathTask = {
  task: string;
  required_skill_ids: string[];
  tools: string[];
  deliverable: string;
  evidence: CareerPathEvidence[];
  subtasks: string[];
  source_refs: string[];
};

export type CareerPathStageOutput = {
  stage: CareerStage;
  period: string;
  goal: string;
  skills: CareerPathSkill[];
  tasks: CareerPathTask[];
  estimated_hours: number;
  estimated_weeks: number;
  satisfied_ratio: number;
  compressed: boolean;
  stage_skipped: boolean;
};

export type CareerPathHardChecks = {
  prerequisite_cycle: boolean;
  missing_skill_id: boolean;
  invalid_level: boolean;
  invalid_stage: boolean;
  invalid_gap: boolean;
  prerequisite_order: boolean;
};

export type CareerPathMetrics = {
  prerequisite_reasonableness: number;
  gap_coverage: number;
  stage_alignment: number;
  personalization: number;
  task_skill_alignment: number;
  executability: number;
};

export type CareerPathEvaluation = {
  path_valid: boolean;
  overall_score: number;
  grade: string;
  metrics: CareerPathMetrics;
  hard_checks: CareerPathHardChecks;
  workload: {
    status: "normal" | "warning" | "critical";
    stages: Record<CareerStage, {
      required_weeks: number;
      recommended_max_weeks: number;
      status: "normal" | "warning" | "critical";
    }>;
  };
  warnings: string[];
  suggestions: string[];
};

export type GeneratedCareerPath = {
  occupation_id: string;
  target_job: string;
  target_job_en: string;
  profile_summary: string;
  match_score: number;
  match_type: string;
  weekly_hours: number;
  skill_gap_summary: {
    total_target_skills: number;
    satisfied_count: number;
    improve_count: number;
    learning_count: number;
    priority_learning_count: number;
  };
  path: CareerPathStageOutput[];
  evaluation: CareerPathEvaluation;
  generated_at: string;
  rules_version: string;
  metrics_version: string;
  /** 后端如实回传的提示（工具归属、工作量、画像等级按入门计等）。 */
  warnings: string[];
};
