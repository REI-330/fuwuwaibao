import Link from "next/link";
import { XiangxinMascot } from "../../../components/brand/xiangxin-mascot";

/**
 * 职场模拟入口（原「内容筹备中」占位页）。
 *
 * 现在挂的是两个真链路：**模拟面试**（`/mock-interview`）与**跨岗位沟通训练**
 * （`/scenarios/cross-role`）。页面结构移植自队友 `career-ai-system` 的 `app/(product)/scenarios/page.tsx`，
 * 只是把入口路径从 `/scenarios` 换成本项目侧边栏已有的 `/actions`（导航不改名字）。
 */
export default function WorkplaceSimulationPage() {
  return <div className="xn-scenarios-page">
    <section className="xn-scenarios-hero">
      <div><span>职场情境练习</span><h1>模拟场景</h1><p>在安全的练习环境中准备面试，也练习真实工作里的跨岗位沟通与协作判断。</p></div>
      <XiangxinMascot size={138} state="coaching" />
    </section>

    <section className="xn-scenario-grid" aria-label="模拟场景类型">
      <article className="xn-card xn-scenario-card interview"><div className="xn-scenario-icon">◎</div><small>专业表达</small><h2>模拟面试</h2><p>选择目标岗位与难度，完成文字面试，获得逐题评分、参考思路和改进建议。</p><ul><li>岗位针对性出题</li><li>逐题保存和恢复</li><li>完整面试报告</li></ul><Link className="xn-btn xn-btn-primary" href="/mock-interview">开始模拟面试 →</Link></article>
      <article className="xn-card xn-scenario-card communication"><div className="xn-scenario-icon">⇄</div><small>协作判断</small><h2>跨岗位沟通训练</h2><p>从 32 个职业、320 个真实协作情境中，练习需求对齐、风险同步和冲突处理。</p><ul><li>练习与测评模式</li><li>选项顺序随机</li><li>四项协作观察</li></ul><Link className="xn-btn xn-btn-primary" href="/scenarios/cross-role">开始沟通训练 →</Link></article>
    </section>

    <p className="xn-scenario-disclaimer">所有结果仅用于练习反馈，不作为正式招聘或人才测评结论。</p>
  </div>;
}
