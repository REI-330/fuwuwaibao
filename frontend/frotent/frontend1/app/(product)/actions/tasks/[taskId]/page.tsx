"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { useParams } from "next/navigation";
import { XiangxinMascot } from "../../../../../components/brand/xiangxin-mascot";
import { evaluateTaskRun, getTask, submitTaskRun } from "../../../../../lib/client/task-api";
import type { TaskDetailResponse, TaskRun, SubmitTaskRunResponse } from "../../../../../types/contracts/task";

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

/**
 * 单个实践任务：看要求 → 提交行动与成果 → 拿反馈。
 *
 * 三步都刻意分开：**提交**只落成长记录与待确认候选；**评估**只产出反馈与「仍需验证」，
 * 不写任何已确认的能力结论。候选的确认入口在记忆面板（`/growth-records`）。
 */
export default function TaskDetailPage() {
  const params = useParams<{ taskId: string }>();
  const taskId = String(params.taskId ?? "");
  const [detail, setDetail] = useState<TaskDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [evaluating, setEvaluating] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<SubmitTaskRunResponse | null>(null);
  const [action, setAction] = useState("");
  const [submission, setSubmission] = useState("");

  const load = useCallback(async () => {
    try {
      const result = await getTask(taskId);
      setDetail(result);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "任务加载失败");
    }
  }, [taskId]);

  useEffect(() => {
    let active = true;
    const run = async () => {
      await load();
      if (active) setLoading(false);
    };
    void run();
    return () => { active = false; };
  }, [load]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!submission.trim() || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const result = await submitTaskRun(taskId, {
        action: action.trim() || undefined,
        submission: submission.trim(),
        requestId: crypto.randomUUID(),
      });
      setNotice(result);
      setAction("");
      setSubmission("");
      await load();
      if (result.run.runId) void evaluate(result.run.runId, false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function evaluate(runId: string, scroll = true) {
    setEvaluating(runId);
    setError("");
    try {
      await evaluateTaskRun(runId);
      await load();
      if (scroll) window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "评估失败");
    } finally {
      setEvaluating("");
    }
  }

  if (loading) return <div className="xn-card xn-interview-loading"><XiangxinMascot size={86} state="thinking" /><h1>正在从图谱里取这条任务…</h1></div>;
  if (!detail) return <div className="xn-card xn-interview-empty-page"><h1>无法打开这条任务</h1><p>{error || "任务不存在"}</p><Link className="xn-btn xn-btn-primary" href="/actions/tasks">返回任务清单</Link></div>;

  const { task, runs, latestFeedback } = detail;
  const latest: TaskRun | undefined = runs[0];

  return <div className="xn-stack xn-task-detail">
    <header className="xn-interview-history-title">
      <div><span>{task.sourcePath.targetJob} · {task.sourcePath.stageName}（{task.sourcePath.period}）</span><h1>{task.title}</h1><p>{task.sourcePath.stageGoal}</p></div>
      <div><Link className="xn-btn xn-btn-outline" href="/actions/tasks">任务清单</Link><Link className="xn-btn xn-btn-outline" href={`/path?occupation=${encodeURIComponent(task.sourcePath.occupationId)}`}>看路径</Link></div>
    </header>

    {error && <p className="xn-interview-error" role="alert">{error}</p>}

    {notice && <section className="xn-card xn-task-notice">
      <header><b>已记录这次行动</b><span>{notice.created ? "新提交" : "重复提交（按 requestId 幂等，没有重复写）"}</span></header>
      <p>{notice.candidateNote.message}</p>
      {notice.candidates.length > 0 && <ul>{notice.candidates.map(item => <li key={item.id}><b>{item.content}</b><small>{item.status === "candidate" ? "待确认" : item.status}</small></li>)}</ul>}
      <p className="xn-session-note">这些只是候选。去 <Link href="/growth-records">成长记录</Link> 确认后才会进入对话与推荐；不确认就不生效。</p>
    </section>}

    <div className="xn-task-detail-grid">
      <section className="xn-card xn-task-requirements">
        <header><small>任务要求</small><h2>要做什么、交什么</h2></header>
        <dl>
          <div><dt>交付成果</dt><dd>{task.deliverable || "图谱未记录"}</dd></div>
          <div><dt>使用工具</dt><dd>{task.tools.join("、") || "图谱未记录"}</dd></div>
          <div><dt>成果证据</dt><dd>{task.evidenceTargets.map(item => item.description).join("；") || "图谱未记录"}</dd></div>
          <div><dt>要求能力</dt><dd>{task.requiredSkills.map(skill => `${skill.name}（差 ${skill.gap} 级）`).join("、") || "图谱未标注"}</dd></div>
          <div><dt>阶段投入</dt><dd>{task.stageEstimatedHours} 小时 / {task.stageEstimatedWeeks} 周<span className="xn-task-warn">（阶段级，不是这一条任务的耗时）</span></dd></div>
          <div><dt>难度</dt><dd>{task.difficulty === null ? <span className="xn-task-warn">图谱未标注</span> : task.difficulty}</dd></div>
        </dl>
        {task.steps.length > 0 && <><h3>执行步骤（图谱考核点）</h3><ol className="xn-task-steps">{task.steps.map(step => <li key={step}>{step}</li>)}</ol></>}
        <p className="xn-session-note">来源：{task.sourceRefs.join("、") || "图谱未标注"} · 任务 ID {task.taskId}</p>
      </section>

      <form className="xn-card xn-task-submit" onSubmit={submit}>
        <header><small>提交行动</small><h2>你做了什么、结果如何</h2><p>按「背景 → 判断 → 行动 → 结果」写。没做过的别写，规则版与模型都只看你写的这段。</p></header>
        <label><span>行动说明（选填）</span><textarea value={action} onChange={event => setAction(event.target.value)} maxLength={2000} placeholder="例如：先核对硬件约束，再按优先级逐项验证" /></label>
        <label><span>文本成果</span><textarea value={submission} onChange={event => setSubmission(event.target.value)} maxLength={8000} required placeholder="做到哪一步、怎么判断、结果是什么、哪里还不确定……" /></label>
        <div className="xn-answer-meta"><span>{submission.length}/8000</span><small>提交后立刻写入成长记录</small></div>
        <button className="xn-btn xn-btn-primary" disabled={submitting || !submission.trim()}>{submitting ? "正在提交…" : "提交这次行动 →"}</button>
        <p className="xn-session-note">提交只会产出**待确认**候选，不会自动把你的能力写成「已掌握」。</p>
      </form>
    </div>

    <section className="xn-card xn-task-feedback">
      <header><small>评估反馈</small><h2>{latestFeedback ? "最近一次反馈" : "还没有反馈"}</h2></header>
      {!latestFeedback && <p className="xn-session-note">提交一次行动后就有逐条反馈。</p>}
      {latestFeedback && <>
        <div className="xn-task-feedback-head">
          <article className="xn-card xn-report-score"><div style={{ "--score": `${latestFeedback.score * 3.6}deg` } as CSSProperties}><strong>{latestFeedback.score}</strong><small>本次评分</small></div><b>{latestFeedback.provider === "llm" ? "模型评估" : "规则版评估"}</b></article>
          <div className="xn-task-feedback-body">
            <p>{latestFeedback.summary}</p>
            <p className="xn-session-note">要求能力覆盖率 {Math.round(latestFeedback.coverage * 100)}% · 单次表现不等于已掌握</p>
          </div>
        </div>
        <div className="xn-report-advice-grid">
          <section className="xn-card"><header><span>✓</span><h2>本次做得好</h2></header><ul>{latestFeedback.strengths.map(item => <li key={item}>{item}</li>)}</ul></section>
          <section className="xn-card"><header><span>↗</span><h2>可以改进</h2></header><ul>{latestFeedback.improvements.map(item => <li key={item}>{item}</li>)}</ul></section>
        </div>
        <div className="xn-task-feedback-lists">
          <section><h3>观察到的能力（{latestFeedback.observedAbilities.length}）</h3>
            {latestFeedback.observedAbilities.length ? <ul>{latestFeedback.observedAbilities.map(item => <li key={item.name}><b>{item.name}</b><em>{item.evidence}</em></li>)}</ul> : <p className="xn-session-note">这次提交里没有出现任务要求的能力名 —— 宁可空着，也不硬判。</p>}
          </section>
          <section><h3>仍需验证（{latestFeedback.needsVerification.length}）</h3>
            {latestFeedback.needsVerification.length ? <ul>{latestFeedback.needsVerification.map(item => <li key={`${item.kind}-${item.name}`}><b>{item.name}</b><em>{item.why}</em></li>)}</ul> : <p className="xn-session-note">没有额外的待验证项。</p>}
          </section>
        </div>
        <p className="xn-session-note">{latestFeedback.disclaimer}</p>
      </>}
    </section>

    {runs.length > 0 && <section className="xn-card xn-task-history">
      <header><small>历史提交</small><h2>{runs.length} 次</h2></header>
      {runs.map(run => <article key={run.runId}>
        <div><time>{formatTime(run.submittedAt)}</time><span className={`status-${run.status === "EVALUATED" ? "evaluated" : "in_progress"}`}>{run.status === "EVALUATED" ? "已评估" : "待评估"}</span></div>
        {run.action && <p className="xn-session-note">行动：{run.action}</p>}
        <p>{run.submission}</p>
        <div className="xn-task-run-actions">
          {!run.feedback && <button type="button" className="xn-btn xn-btn-outline" disabled={Boolean(evaluating)} onClick={() => void evaluate(run.runId)}>{evaluating === run.runId ? "正在评估…" : "评估这次提交"}</button>}
          {run.candidateIds.length > 0 && <Link className="xn-btn xn-btn-outline" href="/growth-records">确认它派生的 {run.candidateIds.length} 条候选</Link>}
        </div>
      </article>)}
    </section>}

    {latest && latest.growthRecordId && <p className="xn-session-note">这次行动已写入成长记录 <code>{latest.growthRecordId}</code>（与运行记录用同一个 ID，便于互相指认）。</p>}
  </div>;
}
