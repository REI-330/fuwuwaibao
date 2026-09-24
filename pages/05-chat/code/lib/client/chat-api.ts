import { apiUrl } from "./http";

export type ChatReply = {
  message: string;
  conversationId: string | null;
};

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
