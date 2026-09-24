"use client";

import type { UserProfile } from "../../types/contracts/profile";

const identityLabels: Record<string, string> = {
  student: "在校生", graduate: "应届生", newEmployee: "职场新人", careerChanger: "转型探索中",
};

export function ProfileReviewCard({ profile, source, onBack, onConfirm }: {
  profile: UserProfile | null;
  source: string;
  onBack: () => void;
  onConfirm: () => void | Promise<void>;
}) {
  const skills = profile?.skills.map((item) => item.name).join("、");
  const experiences = profile?.experiences.map((item) => item.description).join("；");
  const status = profile ? [identityLabels[profile.identity] ?? profile.identity, profile.school, profile.major, profile.careerStage, profile.grade, profile.location].filter(Boolean).join(" · ") : "暂未生成画像";

  return (
    <section className="xn-onboarding-card xn-review-step">
      <button type="button" className="xn-back-button" onClick={onBack}>← 返回修改</button>
      <div className="xn-step-heading"><span>{source} · 待确认</span><h1>确认你的初始画像</h1><p>确认后才会用于职业方向分析，之后仍可随时修改。</p></div>
      <div className="xn-profile-preview">
        <div><span>当前状态</span><b>{status}</b></div>
        <div><span>技能与工具</span><b>{skills || "将在后续对话中逐步了解"}</b></div>
        <div><span>项目与经历</span><b>{experiences || "将在后续行动中逐步积累"}</b></div>
        <div><span>关注方向</span><b>{profile?.interests.join("、") || "暂未确定"}</b></div>
        <div><span>当前问题</span><b>{profile?.currentGoal || "先通过对话探索"}</b></div>
      </div>
      <div className="xn-review-note"><span>候选信息</span><p>系统不会把这份初始画像视为永久结论，后续会根据对话、任务和你确认的证据持续更新。</p></div>
      <div className="xn-step-actions">
        <button type="button" className="xn-entry-secondary" onClick={onBack}>继续修改</button>
        <button type="button" className="xn-entry-primary" disabled={!profile} onClick={() => void onConfirm()}>确认并开始探索</button>
      </div>
    </section>
  );
}
