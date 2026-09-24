export type CareerPathStage = {
  stageId: string;
  period: string;
  title: string;
  goal: string[];
  skillGaps: string[];
  learningUnits: {
    title: string;
    estimatedHours?: number;
    sourceRefs: string[];
  }[];
  tasks: {
    taskId: string;
    title: string;
    deliverable: string;
    evidenceTarget: string;
  }[];
  evidenceTargets: string[];
  milestone: string;
  reasons: string[];
  sourceRefs: string[];
};

export type CareerPath = {
  pathId: string;
  userId: string;
  pathVersion: number;
  status: "draft" | "confirmed" | "superseded";
  targetOccupation: {
    occupationId: string;
    occupationName: string;
  };
  horizonYears: 3 | 5;
  summary: string;
  stages: CareerPathStage[];
  assumptions: string[];
  uncertainties: string[];
  generatedAt: string;
};
