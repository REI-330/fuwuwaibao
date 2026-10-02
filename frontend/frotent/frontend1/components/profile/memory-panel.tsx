"use client";

/**
 * 记忆库管理面板（候选闸门 + 透明预览 + 删除即遗忘）。
 *
 * 界面上刻意把三件事分开显示，因为它们对应三条不可退让的边界：
 *   ① 待确认（candidate）：AI 的观察停在这里，**永远不进入对话注入与推荐**；
 *   ② 已确认（confirmed）：只有这部分会被消费，且每条都能点开看它生成了哪些触发器；
 *   ③ 注入预览：拿一个提问去问「这次会想起什么」，【常驻】/【本次想起】两段可审计。
 *
 * 接口见 `lib/client/memory-api.ts`；机制说明见 `backend/memories.py`。
 */
import { useCallback, useEffect, useState } from "react";
import {
  createMemory,
  deleteMemory,
  generateMemoryTriggers,
  previewMemoryContext,
  listMemories,
  syncProfileMemories,
  updateMemory,
} from "../../lib/client/memory-api";
import type { MemoryContextPayload, MemoryItem } from "../../types/contracts/memory";

const categoryLabels: Record<string, string> = {
  goal: "当前目标",
  preference: "偏好",
  skill: "技能",
  background: "背景",
  career_target: "目标职业",
  custom: "自定义",
};

const categoryOptions = Object.keys(categoryLabels);

function formatTime(value: string | null) {
  if (!value) return "—";
  return value.replace("T", " ").replace("Z", "");
}

export default function MemoryPanel() {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [notice, setNotice] = useState("");
  const [context, setContext] = useState<MemoryContextPayload | null>(null);
  const [probe, setProbe] = useState("我想从后端转做 AI 应用");
  const [draft, setDraft] = useState({ category: "goal", content: "" });

  const reload = useCallback(async () => {
    try {
      const listed = await listMemories();
      setItems(listed.items);
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "记忆库加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  /* 首次加载走 promise 回调而不是在 effect 体里同步调 reload()：
     同步 setState 会触发级联渲染（react-hooks/set-state-in-effect）。 */
  useEffect(() => {
    let active = true;
    listMemories()
      .then(listed => {
        if (!active) return;
        setItems(listed.items);
        setError("");
      })
      .catch((failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : "记忆库加载失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const candidates = items.filter(item => item.status === "candidate");
  const confirmed = items.filter(item => item.status === "confirmed");

  async function run(label: string, id: string, work: () => Promise<unknown>) {
    setBusyId(id);
    setNotice("");
    try {
      await work();
      await reload();
      setNotice(label);
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : `${label}失败`);
    } finally {
      setBusyId("");
    }
  }

  async function handlePreview() {
    setBusyId("preview");
    try {
      setContext(await previewMemoryContext(probe));
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "注入预览失败");
    } finally {
      setBusyId("");
    }
  }

  async function handleCreate() {
    const content = draft.content.trim();
    if (!content) {
      setError("请先填写记忆内容");
      return;
    }
    await run("已写入一条记忆", "create", async () => {
      await createMemory({ category: draft.category as MemoryItem["category"], content, status: "confirmed" });
      setDraft({ category: draft.category, content: "" });
    });
  }

  /** 重建触发器：**显式**走模型版（配好端点就用模型），失败会自动降级并在提示里说明。
      写路径（确认记忆 / 改内容）仍走规则版，避免一次确认就卡 20 秒。 */
  async function handleRegenerate(item: MemoryItem) {
    setBusyId(item.id);
    setNotice("");
    try {
      const result = await generateMemoryTriggers(item.id, "auto");
      await reload();
      const { used, model, error: failure } = result.generator;
      if (used === "llm") setNotice(`已用模型（${model ?? "未知模型"}）重建 ${result.count} 条触发器`);
      else if (used === "rule-based-fallback") setNotice(`模型不可用，已降级为规则版：${failure?.code ?? "未知错误"} — ${failure?.message ?? ""}`);
      else setNotice(`已用规则版重建 ${result.count} 条触发器（未配置模型端点）`);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "重建触发器失败");
    } finally {
      setBusyId("");
    }
  }

  return <section className="xn-memory-panel" aria-label="记忆库管理">
    <header className="xn-memory-head">
      <div>
        <small>长期陪伴底座</small>
        <h2>记忆库</h2>
        <p>AI 的观察先停在「待确认」，只有你确认过的记忆才会进入对话注入与岗位推荐。</p>
      </div>
      <button
        type="button"
        className="xn-btn xn-btn-outline"
        disabled={busyId === "sync"}
        onClick={() => void run("已从画像同步候选记忆", "sync", syncProfileMemories)}
      >
        从画像同步候选
      </button>
    </header>

    {error && <div className="xn-profile-inline-error"><span>{error}</span></div>}
    {!loading && error === "" && notice && <p className="xn-memory-notice" role="status">{notice}</p>}

    {/* 三栏骨架先渲染、数据后填：SSR 首屏就能看见「记忆库存在、候选要我自己确认」，
        不必等接口回来才出现整块面板（也让端到端断言不必依赖客户端 hydrate）。 */}
    <div className="xn-memory-grid">
      <article className="xn-card xn-memory-column">
        <header><h3>待确认</h3><span>{loading ? "读取中" : `${candidates.length} 条`}</span></header>
        <p className="xn-memory-hint">这一栏的记忆不会被 AI 用到，直到你确认。</p>
        {loading ? <p className="xn-profile-empty" role="status">正在读取记忆库…</p>
          : candidates.length ? <ul className="xn-memory-list">{candidates.map(item => <li key={item.id}>
          <div className="xn-memory-meta"><span>{categoryLabels[item.category] ?? item.category}</span><time>{formatTime(item.createdAt)}</time></div>
          <p>{item.content}</p>
          <div className="xn-memory-actions">
            <button type="button" className="xn-text-btn" disabled={busyId === item.id}
              onClick={() => void run("已确认该记忆，并生成触发器", item.id, () => updateMemory(item.id, { status: "confirmed" }))}>
              确认
            </button>
            <button type="button" className="xn-text-btn" disabled={busyId === item.id}
              onClick={() => void run("已删除该记忆", item.id, () => deleteMemory(item.id))}>
              删除
            </button>
          </div>
        </li>)}</ul> : <p className="xn-profile-empty">暂无待确认记忆。确认画像后点「从画像同步候选」。</p>}
      </article>

      <article className="xn-card xn-memory-column">
        <header><h3>已确认</h3><span>{loading ? "读取中" : `${confirmed.length} 条`}</span></header>
        <p className="xn-memory-hint">这一栏会进入对话注入与推荐增强，每条都带证据 id。</p>
        {loading ? <p className="xn-profile-empty" role="status">正在读取记忆库…</p>
          : confirmed.length ? <ul className="xn-memory-list">{confirmed.map(item => <li key={item.id}>
          <div className="xn-memory-meta"><span>{categoryLabels[item.category] ?? item.category}</span><time>重要度 {item.importance}</time></div>
          <p>{item.content}</p>
          <div className="xn-memory-triggers">
            {(item.triggers ?? []).length
              ? (item.triggers ?? []).map(trigger => <span
                  key={trigger.triggerId}
                  title={`${trigger.bridge}（来源：${trigger.generatedBy}${trigger.generatedByModel ? ` · ${trigger.generatedByModel}` : ""}）`}
                >
                  {trigger.generatedBy === "llm" ? "✦ " : ""}{trigger.concept}
                </span>)
              : <em>尚无触发器（点「重建触发器」）</em>}
          </div>
          <div className="xn-memory-actions">
            <button type="button" className="xn-text-btn" disabled={busyId === item.id}
              onClick={() => void handleRegenerate(item)}>
              {busyId === item.id ? "生成中（模型可能要 10–40 s）…" : "重建触发器"}
            </button>
            <button type="button" className="xn-text-btn" disabled={busyId === item.id}
              onClick={() => void run("已删除该记忆，推荐将重算", item.id, () => deleteMemory(item.id))}>
              删除即遗忘
            </button>
          </div>
        </li>)}</ul> : <p className="xn-profile-empty">还没有已确认的记忆。</p>}

        <div className="xn-memory-compose">
          <label>
            <span>新增一条记忆</span>
            <select value={draft.category} onChange={event => setDraft({ ...draft, category: event.target.value })}>
              {categoryOptions.map(option => <option key={option} value={option}>{categoryLabels[option]}</option>)}
            </select>
          </label>
          <textarea
            value={draft.content}
            rows={2}
            maxLength={1000}
            placeholder="例如：目标职业：边缘 AI 工程师"
            onChange={event => setDraft({ ...draft, content: event.target.value })}
          />
          <button type="button" className="xn-btn xn-btn-primary" disabled={busyId === "create"} onClick={() => void handleCreate()}>
            写入并确认
          </button>
        </div>
      </article>

      <article className="xn-card xn-memory-column xn-memory-preview">
        <header><h3>注入预览</h3><span>透明可审计</span></header>
        <p className="xn-memory-hint">换一个提问，看这次会「想起」哪些记忆。触发器只负责把记忆捞出来，最终是否注入仍按打分与上限裁剪。</p>
        <div className="xn-memory-probe">
          <input value={probe} onChange={event => setProbe(event.target.value)} aria-label="试一个提问" />
          <button type="button" className="xn-btn xn-btn-outline" disabled={busyId === "preview"} onClick={() => void handlePreview()}>预览</button>
        </div>
        {context && (context.count === 0
          ? <p className="xn-profile-empty">这次不会注入任何记忆（{context.memoryHash ? "有已确认记忆但都不相关" : "还没有已确认记忆"}）。</p>
          : <>
            <dl className="xn-memory-sections">
              <div><dt>【常驻】</dt><dd>{context.persona.length} 条</dd></div>
              <div><dt>【本次想起】</dt><dd>{context.recalled.length} 条</dd></div>
              <div><dt>记忆指纹</dt><dd>{context.memoryHash.slice(0, 12) || "—"}</dd></div>
            </dl>
            <pre className="xn-memory-summary">{context.summaryText}</pre>
            <ul className="xn-memory-list compact">{context.merged.map(entry => <li key={`${entry.section}-${entry.memoryId}`}>
              <div className="xn-memory-meta">
                <span>{entry.section === "persona" ? "常驻" : "想起"}</span>
                <time>{entry.channel ?? "persona"}{entry.recallScore !== undefined ? ` · ${entry.recallScore}` : ""}</time>
              </div>
              <p>{entry.content}</p>
            </li>)}</ul>
          </>)}
      </article>
    </div>
  </section>;
}
