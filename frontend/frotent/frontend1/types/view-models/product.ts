export type OccupationId = "edge-ai" | "embedded" | "vision";

export type Occupation = {
  id: OccupationId;
  name: string;
  shortName: string;
  tasks: { ai: string[]; collaborate: string[]; human: string[] };
  reason: { label: string; value: string; state: "confirmed" | "transfer" | "verify" }[];
  skills: string[];
  knowledge: string[];
  tools: string[];
};

export type TrainingTask = {
  id: string;
  title: string;
  type: string;
  description: string;
  target: string;
  output: string;
  evidence: string;
  duration: string;
  level: string;
  direction: string;
};
