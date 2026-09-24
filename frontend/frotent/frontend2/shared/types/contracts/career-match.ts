export type CareerMatchConfidence = "low" | "medium" | "high";

export type CareerMatchReason = {
  type: "confirmed_profile" | "transferable_experience" | "catalog_inference";
  label: string;
  detail: string;
  source: string;
};

export type CareerMatchGap = {
  skillId: string;
  name: string;
  targetLevel: number;
  importance: number;
};

export type CareerMatchItem = {
  occupationId: string;
  occupationName: string;
  occupationNameEn: string;
  shortName: string;
  description: string;
  rank: number;
  matchScore: number;
  matchLevel: "high" | "medium" | "exploratory";
  confidence: CareerMatchConfidence;
  confidenceScore: number;
  scores: { interest: number; skills: number; experience: number; entryFeasibility: number };
  matchedEvidence: string[];
  skillAdvantages: string[];
  skillGaps: CareerMatchGap[];
  reasons: CareerMatchReason[];
  needsValidation: string[];
  tools: string[];
  tasks: string[];
};

export type CareerMatchRun = {
  runId: string;
  userId: string;
  profileVersion: number;
  catalogVersion: string;
  confidence: CareerMatchConfidence;
  confidenceScore: number;
  generatedAt: string;
  items: CareerMatchItem[];
};

export type CareerTarget = {
  userId: string;
  occupationId: string;
  matchRunId: string;
  selectedAt: string;
};
