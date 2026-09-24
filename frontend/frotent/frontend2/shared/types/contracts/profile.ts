export type ProfileStatus = "draft" | "confirmed";

export type ProfileSkill = {
  name: string;
  level: string;
  confidence?: number;
  evidenceRefs?: string[];
};

export type ProfileExperience = {
  experienceId: string;
  type: "project" | "internship" | "competition" | "work";
  title: string;
  description: string;
};

export type UserProfile = {
  userId: string;
  profileVersion: number;
  status: ProfileStatus;
  identity: "student" | "graduate" | "newEmployee" | "careerChanger";
  school?: string;
  major?: string;
  grade?: string;
  graduationYear?: number;
  location?: string;
  careerStage: string;
  currentGoal?: string;
  interests: string[];
  skills: ProfileSkill[];
  experiences: ProfileExperience[];
  candidateOccupationIds: string[];
  source: "manual" | "resume" | "chat";
  updatedAt: string;
};
