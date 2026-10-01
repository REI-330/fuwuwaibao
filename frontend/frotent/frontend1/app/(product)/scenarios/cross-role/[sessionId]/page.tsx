"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { XiangxinMascot } from "../../../../../components/brand/xiangxin-mascot";
import {
  completeCrossRoleSession,
  getCrossRoleSession,
  submitCrossRoleAnswer,
} from "../../../../../lib/client/cross-role-api";
import type { CrossRoleSession } from "../../../../../types/contracts/cross-role";

const optionLabels = ["A", "B", "C", "D"];

export default function CrossRoleSessionPage() {
  const params = useParams<{ sessionId: string }>();
  const router = useRouter();
  const sessionId = String(params.sessionId ?? "");
  const [session, setSession] = useState<CrossRoleSession | null>(null);
  const [position, setPosition] = useState(0);
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    getCrossRoleSession(sessionId).then(result => {
      if (!active) return;
      if (result.status === "COMPLETED") {
        router.replace(`/scenarios/cross-role/${encodeURIComponent(sessionId)}/report`);
        return;
      }
      const initial = Math.min(result.currentQuestionIndex, result.questions.length - 1);
      setSession(result);
      setPosition(Math.max(0, initial));
      setSelected(result.questions[initial]?.selectedOptionId ?? "");
    }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "训练加载失败");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [router, sessionId]);

  const question = session?.questions[position];
  const answeredCount = useMemo(() => session?.questions.filter(item => item.selectedOptionId).length ?? 0, [session]);

  function move(nextPosition: number, current = session) {
    if (!current) return;
    const safe = Math.max(0, Math.min(nextPosition, current.questions.length - 1));
    setPosition(safe);
    setSelected(current.questions[safe]?.selectedOptionId ?? "");
    setError("");
  }

  async function finish(current = session) {
    if (!current) return;
    setSaving(true);
    try {
      await completeCrossRoleSession(current.sessionId);
      router.push(`/scenarios/cross-role/${encodeURIComponent(current.sessionId)}/report`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "报告生成失败");
      setSaving(false);
    }
  }

  async function primaryAction() {
    if (!session || !question || saving) return;
    if (question.feedback && session.mode === "practice") {
      if (position === session.questions.length - 1) await finish();
      else move(position + 1);
      return;
    }
    if (!selected) return;
    setSaving(true);
    setError("");
    try {
      const updated = await submitCrossRoleAnswer(session.sessionId, question.questionId, selected);
      setSession(updated);
      if (session.mode === "assessment") {
        if (position === updated.questions.length - 1) await finish(updated);
        else { move(position + 1, updated); setSaving(false); }
      } else {
        setSaving(false);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "答案保存失败");
      setSaving(false);
    }
  }

  async function finishEarly() {
    if (!session || saving) return;
    if (!window.confirm(`当前已回答 ${answeredCount}/${session.totalQuestions} 题，确定提前结束吗？`)) return;
    await finish();
  }

  if (loading) return <div className="xn-card xn-interview-loading"><XiangxinMascot size={86} state="thinking" /><h1>正在准备沟通场景…</h1></div>;
  if (!session || !question) return <div className="xn-card xn-interview-empty-page"><h1>无法进入这次训练</h1><p>{error || "训练题目不存在"}</p><Link className="xn-btn xn-btn-primary" href="/scenarios/cross-role">返回跨岗位沟通</Link></div>;

  const feedback = question.feedback;
  return <div className="xn-cross-role-room">
    <header className="xn-card xn-cross-role-room-header"><div><Link href="/scenarios/cross-role">← 退出训练</Link><h1>{session.roleName} · 跨岗位沟通</h1><p>{session.mode === "practice" ? "练习模式：每题即时反馈" : "测评模式：完成后统一反馈"}</p></div><button className="xn-btn xn-btn-outline" disabled={saving} onClick={() => void finishEarly()}>提前结束</button></header>
    <div className="xn-interview-progress"><span style={{ width: `${((position + 1) / session.totalQuestions) * 100}%` }} /></div>

    <div className="xn-cross-role-room-grid">
      <aside className="xn-card xn-cross-role-coach"><XiangxinMascot size={112} state={saving ? "thinking" : "coaching"} /><small>沟通教练</small><b>先对齐，再推进</b><p>把共同目标、事实依据、风险和下一步行动说清楚。跨岗位沟通的重点不是“赢”，而是推动问题解决。</p><div><span>已完成</span><strong>{answeredCount}/{session.totalQuestions}</strong></div></aside>

      <section className="xn-card xn-cross-role-question">
        <header><span>第 {position + 1} 题 / 共 {session.totalQuestions} 题</span><em>协作情境</em></header>
        <h2>{question.question}</h2>
        <div className="xn-cross-role-options">{question.options.map((option, index) => {
          const isCorrect = feedback?.correctOptionId === option.optionId;
          const isWrongSelected = Boolean(feedback && selected === option.optionId && !isCorrect);
          return <button type="button" disabled={Boolean(feedback) || saving} className={`${selected === option.optionId ? "selected" : ""} ${isCorrect ? "correct" : ""} ${isWrongSelected ? "wrong" : ""}`} onClick={() => setSelected(option.optionId)} key={option.optionId}><span>{optionLabels[index]}</span><b>{option.text}</b>{isCorrect && <em>推荐</em>}</button>;
        })}</div>

        {feedback && <article className={`xn-cross-role-feedback ${feedback.isCorrect ? "correct" : "improve"}`}><header><span>{feedback.isCorrect ? "✓" : "↗"}</span><div><small>{feedback.isCorrect ? "判断符合推荐策略" : "换个角度会更稳妥"}</small><h3>推荐处理方式</h3></div></header><p>{feedback.recommendedApproach}</p><details><summary>查看为什么这样沟通</summary><p>{feedback.explanation}</p></details>{!feedback.isCorrect && <details><summary>查看需要避免的做法</summary><p>{feedback.pitfallAdvice}</p></details>}</article>}
        {error && <p className="xn-interview-error" role="alert">{error}</p>}
        <footer><button className="xn-btn xn-btn-outline" disabled={position === 0 || saving} onClick={() => move(position - 1)}>上一题</button><button className="xn-btn xn-btn-primary" disabled={(!selected && !feedback) || saving} onClick={() => void primaryAction()}>{saving ? "正在保存…" : feedback ? position === session.totalQuestions - 1 ? "查看训练报告 →" : "进入下一题 →" : session.mode === "assessment" && position === session.totalQuestions - 1 ? "提交并查看报告" : "确认选择 →"}</button></footer>
      </section>

      <nav className="xn-card xn-cross-role-nav" aria-label="题目导航"><h2>答题进度</h2><div>{session.questions.map((item, index) => <button type="button" className={`${index === position ? "active" : ""} ${item.selectedOptionId ? "answered" : ""}`} onClick={() => move(index)} key={item.questionId}>{index + 1}</button>)}</div><p><i /> 已作答 <i /> 当前题</p></nav>
    </div>
  </div>;
}
