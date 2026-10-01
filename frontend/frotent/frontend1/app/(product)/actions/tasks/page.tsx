"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { XiangxinMascot } from "../../../../components/brand/xiangxin-mascot";
import { listTasks, reorderTasks, updateTask } from "../../../../lib/client/task-api";
import type { PracticeTask, TaskListResponse, TaskStatus } from "../../../../types/contracts/task";

const statusNames: Record<TaskStatus, string> = {
  available: "现在可做",
  planned: "计划中",
  completed: "已提交",
};

/**
 * 任务实践清单。任务全部来自路径引擎（图谱的 task→skill 边），**不是**演示数据；
 * 状态由「路径阶段顺序 + 你提交过的运行记录」共同决定（见 `backend/tasks.py` 顶部口径）。
 *
 * 支持 `?occupation=` / `?stage=`：`/path` 的任务卡片就是按这两个参数跳进来的。
 * 清单上的「隐藏 / 上下移」改的是**你自己的视图覆盖层**（`PATCH /api/tasks/<id>`、
 * `POST /api/tasks/reorder`），任务内容与完成状态都不受影响。
 */
export default function TasksPage() {
  const [data, setData] = useState<TaskListResponse | null>(null);
  const [status, setStatus] = useState<TaskStatus | "all">("all");
  const [occupation, setOccupation] = useState("");
  const [stageFilter, setStageFilter] = useState("");
  const [includeHidden, setIncludeHidden] = useState(false);
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // 始终把隐藏的也取回来（服务端 `counts` 只数看得见的），由页面上的开关决定显示不显示
  const fetchList = useCallback(async (scope: string) => {
    const result = await listTasks({ occupation: scope || undefined, includeHidden: true });
    setData(result);
    setError("");
    return result;
  }, []);

  // 只在挂载时读一次 URL 参数（`/path` 的任务卡片按 occupation+stage 跳进来）。
  // 所有 setState 都发生在异步回调里 —— 不在 effect 体里同步 setState，避免级联渲染。
  useEffect(() => {
    let active = true;
    const params = new URLSearchParams(window.location.search);
    const requestedOccupation = params.get("occupation") ?? "";
    const requestedStage = params.get("stage") ?? "";
    // 放到任务队列里再发（fetchList 会同步 setState，直接在 effect 里调用会触发级联渲染告警）
    const handle = setTimeout(() => {
      fetchList(requestedOccupation)
        .then(() => {
          if (!active) return;
          setOccupation(requestedOccupation);
          setStageFilter(requestedStage);
        })
        .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : "任务加载失败"); })
        .finally(() => { if (active) setLoading(false); });
    }, 0);
    return () => { active = false; clearTimeout(handle); };
  }, [fetchList]);

  async function run(taskId: string, action: () => Promise<unknown>) {
    setBusy(taskId);
    setError("");
    try {
      await action();
      await fetchList(occupation);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
    } finally {
      setBusy("");
    }
  }

  /** 同一阶段内上/下移：只把这一阶段的新顺序发上去（排序按阶段分组，不影响别的阶段）。 */
  function move(task: PracticeTask, siblings: PracticeTask[], delta: number) {
    const index = siblings.findIndex(item => item.taskId === task.taskId);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= siblings.length) return;
    const next = [...siblings];
    [next[index], next[target]] = [next[target], next[index]];
    void run(task.taskId, () => reorderTasks(next.map(item => item.taskId), occupation || undefined));
  }

  const items = useMemo(() => (data?.items ?? []).filter(task => {
    if (task.hidden && !includeHidden) return false;
    const statusMatched = status === "all" || task.status === status;
    const stageMatched = !stageFilter || task.sourcePath.stage === stageFilter;
    return statusMatched && stageMatched;
  }), [data, includeHidden, stageFilter, status]);

  const groups = useMemo(() => {
    const map = new Map<string, PracticeTask[]>();
    for (const task of items) {
      const key = `${task.sourcePath.stageOrder}|${task.sourcePath.stageName}|${task.sourcePath.period}`;
      map.set(key, [...(map.get(key) ?? []), task]);
    }
    return [...map.entries()].sort((left, right) => Number(left[0].split("|")[0]) - Number(right[0].split("|")[0]));
  }, [items]);

  return <div className="xn-tasks-page">
    <header className="xn-interview-history-title">
      <div><span>职场模拟 · 任务实践</span><h1>实践任务</h1><p>任务来自你的成长路径（图谱的 task→skill 边）。做完之后提交做法与成果，拿一份逐条反馈；其中提到的能力只是**候选**，要你在记忆面板确认才算数。</p></div>
      <div><Link className="xn-btn xn-btn-outline" href="/path">去看成长路径</Link><Link className="xn-btn xn-btn-outline" href="/actions">模拟场景</Link></div>
    </header>

    {data && <section className="xn-interview-history-stats">
      <article className="xn-card"><span>目标职业</span><b>{data.occupation.targetJob}</b><small>{data.occupation.occupationId} · 职业来源：{{ query: "按链接指定", profile: "画像候选", catalog: "目录第一个" }[data.occupationSource]}</small></article>
      <article className="xn-card"><span>现在可做</span><b>{data.counts.available}</b><small>第一个还没做完的阶段</small></article>
      <article className="xn-card"><span>计划中</span><b>{data.counts.planned}</b><small>更后面的阶段</small></article>
      <article className="xn-card"><span>已提交</span><b>{data.counts.completed}</b><small>提交过至少一次</small></article>
    </section>}

    <section className="xn-card xn-interview-history-panel">
      <header>
        <div className="xn-interview-history-filters">
          <button type="button" className={status === "all" ? "active" : ""} onClick={() => setStatus("all")}>全部</button>
          <button type="button" className={status === "available" ? "active" : ""} onClick={() => setStatus("available")}>现在可做</button>
          <button type="button" className={status === "planned" ? "active" : ""} onClick={() => setStatus("planned")}>计划中</button>
          <button type="button" className={status === "completed" ? "active" : ""} onClick={() => setStatus("completed")}>已提交</button>
        </div>
        {occupation && <button type="button" className="xn-text-btn" onClick={() => { window.location.href = "/actions/tasks"; }}>清除职业筛选（{occupation}）×</button>}
        {stageFilter && <button type="button" className="xn-text-btn" onClick={() => setStageFilter("")}>清除阶段筛选（{stageFilter}）×</button>}
        {Boolean(data?.hiddenCount) && <button type="button" className={`xn-text-btn ${includeHidden ? "active" : ""}`} aria-pressed={includeHidden} onClick={() => setIncludeHidden(value => !value)}>{includeHidden ? "不显示已隐藏" : `显示已隐藏（${data?.hiddenCount}）`}</button>}
      </header>

      {error && <p className="xn-interview-error" role="alert">{error}</p>}
      {loading && <div className="xn-interview-empty">正在按图谱算任务…</div>}

      {!loading && groups.map(([key, tasks]) => {
        const [, stageName, period] = key.split("|");
        return <div className="xn-task-group" key={key}>
          <h2>{stageName}<small>{period} · {tasks.length} 条任务</small></h2>
          <div className="xn-task-list">{tasks.map((task, index) => <article className={`xn-task-row ${task.status}${task.hidden ? " is-hidden" : ""}`} key={task.taskId}>
            <div className="xn-task-main">
              <span className={`status-${task.status}`}>{statusNames[task.status]}</span>
              <div><h3>{task.title}</h3><p>{task.sourcePath.stageGoal}</p>{task.note && <p className="xn-task-note">我的备注：{task.note}</p>}</div>
            </div>
            <dl className="xn-task-meta">
              <div><dt>交付成果</dt><dd>{task.deliverable || "图谱未记录"}</dd></div>
              <div><dt>要求能力</dt><dd>{task.requiredSkills.map(skill => skill.name).join("、") || "图谱未标注"}</dd></div>
              <div><dt>阶段投入</dt><dd>{task.stageEstimatedHours} 小时 · {task.stageEstimatedWeeks} 周（阶段级）</dd></div>
            </dl>
            <div className="xn-task-actions">
              {task.status === "completed" && <em>已提交过，可继续补充</em>}
              <button type="button" className="xn-text-btn" disabled={busy === task.taskId || index === 0} onClick={() => move(task, tasks, -1)}>↑ 上移</button>
              <button type="button" className="xn-text-btn" disabled={busy === task.taskId || index === tasks.length - 1} onClick={() => move(task, tasks, 1)}>↓ 下移</button>
              <button type="button" className="xn-text-btn" disabled={busy === task.taskId} onClick={() => void run(task.taskId, () => updateTask(task.taskId, { hidden: !task.hidden }))}>{task.hidden ? "恢复显示" : "隐藏"}</button>
              <Link className="xn-btn xn-btn-primary" href={`/actions/tasks/${encodeURIComponent(task.taskId)}`}>
                {task.status === "completed" ? "查看与再提交" : "去做这个任务"} →
              </Link>
            </div>
          </article>)}</div>
        </div>;
      })}

      {!loading && !groups.length && !error && <div className="xn-interview-empty"><span>◆</span><b>没有符合条件的任务</b><p>换一个筛选条件；如果路径里这条阶段本来就没有任务，这里会是空的（不会编一条给你）。</p></div>}
    </section>

    {data && <section className="xn-card xn-task-notes">
      <header><XiangxinMascot size={72} state="guiding" /><div><h2>口径说明</h2><p>这些数字是怎么来的，以及哪些字段图谱里确实没有。</p></div></header>
      <ul>{data.notes.map(note => <li key={note}>{note}</li>)}</ul>
      <p className="xn-session-note">{data.disclaimer}</p>
    </section>}
  </div>;
}
