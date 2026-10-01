"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { XiangxinMascot } from "../../../components/brand/xiangxin-mascot";

/**
 * 职场模拟入口。三条真链路：
 * - **模拟面试** `/mock-interview`
 * - **跨岗位沟通训练** `/scenarios/cross-role`
 * - **任务实践** `/actions/tasks`（从路径引擎派生的任务 → 提交行动 → 反馈 + 待确认候选）
 *
 * 还支持 `?task=<taskId>` 深链（`/path` 与文档里都按这个写）：带着它进来直接跳到任务详情。
 * 用 `window.location.search` 而不是 `useSearchParams()`：本项目其它页面也是这么做的，
 * 免得为一个可选参数引入 Suspense 边界。
 */
export default function WorkplaceSimulationPage() {
  const router = useRouter();

  useEffect(() => {
    const taskId = new URLSearchParams(window.location.search).get("task");
    if (taskId) router.replace(`/actions/tasks/${encodeURIComponent(taskId)}`);
  }, [router]);

  return <div className="xn-scenarios-page">
    <section className="xn-scenarios-hero">
      <div><span>职场情境练习</span><h1>模拟场景</h1><p>在安全的练习环境中准备面试、练习跨岗位沟通，也把成长路径上的实践任务真正做掉——结果只作反馈，能力要你自己确认才算数。</p></div>
      <XiangxinMascot size={138} state="coaching" />
    </section>

    <section className="xn-scenario-grid xn-scenario-grid-3" aria-label="模拟场景类型">
      <article className="xn-card xn-scenario-card interview"><div className="xn-scenario-icon">◎</div><small>专业表达</small><h2>模拟面试</h2><p>选择目标岗位与难度，完成文字面试，获得逐题评分、参考思路和改进建议。</p><ul><li>岗位针对性出题</li><li>逐题保存和恢复</li><li>完整面试报告</li></ul><Link className="xn-btn xn-btn-primary" href="/mock-interview">开始模拟面试 →</Link></article>
      <article className="xn-card xn-scenario-card communication"><div className="xn-scenario-icon">⇄</div><small>协作判断</small><h2>跨岗位沟通训练</h2><p>从 32 个职业、320 个真实协作情境中，练习需求对齐、风险同步和冲突处理。</p><ul><li>练习与测评模式</li><li>选项顺序随机</li><li>四项协作观察</li></ul><Link className="xn-btn xn-btn-primary" href="/scenarios/cross-role">开始沟通训练 →</Link></article>
      <article className="xn-card xn-scenario-card practice"><div className="xn-scenario-icon">◆</div><small>动手实践</small><h2>任务实践</h2><p>做掉成长路径上的实训任务，提交你的做法与成果，拿一份逐条反馈。</p><ul><li>任务来自图谱</li><li>提交即落成长记录</li><li>能力观察待你确认</li></ul><Link className="xn-btn xn-btn-primary" href="/actions/tasks">查看实践任务 →</Link></article>
    </section>

    <p className="xn-scenario-disclaimer">所有结果仅用于练习反馈，不作为正式招聘或人才测评结论；能力结论一律要你在记忆/画像面板确认后才生效。</p>
  </div>;
}
