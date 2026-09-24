"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { requestJson } from "../../lib/client/http";
import { buildCareerGraph, buildOccupationOverview, buildSkillGraph, SKILLS_PER_PAGE, type GraphNode } from "../../lib/client/career-graph";
import type { OccupationDetail, OccupationSummary, SkillSummary } from "../../types/contracts/catalog";
import type { ProfileCandidate } from "../../types/view-models/dynamic-profile";
import { KnowledgeGraph } from "./knowledge-graph";
import { XiangxinMascot } from "../brand/xiangxin-mascot";
import { CandidateProfileCard } from "../profile/candidate-profile-card";
import { useChat } from "../chat/chat-context";

type Stats = { occupations: number; skills: number; prerequisites: number; source: string };
export function WorkMapExplorer({ initialOccupationId }: { initialOccupationId?: string }) {
  const [occupations, setOccupations] = useState<OccupationSummary[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [occupation, setOccupation] = useState<OccupationDetail | null>(null);
  const [focusSkill, setFocusSkill] = useState<SkillSummary | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [skillDetail, setSkillDetail] = useState<SkillSummary | null>(null);
  const [skillLoading, setSkillLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [skillResults, setSkillResults] = useState<SkillSummary[]>([]);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [candidate, setCandidate] = useState<ProfileCandidate | null>(null);
  const [retry, setRetry] = useState(0);
  const requestId = useRef(0);
  const skillRequestId = useRef(0);
  const { openChat } = useChat();

  const openOccupation = useCallback(async (id: string) => {
    const token = ++requestId.current;
    ++skillRequestId.current;
    setLoading(true); setError(""); setFocusSkill(null); setSelected(null); setSkillDetail(null); setOccupation(null); setPage(0); setCandidate(null);
    try {
      const data = await requestJson<{ occupation: OccupationDetail }>(`/api/v1/occupations/${encodeURIComponent(id)}`);
      if (token !== requestId.current) return;
      setOccupation(data.occupation); setSelected(buildCareerGraph(data.occupation).nodes[0]);
    } catch (caught) { if (token === requestId.current) setError(caught instanceof Error ? caught.message : "职业关系加载失败"); }
    finally { if (token === requestId.current) setLoading(false); }
  }, []);
  useEffect(() => {
    let cancelled = false;
    Promise.all([requestJson<{ items: OccupationSummary[] }>("/api/v1/occupations"), requestJson<{ items: SkillSummary[] }>("/api/v1/skills"), requestJson<Stats>("/api/v1/catalog/stats")]).then(([jobs, skillData, summary]) => {
      if (cancelled) return;
      setOccupations(jobs.items); setSkills(skillData.items); setStats(summary);
      const initial = jobs.items.find(item => item.occupationId === initialOccupationId) ?? jobs.items[0];
      if (initial) void openOccupation(initial.occupationId); else setLoading(false);
    }).catch(caught => { if (!cancelled) { setError(caught instanceof Error ? caught.message : "目录加载失败"); setLoading(false); } });
    return () => { cancelled = true; };
  }, [initialOccupationId, openOccupation, retry]);
  useEffect(() => {
    if (!query.trim()) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      requestJson<{ items: SkillSummary[] }>(`/api/v1/skills?keyword=${encodeURIComponent(query)}`).then(data => { if (!cancelled) setSkillResults(data.items); }).catch(caught => { if (!cancelled) setError(caught instanceof Error ? caught.message : "技能搜索失败"); });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query]);
  async function selectNode(node: GraphNode) {
    if (node.kind === "occupation" && node.ref !== occupation?.occupationId) { await openOccupation(node.ref!); return; }
    setSelected(node); setSkillDetail(null); setSkillLoading(node.kind === "skill");
    const token = ++skillRequestId.current;
    if (node.kind !== "skill" || !node.ref) return;
    try {
      const result = await requestJson<{ items: SkillSummary[] }>(`/api/v1/skills?keyword=${encodeURIComponent(node.ref)}`);
      if (token === skillRequestId.current) setSkillDetail(result.items.find(item => item.skillId === node.ref) ?? null);
    } catch (caught) { if (token === skillRequestId.current) setError(caught instanceof Error ? caught.message : "技能详情加载失败"); }
    finally { if (token === skillRequestId.current) setSkillLoading(false); }
  }
  function exploreSkill(skill: SkillSummary) {
    ++requestId.current; ++skillRequestId.current;
    setLoading(false); setFocusSkill(skill); setSelected(buildSkillGraph(skill, skills).nodes[0]); setSkillDetail(skill); setError(""); setCandidate(null);
  }
  const graph = focusSkill ? buildSkillGraph(focusSkill, skills) : occupation ? buildCareerGraph(occupation, page) : buildOccupationOverview(occupations);
  const related = graph.edges.filter(edge => edge.from === selected?.id || edge.to === selected?.id);
  const matchedJobs = occupations.filter(item => [item.targetJob, item.targetJobEn, item.occupationId, ...item.coreSkills].join(" ").toLowerCase().includes(query.toLowerCase()));
  return <div className="xn-stack">
    <div className="xn-map-summary"><span><b>{stats?.occupations ?? "—"}</b> 个职业</span><span><b>{stats?.skills ?? "—"}</b> 项技能</span><span><b>{stats?.prerequisites ?? "—"}</b> 条前置关系</span><small>{stats ? stats.source === "sqlite" ? "职业目录数据" : "本地职业数据种子" : "正在读取目录"}</small></div>
    <section className="xn-card xn-map-search"><label>查找职业或技能<input value={query} onChange={event => { setQuery(event.target.value); setSkillResults([]); }} placeholder="例如：机器视觉、Python、SK215" /></label><label>聚焦职业<select value={occupation?.occupationId ?? ""} onChange={event => void openOccupation(event.target.value)}><option value="" disabled>选择一个职业</option>{occupations.map(item => <option value={item.occupationId} key={item.occupationId}>{item.targetJob}</option>)}</select></label><button className="xn-btn xn-btn-outline" onClick={() => { ++requestId.current; ++skillRequestId.current; setLoading(false); setOccupation(null); setFocusSkill(null); setSkillDetail(null); setSelected(null); setCandidate(null); }}>浏览全部职业</button></section>
    {query.trim() && <section className="xn-card xn-graph-search-results" aria-label="图谱搜索结果"><div><h3>职业</h3>{matchedJobs.map(item => <button key={item.occupationId} onClick={() => { void openOccupation(item.occupationId); setQuery(""); }}>{item.targetJob}</button>)}{!matchedJobs.length && <p>未找到职业</p>}</div><div><h3>技能</h3>{skillResults.map(item => <button key={item.skillId} onClick={() => { exploreSkill(item); setQuery(""); }}>{item.nameZh}</button>)}{!skillResults.length && <p>暂无技能结果</p>}</div></section>}
    {error && <p role="alert" className="xn-catalog-error">{error} <button onClick={() => { ++requestId.current; ++skillRequestId.current; setLoading(true); setError(""); setRetry(value => value + 1); }}>重新加载</button></p>}
    <div className="xn-knowledge-layout"><div><div className="xn-graph-context"><h2>{focusSkill ? `${focusSkill.nameZh} · 前置关系` : occupation ? `${occupation.targetJob} · 职业关系图` : "职业节点总览"}</h2>{occupation && !focusSkill && <div><button disabled={page === 0} onClick={() => { setPage(value => value - 1); setSelected(null); }}>上一组技能</button><span>{page + 1} / {Math.max(1, Math.ceil(occupation.skills.length / SKILLS_PER_PAGE))}</span><button disabled={(page + 1) * SKILLS_PER_PAGE >= occupation.skills.length} onClick={() => { setPage(value => value + 1); setSelected(null); }}>下一组技能</button></div>}{focusSkill && occupation && <button onClick={() => { setFocusSkill(null); setSkillDetail(null); setSelected(buildCareerGraph(occupation, page).nodes[0]); }}>返回职业关系</button>}</div>
      {loading ? <div className="xn-card xn-graph-loading" role="status">正在加载职业与技能关系…</div> : <KnowledgeGraph key={`${occupation?.occupationId ?? "all"}:${focusSkill?.skillId ?? ""}:${page}`} graph={graph} selected={selected?.id ?? ""} onSelect={node => void selectNode(node)} />}
      <p className="xn-session-note">关系来自职业目录与技能前置数据。“知识 / 学习单元”使用阶段学习内容。技能分组显示，完整查询可切换目录视图。</p>
    </div><aside className="xn-card xn-node-detail" aria-label="节点详情"><header><XiangxinMascot size={60} state="guiding" /><div><small>探索一个节点</small><h2>{selected?.label ?? "点击图谱节点"}</h2></div></header>
      {selected ? <><p>{skillDetail?.descriptionZh ?? selected.description}</p><h3>关联关系</h3><ul>{related.map(edge => <li key={`${edge.from}-${edge.to}`}>{graph.nodes.find(node => node.id === edge.from)?.label} → <b>{edge.relation}</b> → {graph.nodes.find(node => node.id === edge.to)?.label}</li>)}</ul>{!related.length && <p className="xn-session-note">选择职业查看其技能与学习关系。</p>}
        {selected.kind === "skill" && <section><h3>前置技能</h3>{skillDetail ? skillDetail.prerequisites.length ? skillDetail.prerequisites.map(id => <button className="xn-text-btn" key={id} onClick={() => void selectNode({ id: `skill:${id}`, kind: "skill", ref: id, label: skills.find(item => item.skillId === id)?.nameZh ?? id, x: 0, y: 0, description: "正在查询前置技能详情" })}>{skills.find(item => item.skillId === id)?.nameZh ?? id}</button>) : <p>目录未列出前置技能。</p> : <p>{skillLoading ? "正在查询技能详情…" : "目录未返回这项技能的详情，请尝试搜索技能编号。"}</p>}{skillDetail && <button className="xn-btn xn-btn-outline" onClick={() => exploreSkill(skillDetail)}>以此技能为中心</button>}</section>}
        <button className="xn-btn xn-btn-outline xn-full-btn" onClick={() => openChat(`我正在未来工作地图查看「${selected.label}」，请帮我理解它与职业成长的关系。`)}>和新向聊聊这个节点</button>
        {selected.kind === "occupation" && occupation && <><details><summary>职业任务与发展信号</summary><ul>{occupation.tasks.map(task => <li key={task}>{task}</li>)}</ul><p>{occupation.futureSignalZh}</p></details><button className="xn-btn xn-btn-outline xn-full-btn" onClick={() => setCandidate({ id: crypto.randomUUID(), module: "方向偏好", field: "当前候选职业方向", content: occupation.targetJob, level: "用户自述", source: "用户在职业知识图谱选择", task: null, status: "待验证", updatedAt: new Date().toISOString() })}>列为候选方向</button><Link className="xn-btn xn-btn-primary xn-full-btn" href={`/path?occupation=${occupation.occupationId}`}>用这个职业安排成长路径 →</Link></>}
      </> : <p>选择职业展开图谱，点击技能查看前置关系，或搜索你关心的职业与技能。</p>}
      {candidate && <CandidateProfileCard key={candidate.id} candidate={candidate} onDismiss={() => setCandidate(null)} />}
    </aside></div>
  </div>;
}
