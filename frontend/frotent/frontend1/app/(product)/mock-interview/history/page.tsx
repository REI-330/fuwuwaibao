"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { deleteInterview, listInterviewSessions } from "../../../../lib/client/interview-api";
import type { InterviewSessionSummary } from "../../../../types/contracts/interview";

const difficultyNames = { junior: "初级", mid: "中级", senior: "高级" } as const;
const statusNames: Record<string, string> = { IN_PROGRESS: "进行中", COMPLETED: "评估中", EVALUATED: "已完成" };

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

export default function InterviewHistoryPage() {
  const [items, setItems] = useState<InterviewSessionSummary[]>([]);
  const [status, setStatus] = useState("all");
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    listInterviewSessions().then(result => { if (active) setItems(result); })
      .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : "面试记录加载失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const filtered = useMemo(() => items.filter(item => {
    const statusMatched = status === "all" || item.status === status;
    const keywordMatched = !keyword.trim() || `${item.roleName}${item.roleId}`.toLowerCase().includes(keyword.trim().toLowerCase());
    return statusMatched && keywordMatched;
  }), [items, keyword, status]);

  const completed = items.filter(item => item.status === "EVALUATED");
  const average = completed.length ? Math.round(completed.reduce((sum, item) => sum + (item.overallScore ?? 0), 0) / completed.length) : 0;

  async function remove(item: InterviewSessionSummary) {
    if (!window.confirm(`确定删除「${item.roleName}」这次面试及其报告吗？`)) return;
    setDeleting(item.sessionId);
    setError("");
    try {
      await deleteInterview(item.sessionId);
      setItems(current => current.filter(entry => entry.sessionId !== item.sessionId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除失败");
    } finally {
      setDeleting("");
    }
  }

  return <div className="xn-interview-history">
    <header className="xn-interview-history-title"><div><span>模拟面试</span><h1>面试历史</h1><p>回看每次回答和改进轨迹，继续未完成的练习。</p></div><div><Link className="xn-btn xn-btn-outline" href="/actions">模拟场景</Link><Link className="xn-btn xn-btn-primary" href="/mock-interview">开始新面试</Link></div></header>

    <section className="xn-interview-history-stats"><article className="xn-card"><span>面试总数</span><b>{items.length}</b><small>全部文字面试</small></article><article className="xn-card"><span>已完成</span><b>{completed.length}</b><small>已生成评估报告</small></article><article className="xn-card"><span>平均得分</span><b>{average || "—"}</b><small>{completed.length ? "基于已完成面试" : "完成后计算"}</small></article></section>

    <section className="xn-card xn-interview-history-panel">
      <header><div className="xn-interview-history-filters"><button className={status === "all" ? "active" : ""} onClick={() => setStatus("all")}>全部</button><button className={status === "IN_PROGRESS" ? "active" : ""} onClick={() => setStatus("IN_PROGRESS")}>进行中</button><button className={status === "EVALUATED" ? "active" : ""} onClick={() => setStatus("EVALUATED")}>已完成</button></div><input value={keyword} onChange={event => setKeyword(event.target.value)} placeholder="搜索岗位" /></header>
      {error && <p className="xn-interview-error" role="alert">{error}</p>}
      {loading ? <div className="xn-interview-empty">正在加载历史记录…</div> : filtered.length ? <div className="xn-interview-history-list">{filtered.map(item => <article key={item.sessionId}>
        <div className="xn-history-role"><span className={`status-${item.status.toLowerCase()}`}>{statusNames[item.status] ?? item.status}</span><div><h2>{item.roleName}</h2><p>{item.roleId} · {difficultyNames[item.difficulty]} · {item.totalQuestions} 题</p></div></div>
        <time>{formatDate(item.createdAt)}</time>
        <div className="xn-history-score">{item.overallScore === null ? <span>暂无评分</span> : <><strong>{item.overallScore}</strong><small>分</small></>}</div>
        <div className="xn-history-actions"><Link className="xn-btn xn-btn-outline" href={item.status === "EVALUATED" ? `/mock-interview/${item.sessionId}/report` : `/mock-interview/${item.sessionId}`}>{item.status === "EVALUATED" ? "查看报告" : "继续面试"}</Link><button className="xn-text-btn" disabled={deleting === item.sessionId} onClick={() => void remove(item)}>{deleting === item.sessionId ? "删除中…" : "删除"}</button></div>
      </article>)}</div> : <div className="xn-interview-empty"><span>⌁</span><b>没有符合条件的面试</b><p>调整筛选条件，或开始一场新的模拟面试。</p></div>}
    </section>
  </div>;
}
