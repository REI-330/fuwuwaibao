"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { XiangxinMascot } from "../../../components/brand/xiangxin-mascot";
import {
  createInterview,
  getInterviewRoles,
  listInterviewSessions,
} from "../../../lib/client/interview-api";
import { listResumes } from "../../../lib/client/resume-api";
import type {
  InterviewDifficulty,
  InterviewRole,
  InterviewSessionSummary,
} from "../../../types/contracts/interview";
import type { ResumeRecord } from "../../../types/contracts/resume";

const difficultyOptions: Array<{ value: InterviewDifficulty; label: string; note: string }> = [
  { value: "junior", label: "初级", note: "基础概念与常见实践" },
  { value: "mid", label: "中级", note: "原理、排障与方案权衡" },
  { value: "senior", label: "高级", note: "复杂场景与架构决策" },
];

const statusNames: Record<string, string> = {
  IN_PROGRESS: "进行中",
  COMPLETED: "评估中",
  EVALUATED: "已完成",
};

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

export default function MockInterviewPage() {
  const router = useRouter();
  const [roles, setRoles] = useState<InterviewRole[]>([]);
  const [sessions, setSessions] = useState<InterviewSessionSummary[]>([]);
  const [resumes, setResumes] = useState<ResumeRecord[]>([]);
  const [resumeId, setResumeId] = useState("");
  const [roleId, setRoleId] = useState("");
  const [customRole, setCustomRole] = useState("");
  const [difficulty, setDifficulty] = useState<InterviewDifficulty>("mid");
  const [questionCount, setQuestionCount] = useState(5);
  const [jdText, setJdText] = useState("");
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([getInterviewRoles(), listInterviewSessions(), listResumes()])
      .then(([roleItems, sessionItems, resumeItems]) => {
        if (!active) return;
        setRoles(roleItems);
        setSessions(sessionItems);
        setResumes(resumeItems.filter(item => item.analyzeStatus === "COMPLETED" && item.analysis));
        const requestedResume = new URLSearchParams(window.location.search).get("resumeId");
        if (requestedResume && resumeItems.some(item => item.resumeId === requestedResume && item.analyzeStatus === "COMPLETED")) {
          setResumeId(requestedResume);
        }
        setRoleId(roleItems.find(item => item.roleId === "AI001")?.roleId
          ?? roleItems.find(item => item.roleId !== "custom")?.roleId
          ?? roleItems[0]?.roleId
          ?? "custom");
      })
      .catch(caught => {
        if (active) setError(caught instanceof Error ? caught.message : "模拟面试配置加载失败");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const selectedRole = useMemo(() => roles.find(item => item.roleId === roleId), [roles, roleId]);

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!roleId || roleId === "custom" && !customRole.trim() || starting) return;
    setStarting(true);
    setError("");
    try {
      const session = await createInterview({
        roleId,
        roleName: roleId === "custom" ? customRole.trim() : selectedRole?.name,
        difficulty,
        questionCount,
        jdText: jdText.trim() || undefined,
        resumeId: resumeId || undefined,
        requestId: crypto.randomUUID(),
      });
      router.push(`/mock-interview/${encodeURIComponent(session.sessionId)}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "面试创建失败");
      setStarting(false);
    }
  }

  return <div className="xn-interview-page">
    <section className="xn-interview-hero">
      <div><span>AI 模拟面试</span><h1>把每一次回答，变成下一次更稳的表达</h1><p>选择目标岗位与难度，由模型生成针对性问题；端点没配或不可用时自动改用本地备用题。完成后获得逐题反馈、能力雷达和改进建议。</p></div>
      <XiangxinMascot size={132} state="coaching" />
    </section>

    <div className="xn-interview-hub-grid">
      <form className="xn-card xn-interview-config" onSubmit={start}>
        <header><small>新建练习</small><h2>配置本次面试</h2><p>首期为文字面试，通常需要 10—20 分钟。</p></header>

        <label><span>目标岗位</span><select value={roleId} onChange={event => setRoleId(event.target.value)} disabled={loading} required>
          <option value="" disabled>{loading ? "正在读取岗位…" : "请选择岗位"}</option>
          {roles.map(role => <option value={role.roleId} key={role.roleId}>{role.name}</option>)}
        </select></label>

        {roleId === "custom" && <label><span>自定义岗位名称</span><input value={customRole} onChange={event => setCustomRole(event.target.value)} maxLength={120} placeholder="例如：AI 产品经理" required /></label>}

        {selectedRole && roleId !== "custom" && <div className="xn-interview-role-note"><b>{selectedRole.name}</b><p>{selectedRole.description}</p><div>{selectedRole.coreSkills.slice(0, 5).map(skill => <span key={skill}>{skill}</span>)}</div></div>}

        <fieldset><legend>面试难度</legend><div className="xn-interview-difficulty">{difficultyOptions.map(option => <button type="button" className={difficulty === option.value ? "active" : ""} onClick={() => setDifficulty(option.value)} key={option.value}><b>{option.label}</b><small>{option.note}</small></button>)}</div></fieldset>

        <label><span>题目数量 <em>{questionCount} 题</em></span><input type="range" min="3" max="10" value={questionCount} onChange={event => setQuestionCount(Number(event.target.value))} /></label>

        <label><span>岗位描述（选填）</span><textarea value={jdText} onChange={event => setJdText(event.target.value)} maxLength={12000} placeholder="粘贴目标岗位 JD，问题会更有针对性" /></label>

        <label><span>面试依据 <em>使用简历时约 60% 为简历追问</em></span><select value={resumeId} onChange={event => setResumeId(event.target.value)}>
          <option value="">通用岗位面试（不使用简历）</option>
          {resumes.map(item => <option value={item.resumeId} key={item.resumeId}>使用简历面试｜{item.filename} · {item.charCount} 字</option>)}
        </select></label>
        <div className="xn-interview-resume-note">{resumeId ? <><b>已选择“使用简历面试”</b><p>问题会核验项目贡献与技能边界；简历里的内容只作为素材，不会被执行。</p></> : <><b>当前为通用岗位面试</b><p>还没有可用简历？<Link href="/onboarding">前往解析一份简历 →</Link></p></>}</div>

        {error && <p className="xn-interview-error" role="alert">{error}</p>}
        <button className="xn-btn xn-btn-primary xn-interview-start" disabled={loading || starting || !roleId}>{starting ? "正在生成面试题…" : "开始模拟面试 →"}</button>
        <p className="xn-interview-provider-note">模型端点未配置或暂时不可用时，系统会自动使用本地备用题，确保练习可以继续。</p>
      </form>

      <section className="xn-card xn-interview-recent">
        <header><div><small>练习记录</small><h2>最近面试</h2></div><Link href="/mock-interview/history">查看全部</Link></header>
        {loading ? <div className="xn-interview-empty">正在读取记录…</div> : sessions.length ? <div className="xn-interview-session-list">{sessions.slice(0, 5).map(session => <Link href={session.status === "EVALUATED" ? `/mock-interview/${session.sessionId}/report` : `/mock-interview/${session.sessionId}`} key={session.sessionId}>
          <span className={`status-${session.status.toLowerCase()}`}>{statusNames[session.status] ?? session.status}</span>
          <div><b>{session.roleName}</b><small>{difficultyOptions.find(item => item.value === session.difficulty)?.label} · {session.totalQuestions} 题 · {session.questionMode === "resume" ? "简历面试 · " : ""}{formatTime(session.createdAt)}</small></div>
          {session.overallScore === null ? <em>继续</em> : <strong>{session.overallScore}<small>分</small></strong>}
        </Link>)}</div> : <div className="xn-interview-empty"><span>◎</span><b>还没有面试记录</b><p>配置左侧选项，开始第一场练习。</p></div>}
      </section>
    </div>
  </div>;
}
