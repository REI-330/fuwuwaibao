"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { PageHeading } from "../../../components/ui/page-heading";
import { XiangxinMascot } from "../../../components/brand/xiangxin-mascot";
import { requestJson } from "../../../lib/client/http";
import { getProfile } from "../../../lib/client/profile-api";
import type { OccupationSummary } from "../../../types/contracts/catalog";
import type { GeneratedCareerPath } from "../../../types/domain/career-path";

type PathView = "roadmap" | "gaps" | "evaluation";

const DEFAULT_WEEKLY_HOURS = 10;

const stageNames = { junior: "入门基础", intermediate: "独立实践", advanced: "高级发展" };
const metricNames: Record<string, string> = {
  prerequisite_reasonableness: "前置关系合理性",
  gap_coverage: "技能缺口覆盖",
  stage_alignment: "阶段匹配",
  personalization: "个性化裁剪",
  task_skill_alignment: "任务技能一致性",
  executability: "路径可执行性",
};
const checkNames: Record<string, string> = {
  prerequisite_cycle: "无循环依赖",
  missing_skill_id: "技能 ID 有效",
  invalid_level: "技能等级有效",
  invalid_stage: "成长阶段有效",
  invalid_gap: "Gap 计算一致",
  prerequisite_order: "前置顺序正确",
};

export default function PathPage() {
  const [occupations, setOccupations] = useState<OccupationSummary[]>([]);
  const [target, setTarget] = useState("");
  const [weeklyHours, setWeeklyHours] = useState(DEFAULT_WEEKLY_HOURS);
  const [path, setPath] = useState<GeneratedCareerPath | null>(null);
  const [view, setView] = useState<PathView>("roadmap");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  /**
   * 生成路径（M1-6）。
   *
   * **不再硬编码 `AI001` / `SK215=3` / `weekly_hours=10`**：
   * * 目标职业来自 URL 的 `?occupation=`、否则取画像里的候选职业、再否则取目录第一个；
   * * 每周可投入小时数由用户在这里改，直接决定各阶段周数；
   * * 当前技能**不在这里编**：后端会用已确认画像里的技能补（见 `backend/career_path.py`），
   *   用的什么会在 `profile_summary` 与 `warnings` 里如实回传。
   */
  const generate = useCallback(async (targetId: string, hours: number) => {
    setLoading(true);
    setError("");
    try {
      const result = await requestJson<GeneratedCareerPath>("/api/v1/career-path/generate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ target_job: targetId, weekly_hours: hours }),
      });
      setPath(result);
      setTarget(result.occupation_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "职业路径生成失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const [catalog, profile] = await Promise.all([
          requestJson<{ items: OccupationSummary[] }>("/api/v1/occupations"),
          // 画像读不到（未建立/后端不可用）不该挡住路径页：拿目录兜底继续
          getProfile().catch(() => null),
        ]);
        if (!active) return;
        const items = catalog.items;
        setOccupations(items);
        const known = new Set(items.map((item) => item.occupationId));
        const fromQuery = new URLSearchParams(window.location.search).get("occupation");
        const fromProfile = profile?.candidateOccupationIds?.find((id) => known.has(id));
        const initial = [fromQuery, fromProfile].find((id): id is string => Boolean(id && known.has(id)))
          ?? items[0]?.occupationId;
        if (!initial) {
          setError("职业目录为空，暂时无法生成路径。");
          setLoading(false);
          return;
        }
        await generate(initial, DEFAULT_WEEKLY_HOURS);
      } catch (caught) {
        if (!active) return;
        setError(caught instanceof Error ? caught.message : "职业数据加载失败");
        setLoading(false);
      }
    })();
    return () => { active = false; };
  }, [generate]);

  const totals = useMemo(() => path ? {
    hours: path.path.reduce((sum, stage) => sum + stage.estimated_hours, 0),
    weeks: path.path.reduce((sum, stage) => sum + stage.estimated_weeks, 0),
    skills: path.path.flatMap((stage) => stage.skills),
  } : null, [path]);

  const startStage = path?.path.find((stage) => !stage.stage_skipped)?.stage ?? "junior";

  return <div className="xn-stack xn-live-path">
    <PageHeading title="我的成长路径" subtitle="确定阶段目标、能力缺口和任务顺序；职场模拟内容将在后续开放。" />

    <section className="xn-card xn-path-controls" aria-label="路径设置">
      <label><span>目标职业</span>
        <select value={target} disabled={loading} onChange={(event) => { const next = event.target.value; setTarget(next); void generate(next, weeklyHours); }}>
          {occupations.map((item) => <option value={item.occupationId} key={item.occupationId}>{item.targetJob}（{item.occupationId}）</option>)}
        </select>
      </label>
      <label><span>每周可投入小时</span>
        <input type="number" min={1} max={80} value={weeklyHours} disabled={loading}
          onChange={(event) => setWeeklyHours(Math.max(1, Math.min(80, Number(event.target.value) || 1)))}
          onBlur={() => { if (target) void generate(target, weeklyHours); }} />
      </label>
      <button type="button" className="xn-btn xn-btn-outline" disabled={loading || !target} onClick={() => void generate(target, weeklyHours)}>
        {loading ? "正在生成…" : "按当前设置重新生成"}
      </button>
    </section>

    {loading && <p className="xn-session-note" role="status">正在加载路径…</p>}
    {error && <p className="xn-path-error" role="alert">{error}</p>}
    <section className="xn-card xn-stage-companion"><XiangxinMascot size={100} state="coaching" /><div><h2>当前阶段提示</h2><p>{path ? `从${stageNames[startStage]}开始，先完成一个可验证的小任务。` : "正在加载成长路径。"}</p></div></section>

    {path && totals && <>
      {path.warnings.length > 0 && <section className="xn-card xn-path-warnings" aria-label="路径提示">
        <h2>如实说明</h2>
        <ul>{path.warnings.map((item) => <li key={item}>{item}</li>)}</ul>
        <p className="xn-session-note">规则版本 {path.rules_version} · 指标版本 {path.metrics_version} · 生成于 {path.generated_at}</p>
      </section>}

      <section className="xn-path-kpis" aria-label="路径概况">
        <div className="xn-card"><small>目标职业</small><b>{path.target_job}</b><span>{path.occupation_id} · 匹配度 {Math.round(path.match_score * 100)}%</span></div>
        <div className="xn-card"><small>技能差距</small><b>{path.skill_gap_summary.priority_learning_count + path.skill_gap_summary.learning_count + path.skill_gap_summary.improve_count} 项</b><span>{path.skill_gap_summary.satisfied_count} 项已满足</span></div>
        <div className="xn-card"><small>预计投入</small><b>{totals.hours} 小时</b><span>约 {totals.weeks} 周（每周 {path.weekly_hours} 小时）</span></div>
        <div className={`xn-card xn-path-score ${path.evaluation.path_valid ? "valid" : "invalid"}`}><small>合理性评分</small><b>{path.evaluation.overall_score}</b><span>{path.evaluation.grade}</span></div>
      </section>

      {path.profile_summary && <p className="xn-session-note">画像依据：{path.profile_summary}</p>}

      <div className="xn-tabs xn-path-tabs" role="tablist" aria-label="路径视图">
        <button type="button" className={view === "roadmap" ? "active" : ""} onClick={() => setView("roadmap")}>成长路线</button>
        <button type="button" className={view === "gaps" ? "active" : ""} onClick={() => setView("gaps")}>技能差距</button>
        <button type="button" className={view === "evaluation" ? "active" : ""} onClick={() => setView("evaluation")}>合理性评估</button>
      </div>

      {view === "roadmap" && <section className="xn-live-roadmap">
        {path.path.map((stage, index) => <article className="xn-card xn-live-stage" key={stage.stage}>
          <header><span>{index + 1}</span><div><small>{stage.period}</small><h2>{stageNames[stage.stage]}</h2></div><b>{stage.estimated_hours}小时 · {stage.estimated_weeks}周</b></header>
          <p className="xn-stage-goal">{stage.goal}</p>
          <div className="xn-stage-skill-list">{stage.skills.map((skill) => <span className={skill.status} key={skill.skill_id}>{skill.name_zh}<small>{skill.current_level} → {skill.target_level}</small></span>)}</div>
          {stage.stage_skipped ? <div className="xn-stage-skipped">核心能力已经满足，本阶段保留为能力验证节点。</div> : stage.tasks.map((task) => <div className="xn-real-task" key={task.task}>
            <div><small>核心实践任务</small><h3>{task.task}</h3></div>
            <dl><div><dt>使用工具</dt><dd>{task.tools.join("、") || "图谱未记录"}</dd></div><div><dt>交付成果</dt><dd>{task.deliverable || "图谱未记录"}</dd></div><div><dt>成果证据</dt><dd>{task.evidence.map((item) => item.description).join("、") || "图谱未记录"}</dd></div></dl>
            {task.subtasks.length > 0 && <p className="xn-session-note">考核点：{task.subtasks.join("；")}</p>}
            <p className="xn-session-note">来源：{task.source_refs.join("、") || "图谱未标注"}；当前展示实践建议，职场模拟将在后续开放。</p>
          </div>)}
        </article>)}
      </section>}

      {view === "gaps" && <section className="xn-card xn-gap-panel">
        <header><div><h2>技能差距</h2><p>绿色表示当前水平，蓝色表示目标职业要求；技能已按前置关系和优先级排序。</p></div><span>等级范围 0–5</span></header>
        <div className="xn-gap-table"><div className="xn-gap-head"><span>技能</span><span>阶段</span><span>当前 / 目标</span><span>缺口</span></div>{totals.skills.map((skill) => <div className="xn-gap-row" key={skill.skill_id}>
          <div><b>{skill.name_zh}</b><small>{skill.skill_id}{skill.prerequisite_only ? " · 前置技能" : ""} · 先修深度 {skill.prerequisite_depth}</small></div><span>{stageNames[skill.default_stage]}</span><div className="xn-gap-scale"><i style={{ width: `${skill.target_level * 20}%` }} /><b style={{ width: `${skill.current_level * 20}%` }} /></div><strong className={skill.gap === 0 ? "done" : ""}>{skill.gap === 0 ? "已满足" : `差 ${skill.gap} 级`}</strong>
        </div>)}</div>
      </section>}

      {view === "evaluation" && <section className="xn-evaluation-layout">
        <div className="xn-card xn-score-panel"><div className="xn-score-ring" style={{ "--score": `${path.evaluation.overall_score * 3.6}deg` } as CSSProperties}><span>{path.evaluation.overall_score}</span></div><b>{path.evaluation.grade}</b><p>{path.evaluation.path_valid ? "路径已通过全部硬校验" : "路径存在致命问题，暂不建议使用"}</p></div>
        <div className="xn-card xn-metrics-panel"><h2>六维合理性指标</h2>{Object.entries(path.evaluation.metrics).map(([name, score]) => <div className="xn-metric-line" key={name}><span>{metricNames[name] ?? name}</span><div><i style={{ width: `${score}%` }} /></div><b>{score}</b></div>)}</div>
        <div className="xn-card xn-check-panel"><h2>硬校验</h2><div>{Object.entries(path.evaluation.hard_checks).map(([name, failed]) => <span className={failed ? "failed" : "passed"} key={name}>{failed ? "×" : "✓"} {checkNames[name] ?? name}</span>)}</div></div>
        <div className="xn-card xn-workload-panel"><h2>工作量</h2><div className="xn-gap-table">{Object.entries(path.evaluation.workload.stages).map(([name, row]) => <div className="xn-gap-row" key={name}><div><b>{stageNames[name as keyof typeof stageNames] ?? name}</b><small>{row.required_weeks} / 建议上限 {row.recommended_max_weeks} 周</small></div><span>{row.status === "normal" ? "正常" : row.status === "warning" ? "偏紧" : "过载"}</span></div>)}</div></div>
        <div className="xn-card xn-suggestion-panel"><h2>优化建议</h2>{path.evaluation.suggestions.length ? <ul>{path.evaluation.suggestions.map((item) => <li key={item}>{item}</li>)}</ul> : <p>当前路径结构完整，可以按照阶段任务开始行动。</p>}</div>
      </section>}
    </>}
  </div>;
}
