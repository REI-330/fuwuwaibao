"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useDynamicProfile } from "../../../components/profile/profile-provider";
import { writeGrowthRecord } from "../../../lib/client/growth-api";

type ArchiveCategory = "全部" | "能力变化" | "任务行动" | "职业方向" | "路径调整";
type ArchiveStatus = "全部状态" | "已确认" | "已记录" | "待确认";
type ArchivePeriod = "7" | "30" | "all";

type ArchiveItem = {
  id: string;
  category: Exclude<ArchiveCategory, "全部">;
  title: string;
  before?: string;
  after: string;
  explanation: string;
  occurredAt: string;
  source: string;
  status: Exclude<ArchiveStatus, "全部状态">;
  taskRunId?: string;
  evidenceId?: string;
  impact: string;
};

const categories: ArchiveCategory[] = ["全部", "能力变化", "任务行动", "职业方向", "路径调整"];

function isRecent(value: string, period: ArchivePeriod) {
  if (period === "all") return true;
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) && Date.now() - timestamp <= Number(period) * 24 * 60 * 60 * 1000;
}

function displayDate(value: string, withYear = false) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value.slice(0, 16).replace("T", " ");
  return new Intl.DateTimeFormat("zh-CN", withYear
    ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { month: "2-digit", day: "2-digit" }).format(date);
}

export default function GrowthRecordsPage() {
  const { events, taskRuns, evidence, records } = useDynamicProfile();
  const [category, setCategory] = useState<ArchiveCategory>("全部");
  const [period, setPeriod] = useState<ArchivePeriod>("30");
  const [status, setStatus] = useState<ArchiveStatus>("全部状态");
  const [keyword, setKeyword] = useState("");
  const [selectedId, setSelectedId] = useState("");
  /* 写入记忆库：把选中的这条记录作为成长记录提交，后端会派生**待确认**记忆候选。
     记录 id 用 `archive_<item.id>`：重复点不会产生重复记录或重复候选（后端按 recordId 幂等）。 */
  const [saving, setSaving] = useState(false);
  const [saveNote, setSaveNote] = useState("");

  async function saveToMemory(item: ArchiveItem) {
    setSaving(true);
    setSaveNote("");
    try {
      const result = await writeGrowthRecord({
        kind: item.category,
        title: item.title,
        before: item.before,
        after: item.after,
        explanation: item.explanation,
        source: item.source,
        occurredAt: item.occurredAt,
        recordId: `archive_${item.id}`,
      });
      setSaveNote(`${result.note.message}（候选 ${result.candidates.length} 条：${result.candidates.map(candidate => candidate.content).join("、") || "无"}）`);
    } catch (error) {
      setSaveNote(error instanceof Error ? error.message : "写入失败，请稍后重试。");
    } finally {
      setSaving(false);
    }
  }

  const archive = useMemo<ArchiveItem[]>(() => {
    const eventItems: ArchiveItem[] = events.map(event => {
      const proof = event.evidenceId ? evidence.find(item => item.id === event.evidenceId) : undefined;
      const run = proof?.taskRunId ? taskRuns.find(item => item.id === proof.taskRunId) : undefined;
      const eventCategory = event.kind === "职业方向变化" ? "职业方向" : event.kind === "成长路径调整" ? "路径调整" : "能力变化";
      return {
        id: event.id,
        category: eventCategory,
        title: event.title,
        before: event.before,
        after: event.after,
        explanation: event.explanation,
        occurredAt: event.occurredAt,
        source: proof?.source ?? (event.taskId ? "成长路径任务" : "用户确认"),
        status: event.profileId ? "已确认" : "已记录",
        taskRunId: run?.id,
        evidenceId: proof?.id,
        impact: eventCategory === "职业方向" ? "职业推荐将基于新方向重新评估。" : eventCategory === "路径调整" ? "后续行动顺序可能随路径发生变化。" : "该记录已进入动态画像的证据链。",
      };
    });
    const runItems: ArchiveItem[] = taskRuns.map(run => {
      const proof = evidence.find(item => item.taskRunId === run.id);
      return {
        id: `run:${run.id}`,
        category: "任务行动",
        title: `完成「${run.title}」`,
        after: run.submission,
        explanation: `行动：${run.action}。待继续验证：${run.pendingValidation}`,
        occurredAt: run.completedAt,
        source: "用户提交的行动结果",
        status: proof ? "已确认" : "待确认",
        taskRunId: run.id,
        evidenceId: proof?.id,
        impact: proof ? "任务结果已被引用为画像证据。" : "任务已完成，但尚未写入正式画像。",
      };
    });
    return [...eventItems, ...runItems].sort((left, right) => right.occurredAt.localeCompare(left.occurredAt));
  }, [events, evidence, taskRuns]);

  const filtered = archive.filter(item => {
    const searchable = `${item.title}${item.before ?? ""}${item.after}${item.explanation}${item.source}`.toLowerCase();
    return (category === "全部" || item.category === category)
      && (status === "全部状态" || item.status === status)
      && isRecent(item.occurredAt, period)
      && (!keyword.trim() || searchable.includes(keyword.trim().toLowerCase()));
  });
  const selected = filtered.find(item => item.id === selectedId) ?? filtered[0] ?? null;
  const selectedProof = selected?.evidenceId ? evidence.find(item => item.id === selected.evidenceId) : undefined;
  const selectedRun = selected?.taskRunId ? taskRuns.find(item => item.id === selected.taskRunId) : undefined;
  const selectedRecord = selectedProof ? records.find(item => item.id === selectedProof.profileId) : undefined;

  return <div className="xn-archive-dashboard">
    <header className="xn-archive-title"><h1>成长记录档案</h1><p>集中查看已确认的画像变化、行动成果和证据来源</p></header>

    <section className="xn-card xn-archive-summary">
      <article><span>成长变化</span><b>{events.length}</b><small>画像、方向与路径事件</small></article>
      <article><span>完成行动</span><b>{taskRuns.length}</b><small>已提交的任务结果</small></article>
      <article><span>确认证据</span><b>{evidence.length}</b><small>已进入画像证据链</small></article>
      <article><span>待确认</span><b>{taskRuns.filter(run => !evidence.some(item => item.taskRunId === run.id)).length}</b><small>尚未写入正式画像</small></article>
    </section>

    <section className="xn-card xn-archive-filters" aria-label="成长记录筛选">
      <div className="xn-archive-category-tabs">{categories.map(item => <button className={category === item ? "active" : ""} aria-pressed={category === item} onClick={() => { setCategory(item); setSelectedId(""); }} key={item}>{item}</button>)}</div>
      <div className="xn-archive-filter-fields"><label><span>时间范围</span><select value={period} onChange={event => { setPeriod(event.target.value as ArchivePeriod); setSelectedId(""); }}><option value="7">最近7天</option><option value="30">最近30天</option><option value="all">全部时间</option></select></label><label><span>确认状态</span><select value={status} onChange={event => { setStatus(event.target.value as ArchiveStatus); setSelectedId(""); }}><option>全部状态</option><option>已确认</option><option>已记录</option><option>待确认</option></select></label><label className="xn-archive-search"><span>搜索记录</span><input value={keyword} onChange={event => { setKeyword(event.target.value); setSelectedId(""); }} placeholder="搜索任务、能力或来源" /></label></div>
    </section>

    <section className="xn-archive-layout">
      <div className="xn-card xn-archive-timeline">
        <header><div><small>成长时间线</small><h2>{filtered.length} 条记录</h2></div></header>
        {filtered.length ? <div>{filtered.map(item => <button className={selected?.id === item.id ? "active" : ""} onClick={() => setSelectedId(item.id)} key={item.id}><time dateTime={item.occurredAt}>{displayDate(item.occurredAt)}</time><i /><span><em>{item.category}</em><b>{item.title}</b><small>{item.after}</small></span></button>)}</div> : <div className="xn-archive-empty"><span>⌁</span><b>没有符合条件的记录</b><p>调整筛选条件，或者先完成一次行动。</p><Link href="/actions">前往职场模拟</Link></div>}
      </div>

      <aside className="xn-card xn-archive-detail">
        {selected ? <>
          <header><div><span>{selected.category}</span><h2>{selected.title}</h2><time dateTime={selected.occurredAt}>{displayDate(selected.occurredAt, true)}</time></div><em className={`status-${selected.status}`}>{selected.status}</em></header>
          <section><h3>变化内容</h3><dl className="xn-archive-change">{selected.before && <div><dt>变化前</dt><dd>{selected.before}</dd></div>}<div><dt>{selected.before ? "变化后" : "记录内容"}</dt><dd>{selected.after}</dd></div></dl></section>
          <section><h3>变化原因</h3><p>{selected.explanation}</p></section>
          <section><h3>证据与来源</h3><dl className="xn-archive-meta"><div><dt>数据来源</dt><dd>{selected.source}</dd></div><div><dt>证据标识</dt><dd>{selectedProof?.id ?? "尚未关联"}</dd></div><div><dt>证据等级</dt><dd>{selectedProof?.level ?? selectedRecord?.level ?? "待确认"}</dd></div><div><dt>关联任务</dt><dd>{selectedRun?.title ?? "无"}</dd></div></dl></section>
          {selectedRun && <section><h3>任务成果</h3><p>{selectedRun.submission}</p><div className="xn-archive-tags">{selectedRun.observedAbilities.map(item => <span key={item}>{item}</span>)}</div></section>}
          <section className="xn-archive-impact"><h3>对后续成长的影响</h3><p>{selected.impact}</p></section>
          <footer>{selectedRun && <Link className="xn-btn xn-btn-outline" href={`/actions?task=${encodeURIComponent(selectedRun.taskId)}`}>查看关联任务</Link>}<button className="xn-btn xn-btn-outline" disabled={saving} onClick={() => saveToMemory(selected)}>{saving ? "写入中…" : "写入记忆候选"}</button><Link className="xn-btn xn-btn-primary" href="/growth">查看当前画像</Link></footer>
          {saveNote && <p className="xn-memory-hint">记忆库：{saveNote}　（到「用户画像 → 记忆库」的待确认栏里决定是否保留）</p>}
        </> : <div className="xn-archive-empty"><span>◎</span><b>选择一条成长记录</b><p>这里会展示变化前后、证据来源和后续影响。</p></div>}
      </aside>
    </section>
  </div>;
}
