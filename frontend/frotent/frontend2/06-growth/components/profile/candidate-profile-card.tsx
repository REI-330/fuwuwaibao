"use client";
import Link from "next/link";
import { useState } from "react";
import { profileModules, type ProfileCandidate, type ProfileModule } from "../../types/view-models/dynamic-profile";
import { useDynamicProfile } from "./profile-provider";

export function CandidateProfileCard({ candidate, onConfirm, onDismiss, onNavigate }: {
  candidate: ProfileCandidate; onConfirm?: () => void; onDismiss: () => void; onNavigate?: () => void;
}) {
  const { records, confirm } = useDynamicProfile();
  const [content, setContent] = useState(candidate.content);
  const [module, setModule] = useState<ProfileModule>(candidate.module);
  const [field, setField] = useState(candidate.field);
  const [editing, setEditing] = useState(!candidate.content || Boolean(candidate.classificationRequired));
  const [classified, setClassified] = useState(!candidate.classificationRequired);
  const confirmedRecord = records.find(record => record.id === candidate.id);
  const accepted = Boolean(confirmedRecord);
  return <section className="xn-candidate" aria-label="候选画像" aria-live="polite">
    <h3>{accepted ? "已写入本次会话画像" : "候选画像 · 等待你的确认"}</h3>
    <p className="xn-session-note">当前状态描述背景与近期问题；方向偏好描述职业选择；能力基础描述能力。请确认归属，仅保存于本次会话。</p><p>归属：{classified ? `${module} / ${field}` : "等待你分类"}</p>
    {editing && !accepted ? <div className="xn-candidate-editor">
      <label>修改说明<textarea value={content} onChange={event => setContent(event.target.value)} maxLength={2000} /></label>
      <label>画像模块<select value={classified ? module : ""} onChange={event => { const next = event.target.value as ProfileModule; setModule(next); setField(profileModules[next][0]); setClassified(true); }}><option value="" disabled>请选择信息归属</option>{["当前状态", "方向偏好", "能力基础"].map(item => <option key={item}>{item}</option>)}</select></label>
      <label>信息类别<select disabled={!classified} value={field} onChange={event => setField(event.target.value)}>{profileModules[module].map(item => <option key={item}>{item}</option>)}</select></label>
    </div> : <p>{content}</p>}
    <dl className="xn-record-meta"><div><dt>证据等级</dt><dd>{candidate.level}</dd></div><div><dt>数据来源</dt><dd>{candidate.source}</dd></div><div><dt>关联任务</dt><dd>{candidate.task ?? "无（聊天）"}</dd></div><div><dt>用户确认</dt><dd>{accepted ? "已确认" : "未确认"}</dd></div><div><dt>更新时间</dt><dd>{(confirmedRecord?.updatedAt ?? candidate.updatedAt).replace("T", " ").slice(0, 16)} UTC</dd></div><div><dt>验证状态</dt><dd>{candidate.status}</dd></div></dl>
    <div className="xn-candidate-actions">{accepted ? <Link className="xn-btn xn-btn-primary" href="/growth?view=成长动态" onClick={onNavigate}>查看成长变化</Link> : <>
      <button className="xn-btn xn-btn-primary" disabled={!content.trim() || !classified} onClick={() => { confirm({ ...candidate, content, module, field, classificationRequired: false }); setEditing(false); onConfirm?.(); }}>确认写入</button>
      <button className="xn-btn xn-btn-outline" onClick={() => setEditing(true)}>修改说明</button>
      <button className="xn-text-btn" onClick={onDismiss}>暂不写入</button>
    </>}</div>
  </section>;
}
