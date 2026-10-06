"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { EntryBrand } from "../../../components/entry/entry-brand";
import { createGuest } from "../../../lib/client/profile-api";

export default function AuthPage() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function startGuestSession() {
    setSubmitting(true);
    setError("");
    try {
      await createGuest();
      router.push("/onboarding");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "体验账号创建失败");
      setSubmitting(false);
    }
  }

  return (
    <div className="xn-auth-page">
      <section className="xn-auth-intro">
        <EntryBrand />
        <div className="xn-auth-statement">
          <span className="xn-entry-kicker">职业成长工作空间</span>
          <h1>把经历沉淀为证据，<br />把方向落实为行动。</h1>
          <p>向新通过对话、画像、职业路径与行动训练，陪你持续验证适合自己的职业方向。</p>
        </div>
        <div className="xn-auth-process" aria-label="产品流程">
          <div><b>01</b><span><strong>建立画像</strong><small>由你确认每一条信息</small></span></div>
          <div><b>02</b><span><strong>探索方向</strong><small>看见依据与待验证项</small></span></div>
          <div><b>03</b><span><strong>持续行动</strong><small>用真实任务积累证据</small></span></div>
        </div>
        <p className="xn-auth-privacy">你的画像由你控制，AI提取内容在确认前仅作为候选信息。</p>
      </section>

      <section className="xn-auth-panel-wrap">
        <div className="xn-auth-panel">
          <div className="xn-auth-heading">
            <span>欢迎使用向新</span>
            <h2>进入你的成长空间</h2>
            <p>创建一个本地体验会话，开始职业探索与行动计划。</p>
          </div>

          <div className="xn-auth-form">
            <p className="xn-session-note">当前版本使用本地体验会话，不收集邮箱和密码。会话与画像保存在本机 SQLite 中。</p>
            <button type="button" className="xn-entry-primary" disabled={submitting} onClick={() => void startGuestSession()}>{submitting ? "正在进入…" : "开始体验"}</button>
          </div>

          {error && <p className="xn-catalog-error" role="alert">{error}</p>}

          <p className="xn-auth-demo-note">体验账号和画像会保存到本地 SQLite；再次打开时可继续当前浏览器会话。</p>
        </div>
        <footer>© 2026 向新 · AI职业成长伙伴</footer>
      </section>
    </div>
  );
}
