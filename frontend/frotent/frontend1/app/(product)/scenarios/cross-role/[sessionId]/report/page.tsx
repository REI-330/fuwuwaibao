"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { useParams } from "next/navigation";
import { XiangxinMascot } from "../../../../../../components/brand/xiangxin-mascot";
import { getCrossRoleReport } from "../../../../../../lib/client/cross-role-api";
import type { CrossRoleReport } from "../../../../../../types/contracts/cross-role";

function scoreLabel(score: number) {
  if (score >= 90) return "协作判断稳定";
  if (score >= 70) return "整体表现良好";
  if (score >= 60) return "具备基础意识";
  return "建议继续练习";
}

export default function CrossRoleReportPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = String(params.sessionId ?? "");
  const [report, setReport] = useState<CrossRoleReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    getCrossRoleReport(sessionId).then(result => { if (active) setReport(result); }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "报告加载失败");
    });
    return () => { active = false; };
  }, [sessionId]);

  if (!report && !error) return <div className="xn-card xn-interview-loading"><XiangxinMascot size={86} state="thinking" /><h1>正在整理沟通反馈…</h1></div>;
  if (!report) return <div className="xn-card xn-interview-empty-page"><h1>报告暂时不可用</h1><p>{error}</p><Link className="xn-btn xn-btn-primary" href={`/scenarios/cross-role/${sessionId}`}>返回训练</Link></div>;

  return <div className="xn-cross-role-report">
    <header className="xn-interview-report-title"><div><span>跨岗位沟通报告</span><h1>{report.roleName}</h1><p>{report.mode === "practice" ? "练习模式" : "测评模式"} · 完成 {report.answeredQuestions}/{report.totalQuestions} 题</p></div><div><Link className="xn-btn xn-btn-outline" href="/scenarios/cross-role/history">历史记录</Link><Link className="xn-btn xn-btn-primary" href="/scenarios/cross-role">再练一次</Link></div></header>

    <section className="xn-interview-report-overview"><article className="xn-card xn-report-score"><div style={{ "--score": `${report.overallScore * 3.6}deg` } as CSSProperties}><strong>{report.overallScore}</strong><small>推荐策略命中率</small></div><b>{scoreLabel(report.overallScore)}</b></article><article className="xn-card xn-report-summary"><header><XiangxinMascot size={66} state="echo" /><div><small>本次训练</small><h2>你答对了 {report.correctAnswers} 道题</h2></div></header><p>报告关注你是否能对齐共同目标、透明同步信息、维护团队信任，并把交付风险转化为可执行的行动。</p><span className="xn-training-disclaimer">{report.disclaimer}</span></article></section>

    <section className="xn-card xn-report-categories"><header><div><small>能力观察</small><h2>四项协作表现</h2></div><span>基于本次选择</span></header><div>{report.dimensions.map(item => <article key={item.key}><span>{item.name}</span><i><b style={{ width: `${item.score}%` }} /></i><strong>{item.score}</strong></article>)}</div></section>

    <div className="xn-report-advice-grid"><section className="xn-card"><header><span>✓</span><h2>相对优势</h2></header><ul>{report.strengths.map(item => <li key={item}>{item}</li>)}</ul></section><section className="xn-card"><header><span>↗</span><h2>优先练习</h2></header><ul>{report.improvements.map(item => <li key={item}>{item}</li>)}</ul></section></div>

    <section className="xn-report-details"><header><small>逐题复盘</small><h2>你的选择与推荐沟通方式</h2></header>{report.questionDetails.map(item => <details className="xn-card" key={item.questionId} open={!item.isCorrect}><summary><span>{item.position}</span><div><small>{item.isCorrect ? "判断符合推荐策略" : "建议复盘"}</small><b>{item.question}</b></div><strong className={item.isCorrect ? "xn-result-correct" : "xn-result-improve"}>{item.isCorrect ? "正确" : "待改进"}</strong></summary><div className="xn-report-answer"><section><h3>你的选择</h3><p>{item.options.find(option => option.optionId === item.selectedOptionId)?.text ?? "未作答"}</p></section><section><h3>推荐处理方式</h3><p>{item.recommendedApproach}</p></section><section><h3>为什么</h3><p>{item.explanation}</p></section>{!item.isCorrect && <section><h3>需要避免</h3><p>{item.pitfallAdvice}</p></section>}</div></details>)}</section>
  </div>;
}
