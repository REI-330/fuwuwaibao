"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeading } from "../../components/ui/page-heading";
import { requestJson } from "../../lib/client/http";
import type { OccupationDetail, OccupationSummary, SkillSummary } from "../../types/contracts/catalog";

type CatalogStats = {
  occupations: number;
  skills: number;
  prerequisites: number;
  occupationSkills: number;
  stages: number;
  source: "sqlite" | "json-seed";
};

const stageLabel: Record<string, string> = {
  junior: "入门基础",
  intermediate: "独立实践",
  advanced: "高级发展",
};

export function CatalogBrowser() {
  const [view, setView] = useState<"occupations" | "skills">("occupations");
  const [stats, setStats] = useState<CatalogStats | null>(null);
  const [occupations, setOccupations] = useState<OccupationSummary[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selected, setSelected] = useState<OccupationDetail | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadOccupations = useCallback(async (keyword = "") => {
    const data = await requestJson<{ items: OccupationSummary[] }>(`/api/v1/occupations?keyword=${encodeURIComponent(keyword)}`);
    setOccupations(data.items);
  }, []);

  const loadSkills = useCallback(async (keyword = "") => {
    const data = await requestJson<{ items: SkillSummary[] }>(`/api/v1/skills?keyword=${encodeURIComponent(keyword)}`);
    setSkills(data.items);
  }, []);

  useEffect(() => {
    Promise.all([
      requestJson<CatalogStats>("/api/v1/catalog/stats"),
      requestJson<{ items: OccupationSummary[] }>("/api/v1/occupations"),
      requestJson<{ items: SkillSummary[] }>("/api/v1/skills"),
    ]).then(([catalogStats, occupationData, skillData]) => {
      setStats(catalogStats);
      setOccupations(occupationData.items);
      setSkills(skillData.items);
    })
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "数据加载失败"))
      .finally(() => setLoading(false));
  }, [loadOccupations, loadSkills]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const operation = view === "occupations" ? loadOccupations(query) : loadSkills(query);
      operation.catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "搜索失败"));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query, view, loadOccupations, loadSkills]);

  const categories = useMemo(() => [...new Set(skills.map((skill) => skill.categoryZh))], [skills]);

  async function openOccupation(occupationId: string) {
    setError("");
    try {
      const data = await requestJson<{ occupation: OccupationDetail }>(`/api/v1/occupations/${occupationId}`);
      setSelected(data.occupation);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "职业详情加载失败");
    }
  }

  return <div className="xn-stack xn-catalog-page">
    <PageHeading title="职业与技能目录" subtitle="图谱的列表视图：查询完整职业要求、技能目标和前置关系" />

    <section className="xn-catalog-stats" aria-label="数据库概况">
      <div className="xn-card"><small>职业库</small><b>{stats?.occupations ?? 32}</b><span>个职业方向</span></div>
      <div className="xn-card"><small>技能库</small><b>{stats?.skills ?? 297}</b><span>项标准技能</span></div>
      <div className="xn-card"><small>前置关系</small><b>{stats?.prerequisites ?? 247}</b><span>条技能依赖</span></div>
      <div className="xn-card"><small>阶段模板</small><b>{stats?.stages ?? 96}</b><span>个成长阶段</span></div>
    </section>

    <section className="xn-card xn-catalog-browser">
      <header className="xn-catalog-toolbar">
        <div className="xn-tabs" role="tablist" aria-label="目录类型">
          <button type="button" className={view === "occupations" ? "active" : ""} onClick={() => { setView("occupations"); setQuery(""); }}>职业库</button>
          <button type="button" className={view === "skills" ? "active" : ""} onClick={() => { setView("skills"); setQuery(""); }}>技能库</button>
        </div>
        <label className="xn-catalog-search"><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={view === "occupations" ? "搜索职业、别名或关键词" : "搜索技能名称或编号"} /></label>
        <span className="xn-db-source">{stats?.source === "sqlite" ? "SQLite 数据库" : "本地数据种子"}</span>
      </header>

      {error && <p className="xn-catalog-error" role="alert">{error}</p>}
      {loading ? <div className="xn-catalog-empty">正在加载职业与技能数据…</div> : view === "occupations" ? <>
        <div className="xn-catalog-grid">
          {occupations.map((occupation) => <button type="button" className="xn-occupation-card" key={occupation.occupationId} onClick={() => openOccupation(occupation.occupationId)}>
            <span className="xn-occupation-code">{occupation.occupationId}</span>
            <h2>{occupation.targetJob}</h2>
            <small>{occupation.targetJobEn}</small>
            <p>{occupation.descriptionZh}</p>
            <div className="xn-occupation-tags">{occupation.coreSkills.slice(0, 3).map((skill) => <span key={skill}>{skill}</span>)}</div>
            <footer><span>{occupation.skillCount} 项技能</span><span>{occupation.stageCount} 个阶段</span><b>查看详情 →</b></footer>
          </button>)}
        </div>
        {!occupations.length && <div className="xn-catalog-empty">没有找到匹配的职业</div>}
      </> : <>
        <div className="xn-category-row" aria-label="当前技能分类">{categories.slice(0, 8).map((category) => <span key={category}>{category}</span>)}</div>
        <div className="xn-skill-table" role="table" aria-label="技能列表">
          <div className="xn-skill-table-head" role="row"><span>技能</span><span>分类</span><span>前置技能</span><span>说明</span></div>
          {skills.map((skill) => <div className="xn-skill-table-row" role="row" key={skill.skillId}>
            <div><b>{skill.nameZh}</b><small>{skill.skillId} · {skill.name}</small></div>
            <span className="xn-skill-category">{skill.categoryZh}</span>
            <span>{skill.prerequisites.length ? skill.prerequisites.join("、") : "无"}</span>
            <p>{skill.descriptionZh}</p>
          </div>)}
        </div>
        {!skills.length && <div className="xn-catalog-empty">没有找到匹配的技能</div>}
      </>}
    </section>

    {selected && <div className="xn-catalog-drawer-mask" onMouseDown={() => setSelected(null)}>
      <aside className="xn-catalog-drawer" aria-label={`${selected.targetJob}详情`} onMouseDown={(event) => event.stopPropagation()}>
        <button type="button" className="xn-catalog-close" aria-label="关闭详情" onClick={() => setSelected(null)}>×</button>
        <span className="xn-occupation-code">{selected.occupationId}</span><h2>{selected.targetJob}</h2><small>{selected.targetJobEn}</small><p className="xn-detail-intro">{selected.descriptionZh}</p>
        <div className="xn-detail-section"><h3>核心技能要求</h3><div className="xn-detail-skills">{selected.skills.map((skill) => <span key={skill.skillId}><b>{skill.nameZh}</b><small>目标 {skill.targetLevel}级 · 重要度 {skill.importance}</small></span>)}</div></div>
        <div className="xn-detail-section"><h3>三阶段成长设计</h3><div className="xn-detail-stages">{selected.stages.map((stage) => <article key={stage.stage}><span>{stage.period}</span><h4>{stageLabel[stage.stage] ?? stage.stage}</h4><p>{stage.goal}</p><b>阶段任务</b><p>{stage.task}</p><small>{stage.hours} 小时 · 成果：{stage.deliverable}</small></article>)}</div></div>
        <div className="xn-detail-section xn-future-signal"><h3>职业发展信号</h3><p>{selected.futureSignalZh}</p></div>
        <Link className="xn-btn xn-btn-primary xn-full-btn" href={`/path?occupation=${selected.occupationId}`}>用这个职业生成路径 <span>→</span></Link>
      </aside>
    </div>}
  </div>;
}
