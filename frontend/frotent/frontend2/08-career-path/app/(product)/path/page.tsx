"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { PageHeading } from "../../../components/ui/page-heading";
import { XiangxinMascot } from "../../../components/brand/xiangxin-mascot";
import { requestJson } from "../../../lib/client/http";
import type { OccupationSummary } from "../../../types/contracts/catalog";
import type { GeneratedCareerPath } from "../../../types/domain/career-path";

type PathView = "roadmap" | "gaps" | "evaluation";

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



  const [path, setPath] = useState<GeneratedCareerPath | null>(null);
  const [view, setView] = useState<PathView>("roadmap");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const generate = useCallback(async (targetId: string, hours: number) => {
    setLoading(true);
    setError("");
    try {
      const result = await requestJson<GeneratedCareerPath>("/api/v1/career-path/generate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          target_job: targetId,
          current_skills: targetId === "AI001" ? [{ skill_id: "SK215", current_level: 3 }] : [],
          weekly_hours: hours,
        }),
      });
      setPath(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "职业路径生成失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    requestJson<{ items: OccupationSummary[] }>("/api/v1/occupations")
      .then((result) => {

        const queryTarget = new URLSearchParams(window.location.search).get("occupation");
        const initial = result.items.some((item) => item.occupationId === queryTarget) ? queryTarget! : "AI001";

        return generate(initial, 10);
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "职业数据加载失败");
        setLoading(false);
      });
  }, [generate]);

  const totals = useMemo(() => path ? {
    hours: path.path.reduce((sum, stage) => sum + stage.estimated_hours, 0),
    weeks: path.path.reduce((sum, stage) => sum + stage.estimated_weeks, 0),
    skills: path.path.flatMap((stage) => stage.skills),
  } : null, [path]);

  return <div className="xn-stack xn-live-path">
    <PageHeading title="我的成长路径" subtitle="确定阶段目标、能力缺口和任务顺序；职场模拟内容将在后续开放。" />



    {loading && <p className="xn-session-note" role="status">正在加载路径…</p>}
    {error && <p className="xn-path-error" role="alert">{error}</p>}
    <section className="xn-card xn-stage-companion"><XiangxinMascot size={100} state="coaching" /><div><h2>当前阶段提示</h2><p>{path ? `从${stageNames[path.path.find(stage => !stage.stage_skipped)?.stage ?? "junior"]}开始，先完成一个可验证的小任务。` : "正在加载成长路径。"}</p></div></section>
    {path && totals && <>
      <section className="xn-path-kpis" aria-label="路径概况">
        <div className="xn-card"><small>目标职业</small><b>{path.target_job}</b><span>{path.occupation_id} · 匹配度 {Math.round(path.match_score * 100)}%</span></div>
        <div className="xn-card"><small>技能差距</small><b>{path.skill_gap_summary.priority_learning_count + path.skill_gap_summary.learning_count + path.skill_gap_summary.improve_count} 项</b><span>{path.skill_gap_summary.satisfied_count} 项已满足</span></div>
        <div className="xn-card"><small>预计投入</small><b>{totals.hours} 小时</b><span>约 {totals.weeks} 周</span></div>
        <div className={`xn-card xn-path-score ${path.evaluation.path_valid ? "valid" : "invalid"}`}><small>合理性评分</small><b>{path.evaluation.overall_score}</b><span>{path.evaluation.grade}</span></div>
      </section>

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
            <dl><div><dt>使用工具</dt><dd>{task.tools.join("、")}</dd></div><div><dt>交付成果</dt><dd>{task.deliverable}</dd></div><div><dt>成果证据</dt><dd>{task.evidence.map((item) => item.description).join("、")}</dd></div></dl>
            <p className="xn-session-note">当前展示实践建议，职场模拟将在后续开放。</p>
          </div>)}
        </article>)}
      </section>}

      {view === "gaps" && <section className="xn-card xn-gap-panel">
        <header><div><h2>技能差距</h2><p>绿色表示当前水平，蓝色表示目标职业要求；技能已按前置关系和优先级排序。</p></div><span>等级范围 0–5</span></header>
        <div className="xn-gap-table"><div className="xn-gap-head"><span>技能</span><span>阶段</span><span>当前 / 目标</span><span>缺口</span></div>{totals.skills.map((skill) => <div className="xn-gap-row" key={skill.skill_id}>
          <div><b>{skill.name_zh}</b><small>{skill.skill_id}{skill.prerequisite_only ? " · 前置技能" : ""}</small></div><span>{stageNames[skill.default_stage]}</span><div className="xn-gap-scale"><i style={{ width: `${skill.target_level * 20}%` }} /><b style={{ width: `${skill.current_level * 20}%` }} /></div><strong className={skill.gap === 0 ? "done" : ""}>{skill.gap === 0 ? "已满足" : `差 ${skill.gap} 级`}</strong>
        </div>)}</div>
      </section>}

      {view === "evaluation" && <section className="xn-evaluation-layout">
        <div className="xn-card xn-score-panel"><div className="xn-score-ring" style={{ "--score": `${path.evaluation.overall_score * 3.6}deg` } as CSSProperties}><span>{path.evaluation.overall_score}</span></div><b>{path.evaluation.grade}</b><p>{path.evaluation.path_valid ? "路径已通过全部硬校验" : "路径存在致命问题，暂不建议使用"}</p></div>
        <div className="xn-card xn-metrics-panel"><h2>六维合理性指标</h2>{Object.entries(path.evaluation.metrics).map(([name, score]) => <div className="xn-metric-line" key={name}><span>{metricNames[name]}</span><div><i style={{ width: `${score}%` }} /></div><b>{score}</b></div>)}</div>
        <div className="xn-card xn-check-panel"><h2>硬校验</h2><div>{Object.entries(path.evaluation.hard_checks).map(([name, failed]) => <span className={failed ? "failed" : "passed"} key={name}>{failed ? "×" : "✓"} {checkNames[name]}</span>)}</div></div>
        <div className="xn-card xn-suggestion-panel"><h2>优化建议</h2>{path.evaluation.suggestions.length ? <ul>{path.evaluation.suggestions.map((item) => <li key={item}>{item}</li>)}</ul> : <p>当前路径结构完整，可以按照阶段任务开始行动。</p>}</div>
      </section>}
    </>}
  </div>;
}
