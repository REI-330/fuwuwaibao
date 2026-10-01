"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { deleteCrossRoleSession, listCrossRoleSessions } from "../../../../../lib/client/cross-role-api";
import type { CrossRoleSessionSummary } from "../../../../../types/contracts/cross-role";

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export default function CrossRoleHistoryPage() {
  const [sessions, setSessions] = useState<CrossRoleSessionSummary[]>([]);
  const [filter, setFilter] = useState("ALL");
  const [keyword, setKeyword] = useState("");
  const [deleting, setDeleting] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    listCrossRoleSessions().then(items => { if (active) setSessions(items); }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "历史记录加载失败");
    });
    return () => { active = false; };
  }, []);

  const visible = useMemo(() => sessions.filter(item => {
    const matchesStatus = filter === "ALL" || item.status === filter;
    return matchesStatus && item.roleName.toLowerCase().includes(keyword.trim().toLowerCase());
  }), [filter, keyword, sessions]);
  const completed = sessions.filter(item => item.status === "COMPLETED");
  const average = completed.length ? Math.round(completed.reduce((sum, item) => sum + (item.overallScore ?? 0), 0) / completed.length) : 0;

  async function remove(item: CrossRoleSessionSummary) {
    if (!window.confirm(`确定删除“${item.roleName}”的训练记录吗？`)) return;
    setDeleting(item.sessionId);
    try {
      await deleteCrossRoleSession(item.sessionId);
      setSessions(current => current.filter(row => row.sessionId !== item.sessionId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除失败");
    } finally {
      setDeleting("");
    }
  }

  return <div className="xn-interview-history xn-cross-role-history">
    <header className="xn-interview-history-title"><div><span>模拟场景</span><h1>跨岗位沟通记录</h1><p>回看不同岗位下的协作判断和改进轨迹。</p></div><div><Link className="xn-btn xn-btn-outline" href="/actions">模拟场景</Link><Link className="xn-btn xn-btn-primary" href="/scenarios/cross-role">开始新训练</Link></div></header>
    <section className="xn-interview-history-stats"><article className="xn-card"><span>全部训练</span><b>{sessions.length}</b><small>已保存的练习与测评</small></article><article className="xn-card"><span>已完成</span><b>{completed.length}</b><small>可查看完整报告</small></article><article className="xn-card"><span>平均命中率</span><b>{average}</b><small>推荐沟通策略</small></article></section>
    <section className="xn-card xn-interview-history-panel"><header><div className="xn-interview-history-filters">{[["ALL", "全部"], ["IN_PROGRESS", "进行中"], ["COMPLETED", "已完成"]].map(([value, label]) => <button type="button" className={filter === value ? "active" : ""} onClick={() => setFilter(value)} key={value}>{label}</button>)}</div><input value={keyword} onChange={event => setKeyword(event.target.value)} placeholder="搜索岗位" /></header>
      {error && <p className="xn-interview-error" role="alert">{error}</p>}
      <div className="xn-interview-history-list">{visible.map(item => <article key={item.sessionId}><div className="xn-history-role"><span className={`status-${item.status.toLowerCase()}`}>{item.status === "COMPLETED" ? "已完成" : "进行中"}</span><div><h2>{item.roleName}</h2><p>{item.mode === "practice" ? "练习模式" : "测评模式"} · 10 个协作场景</p></div></div><time>{formatTime(item.createdAt)}</time><div className="xn-history-score">{item.overallScore === null ? <span>尚未评分</span> : <><strong>{item.overallScore}</strong><small> 分</small></>}</div><div className="xn-history-actions"><Link className="xn-btn xn-btn-outline" href={item.status === "COMPLETED" ? `/scenarios/cross-role/${item.sessionId}/report` : `/scenarios/cross-role/${item.sessionId}`}>{item.status === "COMPLETED" ? "查看报告" : "继续训练"}</Link><button className="xn-text-btn" disabled={deleting === item.sessionId} onClick={() => void remove(item)}>{deleting === item.sessionId ? "删除中…" : "删除"}</button></div></article>)}</div>
      {!visible.length && !error && <div className="xn-interview-empty"><span>⇄</span><b>暂无匹配的训练记录</b><p>选择一个岗位，开始第一次跨岗位沟通训练。</p></div>}
    </section>
  </div>;
}
