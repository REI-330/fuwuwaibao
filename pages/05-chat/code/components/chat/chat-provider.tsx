"use client";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import { XiangxinMascot } from "../brand/xiangxin-mascot";
import { ChatConversation } from "./chat-conversation";
import { ChatContext } from "./chat-context";

const subscribe = (listener: () => void) => { const media = window.matchMedia("(max-width: 1399px)"); media.addEventListener("change", listener); return () => media.removeEventListener("change", listener); };

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const params = useSearchParams();
  const [isOpen, setOpen] = useState(params.get("chat") === "open");
  const [topic, setTopic] = useState("");
  const compact = useSyncExternalStore(subscribe, () => window.matchMedia("(max-width: 1399px)").matches, () => false);
  const drawer = useRef<HTMLElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!isOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const panel = drawer.current;
    const button = launcher.current;
    panel?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("keydown", escape); if (previous?.isConnected && !panel?.contains(previous)) previous.focus(); else button?.focus(); };
  }, [isOpen]);
  return <ChatContext.Provider value={{ isOpen, topic, openChat: value => { if (value) setTopic(value); setOpen(true); }, closeChat: () => setOpen(false) }}>
    <div className={isOpen ? "xn-chat-shell is-chat-open" : "xn-chat-shell"}>{children}</div>
    <button ref={launcher} type="button" className="xn-chat-launcher" aria-label="点击新向，打开和我聊聊" title="打开 AI 助手" aria-expanded={isOpen} aria-controls="global-chat" hidden={isOpen} onClick={() => setOpen(true)}><XiangxinMascot size={70} animated={false} /><span>和我聊聊</span></button>
    {isOpen && compact && <button className="xn-chat-backdrop" aria-label="收起聊天侧栏" onClick={() => setOpen(false)} />}
    <aside id="global-chat" ref={drawer} hidden={!isOpen} tabIndex={-1} role="dialog" aria-modal={compact} aria-label="和我聊聊" className="xn-global-chat" onKeyDown={event => {
      if (event.key === "Escape") { event.stopPropagation(); setOpen(false); }
      if (compact && event.key === "Tab") {
        const items = drawer.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],textarea,select:not(:disabled)');
        if (!items?.length) return;
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === drawer.current)) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }}>
      <header className="xn-global-chat-heading"><div><b>和我聊聊</b><small>新向 · 随时陪你理清下一步</small></div><button className="xn-text-btn" aria-label="收起聊天" onClick={() => setOpen(false)}>收起 ×</button></header>
      <ChatConversation />
    </aside>
  </ChatContext.Provider>;
}
