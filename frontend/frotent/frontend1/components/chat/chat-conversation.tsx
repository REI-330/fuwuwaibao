"use client";

import Link from "next/link";
import { useChat } from "./chat-context";
import { FormEvent, useEffect, useState } from "react";
import { getChatSession, listChatSessions, sendChatMessage } from "../../lib/client/chat-api";
import { XiangxinMascot, type MascotState } from "../../components/brand/xiangxin-mascot";
import { CandidateProfileCard } from "../../components/profile/candidate-profile-card";
import type { ProfileCandidate } from "../../types/view-models/dynamic-profile";

type Message = { role: "user" | "ai"; text: string };

const GREETING: Message = { role: "ai", text: "你好，我是向新职业成长伙伴。你现在最想确认的是职业方向、能力差距，还是下一步该做什么？" };

export function ChatConversation() {
  const { topic, closeChat } = useChat();
  const [messages, setMessages] = useState<Message[]>([GREETING]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string>();
  const [candidates, setCandidates] = useState<ProfileCandidate[]>([]);
  const [mascotState, setMascotState] = useState<MascotState>("listening");
  /* 本轮回答到底有没有用上模型、注入了哪几条记忆 —— 后端逐轮回传，界面如实展示。
     与记忆管理面板的「注入预览」是同一条链路：预览看的是"将要"，这里看的是"实际"。 */
  const [replyNote, setReplyNote] = useState("");

  /* 会话与消息已经落库，所以打开侧栏先把最近一段读回来 —— 刷新页面不再从零开始。 */
  useEffect(() => {
    let active = true;
    const handle = setTimeout(() => {
      listChatSessions(1)
        .then(page => (page.items[0] ? getChatSession(page.items[0].sessionId) : null))
        .then(detail => {
          if (!active || !detail) return;
          setConversationId(detail.session.sessionId);
          const restored = detail.messages.map(item => ({
            role: item.role === "user" ? "user" as const : "ai" as const,
            text: item.text,
          }));
          if (restored.length) setMessages([GREETING, ...restored]);
        })
        .catch(() => { /* 读不到历史不影响继续聊 */ });
    }, 0);
    return () => { active = false; clearTimeout(handle); };
  }, []);

  function startNew() {
    setConversationId(undefined);
    setMessages([GREETING]);
    setCandidates([]);
    setReplyNote("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || loading) return;
    setInput(""); setMessages((current) => [...current, { role: "user", text }]); setLoading(true);
    const draft: ProfileCandidate = { id: crypto.randomUUID(), classificationRequired: true, module: "当前状态", field: "当前目标", content: text, level: "用户自述", source: "用户聊天原文 · 前端候选整理", task: null, updatedAt: new Date().toISOString(), status: "待验证" };
    setCandidates(current => [...current, draft]);
    setMascotState("listening");
    try {
      const reply = await sendChatMessage(text, conversationId);
      setConversationId(reply.conversationId ?? undefined);
      setMessages((current) => [...current, { role: "ai", text: reply.message }]);
      const injected = reply.injected?.count ?? 0;
      setReplyNote(reply.provider === "rule-based"
        ? `本轮未接模型：回答由图谱事实 + ${injected} 条已确认记忆（规则版）拼出${reply.llm?.error?.code ? `；模型未生效：${reply.llm.error.code}` : ""}。`
        : `本轮由 ${reply.llm?.model ?? "模型"} 生成 · 注入 ${injected} 条已确认记忆。`);
      setMascotState(current => current === "echo" ? current : /职业|建议|方向|推荐/.test(reply.message) ? "guiding" : "listening");
    } catch (error) {
      setMessages((current) => [...current, {
        role: "ai",
        text: error instanceof Error ? error.message : "智能体暂时无法回复，请稍后重试。",
      }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="xn-drawer-conversation">
      <section className="xn-chat-column">
        <div className="xn-drawer-welcome"><XiangxinMascot size={90} state={loading ? "thinking" : mascotState} intensity="soft" /><p>今天想先聊聊什么？<small>对话存在服务端：刷新页面或重启后端都不会丢。</small></p></div>{topic && <button className="xn-topic-chip" onClick={() => setInput(topic)}>围绕当前内容提问：{topic}</button>}<button type="button" className="xn-text-btn" onClick={startNew}>开一段新对话</button>
        <div className="xn-card xn-chat-card">
          <div className="xn-messages">
            {messages.map((message, index) => (
              <div className={`xn-message-row ${message.role}`} key={`${message.role}-${index}`}>
                {message.role === "ai" && <XiangxinMascot size={40} state="listening" animated={false} className="xn-message-mascot" />}
                <div className="xn-message-bubble">{message.text}</div>
                {message.role === "user" && <span className="xn-message-avatar">周</span>}
              </div>
            ))}
            {loading && <div className="xn-message-row ai"><XiangxinMascot size={40} state="thinking" animated={false} className="xn-message-mascot" /><div className="xn-message-bubble xn-thinking"><i /><i /><i /></div></div>}
          </div>

          {candidates.map(candidate => <CandidateProfileCard onNavigate={closeChat} key={candidate.id} candidate={candidate} onConfirm={() => setMascotState("echo")} onDismiss={() => setCandidates(current => current.filter(item => item.id !== candidate.id))} />)}
          {replyNote && <p className="xn-chat-note">{replyNote}</p>}
          <form className="xn-chat-input" onSubmit={submit}>
            <textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="告诉新向，你现在最想解决的问题..." aria-label="对话输入" />
            <button type="button" className="xn-attach" aria-label="附件使用说明" onClick={() => setMessages(current => [...current, { role: "ai", text: "本阶段聊天暂不解析附件。你可以把项目经历或任务结果作为文字发给我，再确认是否写入画像。" }])}>⌕</button>
            <button className="xn-send" aria-label="发送" disabled={!input.trim() || loading}>↑</button>
          </form>
        </div>
      </section>

      <Link className="xn-drawer-growth-link" href="/growth" onClick={closeChat}>查看我的成长 →</Link>

    </div>
  );
}
