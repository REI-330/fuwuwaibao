export type CatalogSource = "sqlite" | "json-seed";

export type OccupationSummary = {
  occupationId: string;
  targetJob: string;
  targetJobEn: string;
  shortName: string;
  descriptionZh: string;
  coreSkills: string[];
  tools: string[];
  skillCount: number;
  stageCount: number;
};

export type OccupationDetail = OccupationSummary & {
  futureSignalZh: string;
  tasks: string[];
  aliases: string[];
  skills: Array<{
    skillId: string;
    name: string;
    nameZh: string;
    categoryZh: string;
    targetLevel: number;
    levelName: string;
    stage: string;
    stageLabel: string;
    importance: number;
    prerequisites: string[];
  }>;
  stages: Array<{
    stage: string;
    stageOrder: number;
    period: string;
    goal: string;
    gaps: string[];
    unit: string;
    hours: number;
    task: string;
    deliverable: string;
    evidence: string;
  }>;
};

export type SkillSummary = {
  skillId: string;
  name: string;
  nameZh: string;
  category: string;
  categoryZh: string;
  descriptionZh: string;
  prerequisites: string[];
};
