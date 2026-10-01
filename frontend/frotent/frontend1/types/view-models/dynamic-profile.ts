export const profileModules = {
  "当前状态": ["年级、专业、学校", "当前目标", "学习经历", "项目、竞赛、实习和实践经历"],
  "方向偏好": ["职业兴趣", "行业偏好", "工作内容偏好", "工作环境偏好", "当前候选职业方向"],
  "能力基础": ["已有技能", "已掌握知识", "软件与工具", "项目成果", "可迁移能力"],
  "行动表现": ["已完成的体验任务", "用户采取的行动", "提交结果", "体现出的能力", "待继续验证的内容"],
  "成长动态": ["新增画像证据", "能力证据升级", "职业方向变化", "成长路径调整", "最近任务时间线"],
} as const;
export type ProfileModule = keyof typeof profileModules;
export type ProfileCandidate = {
  classificationRequired?: boolean; taskRunId?: string;
  id: string; module: ProfileModule; field: string; content: string;
  level: "用户自述" | "单次观察" | "多源支持";
  source: string; task: string | null; updatedAt: string;
  status: "待验证" | "正在验证" | "已验证";
};
export type ProfileRecord = ProfileCandidate & { confirmed: true };

export type TaskRun = {
  id: string; taskId: string; title: string; action: string; submission: string;
  observedAbilities: string[]; pendingValidation: string; completedAt: string;
};
export type ProfileEvidence = {
  id: string; profileId: string; taskRunId?: string; source: string;
  level: ProfileCandidate["level"]; observedAt: string;
};
export type GrowthEvent = {
  id: string; kind: "新增画像证据" | "职业方向变化" | "成长路径调整";
  profileId?: string; evidenceId?: string; taskId?: string;
  title: string; explanation: string; before?: string; after: string; occurredAt: string;
};
export type GrowthState = {
  records: ProfileRecord[]; taskRuns: TaskRun[]; evidence: ProfileEvidence[];
  events: GrowthEvent[];
};
