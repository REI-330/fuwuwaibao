"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { XiangxinMascot } from "../../../../components/brand/xiangxin-mascot";
import {
  createCrossRoleSession,
  getCrossRoleRoles,
  listCrossRoleSessions,
} from "../../../../lib/client/cross-role-api";
import type { CrossRole, CrossRoleMode, CrossRoleSessionSummary } from "../../../../types/contracts/cross-role";

const modeOptions: Array<{ value: CrossRoleMode; title: string; note: string }> = [
  { value: "practice", title: "练习模式", note: "每题作答后立即查看推荐策略与避坑提示" },
  { value: "assessment", title: "测评模式", note: "完成全部题目后统一查看结果，过程不提示答案" },
];

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export default function CrossRoleLandingPage() {
  const router = useRouter();
  const [roles, setRoles] = useState<CrossRole[]>([]);
  const [sessions, setSessions] = useState<CrossRoleSessionSummary[]>([]);
  const [roleId, setRoleId] = useState("AI001");
  const [mode, setMode] = useState<CrossRoleMode>("practice");
  const [keyword, setKeyword] = useState("");
  const [category, setCategory] = useState("全部");
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([getCrossRoleRoles(), listCrossRoleSessions()]).then(([roleItems, sessionItems]) => {
      if (!active) return;
      setRoles(roleItems);
      setSessions(sessionItems);
      setRoleId(current => roleItems.some(item => item.roleId === current) ? current : roleItems[0]?.roleId ?? "");
    }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "训练配置加载失败");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const categories = useMemo(() => ["全部", ...Array.from(new Set(roles.map(item => item.category)))], [roles]);
  const visibleRoles = useMemo(() => roles.filter(item => {
    const matchesCategory = category === "全部" || item.category === category;
    const query = keyword.trim().toLowerCase();
    return matchesCategory && (!query || `${item.name}${item.description}`.toLowerCase().includes(query));
  }), [category, keyword, roles]);
  const selectedRole = roles.find(item => item.roleId === roleId);

  async function start() {
    if (!roleId || starting) return;
    setStarting(true);
    setError("");
    try {
      const session = await createCrossRoleSession(roleId, mode);
      router.push(`/scenarios/cross-role/${encodeURIComponent(session.sessionId)}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "训练创建失败");
      setStarting(false);
    }
  }

  return <div className="xn-cross-role-landing">
    <header className="xn-scenario-page-title"><div><Link href="/actions">← 返回模拟场景</Link><span>跨岗位沟通</span><h1>选择你要扮演的岗位</h1><p>你将从这个岗位的视角处理需求变更、信息同步、分歧和交付风险。</p></div><XiangxinMascot size={108} state="coaching" /></header>

    <div className="xn-cross-role-layout">
      <section className="xn-card xn-role-picker">
        <header><div><small>32 个职业 · 每个岗位 10 个场景</small><h2>岗位题库</h2></div><input aria-label="搜索岗位" value={keyword} onChange={event => setKeyword(event.target.value)} placeholder="搜索岗位…" /></header>
        <div className="xn-role-categories">{categories.map(item => <button type="button" className={category === item ? "active" : ""} onClick={() => setCategory(item)} key={item}>{item}</button>)}</div>
        <div className="xn-role-options">{visibleRoles.map(role => <button type="button" className={roleId === role.roleId ? "active" : ""} onClick={() => setRoleId(role.roleId)} key={role.roleId}><span>{role.roleId}</span><b>{role.name}</b><small>{role.category}</small></button>)}</div>
        {!loading && !visibleRoles.length && <p className="xn-cross-role-empty">没有匹配的岗位，请换个关键词。</p>}
      </section>

      <aside className="xn-cross-role-side">
        <section className="xn-card xn-selected-role"><small>本次角色</small><h2>{selectedRole?.name ?? "正在读取岗位…"}</h2><p>{selectedRole?.description}</p><div><span>10 个场景</span><span>预计 8—12 分钟</span></div></section>
        <section className="xn-card xn-mode-picker"><small>训练方式</small><h2>你想怎样练习？</h2>{modeOptions.map(item => <button type="button" className={mode === item.value ? "active" : ""} onClick={() => setMode(item.value)} key={item.value}><b>{item.title}</b><span>{item.note}</span></button>)}
          {error && <p className="xn-interview-error" role="alert">{error}</p>}
          <button className="xn-btn xn-btn-primary xn-cross-role-start" disabled={loading || starting || !roleId} onClick={() => void start()}>{starting ? "正在创建训练…" : "开始跨岗位沟通训练 →"}</button>
          <p>训练结果用于发现沟通习惯，不作为正式人才测评结论。</p>
        </section>
      </aside>
    </div>

    {sessions.length > 0 && <section className="xn-card xn-cross-role-recent"><header><div><small>练习记录</small><h2>最近的沟通训练</h2></div><Link href="/scenarios/cross-role/history">查看全部</Link></header><div>{sessions.slice(0, 4).map(item => <Link href={item.status === "COMPLETED" ? `/scenarios/cross-role/${item.sessionId}/report` : `/scenarios/cross-role/${item.sessionId}`} key={item.sessionId}><b>{item.roleName}</b><span>{item.mode === "practice" ? "练习模式" : "测评模式"} · {formatTime(item.createdAt)}</span><strong>{item.overallScore === null ? "继续" : `${item.overallScore} 分`}</strong></Link>)}</div></section>}
  </div>;
}
