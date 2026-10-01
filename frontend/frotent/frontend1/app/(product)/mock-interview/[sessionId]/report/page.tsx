"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { useParams } from "next/navigation";
import { XiangxinMascot } from "../../../../../components/brand/xiangxin-mascot";
import { getInterviewReport, InterviewApiError } from "../../../../../lib/client/interview-api";
import type { InterviewReport } from "../../../../../types/contracts/interview";

const difficultyNames = { junior: "初级", mid: "中级", senior: "高级" } as const;

function scoreLabel(score: number) {
  if (score >= 90) return "优秀";
  if (score >= 75) return "良好";
  if (score >= 60) return "合格";
  return "继续练习";
}

export default function InterviewReportPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = String(params.sessionId ?? "");
  const [report, setReport] = useState<InterviewReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    let attempts = 0;
    const load = async () => {
      try {
        const result = await getInterviewReport(sessionId);
        if (active) { setReport(result); setLoading(false); }
      } catch (caught) {
        if (!active) return;
        if (caught instanceof InterviewApiError && caught.code === "REPORT_NOT_READY" && attempts++ < 20) {
          timer = window.setTimeout(load, 1200);
          return;
        }
        setError(caught instanceof Error ? caught.message : "面试报告加载失败");
        setLoading(false);
      }
    };
    void load();
    return () => { active = false; if (timer) window.clearTimeout(timer); };
  }, [sessionId]);

  if (loading) return <div className="xn-card xn-interview-loading"><XiangxinMascot size={90} state="thinking" /><h1>正在整理逐题反馈…</h1><p>模型评估可能需要一点时间，请不要关闭页面。</p></div>;
  if (!report) return <div className="xn-card xn-interview-empty-page"><h1>报告暂时不可用</h1><p>{error}</p><Link className="xn-btn xn-btn-primary" href={`/mock-interview/${sessionId}`}>返回面试</Link></div>;

  return <div className="xn-interview-report">
    <header className="xn-interview-report-title"><div><span>模拟面试报告</span><h1>{report.roleName}</h1><p>{difficultyNames[report.difficulty]} · 已回答 {report.answeredQuestions}/{report.totalQuestions} 题</p></div><div><Link className="xn-btn xn-btn-outline" href="/mock-interview/history">历史记录</Link><Link className="xn-btn xn-btn-primary" href="/mock-interview">再次练习</Link></div></header>

    <section className="xn-interview-report-overview">
      <article className="xn-card xn-report-score"><div style={{ "--score": `${report.overallScore * 3.6}deg` } as CSSProperties}><strong>{report.overallScore}</strong><small>综合得分</small></div><b>{scoreLabel(report.overallScore)}</b></article>
      <article className="xn-card xn-report-summary"><header><XiangxinMascot size={66} state="echo" /><div><small>面试官总结</small><h2>本次表现回响</h2></div></header><p>{report.overallFeedback}</p></article>
    </section>

    <section className="xn-card xn-report-categories"><header><div><small>能力维度</small><h2>分类得分</h2></div><span>0—100 分</span></header><div>{report.categoryScores.map(item => <article key={item.category}><span>{item.category}<small>{item.questionCount} 题</small></span><i><b style={{ width: `${item.score}%` }} /></i><strong>{item.score}</strong></article>)}</div></section>

    <div className="xn-report-advice-grid">
      <section className="xn-card"><header><span>✓</span><h2>表现亮点</h2></header><ul>{report.strengths.map(item => <li key={item}>{item}</li>)}</ul></section>
      <section className="xn-card"><header><span>↗</span><h2>优先改进</h2></header><ul>{report.improvements.map(item => <li key={item}>{item}</li>)}</ul></section>
    </div>

    <section className="xn-report-details"><header><small>逐题复盘</small><h2>回答、评分与参考思路</h2></header>{report.questionDetails.map(item => <details className="xn-card" key={item.questionId} open={item.position === 0}><summary><span>{item.position + 1}</span><div><small>{item.category}</small><b>{item.question}</b></div><strong>{item.score} 分</strong></summary><div className="xn-report-answer"><section><h3>你的回答</h3><p>{item.answer || "未作答"}</p></section><section><h3>面试官反馈</h3><p>{item.feedback}</p></section><section><h3>参考思路</h3><p>{item.referenceAnswer}</p><div>{item.keyPoints.map(point => <span key={point}>{point}</span>)}</div></section></div></details>)}</section>
  </div>;
}
