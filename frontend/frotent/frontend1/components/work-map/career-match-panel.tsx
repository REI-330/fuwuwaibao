"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CareerMatchApiError, generateCareerMatches, getCareerMatches, selectCareerTarget } from "../../lib/client/career-match-api";
import type { CareerMatchItem, CareerMatchRun } from "../../types/contracts/career-match";
import { XiangxinMascot } from "../brand/xiangxin-mascot";

const confidence = { low: "低", medium: "中", high: "高" } as const;
const scoreNames: Array<[keyof CareerMatchItem["scores"], string]> = [
  ["interest", "兴趣与目标"], ["skills", "核心技能"], ["experience", "项目经历"], ["entryFeasibility", "入门可行性"],
];

function Empty({ code, message, retry }: { code: string; message: string; retry: () => void }) {
  // 501：契约里声明了、但最小后端未实现。**如实说「尚未上线」**，不要伪装成
  // 「你还没有画像」把用户领去重建画像 —— 那会让人白填一遍表单。
  if (code === "NOT_IMPLEMENTED") {
    return <section className="xn-card xn-match-empty"><XiangxinMascot size={108} state="guiding" /><div><small>契约已声明 · 服务端未实现</small><h2>职业匹配尚未上线</h2><p>后端对 `/api/career-matches/*` 明确返回 501，本页不显示任何编造的匹配结果。当前可以先在工作地图里浏览职业图谱，或直接前往成长路径按目标职业编排。</p><div className="xn-match-empty-actions"><Link className="xn-btn xn-btn-outline" href="/catalog">浏览职业与技能目录</Link><Link className="xn-btn xn-btn-primary" href="/path">去编排成长路径</Link></div></div></section>;
  }
  const needsProfile = ["UNAUTHORIZED", "PROFILE_NOT_FOUND", "INSUFFICIENT_PROFILE", "PROFILE_NOT_CONFIRMED"].includes(code);
  return <section className="xn-card xn-match-empty"><XiangxinMascot size={108} state="guiding" /><div><small>职业匹配需要已确认的信息</small><h2>{needsProfile ? "先建立并确认用户画像" : "暂时没有匹配结果"}</h2><p>{message}</p>{needsProfile ? <Link className="xn-btn xn-btn-primary" href="/onboarding">前往建立画像</Link> : <button className="xn-btn xn-btn-primary" onClick={retry}>重新生成</button>}</div></section>;
}

export function CareerMatchPanel() {
  const router = useRouter();
  const [run, setRun] = useState<CareerMatchRun | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [choosing, setChoosing] = useState("");
  const [error, setError] = useState({ code: "", message: "" });

  const regenerate = useCallback(async () => {
    setGenerating(true); setError({ code: "", message: "" });
    try {
      const result = await generateCareerMatches();
      setRun(result); setSelectedId(result.items[0]?.occupationId ?? "");
    } catch (caught) {
      const failure = caught instanceof CareerMatchApiError ? caught : new CareerMatchApiError(500, "MATCH_FAILED", "职业匹配生成失败");
      setError({ code: failure.code, message: failure.message });
    } finally { setGenerating(false); setLoading(false); }
  }, []);

  useEffect(() => {
    let active = true;
    getCareerMatches().then(result => {
      if (!active) return;
      setRun(result); setSelectedId(result.items[0]?.occupationId ?? ""); setLoading(false);
    }).catch(caught => {
      if (!active) return;
      if (caught instanceof CareerMatchApiError && ["CAREER_MATCH_NOT_FOUND", "CAREER_MATCH_STALE"].includes(caught.code)) { void regenerate(); return; }
      const failure = caught instanceof CareerMatchApiError ? caught : new CareerMatchApiError(500, "MATCH_FAILED", "职业匹配读取失败");
      setError({ code: failure.code, message: failure.message }); setLoading(false);
    });
    return () => { active = false; };
  }, [regenerate]);

  const selected = useMemo(() => run?.items.find(item => item.occupationId === selectedId) ?? run?.items[0] ?? null, [run, selectedId]);

  async function choose(item: CareerMatchItem) {
    setChoosing(item.occupationId); setError({ code: "", message: "" });
    try {
      await selectCareerTarget(item.occupationId);
      router.push(`/path?occupation=${encodeURIComponent(item.occupationId)}`);
    } catch (caught) {
      const failure = caught instanceof CareerMatchApiError ? caught : new CareerMatchApiError(500, "TARGET_FAILED", "目标职业保存失败");
      setError({ code: failure.code, message: failure.message }); setChoosing("");
    }
  }

  if (loading || generating && !run) return <section className="xn-card xn-match-loading"><XiangxinMascot size={90} state="guiding" /><div><h2>正在匹配职业…</h2><p>综合画像中的兴趣、技能、经历和入门可行性。</p></div></section>;
  if (!run) return <Empty code={error.code} message={error.message || "当前没有可读取的职业匹配结果。"} retry={() => void regenerate()} />;

  return <div className="xn-match-page">
    <section className="xn-card xn-match-head"><div><small>基于已确认画像 V{run.profileVersion}</small><h2>优先比较方向，再决定成长路径</h2><p>匹配分表示探索优先级，不是对个人能力的最终评价。</p></div><dl><div><dt>结果置信度</dt><dd>{confidence[run.confidence]} · {run.confidenceScore}</dd></div><div><dt>候选职业</dt><dd>{run.items.length}</dd></div></dl><button className="xn-btn xn-btn-outline" disabled={generating} onClick={() => void regenerate()}>{generating ? "正在计算…" : "重新生成"}</button></section>
    {error.message && <p className="xn-catalog-error" role="alert">{error.message}</p>}
    <section className="xn-match-cards">{run.items.map(item => <article className={`xn-card xn-match-card ${selected?.occupationId === item.occupationId ? "selected" : ""}`} key={item.occupationId}><header><b>TOP {item.rank}</b><span>{item.matchScore} 分</span></header><h2>{item.occupationName}</h2><p>{item.description}</p><div className="xn-match-bar"><i style={{ width: `${item.matchScore}%` }} /></div>{item.reasons.slice(0, 2).map(reason => <small key={reason.label}>✓ <b>{reason.label}</b>：{reason.detail}</small>)}<footer><button className="xn-btn xn-btn-outline" onClick={() => setSelectedId(item.occupationId)}>查看分析</button><button className="xn-btn xn-btn-primary" disabled={Boolean(choosing)} onClick={() => void choose(item)}>{choosing === item.occupationId ? "正在保存…" : "选择为目标"}</button></footer></article>)}</section>
    {selected && <section className="xn-card xn-match-detail"><header><div><small>TOP {selected.rank} · 详细分析</small><h2>{selected.occupationName}</h2><p>{selected.occupationNameEn} · {selected.occupationId}</p></div><Link className="xn-btn xn-btn-outline" href={`/work-map?view=graph&occupation=${selected.occupationId}`}>查看职业图谱</Link></header><div className="xn-match-scores">{scoreNames.map(([key, label]) => <div key={key}><span>{label}</span><i><b style={{ width: `${selected.scores[key]}%` }} /></i><strong>{selected.scores[key]}</strong></div>)}</div><div className="xn-match-columns"><section><h3>推荐依据</h3>{selected.reasons.map(reason => <article key={`${reason.label}-${reason.detail}`}><small>{reason.type === "confirmed_profile" ? "画像确认" : reason.type === "transferable_experience" ? "经历关联" : "目录推断"}</small><b>{reason.label}</b><p>{reason.detail}</p><em>来源：{reason.source}</em></article>)}</section><section><h3>已有基础</h3><div className="xn-match-tags">{selected.skillAdvantages.length ? selected.skillAdvantages.map(item => <span key={item}>{item}</span>) : <p>尚无直接技能证据</p>}</div><h3>主要差距</h3>{selected.skillGaps.map(gap => <p className="xn-match-gap" key={gap.skillId}><b>{gap.name}</b><span>目标 {gap.targetLevel} 级 · 重要度 {gap.importance}/5</span></p>)}</section><section><h3>待验证问题</h3>{selected.needsValidation.map(item => <p className="xn-match-question" key={item}>? {item}</p>)}<h3>典型任务</h3><ul>{selected.tasks.map(item => <li key={item}>{item}</li>)}</ul><h3>常用工具</h3><div className="xn-match-tags">{selected.tools.map(item => <span key={item}>{item}</span>)}</div></section></div><footer><button className="xn-btn xn-btn-primary" disabled={Boolean(choosing)} onClick={() => void choose(selected)}>选择该职业并生成路径 →</button></footer></section>}
  </div>;
}
