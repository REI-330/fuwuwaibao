"use client";
import { createContext, useContext, useState } from "react";
import type { ProfileCandidate, GrowthState, PlannedTask, TaskRun } from "../../types/view-models/dynamic-profile";
import { confirmGrowthCandidate, emptyGrowthState, saveTaskRun, planGrowthTask } from "../../lib/client/profile-state";

const ProfileContext = createContext<GrowthState & {
  confirm: (candidate: ProfileCandidate) => void;
  saveRun: (run: TaskRun) => void;
  planTask: (task: PlannedTask) => void;
} | null>(null);

export function ProfileProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GrowthState>(emptyGrowthState);
  function confirm(candidate: ProfileCandidate) {
    setState(current => confirmGrowthCandidate(current, candidate, new Date().toISOString()));
  }
  return <ProfileContext.Provider value={{ ...state, confirm, saveRun: run => setState(current => saveTaskRun(current, run)), planTask: task => setState(current => planGrowthTask(current, task, new Date().toISOString())) }}>{children}</ProfileContext.Provider>;
}

export function useDynamicProfile() {
  const context = useContext(ProfileContext);
  if (!context) throw new Error("Dynamic profile requires ProfileProvider");
  return context;
}
