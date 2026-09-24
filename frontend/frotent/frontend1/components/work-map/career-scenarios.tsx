"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { XiangxinMascot } from "../../components/brand/xiangxin-mascot";
import { PageHeading } from "../../components/ui/page-heading";
import { occupations } from "../../lib/client/demo-api";
import type { OccupationId } from "../../types/view-models/product";
import { CandidateProfileCard } from "../../components/profile/candidate-profile-card";
import type { ProfileCandidate } from "../../types/view-models/dynamic-profile";

export function CareerScenarios() {
  const router = useRouter();
  const [selected, setSelected] = useState<OccupationId>("edge-ai");
  const [sourceInfo, setSourceInfo] = useState("");
  const [candidate, setCandidate] = useState<ProfileCandidate | null>(null);
  const occupation = occupations.find((item) => item.id === selected) ?? occupations[0];
  const comparison = occupations.find(item => item.id !== selected && item.id === "embedded") ?? occupations.find(item => item.id !== selected)!;
  const columns = [
    { key: "ai" as const, title: "AI可代办", icon: "✦", tone: "teal" },
    { key: "collaborate" as const, title: "人机协作", icon: "♟", tone: "blue" },
    { key: "human" as const, title: "人主导", icon: "●", tone: "orange" },
  ];

  return <div className="xn-stack">
    <PageHeading title="职业情境示例" subtitle="理解职业在做什么、比较方向差异；选定候选方向后，再安排成长路径。" />
    <div className="xn-tabs xn-occupation-tabs">{occupations.map((item) => <button className={selected === item.id ? "active" : ""} onClick={() => setSelected(item.id)} key={item.id}>{item.shortName}</button>)}</div>
    <div className="xn-work-layout">
      <div className="xn-work-main">
        <section className="xn-card xn-task-map">
          <h2>这个职业平时在做什么？ <span title="任务来自职业知识库与人工核实">?</span></h2>
          <div className="xn-task-columns">{columns.map((column, index) => <div className={`xn-task-column ${column.tone}`} key={column.key}>
            <h3><span>{column.icon}</span>{column.title}</h3>
            {occupation.tasks[column.key].map((task) => <div className="xn-mini-task" key={task}>{task}</div>)}
            {index < 2 && <i className="xn-flow-arrow">→</i>}
          </div>)}</div>
          <div className="xn-map-trend">↗ 人机协作任务正在增加</div>
        </section>
        <div className="xn-work-bottom">
          <section className="xn-card xn-requirements"><h2>职业要求 <span>?</span></h2><div className="xn-requirement-grid"><div><b className="teal">⌁ 技能</b>{occupation.skills.map((item) => <span key={item}>{item}</span>)}</div><div><b className="blue">▥ 知识</b>{occupation.knowledge.map((item) => <span key={item}>{item}</span>)}</div><div><b className="orange">▣ 工具</b>{occupation.tools.map((item) => <span key={item}>{item}</span>)}</div></div></section>
          <section className="xn-card xn-comparison"><h2>方向对比</h2><div className="xn-compare-table"><div><span /><b>{occupation.shortName}</b><b>{comparison.shortName}</b></div><div><span>任务聚焦</span><p>{occupation.tasks.human.join("、")}</p><p>{comparison.tasks.human.join("、")}</p></div><div><span>人机协作</span><p>{occupation.tasks.collaborate.join("、")}</p><p>{comparison.tasks.collaborate.join("、")}</p></div><div><span>待了解的问题</span><p>{occupation.reason.find(item => item.state === "verify")?.value}</p><p>{comparison.reason.find(item => item.state === "verify")?.value}</p></div></div></section>
        </div>
        <div className="xn-source-row"><span>数据来源</span>{["O*NET直接数据", "B组整理与补充", "用户数据"].map(source => <button key={source} onClick={() => setSourceInfo(source)}>{source}</button>)}</div>
        {sourceInfo && <p className="xn-session-note" role="status">{sourceInfo}：当前地图使用项目原有示例资料，未实时查询外部来源或本次会话画像。<button className="xn-text-btn" onClick={() => setSourceInfo("")}>关闭</button></p>}
      </div>
      <aside className="xn-card xn-recommend-card">
        <div className="xn-recommend-title"><XiangxinMascot size={60} state="guiding" /><h2>为什么推荐这个方向</h2></div>
        <div className="xn-reasons">{occupation.reason.map((item) => <div className={item.state} key={item.label}><span>{item.state === "confirmed" ? "✦" : item.state === "transfer" ? "◇" : "?"}</span><p><b>{item.label}：</b><small>{item.value}</small></p></div>)}</div>
        <button className="xn-btn xn-btn-outline xn-full-btn" onClick={() => setCandidate({ id: crypto.randomUUID(), module: "方向偏好", field: "当前候选职业方向", content: occupation.name, level: "用户自述", source: "用户在职业地图选择", task: null, status: "待验证", updatedAt: new Date().toISOString() })}>列为候选方向</button>
        {candidate && <CandidateProfileCard key={candidate.id} candidate={candidate} onDismiss={() => setCandidate(null)} />}
        <button className="xn-btn xn-btn-primary xn-full-btn" onClick={() => router.push(selected === "vision" ? "/path?occupation=AI001" : "/path")}>选择目标并生成成长路径<span>›</span></button>
        <button className="xn-btn xn-btn-outline xn-full-btn" onClick={() => router.push("/actions")}>职场模拟（筹备中）<span>›</span></button>
        <p className="xn-recommend-note">这里保留原有示例推荐，尚未根据本次会话画像重新计算。边缘AI与嵌入式方向需在路径页选择对应目标，不代表唯一适合方向。</p>
      </aside>
    </div>
  </div>;
}
