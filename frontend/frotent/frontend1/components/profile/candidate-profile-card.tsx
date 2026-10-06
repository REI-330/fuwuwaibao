"use client";
import Link from "next/link";
import { useState } from "react";
import { profileModules, type ProfileCandidate, type ProfileModule } from "../../types/view-models/dynamic-profile";
import type { MemoryCategory } from "../../types/contracts/memory";
import { createMemory } from "../../lib/client/memory-api";
import { useDynamicProfile } from "./profile-provider";

/** 画像模块/类别 → 记忆库类别。确认时按它归类写入，免得所有候选都挤成 custom。 */
function memoryCategoryOf(module: ProfileModule, field: string): MemoryCategory {
  if (module === "能力基础") return "skill";
  if (module === "方向偏好") return field === "当前候选职业方向" ? "career_target" : "preference";
  return field === "当前目标" ? "goal" : "background";
}

export function CandidateProfileCard({ candidate, onConfirm, onDismiss, onNavigate }: {
  candidate: ProfileCandidate; onConfirm?: () => void; onDismiss: () => void; onNavigate?: () => void;
}) {
  const { records, confirm } = useDynamicProfile();
  const [content, setContent] = useState(candidate.content);
  const [module, setModule] = useState<ProfileModule>(candidate.module);
  const [field, setField] = useState(candidate.field);
  const [editing, setEditing] = useState(!candidate.content || Boolean(candidate.classificationRequired));
  const [classified, setClassified] = useState(!candidate.classificationRequired);
  const [persisting, setPersisting] = useState(false);
  const [persistNote, setPersistNote] = useState("");
  const confirmedRecord = records.find(record => record.id === candidate.id);
  const accepted = Boolean(confirmedRecord);

  /**
   * 确认：① 进本次会话的画像（旧行为）；② **同时**写进记忆库的待确认区（落库、刷新不丢）。
   *
   * ② 用 `status: "candidate"` —— 这里只把用户的确认落成一条**待确认**记忆，
   * 是否留用仍然在「用户画像 → 记忆库」里决定，不在这里替他生效。
   */
  async function accept() {
    const acceptedCandidate = { ...candidate, content, module, field, classificationRequired: false };
    setPersisting(true);
    setPersistNote("");
    try {
      await confirm(acceptedCandidate);
      setEditing(false);
      onConfirm?.();
      const item = await createMemory({
        category: memoryCategoryOf(module, field),
        content: content.trim(),
        importance: 50,
        status: "candidate",
      });
      setPersistNote(`已写入记忆库待确认区（${item.id}）：到「用户画像 → 记忆库」里决定是否保留。`);
    } catch (error) {
      setPersistNote(`写入记忆库失败：${error instanceof Error ? error.message : "未知错误"}（本次会话内仍可见）`);
    } finally {
      setPersisting(false);
    }
  }

  return <section className="xn-candidate" aria-label="候选画像" aria-live="polite">
    <h3>{accepted ? "已确认并写入记忆库待确认区" : "候选画像 · 等待你的确认"}</h3>
    <p className="xn-session-note">当前状态描述背景与近期问题；方向偏好描述职业选择；能力基础描述能力。确认后会落库（刷新不丢），并停在记忆库的待确认区等你决定是否留用。</p><p>归属：{classified ? `${module} / ${field}` : "等待你分类"}</p>
    {editing && !accepted ? <div className="xn-candidate-editor">
      <label>修改说明<textarea value={content} onChange={event => setContent(event.target.value)} maxLength={2000} /></label>
      <label>画像模块<select value={classified ? module : ""} onChange={event => { const next = event.target.value as ProfileModule; setModule(next); setField(profileModules[next][0]); setClassified(true); }}><option value="" disabled>请选择信息归属</option>{["当前状态", "方向偏好", "能力基础"].map(item => <option key={item}>{item}</option>)}</select></label>
      <label>信息类别<select disabled={!classified} value={field} onChange={event => setField(event.target.value)}>{profileModules[module].map(item => <option key={item}>{item}</option>)}</select></label>
    </div> : <p>{content}</p>}
    <dl className="xn-record-meta"><div><dt>证据等级</dt><dd>{candidate.level}</dd></div><div><dt>数据来源</dt><dd>{candidate.source}</dd></div><div><dt>关联任务</dt><dd>{candidate.task ?? "无（聊天）"}</dd></div><div><dt>用户确认</dt><dd>{accepted ? "已确认" : "未确认"}</dd></div><div><dt>更新时间</dt><dd>{(confirmedRecord?.updatedAt ?? candidate.updatedAt).replace("T", " ").slice(0, 16)} UTC</dd></div><div><dt>验证状态</dt><dd>{candidate.status}</dd></div></dl>
    {persistNote && <p className="xn-memory-hint">{persistNote}</p>}
    <div className="xn-candidate-actions">{accepted ? <Link className="xn-btn xn-btn-primary" href="/growth?view=成长动态" onClick={onNavigate}>查看成长变化</Link> : <>
      <button className="xn-btn xn-btn-primary" disabled={!content.trim() || !classified || persisting} onClick={() => void accept()}>{persisting ? "写入中…" : "确认写入"}</button>
      <button className="xn-btn xn-btn-outline" onClick={() => setEditing(true)}>修改说明</button>
      <button className="xn-text-btn" onClick={onDismiss}>暂不写入</button>
    </>}</div>
  </section>;
}
