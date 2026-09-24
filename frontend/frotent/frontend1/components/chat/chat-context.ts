"use client";
import { createContext, useContext } from "react";

export type ChatState = {
  isOpen: boolean;
  openChat: (topic?: string) => void;
  closeChat: () => void;
  topic: string;
};

// Keep the context outside refreshable UI modules so provider and consumers
// share the same identity when the drawer or its conversation is hot-reloaded.
export const ChatContext = createContext<ChatState | null>(null);

export function useChat() {
  const context = useContext(ChatContext);
  if (!context) throw new Error("ChatProvider is required");
  return context;
}
