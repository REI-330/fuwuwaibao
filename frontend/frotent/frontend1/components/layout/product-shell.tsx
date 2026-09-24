"use client";
import { AppShell } from "./app-shell";
import { ProfileProvider } from "../profile/profile-provider";
import { ChatProvider } from "../chat/chat-provider";

export function ProductShell({ children }: { children: React.ReactNode }) {
  return <ProfileProvider><ChatProvider><AppShell>{children}</AppShell></ChatProvider></ProfileProvider>;
}
