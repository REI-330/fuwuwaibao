"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useDynamicProfile } from "../../../components/profile/profile-provider";
import { writeGrowthRecord } from "../../../lib/client/growth-api";
import { getProfileSnapshot, listProfileHistory } from "../../../lib/client/profile-api";
import type { GrowthRecord } from "../../../types/contracts/growth";
import type { ProfileSnapshot, UserProfile } from "../../../types/contracts/profile";

/** 画像历史一次取多少个版本（点「加载更早的版本」再取一页）。 */
const HISTORY_PAGE_SIZE = 10;

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
  /** 服务端已落库的记录（刷新不丢）；本地事件为 false */
  persisted: boolean;
};

const categories: ArchiveCategory[] = ["全部", "能力变化", "任务行动", "职业方向", "路径调整"];

function isRecent(value: string, period: ArchivePeriod) {
  if (period === "all") return true;
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) && Date.now() - timestamp <= Number(period) * 24 * 60 * 60 * 1000;
}

function textOr(value: unknown) {
  return value === undefined || value === null || value === "" ? "未填" : String(value);
}

function displayDate(value: string, withYear = false) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value.slice(0, 16).replace("T", " ");
  return new Intl.DateTimeFormat("zh-CN", withYear
    ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { month: "2-digit", day: "2-digit" }).format(date);
}

/**
 * 服务端成长记录 → 档案条目。
 *
 * 状态按「它派生的候选到哪一步了」如实给：有已确认的候选 = 已确认；只有待确认候选 = 待确认；
 * 一条候选都没有（内容里没有图谱已知的职业/技能名）= 只落了记录本身。**不替用户判定**。
 */
function itemFromRecord(record: GrowthRecord): ArchiveItem {
  const confirmed = record.candidates.some(candidate => candidate.status === "confirmed");
  return {
    id: `server:${record.id}`,
    category: record.kind,
    title: record.title,
    before: record.before || undefined,
    after: record.after,
    explanation: record.explanation,
    occurredAt: record.occurredAt,
    source: record.source || "成长记录",
    status: confirmed ? "已确认" : record.candidates.length ? "待确认" : "已记录",
    impact: confirmed
      ? "这条记录派生的候选已经确认，进入了记忆库的已确认区。"
      : record.candidates.length
        ? "它派生的候选还在待确认区 —— 你确认之后才会进入对话与推荐。"
        : "只落了记录本身：内容里没有图谱已知的职业/技能名，所以没有派生候选。",
    persisted: true,
  };
}

export default function GrowthRecordsPage() {
  const {
    events, taskRuns, evidence, records,
    archive, archiveStatus, archiveError, refreshArchive, loadMoreArchive, addArchiveRecord, setArchiveKind,
  } = useDynamicProfile();
  const [category, setCategory] = useState<ArchiveCategory>("全部");
  const [period, setPeriod] = useState<ArchivePeriod>("30");
  const [status, setStatus] = useState<ArchiveStatus>("全部状态");
  const [keyword, setKeyword] = useState("");
  const [selectedId, setSelectedId] = useState("");
  /* 写入记忆库：把选中的这条记录作为成长记录提交，后端会派生**待确认**记忆候选。
     记录 id 用 `archive_<item.id>`：重复点不会产生重复记录或重复候选（后端按 recordId 幂等）。 */
  const [saving, setSaving] = useState(false);
  const [saveNote, setSaveNote] = useState("");
  /* 画像历史档案（2026-10-01 第七轮）：读服务端只增不改的快照，回答「上周那一刻的档案长什么样」。
     这是**只读**面板：点开历史版本不会改当前画像，也不会产生新快照。 */
  const [snapshots, setSnapshots] = useState<ProfileSnapshot[]>([]);
  const [snapshotTotal, setSnapshotTotal] = useState(0);
  const [snapshotCursor, setSnapshotCursor] = useState<string | null>(null);
  const [snapshotHasMore, setSnapshotHasMore] = useState(false);
  const [snapshotLoading, setSnapshotLoading] = useState(true);
  const [snapshotError, setSnapshotError] = useState("");
  const [snapshotPicked, setSnapshotPicked] = useState("");
  const [snapshotProfile, setSnapshotProfile] = useState<UserProfile | null>(null);
  const [snapshotDetailLoading, setSnapshotDetailLoading] = useState(false);

  useEffect(() => {
    let active = true;
    listProfileHistory({ limit: HISTORY_PAGE_SIZE })
      .then(page => {
        if (!active) return;
        setSnapshots(page.items);
        setSnapshotTotal(page.total);
        setSnapshotCursor(page.nextCursor);
        setSnapshotHasMore(page.hasMore);
      })
      .catch(caught => { if (active) setSnapshotError(caught instanceof Error ? caught.message : "画像历史读取失败"); })
      .finally(() => { if (active) setSnapshotLoading(false); });
    return () => { active = false; };
  }, []);

  async function loadMoreSnapshots() {
    if (!snapshotCursor) return;
    setSnapshotLoading(true);
    setSnapshotError("");
    try {
      const page = await listProfileHistory({ limit: HISTORY_PAGE_SIZE, cursor: snapshotCursor });
      setSnapshots(current => [...current, ...page.items]);
      setSnapshotTotal(page.total);
      setSnapshotCursor(page.nextCursor);
      setSnapshotHasMore(page.hasMore);
    } catch (caught) {
      setSnapshotError(caught instanceof Error ? caught.message : "画像历史读取失败");
    } finally {
      setSnapshotLoading(false);
    }
  }

  async function openSnapshot(snapshotId: string) {
    setSnapshotPicked(snapshotId);
    setSnapshotDetailLoading(true);
    setSnapshotError("");
    try {
      const result = await getProfileSnapshot(snapshotId);
      setSnapshotProfile(result.profile);
    } catch (caught) {
      setSnapshotProfile(null);
      setSnapshotError(caught instanceof Error ? caught.message : "画像快照读取失败");
    } finally {
      setSnapshotDetailLoading(false);
    }
  }

  async function saveToMemory(item: ArchiveItem) {
    if (item.persisted) {
      setSaveNote("这条成长记录已经在服务端保存；请到记忆库确认或删除它派生的候选。");
      return;
    }
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
      // 服务端已经落库的那一条直接进列表（不是乐观猜测）；重复点按 recordId 幂等，不会变成两条
      addArchiveRecord(result.record);
      setSaveNote(`${result.note.message}（候选 ${result.candidates.length} 条：${result.candidates.map(candidate => candidate.content).join("、") || "无"}）`);
    } catch (error) {
      setSaveNote(error instanceof Error ? error.message : "写入失败，请稍后重试。");
    } finally {
      setSaving(false);
    }
  }

  const archiveItems = useMemo<ArchiveItem[]>(() => {
    const serverIds = new Set(archive.items.map(item => item.id));
    // 已经写进服务端的本地事件不要再显示一遍：任务实践用 runId 当记录 id，
    // 「写入记忆候选」用 archive_<item.id> —— 两种都认。
    const alreadyPersisted = (localId: string) => serverIds.has(localId) || serverIds.has(`archive_${localId}`);

    const eventItems: ArchiveItem[] = events.filter(event => !alreadyPersisted(event.id)).map(event => {
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
        persisted: false,
      };
    });
    const runItems: ArchiveItem[] = taskRuns.filter(run => !alreadyPersisted(run.id)).map(run => {
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
        persisted: false,
      };
    });
    return [...archive.items.map(itemFromRecord), ...eventItems, ...runItems]
      .sort((left, right) => right.occurredAt.localeCompare(left.occurredAt));
  }, [archive.items, events, evidence, taskRuns]);

  const filtered = archiveItems.filter(item => {
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
  const remaining = Math.max(archive.total - archive.items.length, 0);

  return <div className="xn-archive-dashboard">
    <header className="xn-archive-title"><h1>成长记录档案</h1><p>集中查看已确认的画像变化、行动成果和证据来源</p></header>

    <section className="xn-card xn-archive-summary">
      <article><span>成长记录</span><b>{archive.total}</b><small>服务端已落库，刷新不丢</small></article>
      <article><span>本次会话事件</span><b>{events.length + taskRuns.length}</b><small>还没写进服务端的本地事件</small></article>
      <article><span>确认证据</span><b>{evidence.length}</b><small>已进入画像证据链</small></article>
      <article><span>待确认</span><b>{taskRuns.filter(run => !evidence.some(item => item.taskRunId === run.id)).length}</b><small>尚未写入正式画像</small></article>
    </section>

    <section className="xn-card xn-archive-filters" aria-label="成长记录筛选">
      <div className="xn-archive-category-tabs">{categories.map(item => <button className={category === item ? "active" : ""} aria-pressed={category === item} onClick={() => { setCategory(item); setArchiveKind(item === "全部" ? undefined : item); setSelectedId(""); }} key={item}>{item}</button>)}</div>
      <div className="xn-archive-filter-fields"><label><span>时间范围</span><select value={period} onChange={event => { setPeriod(event.target.value as ArchivePeriod); setSelectedId(""); }}><option value="7">最近7天</option><option value="30">最近30天</option><option value="all">全部时间</option></select></label><label><span>确认状态</span><select value={status} onChange={event => { setStatus(event.target.value as ArchiveStatus); setSelectedId(""); }}><option>全部状态</option><option>已确认</option><option>已记录</option><option>待确认</option></select></label><label className="xn-archive-search"><span>搜索记录</span><input value={keyword} onChange={event => { setKeyword(event.target.value); setSelectedId(""); }} placeholder="搜索任务、能力或来源" /></label></div>
      <p className="xn-session-note">分类与分页走服务端（一次 {archive.items.length}/{archive.total} 条）；时间范围、状态与关键词只作用于已加载的这批记录。</p>
    </section>

    {archiveStatus === "error" && <p className="xn-interview-error" role="alert">{archiveError || "成长记录读取失败"}<button className="xn-btn xn-btn-outline" onClick={() => void refreshArchive()}>重试</button></p>}

    <section className="xn-archive-layout">
      <div className="xn-card xn-archive-timeline">
        <header><div><small>成长时间线</small><h2>{filtered.length} 条记录</h2></div>{archiveStatus === "loading" && <span className="xn-session-note">读取中…</span>}</header>
        {filtered.length ? <div>{filtered.map(item => <button className={selected?.id === item.id ? "active" : ""} onClick={() => setSelectedId(item.id)} key={item.id}><time dateTime={item.occurredAt}>{displayDate(item.occurredAt)}</time><i /><span><em>{item.category}</em><b>{item.title}</b><small>{item.after}</small></span></button>)}</div> : <div className="xn-archive-empty"><span>⌁</span><b>没有符合条件的记录</b><p>调整筛选条件，或者先完成一次行动。</p><div className="xn-archive-empty-actions"><Link href="/actions/tasks">去做一条实践任务</Link><Link href="/actions">前往职场模拟</Link></div></div>}
        {archive.hasMore && <div className="xn-archive-more"><button className="xn-btn xn-btn-outline" disabled={archiveStatus === "loading"} onClick={() => void loadMoreArchive()}>{archiveStatus === "loading" ? "加载中…" : `加载更多（还有 ${remaining} 条）`}</button></div>}
      </div>

      <aside className="xn-card xn-archive-detail">
        {selected ? <>
          <header><div><span>{selected.category}{selected.persisted ? "" : " · 本会话"}</span><h2>{selected.title}</h2><time dateTime={selected.occurredAt}>{displayDate(selected.occurredAt, true)}</time></div><em className={`status-${selected.status}`}>{selected.status}</em></header>
          <section><h3>变化内容</h3><dl className="xn-archive-change">{selected.before && <div><dt>变化前</dt><dd>{selected.before}</dd></div>}<div><dt>{selected.before ? "变化后" : "记录内容"}</dt><dd>{selected.after}</dd></div></dl></section>
          <section><h3>变化原因</h3><p>{selected.explanation}</p></section>
          <section><h3>证据与来源</h3><dl className="xn-archive-meta"><div><dt>数据来源</dt><dd>{selected.source}</dd></div><div><dt>证据标识</dt><dd>{selectedProof?.id ?? "尚未关联"}</dd></div><div><dt>证据等级</dt><dd>{selectedProof?.level ?? selectedRecord?.level ?? "待确认"}</dd></div><div><dt>关联任务</dt><dd>{selectedRun?.title ?? "无"}</dd></div></dl></section>
          {selectedRun && <section><h3>任务成果</h3><p>{selectedRun.submission}</p><div className="xn-archive-tags">{selectedRun.observedAbilities.map(item => <span key={item}>{item}</span>)}</div></section>}
          <section className="xn-archive-impact"><h3>对后续成长的影响</h3><p>{selected.impact}</p></section>
          <footer>{selectedRun && <Link className="xn-btn xn-btn-outline" href={`/actions?task=${encodeURIComponent(selectedRun.taskId)}`}>查看关联任务</Link>}<button className="xn-btn xn-btn-outline" disabled={saving || selected.persisted} onClick={() => void saveToMemory(selected)}>{saving ? "写入中…" : selected.persisted ? "已写入服务端" : "写入记忆候选"}</button><Link className="xn-btn xn-btn-primary" href="/growth">查看当前画像</Link></footer>
          {saveNote && <p className="xn-memory-hint">记忆库：{saveNote}　（到「用户画像 → 记忆库」的待确认栏里决定是否保留）</p>}
        </> : <div className="xn-archive-empty"><span>◎</span><b>选择一条成长记录</b><p>这里会展示变化前后、证据来源和后续影响。</p></div>}
      </aside>
    </section>

    <section className="xn-card xn-archive-history" aria-label="画像历史档案">
      <header>
        <div><small>画像历史档案</small><h2>画像版本回看</h2></div>
        <span className="xn-session-note">共 {snapshotTotal} 个版本 · 最新在前</span>
      </header>
      <p className="xn-session-note">每次画像写入或确认都留一份只增不改的快照；点一个版本看当时那一刻的档案，读历史不会改当前画像。</p>
      {snapshotError && <p className="xn-interview-error" role="alert">{snapshotError}</p>}
      {snapshotLoading && !snapshots.length
        ? <div className="xn-interview-empty">正在读取画像历史…</div>
        : snapshots.length
          ? <div className="xn-archive-history-layout">
              <div className="xn-archive-history-list">
                {snapshots.map(item => <button className={snapshotPicked === item.snapshotId ? "active" : ""} onClick={() => void openSnapshot(item.snapshotId)} key={item.snapshotId}>
                  <time dateTime={item.capturedAt}>{displayDate(item.capturedAt, true)}</time>
                  <span><em>v{item.profileVersion} · {item.status === "confirmed" ? "已确认" : "草稿"}</em><b>{item.summary.major || item.summary.school || "未填学校/专业"}</b><small>{(item.summary.skills ?? []).slice(0, 3).join("、") || "未填技能"}</small></span>
                </button>)}
                {snapshotHasMore && <div className="xn-archive-more"><button className="xn-btn xn-btn-outline" disabled={snapshotLoading} onClick={() => void loadMoreSnapshots()}>{snapshotLoading ? "加载中…" : "加载更早的版本"}</button></div>}
              </div>
              <div className="xn-archive-history-detail">
                {snapshotDetailLoading
                  ? <p className="xn-session-note">读取中…</p>
                  : snapshotProfile
                    ? <>
                        <h3>v{snapshotProfile.profileVersion} · {snapshotProfile.status === "confirmed" ? "已确认" : "草稿"}</h3>
                        <dl className="xn-archive-meta">
                          <div><dt>身份</dt><dd>{textOr(snapshotProfile.identity)}</dd></div>
                          <div><dt>学校</dt><dd>{textOr(snapshotProfile.school)}</dd></div>
                          <div><dt>专业</dt><dd>{textOr(snapshotProfile.major)}</dd></div>
                          <div><dt>目标</dt><dd>{textOr(snapshotProfile.currentGoal)}</dd></div>
                        </dl>
                        <h4>技能（{(snapshotProfile.skills ?? []).length}）</h4>
                        {(snapshotProfile.skills ?? []).length
                          ? <div className="xn-archive-tags">{snapshotProfile.skills.map(skill => <span key={skill.name}>{skill.name}</span>)}</div>
                          : <p className="xn-session-note">这一版没有记录技能。</p>}
                      </>
                    : <div className="xn-archive-empty"><span>◎</span><b>选一个版本</b><p>这里显示那一版画像的完整内容。</p></div>}
              </div>
            </div>
          : <div className="xn-archive-empty"><span>⌁</span><b>还没有历史版本</b><p>保存或确认画像后，这里会按时间留下每个版本。</p></div>}
    </section>
  </div>;
}
