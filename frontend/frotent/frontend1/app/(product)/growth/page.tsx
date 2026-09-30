"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import MemoryPanel from "../../../components/profile/memory-panel";
import { getCareerRecommendations } from "../../../lib/client/career-recommendation-api";
import { getProfile } from "../../../lib/client/profile-api";
import type { CareerRecommendation } from "../../../types/contracts/career-recommendation";
import type { UserProfile } from "../../../types/contracts/profile";

type Tab = "profile" | "market";

const identityNames: Record<UserProfile["identity"], string> = {
  student: "在校生",
  graduate: "应届生",
  newEmployee: "职场新人",
  careerChanger: "转型探索中",
};

const chartHeights = [28, 39, 35, 53, 59, 72, 81];

function valueOrEmpty(value?: string | number | null) {
  return value === undefined || value === null || value === "" ? "待补充" : String(value);
}

export default function GrowthPage() {
  const [tab, setTab] = useState<Tab>("profile");
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [recommendations, setRecommendations] = useState<CareerRecommendation[]>([]);
  const [memoryHash, setMemoryHash] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [profileError, setProfileError] = useState("");
  const [recommendationError, setRecommendationError] = useState("");

  useEffect(() => {
    let active = true;
    const profileRequest = getProfile().then(result => {
      if (active) setProfile(result);
    }).catch(error => {
      if (active) setProfileError(error instanceof Error ? error.message : "用户画像加载失败");
    });
    const recommendationRequest = getCareerRecommendations().then(result => {
      if (!active) return;
      setRecommendations(result.recommendations);
      setMemoryHash(result.memory_hash ?? "");
      setSelectedId(result.recommendations[0]?.occupation_id ?? "");
    }).catch(error => {
      if (active) setRecommendationError(error instanceof Error ? error.message : "岗位推荐加载失败");
    });
    Promise.allSettled([profileRequest, recommendationRequest]).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, []);

  const selectedJob = recommendations.find(item => item.occupation_id === selectedId) ?? recommendations[0] ?? null;
  const interestTags = useMemo(() => profile ? Array.from(new Set([
    ...profile.interests,
    ...profile.skills.map(skill => skill.name),
  ])).slice(0, 8) : [], [profile]);

  function showJob(job: CareerRecommendation) {
    setSelectedId(job.occupation_id);
    setTab("market");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  return <div className="xn-profile-showcase">
    <header className="xn-profile-page-title"><h1>动态用户画像展示</h1><p>整合个人信息、经历与知识库检索，智能匹配适配岗位</p></header>

    <nav className="xn-profile-showcase-tabs" aria-label="用户画像视图">
      <button className={tab === "profile" ? "active" : ""} aria-selected={tab === "profile"} role="tab" onClick={() => setTab("profile")}>用户画像总览</button>
      <button className={tab === "market" ? "active" : ""} aria-selected={tab === "market"} role="tab" onClick={() => setTab("market")}>岗位市场动态</button>
    </nav>

    {loading && <div className="xn-card xn-profile-loading" role="status"><i /><span>正在整合画像和岗位知识库…</span></div>}

    {!loading && tab === "profile" && <div className="xn-profile-overview" role="tabpanel">
      <section className="xn-card xn-user-profile-card">
        <div className="xn-profile-base-info">
          <div className="xn-profile-avatar" aria-label="用户头像">周</div>
          <div><small>个人基本信息</small><h2>{profile ? `${identityNames[profile.identity]}画像` : "用户画像待建立"}</h2>
            <dl>
              <div><dt>姓名</dt><dd>周同学</dd></div><div><dt>性别</dt><dd>待补充</dd></div><div><dt>年龄</dt><dd>待补充</dd></div>
              <div><dt>学历/阶段</dt><dd>{profile ? identityNames[profile.identity] : "待补充"}</dd></div><div><dt>专业</dt><dd>{valueOrEmpty(profile?.major)}</dd></div>
              <div><dt>意向城市</dt><dd>{valueOrEmpty(profile?.location)}</dd></div><div><dt>期望行业</dt><dd>{valueOrEmpty(profile?.interests[0])}</dd></div>
            </dl>
          </div>
        </div>
        {profileError && <div className="xn-profile-inline-error"><span>{profileError}</span><Link href="/onboarding">建立或完善画像</Link></div>}

        <div className="xn-profile-content-grid">
          <section className="xn-profile-block xn-profile-hobbies"><header><span>✦</span><h3>兴趣爱好</h3></header>{interestTags.length ? <div>{interestTags.map(tag => <span key={tag}>{tag}</span>)}</div> : <p>还没有兴趣和技能信息，可以在初始画像中补充。</p>}</section>
          <section className="xn-profile-block xn-profile-school"><header><span>⌂</span><h3>学校经历</h3></header>{profile?.school ? <article><i /><div><time>{valueOrEmpty(profile.grade ?? profile.graduationYear)}</time><h4>{profile.school}</h4><dl><div><dt>所学专业</dt><dd>{valueOrEmpty(profile.major)}</dd></div><div><dt>在校职务</dt><dd>待补充</dd></div><div><dt>获奖信息</dt><dd>待补充</dd></div><div><dt>核心课程</dt><dd>待补充</dd></div></dl></div></article> : <p>暂无学校经历。</p>}</section>
          <section className="xn-profile-block xn-profile-projects" id="profile-projects"><header><span>◇</span><h3>项目经历</h3></header>{profile?.experiences.length ? <div className="xn-profile-project-list">{profile.experiences.map(item => <article key={item.experienceId}><header><h4>{item.title}</h4><span>{item.type === "internship" ? "实习" : item.type === "competition" ? "竞赛" : item.type === "work" ? "工作" : "项目"}</span></header><p>{item.description}</p><dl><div><dt>使用技能</dt><dd>{profile.skills.slice(0, 4).map(skill => skill.name).join("、") || "待补充"}</dd></div><div><dt>个人职责</dt><dd>{item.description}</dd></div></dl></article>)}</div> : <p>暂无项目经历，完成初始画像或上传简历后会展示在这里。</p>}</section>
        </div>
      </section>

      <section className="xn-job-recommendations">
        <header><div><small>知识库检索</small><h2>推荐岗位</h2></div><span>{memoryHash ? `结合已确认记忆 · ${memoryHash.slice(0, 8)}` : "基于已确认画像"}</span></header>
        {recommendationError && <div className="xn-profile-inline-error"><span>{recommendationError}</span><Link href="/onboarding">确认画像后重试</Link></div>}
        {recommendations.length ? <div className="xn-profile-job-grid">{recommendations.slice(0, 3).map(job => <article className="xn-card xn-profile-job-card" key={job.occupation_id}><header><div><small>{job.occupation_id}</small><h3>{job.occupation_name}</h3></div><strong>{job.match_score}<span>%</span></strong></header><div className="xn-job-skill-tags">{job.core_skills.slice(0, 3).map(skill => <span key={skill}>{skill}</span>)}</div><p>{job.reason}</p>{job.confirmed_memory?.length ? <p className="xn-memory-evidence">结合你确认过的记忆：{job.confirmed_memory.map(entry => entry.content).join("；")}</p> : null}<button className="xn-text-btn" onClick={() => showJob(job)}>查看岗位详情 <span aria-hidden="true">→</span></button></article>)}</div> : !recommendationError && <div className="xn-card xn-profile-empty">暂无岗位推荐，确认画像后系统将从职业知识库中匹配适合的岗位。</div>}
      </section>
    </div>}

    {/* 记忆库独立于画像/推荐的加载状态：它自己取数，且「候选要不要确认」不该等推荐跑完才看得见 */}
    {tab === "profile" && <MemoryPanel />}

    {!loading && tab === "market" && <section className="xn-card xn-job-market-detail" role="tabpanel">
      {selectedJob ? <>
        <header><div><small>{selectedJob.occupation_id} · 匹配度 {selectedJob.match_score}%</small><h2>{selectedJob.occupation_name}市场动态详情</h2></div><Link className="xn-btn xn-btn-outline" href={selectedJob.career_path}>查看个性化路径</Link></header>
        <div className="xn-market-detail-grid">
          <section className="xn-market-panel xn-market-demand"><header><h3>人才需求趋势</h3><span>趋势示意</span></header><div className="xn-market-chart" aria-label="人才需求趋势占位图">{chartHeights.map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}<b /></div><p>当前尚未接入逐年招聘量数据，图表为布局占位，不能作为市场结论。</p></section>
          <section className="xn-market-panel xn-market-salary"><header><h3>薪资区间分布</h3><span>参考</span></header><strong>{selectedJob.salary_range}</strong><p>{selectedJob.salary_range === "暂未提供" ? "等待接入包含地区、经验和更新时间的可信薪资数据。" : "当前为需求方提供的参考区间，实际薪资受城市和经验影响。"}</p></section>
          <section className="xn-market-panel xn-market-outlook"><header><h3>行业前景简述</h3><span>知识库</span></header><p>{selectedJob.future_signal || "暂无行业趋势信息。"}</p></section>
          <section className="xn-market-panel xn-market-skills"><header><h3>近年岗位能力要求变化</h3><span>能力结构</span></header><div><article><b>持续核心能力</b><div className="xn-job-skill-tags">{selectedJob.core_skills.map(skill => <span key={skill}>{skill}</span>)}</div></article><article><b>建议优先补齐</b><div className="xn-job-skill-tags warning">{selectedJob.skill_gaps.length ? selectedJob.skill_gaps.map(skill => <span key={skill}>{skill}</span>) : <span>暂未发现明显差距</span>}</div></article></div></section>
        </div>
      </> : <div className="xn-profile-empty"><h2>请先选择一个推荐岗位</h2><p>返回用户画像总览，点击岗位卡片中的“查看岗位详情”。</p><button className="xn-btn xn-btn-primary" onClick={() => setTab("profile")}>返回岗位推荐</button></div>}
    </section>}
  </div>;
}
