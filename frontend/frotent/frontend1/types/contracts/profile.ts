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

/**
 * 画像历史快照（2026-10-01 第七轮）：`GET /api/profile/history` 的列表项。
 *
 * 快照是**只增不改**的：每次画像写入 / 确认留一份，回答「上周那一刻的档案长什么样」。
 * 列表项只给「一眼看出这是哪一版」的摘要，完整画像走 `GET /api/profile/history/<snapshotId>`。
 */
export type ProfileSnapshotSummary = {
  identity?: string | null;
  school?: string | null;
  major?: string | null;
  currentGoal?: string | null;
  skills: string[];
};

export type ProfileSnapshot = {
  snapshotId: string;
  profileVersion: number;
  status: ProfileStatus;
  capturedAt: string;
  summary: ProfileSnapshotSummary;
};

export type ProfileHistoryPayload = {
  items: ProfileSnapshot[];
  count: number;
  total: number;
  limit: number | null;
  offset: number;
  nextCursor: string | null;
  hasMore: boolean;
};
