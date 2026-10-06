"use client";

import Link from "next/link";
import { ChangeEvent, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { EntryBrand } from "../../../components/entry/entry-brand";
import { ProfileForm } from "../../../components/entry/profile-form";
import { ImageEditor } from "../../../components/entry/image-editor";
import { ProfileReviewCard } from "../../../components/entry/profile-review-card";
import type { UserProfile } from "../../../types/contracts/profile";
import { useEffect, useRef } from "react";
import { apiUrl } from "../../../lib/client/http";

type View = "choices" | "resume" | "form" | "review";
type EntryMode = "resume" | "manual";
type ProfileResponse = {
  data?: { profile?: UserProfile };
  error?: { message?: string };
  message?: string;
};

const STEP_LABELS: Record<EntryMode, { s2: string; s3: string }> = {
  resume: { s2: "简历上传", s3: "简历解析" },
  manual: { s2: "信息录入", s3: "信息解析" },
};

export default function OnboardingPage() {
  const router = useRouter();
  const params = useSearchParams();
  const [view, setView] = useState<View>("choices");
  const [file, setFile] = useState<File | null>(null);
  const [entryMode, setEntryMode] = useState<EntryMode>("resume");
  const [processing, setProcessing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [showImageEditor, setShowImageEditor] = useState(false);
  const [reviewProfile, setReviewProfile] = useState<UserProfile | null>(null);
  const [resumeError, setResumeError] = useState("");
  /** 后端抽取时的如实提醒（不支持的形态、图谱没收录的技能词…），不在前端二次解说 */
  const [resumeWarnings, setResumeWarnings] = useState<string[]>([]);
  const guestReadyRef = useRef(false);

  // Demo 模式：URL ?mode=demo → 自动 guest auth + PUT mock 数据 → 跳 review
  useEffect(() => {
    if (params.get("mode") !== "demo") return;
    let cancelled = false;
    (async () => {
      try {
        // 1. 先 guest auth（确保有 userId cookie）
        if (!(await ensureGuest("演示用户"))) {
          throw new Error("guest session unavailable");
        }
        if (cancelled) return;

        // 2. PUT 完整 mock 画像
        const demo = {
          identity: "在校生",
          school: "浙江某高校",
          major: "自动化",
          grade: "大三",
          skills: ["C语言", "STM32", "Python", "CamMV", "Keil"],
          experience: "STM32传感器采集项目：使用F103开发板完成多路温湿度采集与OLED显示；CamMV目标识别小车：基于K210的颜色识别+电机PID控制竞赛作品",
          directions: "嵌入式开发,机器视觉,边缘AI",
          location: "杭州",
          question: "希望确认更适合底层嵌入式开发还是边缘AI应用方向？",
          source: "resume",
        };
        const r = await fetch(apiUrl("/api/profile"), {
          method: "PUT",
          credentials: "include",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(demo),
        });
        if (cancelled || !r.ok) return;
        const data = await r.json().catch(() => ({})) as ProfileResponse;
        setReviewProfile(data?.data?.profile ?? null);
        setEntryMode("resume");
        setView("review");
      } catch {}
    })();
    return () => { cancelled = true; };
  }, [params]);

  const isImageFile = (f: File | null) =>
    !!f && ["image/jpeg", "image/jpg", "image/png", "image/webp"].includes(f.type);

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const f = event.target.files?.[0] ?? null;
    setFile(f);
    setResumeError("");
    setResumeWarnings([]);
    // 选中图片 → 弹出 ImageEditor
    if (isImageFile(f)) {
      setShowImageEditor(true);
    }
  }

  function handleImageEditConfirm(processedBlob: Blob) {
    // 用处理后的文件替换原 file
    const edited = new File([processedBlob], "edited-resume.png", { type: "image/png" });
    setFile(edited);
    setShowImageEditor(false);
  }

  function handleImageEditCancel() {
    setShowImageEditor(false);
  }

  async function parseResume() {
    if (!file) return;
    setProcessing(true);
    setResumeError("");
    setResumeWarnings([]);
    try {
      // 上传入口也可能直接从 /auth 进入，此时浏览器还没有访客 Cookie。
      if (!(await ensureGuest())) {
        throw new Error("无法准备会话，请刷新后重试");
      }
      // 真解析：把文件交给后端（零依赖：DOCX 用 stdlib 读，PDF/图片会明确回 415）
      const form = new FormData();
      form.append("file", file);
      const extracted = await fetch(apiUrl("/api/resumes/extract"), {
        method: "POST",
        credentials: "include",
        body: form,
      });
      const payload = await extracted.json().catch(() => ({})) as {
        data?: { profileDraft?: Record<string, unknown>; warnings?: string[]; candidateCount?: number };
        error?: { message?: string };
      };
      if (!extracted.ok || !payload?.data) {
        throw new Error(payload?.error?.message || `简历解析失败（HTTP ${extracted.status}）`);
      }
      const draft = payload.data.profileDraft ?? {};
      // 解析结果只是**草稿**：这里走真实画像接口落成 draft 画像，用户在复核页确认
      const r = await fetch(apiUrl("/api/profile"), {
        method: "PUT",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(draft),
      });
      const data = await r.json().catch(() => ({})) as ProfileResponse;
      const p: UserProfile | null = data?.data?.profile ?? null;
      if (!r.ok || !p) {
        throw new Error(data?.error?.message || data?.message || `profile request failed: ${r.status}`);
      }
      setReviewProfile(p);
      const warnings = payload.data.warnings ?? [];
      setResumeWarnings(
        (payload.data.candidateCount ?? 0) > 0
          ? [...warnings, `已在记忆库生成 ${payload.data.candidateCount} 条待确认记忆，到「用户画像 → 记忆库」里逐条确认`]
          : warnings,
      );
      setView("review");
    } catch (error) {
      setReviewProfile(null);
      setResumeError(
        error instanceof Error && error.message
          ? `${error.message}（也可以改用手动填写）`
          : "简历解析失败，请重试；也可以改用手动填写。",
      );
    } finally {
      setProcessing(false);
    }
  }

  function resetChoices() {
    setView("choices");
    setEntryMode("resume");
    setFile(null);
    setProcessing(false);
    setSubmitting(false);
    setResumeError("");
  }

  async function handleManualSubmit(mapped: Record<string, unknown>) {
    setSubmitting(true);
    try {
      if (!(await ensureGuest())) {
        throw new Error("guest session unavailable");
      }
      const r1 = await fetch(apiUrl("/api/profile"), {
        method: "PUT",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(mapped),
      });
      if (!r1.ok) {
        const data = await r1.json().catch(() => ({})) as ProfileResponse;
        alert("保存失败：" + (data?.message || r1.status));
        setSubmitting(false);
        return;
      }
      // 跳 review 视图（不 confirm），让用户在 review 里二次确认
      const data = await r1.json().catch(() => ({})) as ProfileResponse;
      setReviewProfile(data?.data?.profile ?? null);
      setSubmitting(false);
      setEntryMode("manual");
      setView("review");
    } catch {
      setSubmitting(false);
      alert("网络异常，请重试");
    }
  }

  // 兜底：确保有 userId cookie（guest auth）。刷新页面或 cookie 过期时自动重建。
  async function ensureGuest(displayName = "访客") {
    if (guestReadyRef.current) return true;
    try {
      const r = await fetch(apiUrl("/api/auth/guest"), {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ displayName }),
      });
      // 501 = 这个构建没有会话系统（本机单用户形态，`user_local`）。这不是失败：
      // 只有真正的错误才该拦住流程，否则「很诚实的 501」会变成前端不可用。
      if (r.status === 501) {
        guestReadyRef.current = true;
        return true;
      }
      if (!r.ok) throw new Error("guest failed");
      guestReadyRef.current = true;
      return true;
    } catch {
      return false;
    }
  }

  return (
    <div className={`xn-onboarding-page ${view === "choices" ? "is-choices" : view === "form" ? "is-form" : ""}`}>
      <header className="xn-onboarding-header">
        <EntryBrand />
        <div className="xn-onboarding-actions">
          <span>资料可以稍后补充</span>
          <Link href="/work-map">进入未来工作地图</Link>
        </div>
      </header>

      {view !== "form" && <div className="xn-onboarding-progress" aria-label="引导进度">
        <span className="done"><i>1</i>创建账号</span><b />
        <span className={
          view === "review" || (entryMode === "resume" && (processing || view === "resume"))
            ? "done" : view === "resume" || submitting ? "active" : ""
        }><i>2</i>{STEP_LABELS[entryMode].s2}</span><b />
        <span className={
          view === "review" ? "done" : (processing || submitting) ? "active" : ""
        }><i>3</i>{STEP_LABELS[entryMode].s3}</span><b />
        <span className={view === "review" || submitting ? "done" : view === "resume" ? "active" : ""}><i>4</i>用户画像生成</span>
      </div>}

      {view === "choices" && (
        <section className="xn-onboarding-content">
          <div className="xn-onboarding-options">
            <div className="xn-option-card">
              <button className="featured" onClick={() => { setEntryMode("resume"); setView("resume"); }}>
                <span className="xn-option-index">01</span><em>最快完成</em>
                <h2>上传简历</h2>
                <p>从已有经历中提取教育、技能、项目和实习信息。</p>
                <b>开始上传 <i>→</i></b>
              </button>
              <button
                className="xn-option-footnote"
                type="button"
                onClick={() => { setEntryMode("manual"); setView("form"); }}
              >
                若无简历，请回答基础问题
              </button>
            </div>
          </div>
          <button
            className="xn-onboarding-skip"
            onClick={() => router.push("/work-map")}
          >
            暂时跳过，直接进入成长空间
          </button>
        </section>
      )}

      {view === "resume" && (
        <section className="xn-onboarding-card xn-resume-step">
          <button className="xn-back-button" onClick={resetChoices}>
            ← 返回选择
          </button>
          <div className="xn-step-heading">
            <h1>上传一份最近使用的简历</h1>
          </div>
          <label className={`xn-upload-zone ${file ? "has-file" : ""}`}>
            <input
              type="file"
              accept=".docx,.txt,.md,.pdf,.doc,.jpg,.jpeg,.png"
              onChange={chooseFile}
            />
            <span className="xn-upload-symbol">↑</span>
            {file ? (
              <>
                <strong>{file.name}</strong>
                <small>
                  {(file.size / 1024 / 1024).toFixed(2)} MB · 已准备
                </small>
              </>
            ) : (
              <>
                <strong>拖放或选择简历文件</strong>
                <small>支持 DOCX / 纯文本；PDF 与图片暂不支持（后端零依赖、无 OCR），不超过 10MB</small>
              </>
            )}
          </label>
          {resumeError && (
            <p className="xn-form-errors" role="alert">
              {resumeError}
            </p>
          )}
          {resumeWarnings.length > 0 && (
            <ul className="xn-form-warnings">
              {resumeWarnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
          {isImageFile(file) && (
            <div className="xn-image-edit-hint">
              <span>已选图片简历 —</span>
              <button
                type="button"
                className="xn-image-edit-open"
                onClick={() => setShowImageEditor(true)}
              >
                编辑图片内容（删除区域）
              </button>
            </div>
          )}
          <div className="xn-file-principles">
            <span>仅用于生成候选画像</span>
            <span>提取后由你确认</span>
            <span>支持删除原始文件</span>
          </div>
          <div className="xn-resume-footnote">
            <button
              type="button"
              className="xn-footnote-link"
              onClick={() => { setEntryMode("manual"); setView("form"); }}
            >
              若无简历，请回答基础问题
            </button>
          </div>
          <div className="xn-step-actions">
            <button className="xn-entry-secondary" onClick={resetChoices}>
              取消
            </button>
            <button
              className="xn-entry-primary"
              disabled={!file || processing}
              onClick={parseResume}
            >
              {processing ? "正在提取信息…" : "开始提取"}
            </button>
          </div>
        </section>
      )}

      {view === "form" && (
        <ProfileForm
          showBack
          onBack={resetChoices}
          onSubmit={(mapped) => handleManualSubmit(mapped)}
        />
      )}

      {view === "review" && (
        <>
          <ProfileReviewCard
            profile={reviewProfile}
            source={entryMode === "resume" ? "简历提取" : "手动填写"}
            onBack={() => setView(entryMode === "resume" ? "resume" : "form")}
            onConfirm={async () => {
              try {
                if (!(await ensureGuest())) {
                  throw new Error("guest session unavailable");
                }
                const r = await fetch(apiUrl("/api/profile/confirm"), {
                  method: "POST",
                  credentials: "include",
                });
                if (!r.ok) {
                  const d = await r.json().catch(() => ({})) as ProfileResponse;
                  alert("确认失败：" + (d?.message || r.status));
                  return;
                }
                router.push("/chat");
              } catch {
                alert("网络异常");
              }
            }}
          />
        </>
      )}

      {showImageEditor && file && (
        <ImageEditor
          file={file}
          onConfirm={handleImageEditConfirm}
          onCancel={handleImageEditCancel}
        />
      )}
    </div>
  );
}
