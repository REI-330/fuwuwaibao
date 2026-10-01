import { apiUrl } from "./http";

/** 这一轮实际注入了什么（后端可审计前缀的产物，逐条能指回 memoryId）。 */
export type ChatInjection = {
  /** 检索用的原始提问（后端只去首尾空白，不做改写/扩写） */
  query: string;
  count: number;
  memoryIds: string[];
  memoryHash: string;
  summaryText: string;
  prefixBytes: number;
  memoryBlockBytes: number;
  promptTemplate: string;
};

export type ChatLlmNote = {
  requested: "auto" | "llm" | "rule-based";
  used: "llm" | "rule-based";
  model?: string | null;
  /** 降级时的错误码与说明；正常走模型时为 null */
  error?: { code: string; message: string } | null;
  elapsedMs?: number;
};

export type ChatReply = {
  message: string;
  conversationId: string | null;
  /** `rule-based` = 本轮没有用到模型（没配端点或调用失败），回答只由图谱事实与已确认记忆拼成 */
  provider?: "llm" | "rule-based";
  injected?: ChatInjection;
  llm?: ChatLlmNote;
};

export type ChatStoredMessage = {
  messageId: string;
  sessionId: string;
  role: "user" | "assistant";
  text: string;
  provider: string | null;
  injected: ChatInjection | null;
  createdAt: string;
};

export type ChatSessionSummary = {
  sessionId: string;
  title: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
};

export type ChatSessionPage = {
  items: ChatSessionSummary[];
  count: number;
  /** false = 这次运行没挂会话库（只有单测会这样），不是「没有历史」 */
  persisted: boolean;
  note?: string;
};

export type ChatSessionDetail = {
  session: ChatSessionSummary;
  messages: ChatStoredMessage[];
};

async function readChat<T>(response: Response): Promise<T> {
  const body = await response.json() as { data?: T; error?: { code?: string; message?: string } };
  if (!response.ok || !body.data) {
    throw new Error(body.error?.message || "会话请求失败");
  }
  return body.data;
}

/** 会话与消息落库（2026-10-01 起）：刷新或重启后端都不丢。 */
export async function listChatSessions(limit = 20): Promise<ChatSessionPage> {
  return readChat<ChatSessionPage>(await fetch(apiUrl(`/api/chat/sessions?limit=${limit}`), {
    credentials: "include",
    cache: "no-store",
  }));
}

export async function getChatSession(sessionId: string): Promise<ChatSessionDetail> {
  return readChat<ChatSessionDetail>(await fetch(apiUrl(`/api/chat/sessions/${encodeURIComponent(sessionId)}`), {
    credentials: "include",
    cache: "no-store",
  }));
}

export async function sendChatMessage(
  message: string,
  conversationId?: string,
): Promise<ChatReply> {
  const url = apiUrl("/api/chat");

  let response = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message, conversationId }),
  });
  if (response.status === 401) {
    const guestResponse = await fetch(apiUrl("/api/auth/guest"), {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ displayName: "体验用户" }),
    });
    if (!guestResponse.ok) throw new Error("无法创建体验会话");
    response = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message, conversationId }),
    });
  }

  const body = await response.json() as {
    data?: ChatReply;
    error?: { message?: string };
  };
  if (!response.ok || !body.data) {
    throw new Error(body.error?.message || "智能体暂时无法回复");
  }
  return body.data;
}
