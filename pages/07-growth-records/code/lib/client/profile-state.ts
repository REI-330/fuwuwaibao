import type { ProfileCandidate, ProfileRecord } from "../../types/view-models/dynamic-profile";
import type { GrowthState, PlannedTask, TaskRun } from "../../types/view-models/dynamic-profile";

export function emptyGrowthState(): GrowthState {
  return { records: [], taskRuns: [], evidence: [], events: [], plannedTasks: [] };
}

export function saveTaskRun(state: GrowthState, run: TaskRun): GrowthState {
  if (state.taskRuns.some(item => item.id === run.id)) return state;
  return { ...state, taskRuns: [run, ...state.taskRuns] };
}

export function planGrowthTask(state: GrowthState, task: PlannedTask, now: string): GrowthState {
  if (state.plannedTasks.some(item => item.id === task.id)) return state;
  return { ...state, plannedTasks: [...state.plannedTasks, task], events: [{
    id: `plan:${task.id}`, kind: "成长路径调整", taskId: task.id,
    title: "安排了一项阶段任务", explanation: "你主动将路径任务加入行动列表，尚未完成，也不会自动改变能力画像。",
    after: `${task.direction} · ${task.stage} · ${task.title}`, occurredAt: now,
  }, ...state.events] };
}

export function confirmGrowthCandidate(state: GrowthState, candidate: ProfileCandidate, now: string): GrowthState {
  if (candidate.classificationRequired || !["当前状态", "方向偏好", "能力基础"].includes(candidate.module)) return state;
  if (candidate.taskRunId && !state.taskRuns.some(run => run.id === candidate.taskRunId)) return state;
  const records = confirmProfileCandidate(state.records, candidate, now);
  if (records === state.records) return state;
  const previous = state.records.find(record => record.module === candidate.module && record.field === candidate.field);
  const directionChanged = candidate.field === "当前候选职业方向" && previous && previous.content !== candidate.content.trim();
  const evidenceId = `evidence:${candidate.id}`;
  return { ...state, records, evidence: [{ id: evidenceId, profileId: candidate.id, taskRunId: candidate.taskRunId, source: candidate.source, level: candidate.level, observedAt: now }, ...state.evidence], events: [{
    id: `profile:${candidate.id}`, kind: directionChanged ? "职业方向变化" : "新增画像证据", profileId: candidate.id, evidenceId,
    title: directionChanged ? "候选职业方向已更新" : `${candidate.module}新增一条已确认信息`,
    explanation: candidate.taskRunId ? "这次行动为画像提供了一次观察。确认表示你认可记录，并不代表能力已被充分验证。" : "你补充并确认了对自己的描述，后续可以通过行动提供更多依据。",
    before: directionChanged ? previous.content : undefined, after: candidate.content.trim(), occurredAt: now,
  }, ...state.events] };
}

// Only an explicit confirmation action may promote a candidate into the profile.
export function confirmProfileCandidate(records: ProfileRecord[], candidate: ProfileCandidate, confirmedAt: string): ProfileRecord[] {
  if (!candidate.content.trim() || records.some(record => record.id === candidate.id)) return records;
  return [{ ...candidate, content: candidate.content.trim(), confirmed: true, updatedAt: confirmedAt }, ...records];
}
