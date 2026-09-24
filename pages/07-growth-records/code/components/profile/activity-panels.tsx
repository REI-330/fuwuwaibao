"use client";
import Link from "next/link";
import { useDynamicProfile } from "./profile-provider";

export function ActionEvidencePanel() {
  const { taskRuns, evidence } = useDynamicProfile();
  return <section><h3>已完成的体验任务</h3><p className="xn-session-note">这里记录你做了什么。任务提交不等于能力已掌握，已确认的能力结论在“能力基础”中查看。</p>
    {!taskRuns.length && <p>暂无任务记录，<Link href="/actions">开始一次行动</Link>。</p>}
    {taskRuns.map(run => <article className="xn-profile-record" key={run.id} id={`run-${run.id}`}><h3>{run.title}</h3>
      <dl className="xn-record-meta"><div><dt>用户采取的行动</dt><dd>{run.action}</dd></div><div><dt>提交结果</dt><dd>{run.submission}</dd></div><div><dt>体现出的能力（待核验）</dt><dd>{run.observedAbilities.join("、")}</dd></div><div><dt>待继续验证的内容</dt><dd>{run.pendingValidation}</dd></div><div><dt>完成时间</dt><dd>{run.completedAt.replace("T", " ").slice(0,16)} UTC</dd></div><div><dt>画像关联</dt><dd>{evidence.some(item => item.taskRunId === run.id) ? "已确认引用为画像证据" : "仅任务记录，未写入画像"}</dd></div></dl>
      <details><summary>任务标识与来源</summary><p>任务：{run.taskId}；本次提交：{run.id}；来源：用户提交，反馈为前端模拟。</p></details>
    </article>)}
  </section>;
}

export function GrowthChangesPanel() {
  const { events, records, evidence, taskRuns } = useDynamicProfile();
  return <>{["新增画像证据", "能力证据升级", "职业方向变化", "成长路径调整"].map(kind => <section className="xn-profile-field" key={kind}><h3>{kind}</h3>
    {events.filter(event => event.kind === kind).map(event => {
      const record = records.find(item => item.id === event.profileId);
      const proof = evidence.find(item => item.id === event.evidenceId);
      const run = taskRuns.find(item => item.id === proof?.taskRunId);
      return <article className="xn-profile-record" id={event.id} key={event.id}><b>{event.title}</b>{event.before && <p>此前：{event.before}</p>}<p>现在：{event.after}</p><p>{event.explanation}</p><small>{event.occurredAt.replace("T", " ").slice(0,16)} UTC</small>
        {record && <dl className="xn-record-meta"><div><dt>证据等级</dt><dd>{proof?.level}</dd></div><div><dt>数据来源</dt><dd>{proof?.source}</dd></div><div><dt>关联任务</dt><dd>{run?.title ?? "无"}</dd></div><div><dt>用户确认</dt><dd>已确认</dd></div><div><dt>验证状态</dt><dd>{record.status}</dd></div></dl>}
        {event.taskId && <Link href="/actions">查看已安排任务</Link>}
      </article>;
    })}
    {!events.some(event => event.kind === kind) && <p className="xn-session-note">{kind === "能力证据升级" ? "暂无升级事件。确认或重复练习不会自动升级证据等级。" : "尚未发生这类变化。"}</p>}
  </section>)}<section className="xn-profile-field"><h3>最近任务时间线</h3>{taskRuns.length ? taskRuns.map(run => <p key={run.id}><time>{run.completedAt.replace("T", " ").slice(0,16)} UTC</time> · 提交了「{run.title}」</p>) : <p className="xn-session-note">暂无任务提交。</p>}</section></>;
}
