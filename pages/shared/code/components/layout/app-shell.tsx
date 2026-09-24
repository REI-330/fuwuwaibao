"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useDynamicProfile } from "../profile/profile-provider";

const navigation = [
  { href: "/growth", label: "用户画像动态展示", icon: "◉" },
  { href: "/path", label: "个性化学习路径", icon: "⌁" },
  { href: "/actions", label: "职场模拟", icon: "▣" },
  { href: "/growth-records", label: "成长记录档案", icon: "▥" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [noticeOpen, setNoticeOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const { records } = useDynamicProfile();

  return (
    <div className="xn-app-shell">
      <aside className={`xn-sidebar ${menuOpen ? "is-open" : ""}`}>
        <Link href="/growth" className="xn-brand" onClick={() => setMenuOpen(false)}>
          <span className="xn-brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <span className="xn-brand-copy"><strong>向新</strong><small>AI职业成长伙伴</small></span>
        </Link>
        <nav className="xn-nav" aria-label="主要导航">
          {navigation.map((item) => (
            <Link className={pathname === item.href ? "active" : ""} href={item.href} key={item.href} onClick={() => setMenuOpen(false)}>
              <span aria-hidden="true">{item.icon}</span><b>{item.label}</b>
            </Link>
          ))}
        </nav>
      </aside>
      {menuOpen && <button className="xn-nav-mask" aria-label="关闭导航" onClick={() => setMenuOpen(false)} />}
      <div className="xn-main">
        <header className="xn-topbar">
          <button className="xn-menu" aria-label="打开导航" onClick={() => setMenuOpen(true)}>☰</button>
          <div className="xn-top-actions">
            <button className="xn-header-help" aria-expanded={helpOpen} onClick={() => { setHelpOpen(value => !value); setNoticeOpen(false); }}><span aria-hidden="true">?</span>帮助与反馈</button>
            <button className="xn-bell" aria-label="通知" onClick={() => setNoticeOpen((value) => !value)}>♧<i /></button>
            <Link href="/growth" className="xn-user" aria-label="个人资料">周</Link>
          </div>
          {noticeOpen && <div className="xn-notice-pop"><b>本次会话已确认 {records.length} 条画像信息</b><p>{records[0]?.content ?? "通过聊天或体验任务生成候选信息。"}</p><Link href="/growth" onClick={() => setNoticeOpen(false)}>查看成长变化</Link></div>}
          {helpOpen && <div className="xn-notice-pop"><b>如何更新画像</b><p>聊天或完成体验任务 → 修改候选说明 → 确认写入 → 查看我的成长。本次会话数据刷新后重置。</p><button className="xn-text-btn" onClick={() => setHelpOpen(false)}>知道了</button></div>}
        </header>
        <main className="xn-page">{children}</main>
      </div>
    </div>
  );
}
