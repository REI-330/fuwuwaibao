"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { XiangxinMascot } from "../../../../components/brand/xiangxin-mascot";
import {
  completeInterview,
  getInterview,
  submitInterviewAnswer,
} from "../../../../lib/client/interview-api";
import type { InterviewSession } from "../../../../types/contracts/interview";

const difficultyNames = { junior: "初级", mid: "中级", senior: "高级" } as const;

export default function InterviewSessionPage() {
  const params = useParams<{ sessionId: string }>();
  const router = useRouter();
  const sessionId = String(params.sessionId ?? "");
  const [session, setSession] = useState<InterviewSession | null>(null);
  const [position, setPosition] = useState(0);
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    getInterview(sessionId).then(result => {
      if (!active) return;
      if (result.status === "EVALUATED") {
        router.replace(`/mock-interview/${encodeURIComponent(sessionId)}/report`);
        return;
      }
      const initial = Math.min(result.currentQuestionIndex, Math.max(0, result.questions.length - 1));
      setSession(result);
      setPosition(initial);
      setAnswer(result.questions[initial]?.answer ?? "");
    }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "面试加载失败");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [router, sessionId]);

  const question = session?.questions[position];
  const answeredCount = useMemo(() => session?.questions.filter(item => item.answer.trim()).length ?? 0, [session]);

  function move(nextPosition: number, current = session) {
    if (!current) return;
    const safe = Math.max(0, Math.min(nextPosition, current.questions.length - 1));
    setPosition(safe);
    setAnswer(current.questions[safe]?.answer ?? "");
    setError("");
  }

  async function finish() {
    if (!session || finishing) return;
    setFinishing(true);
    setError("");
    try {
      await completeInterview(session.sessionId);
      router.push(`/mock-interview/${encodeURIComponent(session.sessionId)}/report`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "面试评估失败");
      setFinishing(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !question || !answer.trim() || saving || finishing) return;
    setSaving(true);
    setError("");
    try {
      const updated = await submitInterviewAnswer(session.sessionId, question.questionId, answer.trim());
      setSession(updated);
      if (position >= updated.questions.length - 1) {
        await finish();
        setSaving(false);
      } else {
        move(position + 1, updated);
        setSaving(false);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "答案保存失败");
      setSaving(false);
    }
  }

  async function finishEarly() {
    if (!session || finishing) return;
    if (!window.confirm(`当前已回答 ${answeredCount}/${session.totalQuestions} 题，确定提前交卷吗？未回答题目将按 0 分处理。`)) return;
    if (question && answer.trim() && answer.trim() !== question.answer) {
      setSaving(true);
      try {
        const updated = await submitInterviewAnswer(session.sessionId, question.questionId, answer.trim());
        setSession(updated);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "当前答案保存失败");
        setSaving(false);
        return;
      }
      setSaving(false);
    }
    await finish();
  }

  if (loading) return <div className="xn-card xn-interview-loading"><XiangxinMascot size={86} state="thinking" /><h1>正在准备面试现场…</h1></div>;
  if (!session || !question) return <div className="xn-card xn-interview-empty-page"><h1>无法进入这场面试</h1><p>{error || "面试题目不存在"}</p><Link className="xn-btn xn-btn-primary" href="/mock-interview">返回模拟面试</Link></div>;

  return <div className="xn-interview-room">
    <header className="xn-card xn-interview-room-header">
      <div><Link href="/mock-interview">← 退出面试</Link><h1>{session.roleName}模拟面试</h1><p>{difficultyNames[session.difficulty]} · {session.totalQuestions} 题 · {session.questionMode === "resume" ? `基于 ${session.resumeFilename}` : "通用岗位面试"} · {session.questionSource === "llm" ? "模型智能出题" : "本地备用题"}</p></div>
      <button className="xn-btn xn-btn-outline" disabled={saving || finishing} onClick={() => void finishEarly()}>提前交卷</button>
    </header>

    <div className="xn-interview-progress"><span style={{ width: `${((position + 1) / session.totalQuestions) * 100}%` }} /></div>

    <div className="xn-interview-room-grid">
      <aside className="xn-card xn-interviewer-card"><XiangxinMascot size={116} state={finishing ? "thinking" : "listening"} /><small>AI 面试官</small><b>请结合真实经历作答</b><p>不知道也可以说明边界和你的分析思路。具体、诚实、有结构，比堆砌术语更重要。</p><div><span>已作答</span><strong>{answeredCount}/{session.totalQuestions}</strong></div></aside>

      <form className="xn-card xn-question-card" onSubmit={submit}>
        <header><span>第 {position + 1} 题 / 共 {session.totalQuestions} 题</span><em>{question.category}</em></header>
        <h2>{question.question}</h2>
        <label><span>你的回答</span><textarea autoFocus value={answer} onChange={event => setAnswer(event.target.value)} maxLength={8000} placeholder="建议按背景、判断、行动、结果和复盘组织回答……" /></label>
        <div className="xn-answer-meta"><span>{answer.length}/8000</span><small>答案会在点击下一题时保存到 SQLite</small></div>
        {error && <p className="xn-interview-error" role="alert">{error}</p>}
        <footer>
          <button type="button" className="xn-btn xn-btn-outline" disabled={position === 0 || saving || finishing} onClick={() => move(position - 1)}>上一题</button>
          <button className="xn-btn xn-btn-primary" disabled={!answer.trim() || saving || finishing}>{finishing ? "正在生成报告…" : saving ? "正在保存…" : position === session.totalQuestions - 1 ? "提交并生成报告" : "保存并进入下一题 →"}</button>
        </footer>
      </form>

      <nav className="xn-card xn-question-nav" aria-label="题目导航"><h2>答题进度</h2><div>{session.questions.map(item => <button type="button" className={`${item.position === position ? "active" : ""} ${item.answer ? "answered" : ""}`} onClick={() => move(item.position)} key={item.questionId}>{item.position + 1}</button>)}</div><p><i /> 已保存 <i /> 当前题</p></nav>
    </div>
  </div>;
}
