"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { EntryBrand } from "../../../components/entry/entry-brand";
import { createGuest } from "../../../lib/client/profile-api";

type AuthMode = "login" | "register";

export default function AuthPage() {
  const router = useRouter();
  const [mode, setMode] = useState<AuthMode>("login");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const displayName = String(form.get("name") || form.get("email") || "体验用户").split("@")[0];
    try {
      await createGuest(displayName);
      router.push("/onboarding");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "账号创建失败，请检查 FastAPI 服务是否已启动");
      setSubmitting(false);
    }
  }

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
            <h2>{mode === "login" ? "登录你的成长空间" : "创建你的成长空间"}</h2>
            <p>{mode === "login" ? "继续上次的职业探索与行动计划。" : "从一次真实的自我了解开始。"}</p>
          </div>

          <div className="xn-auth-tabs" role="tablist" aria-label="登录方式">
            <button type="button" role="tab" aria-selected={mode === "login"} className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>登录</button>
            <button type="button" role="tab" aria-selected={mode === "register"} className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>注册</button>
          </div>

          <form className="xn-auth-form" onSubmit={submit}>
            {mode === "register" && (
              <label>
                <span>姓名或昵称</span>
                <input name="name" autoComplete="name" placeholder="用于成长空间内展示" required />
              </label>
            )}
            <label>
              <span>邮箱</span>
              <input type="email" name="email" autoComplete="email" placeholder="name@example.com" required />
            </label>
            <label>
              <span>密码</span>
              <input type="password" name="password" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={8} placeholder="至少8位字符" required />
            </label>
            {mode === "login" ? (
              <div className="xn-auth-row"><label className="xn-check"><input type="checkbox" />保持登录</label><button type="button" className="xn-auth-link">忘记密码？</button></div>
            ) : (
              <label className="xn-check xn-agreement"><input type="checkbox" required /><span>我已阅读并同意服务协议与隐私说明</span></label>
            )}
            <button className="xn-entry-primary" disabled={submitting}>{submitting ? "正在进入…" : mode === "login" ? "登录" : "注册并继续"}</button>
          </form>

          {error && <p className="xn-catalog-error" role="alert">{error}</p>}

          <div className="xn-auth-divider"><span>或</span></div>
          <button type="button" className="xn-entry-secondary" disabled={submitting} onClick={() => void startGuestSession()}>使用体验账号</button>
          <p className="xn-auth-demo-note">体验账号和画像会保存到本地 SQLite；正式上线前仍需补充密码认证。</p>
        </div>
        <footer>© 2026 向新 · AI职业成长伙伴</footer>
      </section>
    </div>
  );
}
