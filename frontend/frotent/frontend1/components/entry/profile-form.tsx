"use client";

import { useMemo, useState } from "react";

// ────────────────────────────────────────────────────────────────────────
// 数据结构定义
// ────────────────────────────────────────────────────────────────────────

export type Identity = "student" | "graduate" | "newEmployee" | "careerChanger";

export const IDENTITY_LABELS: Record<Identity, string> = {
  student: "在校生",
  graduate: "应届生",
  newEmployee: "职场新人",
  careerChanger: "转岗探索中",
};

// 在校生专属
type StudentFields = {
  school?: string;
  degree?: "专科" | "本科" | "硕士" | "博士";
  major?: string;
  expectedGraduation?: string; // date
  currentGrade?: string;
  hasInternship?: "有" | "无";
  internshipRole?: string;
  internshipDuration?: string;
  internshipDesc?: string;
  skills: string[];
  projects?: string;
  certificates?: string;
  targetType?: "日常实习" | "暑期实习" | "提前准备校招";
  weeklyHours?: string;
};

// 应届生专属
type GraduateFields = {
  school?: string;
  degree?: "专科" | "本科" | "硕士" | "博士";
  major?: string;
  graduationDate?: string;
  hasInternship?: "有" | "无";
  internshipRole?: string;
  internshipDuration?: string;
  internshipDesc?: string;
  skills: string[];
  projects?: string;
  certificates?: string;
  hasTrialJob?: "是" | "否";
  salaryRange?: string;
  acceptOvertime?: "是" | "否";
  jobPriority?: "薪资优先" | "平台优先" | "成长学习优先";
};

// 职场新人专属
type NewEmployeeFields = {
  company?: string;
  jobTitle?: string;
  employmentStart?: string;
  employmentEnd?: string;
  jobResponsibilities?: string;
  skills: string[];
  workProjects?: string;
  certificates?: string;
  resignationReason?: string;
  salaryRange?: string;
  workType?: "全职" | "远程" | "外包";
  jobAppeal?: "涨薪" | "换行业" | "换赛道" | "更好平台";
};

// 转岗专属
type CareerChangerFields = {
  currentIndustry?: string;
  currentJobTitle?: string;
  totalYears?: string;
  currentJobDesc?: string;
  currentSkills: string[];
  targetIndustry?: string;
  targetJobTitle?: string;
  prepStatus?: "自学课程" | "做练习项目" | "考取证书" | "无准备";
  changeReason?: string;
  transitionMethod?: "直接跳槽" | "先实习过渡" | "兼职试手";
  salaryExpectation?: string;
  weeklyStudyHours?: string;
  targetAwareness?: "了解较多" | "略有了解" | "几乎不了解";
};

type SpecialFields = StudentFields | GraduateFields | NewEmployeeFields | CareerChangerFields;

// 公共字段（所有身份必填，始终展示）
export type ProfileFormData = {
  identity: Identity;
  name?: string;
  phone?: string;
  targetCity?: string;
  targetOccupation?: string;
  targetOccupationCustom?: string;
  availableDate?: string;
  selfDescription?: string;
  jobStatus?: "正在找工作" | "观望机会" | "仅学习提升暂不求职";
  // 专属
  student?: StudentFields;
  graduate?: GraduateFields;
  newEmployee?: NewEmployeeFields;
  careerChanger?: CareerChangerFields;
};

// ────────────────────────────────────────────────────────────────────────
// 专属字段初始值工厂
// ────────────────────────────────────────────────────────────────────────

function initStudent(): StudentFields {
  return { skills: [], hasInternship: "无" };
}
function initGraduate(): GraduateFields {
  return { skills: [], hasInternship: "无" };
}
function initNewEmployee(): NewEmployeeFields {
  return { skills: [] };
}
function initCareerChanger(): CareerChangerFields {
  return { currentSkills: [] };
}

// ────────────────────────────────────────────────────────────────────────
// 表单 → buildProfile 输入 映射（桥接现有 contract）
// ────────────────────────────────────────────────────────────────────────

export function formDataToBuildProfileInput(data: ProfileFormData): Record<string, unknown> {
  const shared = {
    identity: IDENTITY_LABELS[data.identity],
    school: (() => {
      if (data.identity === "student") return data.student?.school;
      if (data.identity === "graduate") return data.graduate?.school;
      return undefined;
    })(),
    major: (() => {
      if (data.identity === "student") return data.student?.major;
      if (data.identity === "graduate") return data.graduate?.major;
      return undefined;
    })(),
    grade: (() => {
      if (data.identity === "student") return data.student?.currentGrade;
      return undefined;
    })(),
    location: data.targetCity,
    skills: data.identity === "student" ? data.student?.skills
      : data.identity === "graduate" ? data.graduate?.skills
      : data.identity === "newEmployee" ? data.newEmployee?.skills
      : data.careerChanger?.currentSkills,
    experience: (() => {
      const parts: string[] = [];
      if (data.selfDescription) parts.push(`自我描述：${data.selfDescription}`);
      if (data.identity === "student") {
        if (data.student?.projects) parts.push(`项目/竞赛：${data.student.projects}`);
        if (data.student?.hasInternship === "有" && data.student.internshipDesc)
          parts.push(`实习(${data.student.internshipRole || ""}, ${data.student.internshipDuration || ""})：${data.student.internshipDesc}`);
        if (data.student?.certificates) parts.push(`证书：${data.student.certificates}`);
      } else if (data.identity === "graduate") {
        if (data.graduate?.projects) parts.push(`项目/毕设：${data.graduate.projects}`);
        if (data.graduate?.hasInternship === "有" && data.graduate.internshipDesc)
          parts.push(`实习(${data.graduate.internshipRole || ""})：${data.graduate.internshipDesc}`);
        if (data.graduate?.certificates) parts.push(`证书：${data.graduate.certificates}`);
      } else if (data.identity === "newEmployee") {
        const ne = data.newEmployee!;
        if (ne?.company) parts.push(`上一家公司：${ne.company} / ${ne.jobTitle || ""}`);
        if (ne?.jobResponsibilities) parts.push(`工作职责：${ne.jobResponsibilities}`);
        if (ne?.workProjects) parts.push(`项目：${ne.workProjects}`);
        if (ne?.resignationReason) parts.push(`离职原因：${ne.resignationReason}`);
      } else if (data.identity === "careerChanger") {
        const cc = data.careerChanger!;
        if (cc?.currentIndustry) parts.push(`当前行业：${cc.currentIndustry} / ${cc.currentJobTitle || ""}（${cc.totalYears || "?"}年）`);
        if (cc?.currentJobDesc) parts.push(`当前工作：${cc.currentJobDesc}`);
        if (cc?.targetIndustry) parts.push(`目标：${cc.targetIndustry} / ${cc.targetJobTitle || ""}`);
        if (cc?.changeReason) parts.push(`转岗原因：${cc.changeReason}`);
      }
      return parts.join("\n");
    })(),
    directions: data.targetOccupationCustom || data.targetOccupation,
    question: data.jobStatus === "仅学习提升暂不求职"
      ? "暂不求职，希望系统能帮助提升技能"
      : data.jobStatus === "观望机会"
      ? "正在观望合适机会"
      : `意向${data.targetOccupation || ""}${data.targetCity ? "，意向城市：" + data.targetCity : ""}${data.availableDate ? "，可到岗：" + data.availableDate : ""}`,
  };
  return shared;
}

// ────────────────────────────────────────────────────────────────────────
// 工具
// ────────────────────────────────────────────────────────────────────────

const MOBILE_RE = /^1[3-9]\d{9}$/;

type FieldError = { field: string; message: string };

function validate(data: ProfileFormData): FieldError[] {
  const errs: FieldError[] = [];
  if (!data.name?.trim()) errs.push({ field: "name", message: "请填写姓名" });
  if (!data.phone?.trim()) {
    errs.push({ field: "phone", message: "请填写联系电话" });
  } else if (!MOBILE_RE.test(data.phone.trim())) {
    errs.push({ field: "phone", message: "手机号格式不正确" });
  }
  if (!data.targetCity?.trim()) errs.push({ field: "targetCity", message: "请填写意向城市" });
  if (!data.targetOccupation?.trim() && !data.targetOccupationCustom?.trim()) {
    errs.push({ field: "targetOccupation", message: "请选择或填写意向岗位方向" });
  }
  if (!data.availableDate?.trim()) errs.push({ field: "availableDate", message: "请选择可到岗时间" });
  if (!data.jobStatus) errs.push({ field: "jobStatus", message: "请选择求职状态" });
  return errs;
}

function MultiTag({
  tags,
  onChange,
  placeholder = "输入后回车添加",
}: {
  tags: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");
  return (
    <div className="xn-tag-input">
      {tags.map((t) => (
        <span key={t} className="xn-tag">
          {t}
          <button
            type="button"
            className="xn-tag-remove"
            onClick={() => onChange(tags.filter((x) => x !== t))}
            aria-label={`删除 ${t}`}
          >
            ×
          </button>
        </span>
      ))}
      <input
        type="text"
        className="xn-tag-draft"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === "," || e.key === "，") {
            e.preventDefault();
            const v = draft.trim();
            if (v && !tags.includes(v)) onChange([...tags, v]);
            setDraft("");
          } else if (e.key === "Backspace" && !draft && tags.length) {
            onChange(tags.slice(0, -1));
          }
        }}
        placeholder={tags.length ? "" : placeholder}
      />
    </div>
  );
}

function CharCount({ value, max }: { value: string; max: number }) {
  return (
    <span className={`xn-char-count ${value.length >= max ? "over" : ""}`}>
      {value.length}/{max}
    </span>
  );
}

// ────────────────────────────────────────────────────────────────────────
// ProfileForm 主组件
// ────────────────────────────────────────────────────────────────────────

type Props = {
  onSubmit: (mapped: Record<string, unknown>, raw: ProfileFormData) => void | Promise<void>;
  initial?: Partial<ProfileFormData>;
  showBack?: boolean;
  onBack?: () => void;
};

const OCCUPATION_OPTIONS = [
  "嵌入式开发", "边缘AI", "机器视觉", "算法工程师", "后端开发",
  "前端开发", "数据分析", "产品经理", "UI/UX设计", "测试工程师",
];

export function ProfileForm({ onSubmit, initial, showBack = true, onBack }: Props) {
  const [data, setData] = useState<ProfileFormData>(() => ({
    identity: initial?.identity ?? "student",
    name: initial?.name ?? "",
    phone: initial?.phone ?? "",
    targetCity: initial?.targetCity ?? "",
    targetOccupation: initial?.targetOccupation ?? "",
    targetOccupationCustom: initial?.targetOccupationCustom ?? "",
    availableDate: initial?.availableDate ?? "",
    selfDescription: initial?.selfDescription ?? "",
    jobStatus: initial?.jobStatus,
    student: initial?.student ?? initStudent(),
    graduate: initial?.graduate ?? initGraduate(),
    newEmployee: initial?.newEmployee ?? initNewEmployee(),
    careerChanger: initial?.careerChanger ?? initCareerChanger(),
  }));

  const [errs, setErrs] = useState<FieldError[]>([]);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [step, setStep] = useState<1 | 2>(1);
  const [identityConfirmed, setIdentityConfirmed] = useState(false);
  const [showIdentitySwitch, setShowIdentitySwitch] = useState(true);

  const update = <K extends keyof ProfileFormData>(key: K, value: ProfileFormData[K]) => {
    setData((d) => ({ ...d, [key]: value }));
    setErrs((e) => e.filter((x) => x.field !== key));
  };

  // 切换身份 → 保留公共字段，重置专属字段
  const changeIdentity = (next: Identity) => {
    setData((d) => ({
      ...d,
      identity: next,
      student: next === "student" ? d.student ?? initStudent() : initStudent(),
      graduate: next === "graduate" ? d.graduate ?? initGraduate() : initGraduate(),
      newEmployee: next === "newEmployee" ? d.newEmployee ?? initNewEmployee() : initNewEmployee(),
      careerChanger: next === "careerChanger" ? d.careerChanger ?? initCareerChanger() : initCareerChanger(),
    }));
    setErrs([]);
    setIdentityConfirmed(true);
    setShowIdentitySwitch(false);
  };

  // 点击身份 chip → 重新展开选择栏
  const reopenIdentitySwitch = () => {
    setShowIdentitySwitch(true);
    setTimeout(() => {
      const sw = document.querySelector(".xn-identity-switch");
      sw?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 50);
  };

  const current = data.identity;

  const mapped = useMemo(() => formDataToBuildProfileInput(data), [data]);

  const handlePreview = () => {
    const e = validate(data);
    if (e.length) {
      setErrs(e);
      return;
    }
    setErrs([]);
    setPreviewOpen(true);
  };

  const handleNext = () => {
    const e = validate(data);
    if (e.length) {
      setErrs(e);
      return;
    }
    setErrs([]);
    setStep(2);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleSubmit = async () => {
    const e = validate(data);
    if (e.length) {
      setErrs(e);
      return;
    }
    setErrs([]);
    setSubmitting(true);
    try {
      await onSubmit(mapped, data);
    } finally {
      setSubmitting(false);
    }
  };

  // 专属字段便捷更新
  const updateCurrent = (key: string, value: unknown) => {
    setData((d) => ({
      ...d,
      [current]: { ...(d[current] as Record<string, unknown>), [key]: value },
    }));
  };

  const cur: SpecialFields = data[current] ?? (
    current === "student" ? initStudent()
      : current === "graduate" ? initGraduate()
        : current === "newEmployee" ? initNewEmployee()
          : initCareerChanger()
  );

  return (
    <section className="xn-onboarding-card xn-form-card">
      {showBack && (
        <button type="button" className="xn-back-button" onClick={onBack}>
          ← 返回选择
        </button>
      )}

      <div className="xn-step-heading">
        <span>基础信息 · {step}/2</span>
        <h1>{step === 1 ? "先了解你现在的位置" : "再了解你的经历与目标"}</h1>
        <p>除当前身份外都可以稍后修改或补充。</p>
      </div>

      {step === 1 && showIdentitySwitch && <div className="xn-identity-switch-wrap">
        <div className="xn-identity-switch" role="radiogroup" aria-label="当前身份">
          {(Object.keys(IDENTITY_LABELS) as Identity[]).map((id) => (
            <button key={id} type="button" role="radio" aria-checked={current === id} className={`xn-identity-pill ${current === id ? "active" : ""}`} onClick={() => changeIdentity(id)}>
              {IDENTITY_LABELS[id]}
            </button>
          ))}
        </div>
      </div>}

      {step === 1 && identityConfirmed && !showIdentitySwitch && (
        <div className="xn-identity-confirmed" role="status">
          <span>当前身份为：<b>{IDENTITY_LABELS[current]}</b></span>
          <button type="button" onClick={reopenIdentitySwitch}>重新选择</button>
        </div>
      )}

      {/* ── 公共基础模块 ── */}
      {step === 1 && <fieldset className="xn-form-section">
        <legend>公共基础信息</legend>
        <div className="xn-form-grid">
          <label>
            <span>姓名 <em>*</em></span>
            <input
              type="text"
              value={data.name ?? ""}
              onChange={(e) => update("name", e.target.value)}
              placeholder="请输入真实姓名"
              maxLength={20}
              className={errs.some((x) => x.field === "name") ? "xn-field-err" : ""}
            />
          </label>
          <label>
            <span>联系电话 <em>*</em></span>
            <input
              type="tel"
              value={data.phone ?? ""}
              onChange={(e) => update("phone", e.target.value.replace(/\D/g, "").slice(0, 11))}
              placeholder="11 位手机号"
              maxLength={11}
              className={errs.some((x) => x.field === "phone") ? "xn-field-err" : ""}
            />
          </label>
          <label>
            <span>意向城市 <em>*</em></span>
            <input
              type="text"
              value={data.targetCity ?? ""}
              onChange={(e) => update("targetCity", e.target.value)}
              placeholder="例如：杭州"
              maxLength={20}
              className={errs.some((x) => x.field === "targetCity") ? "xn-field-err" : ""}
            />
          </label>
          <label className="xn-span-2">
            <span>意向岗位方向 <em>*</em></span>
            <div className="xn-combo-input">
              <select
                value={data.targetOccupation ?? ""}
                onChange={(e) => {
                  update("targetOccupation", e.target.value);
                  if (e.target.value) update("targetOccupationCustom", "");
                }}
              >
                <option value="">选择一个方向</option>
                {OCCUPATION_OPTIONS.map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
              <input
                type="text"
                value={data.targetOccupationCustom ?? ""}
                onChange={(e) => {
                  update("targetOccupationCustom", e.target.value);
                  if (e.target.value) update("targetOccupation", "");
                }}
                placeholder="或手动输入自定义方向"
                maxLength={30}
              />
            </div>
          </label>
          <label>
            <span>可到岗时间 <em>*</em></span>
            <input
              type="date"
              value={data.availableDate ?? ""}
              onChange={(e) => update("availableDate", e.target.value)}
              className={errs.some((x) => x.field === "availableDate") ? "xn-field-err" : ""}
            />
          </label>
          <label>
            <span>求职状态 <em>*</em></span>
            <div className="xn-choice-pills">
              {(["正在找工作", "观望机会", "仅学习提升暂不求职"] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`xn-pill ${data.jobStatus === s ? "active" : ""}`}
                  onClick={() => update("jobStatus", s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </label>
        </div>

        <label>
          <span>自我简短描述 <em>*</em></span>
          <textarea
            value={data.selfDescription ?? ""}
            onChange={(e) => update("selfDescription", e.target.value.slice(0, 100))}
            placeholder="一句话介绍自己，帮助 AI 快速理解你的情况"
            maxLength={100}
          />
          <CharCount value={data.selfDescription ?? ""} max={100} />
        </label>
      </fieldset>}

      {/* ── 专属模块 ── */}
      {step === 2 && <div className="xn-form-special" key={current}>
        {renderSpecial(current, cur, updateCurrent)}
      </div>}

      {/* ── 错误提示 ── */}
      {errs.length > 0 && (
        <div className="xn-form-errors" role="alert">
          {errs.map((e, i) => (
            <div key={i}>• {e.message}</div>
          ))}
        </div>
      )}

      {/* ── 底部操作 ── */}
      <div className="xn-step-actions xn-form-footer">
        {step === 1 ? <>
          <button type="button" className="xn-entry-secondary" onClick={onBack} disabled={submitting}>取消</button>
          <button type="button" className="xn-entry-primary" onClick={handleNext} disabled={submitting}>下一步</button>
        </> : <>
          <button type="button" className="xn-entry-secondary" onClick={() => { setStep(1); window.scrollTo({ top: 0, behavior: "smooth" }); }} disabled={submitting}>上一步</button>
          <button type="button" className="xn-entry-primary" onClick={handlePreview} disabled={submitting}>预览画像</button>
        </>}
      </div>

      {/* ── 预览对话框 ── */}
      {previewOpen && (
        <div className="xn-preview-mask" onClick={() => setPreviewOpen(false)}>
          <div className="xn-preview-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>用户画像预览</h3>
            <div className="xn-preview-raw">
              <pre>{JSON.stringify(mapped, null, 2)}</pre>
            </div>
            <div className="xn-step-actions">
              <button
                type="button"
                className="xn-entry-secondary"
                onClick={() => setPreviewOpen(false)}
              >
                返回修改
              </button>
              <button
                type="button"
                className="xn-entry-primary"
                onClick={handleSubmit}
                disabled={submitting}
              >
                {submitting ? "正在提交…" : "确认提交"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

// ────────────────────────────────────────────────────────────────────────
// 专属模块渲染器
// ────────────────────────────────────────────────────────────────────────

function renderSpecial(
  identity: Identity,
  cur: SpecialFields,
  update: (key: string, value: unknown) => void,
) {
  if (identity === "student") return <StudentBlock data={cur as StudentFields} update={update} />;
  if (identity === "graduate") return <GraduateBlock data={cur as GraduateFields} update={update} />;
  if (identity === "newEmployee") return <NewEmployeeBlock data={cur as NewEmployeeFields} update={update} />;
  return <CareerChangerBlock data={cur as CareerChangerFields} update={update} />;
}

function StudentBlock({ data, update }: { data: StudentFields; update: (k: string, v: unknown) => void }) {
  return (
    <fieldset className="xn-form-section xn-special">
      <legend>在校生专属信息 · 实习/校招准备</legend>
      <div className="xn-form-grid">
        <label><span>学校名称</span><input type="text" value={data.school ?? ""} onChange={(e) => update("school", e.target.value)} maxLength={40} placeholder="例如：浙江大学" /></label>
        <label><span>学历层次</span>
          <div className="xn-choice-pills">
            {(["专科", "本科", "硕士", "博士"] as const).map((d) => (
              <button type="button" key={d} className={`xn-pill ${data.degree === d ? "active" : ""}`} onClick={() => update("degree", d)}>{d}</button>
            ))}
          </div>
        </label>
        <label><span>所学专业</span><input type="text" value={data.major ?? ""} onChange={(e) => update("major", e.target.value)} maxLength={30} placeholder="例如：自动化" /></label>
        <label><span>当前年级</span><input type="text" value={data.currentGrade ?? ""} onChange={(e) => update("currentGrade", e.target.value)} maxLength={20} placeholder="例如：大三 / 研一" /></label>
        <label><span>预计毕业时间</span><input type="date" value={data.expectedGraduation ?? ""} onChange={(e) => update("expectedGraduation", e.target.value)} /></label>
      </div>
      <label><span>掌握技能</span>
        <MultiTag tags={data.skills ?? []} onChange={(v) => update("skills", v)} />
      </label>
      <label><span>在校项目 / 竞赛经历</span>
        <textarea value={data.projects ?? ""} onChange={(e) => update("projects", e.target.value.slice(0, 300))} maxLength={300} placeholder="项目名称、承担工作、成果…" />
        <CharCount value={data.projects ?? ""} max={300} />
      </label>
      <label><span>证书</span><input type="text" value={data.certificates ?? ""} onChange={(e) => update("certificates", e.target.value)} maxLength={80} placeholder="例如：CET-6 / 计算机二级" /></label>

      <label>
        <span>有无实习经历</span>
        <div className="xn-choice-pills">
          {(["有", "无"] as const).map((v) => (
            <button type="button" key={v} className={`xn-pill ${data.hasInternship === v ? "active" : ""}`} onClick={() => update("hasInternship", v)}>{v}</button>
          ))}
        </div>
      </label>
      {data.hasInternship === "有" && (
        <div className="xn-inline-grid xn-slide-in">
          <label><span>实习岗位</span><input type="text" value={data.internshipRole ?? ""} onChange={(e) => update("internshipRole", e.target.value)} maxLength={30} placeholder="例如：嵌入式实习生" /></label>
          <label><span>实习时长</span><input type="text" value={data.internshipDuration ?? ""} onChange={(e) => update("internshipDuration", e.target.value)} maxLength={20} placeholder="例如：3 个月" /></label>
          <label className="xn-span-2"><span>实习核心工作内容</span>
            <textarea value={data.internshipDesc ?? ""} onChange={(e) => update("internshipDesc", e.target.value.slice(0, 200))} maxLength={200} />
            <CharCount value={data.internshipDesc ?? ""} max={200} />
          </label>
        </div>
      )}

      <div className="xn-form-grid">
        <label><span>目标类型</span>
          <div className="xn-choice-pills">
            {(["日常实习", "暑期实习", "提前准备校招"] as const).map((v) => (
              <button type="button" key={v} className={`xn-pill ${data.targetType === v ? "active" : ""}`} onClick={() => update("targetType", v)}>{v}</button>
            ))}
          </div>
        </label>
        <label><span>每周可投入实习时长（小时）</span><input type="number" value={data.weeklyHours ?? ""} onChange={(e) => update("weeklyHours", e.target.value)} min={0} max={60} /></label>
      </div>
    </fieldset>
  );
}

function GraduateBlock({ data, update }: { data: GraduateFields; update: (k: string, v: unknown) => void }) {
  return (
    <fieldset className="xn-form-section xn-special">
      <legend>应届生专属信息 · 毕业 1 年内</legend>
      <div className="xn-form-grid">
        <label><span>学校名称</span><input type="text" value={data.school ?? ""} onChange={(e) => update("school", e.target.value)} maxLength={40} /></label>
        <label><span>学历层次</span>
          <div className="xn-choice-pills">{(["专科", "本科", "硕士", "博士"] as const).map((d) => (
            <button type="button" key={d} className={`xn-pill ${data.degree === d ? "active" : ""}`} onClick={() => update("degree", d)}>{d}</button>
          ))}</div>
        </label>
        <label><span>所学专业</span><input type="text" value={data.major ?? ""} onChange={(e) => update("major", e.target.value)} maxLength={30} /></label>
        <label><span>毕业时间</span><input type="date" value={data.graduationDate ?? ""} onChange={(e) => update("graduationDate", e.target.value)} /></label>
      </div>
      <label><span>掌握技能</span>
        <MultiTag tags={data.skills ?? []} onChange={(v) => update("skills", v)} />
      </label>
      <label><span>在校项目 / 毕设 / 竞赛</span>
        <textarea value={data.projects ?? ""} onChange={(e) => update("projects", e.target.value.slice(0, 300))} maxLength={300} placeholder="项目名称、承担工作、成果…" />
        <CharCount value={data.projects ?? ""} max={300} />
      </label>
      <label><span>已获得证书</span><input type="text" value={data.certificates ?? ""} onChange={(e) => update("certificates", e.target.value)} maxLength={80} /></label>

      <label>
        <span>有无实习经历</span>
        <div className="xn-choice-pills">
          {(["有", "无"] as const).map((v) => (
            <button type="button" key={v} className={`xn-pill ${data.hasInternship === v ? "active" : ""}`} onClick={() => update("hasInternship", v)}>{v}</button>
          ))}
        </div>
      </label>
      {data.hasInternship === "有" && (
        <div className="xn-inline-grid xn-slide-in">
          <label><span>实习岗位</span><input type="text" value={data.internshipRole ?? ""} onChange={(e) => update("internshipRole", e.target.value)} maxLength={30} /></label>
          <label><span>实习时长</span><input type="text" value={data.internshipDuration ?? ""} onChange={(e) => update("internshipDuration", e.target.value)} maxLength={20} /></label>
          <label className="xn-span-2"><span>核心工作内容</span>
            <textarea value={data.internshipDesc ?? ""} onChange={(e) => update("internshipDesc", e.target.value.slice(0, 200))} maxLength={200} />
            <CharCount value={data.internshipDesc ?? ""} max={200} />
          </label>
        </div>
      )}

      <div className="xn-form-grid">
        <label><span>是否有全职试用/短期经历</span>
          <div className="xn-choice-pills">{(["是", "否"] as const).map((v) => (
            <button type="button" key={v} className={`xn-pill ${data.hasTrialJob === v ? "active" : ""}`} onClick={() => update("hasTrialJob", v)}>{v}</button>
          ))}</div>
        </label>
        <label><span>期望薪资范围</span><input type="text" value={data.salaryRange ?? ""} onChange={(e) => update("salaryRange", e.target.value)} maxLength={30} placeholder="例如：8-12k/月" /></label>
        <label><span>是否接受加班/出差</span>
          <div className="xn-choice-pills">{(["是", "否"] as const).map((v) => (
            <button type="button" key={v} className={`xn-pill ${data.acceptOvertime === v ? "active" : ""}`} onClick={() => update("acceptOvertime", v)}>{v}</button>
          ))}</div>
        </label>
        <label><span>求职优先级</span>
          <div className="xn-choice-pills">{(["薪资优先", "平台优先", "成长学习优先"] as const).map((v) => (
            <button type="button" key={v} className={`xn-pill ${data.jobPriority === v ? "active" : ""}`} onClick={() => update("jobPriority", v)}>{v}</button>
          ))}</div>
        </label>
      </div>
    </fieldset>
  );
}

function NewEmployeeBlock({ data, update }: { data: NewEmployeeFields; update: (k: string, v: unknown) => void }) {
  return (
    <fieldset className="xn-form-section xn-special">
      <legend>职场新人专属信息 · 毕业 1–3 年</legend>
      <div className="xn-form-grid">
        <label><span>当前/上一家公司</span><input type="text" value={data.company ?? ""} onChange={(e) => update("company", e.target.value)} maxLength={40} /></label>
        <label><span>岗位名称</span><input type="text" value={data.jobTitle ?? ""} onChange={(e) => update("jobTitle", e.target.value)} maxLength={30} /></label>
        <label><span>入职年月</span><input type="date" value={data.employmentStart ?? ""} onChange={(e) => update("employmentStart", e.target.value)} /></label>
        <label><span>离职年月（在职请留空）</span><input type="date" value={data.employmentEnd ?? ""} onChange={(e) => update("employmentEnd", e.target.value)} /></label>
      </div>
      <label><span>主要工作职责与业绩</span>
        <textarea value={data.jobResponsibilities ?? ""} onChange={(e) => update("jobResponsibilities", e.target.value.slice(0, 400))} maxLength={400} placeholder="团队/职责范围/关键成果数据…" />
        <CharCount value={data.jobResponsibilities ?? ""} max={400} />
      </label>
      <label><span>掌握技能</span>
        <MultiTag tags={data.skills ?? []} onChange={(v) => update("skills", v)} />
      </label>
      <label><span>项目工作经历</span>
        <textarea value={data.workProjects ?? ""} onChange={(e) => update("workProjects", e.target.value.slice(0, 300))} maxLength={300} placeholder="业务项目、个人贡献…" />
        <CharCount value={data.workProjects ?? ""} max={300} />
      </label>
      <label><span>职业证书</span><input type="text" value={data.certificates ?? ""} onChange={(e) => update("certificates", e.target.value)} maxLength={80} /></label>
      <label><span>离职原因</span>
        <textarea value={data.resignationReason ?? ""} onChange={(e) => update("resignationReason", e.target.value.slice(0, 200))} maxLength={200} />
        <CharCount value={data.resignationReason ?? ""} max={200} />
      </label>
      <div className="xn-form-grid">
        <label><span>期望薪资范围</span><input type="text" value={data.salaryRange ?? ""} onChange={(e) => update("salaryRange", e.target.value)} maxLength={30} placeholder="例如：15-25k/月" /></label>
        <label><span>期望工作类型</span>
          <div className="xn-choice-pills">{(["全职", "远程", "外包"] as const).map((v) => (
            <button type="button" key={v} className={`xn-pill ${data.workType === v ? "active" : ""}`} onClick={() => update("workType", v)}>{v}</button>
          ))}</div>
        </label>
        <label className="xn-span-2"><span>求职诉求</span>
          <div className="xn-choice-pills">{(["涨薪", "换行业", "换赛道", "更好平台"] as const).map((v) => (
            <button type="button" key={v} className={`xn-pill ${data.jobAppeal === v ? "active" : ""}`} onClick={() => update("jobAppeal", v)}>{v}</button>
          ))}</div>
        </label>
      </div>
    </fieldset>
  );
}

function CareerChangerBlock({ data, update }: { data: CareerChangerFields; update: (k: string, v: unknown) => void }) {
  return (
    <fieldset className="xn-form-section xn-special">
      <legend>转岗探索中 · 毕业 3 年以上</legend>
      <div className="xn-form-grid">
        <label><span>当前所在行业</span><input type="text" value={data.currentIndustry ?? ""} onChange={(e) => update("currentIndustry", e.target.value)} maxLength={30} /></label>
        <label><span>当前岗位名称</span><input type="text" value={data.currentJobTitle ?? ""} onChange={(e) => update("currentJobTitle", e.target.value)} maxLength={30} /></label>
        <label><span>总工作年限</span><input type="number" value={data.totalYears ?? ""} onChange={(e) => update("totalYears", e.target.value)} min={0} max={50} placeholder="年" /></label>
      </div>
      <label><span>当前岗位核心工作内容</span>
        <textarea value={data.currentJobDesc ?? ""} onChange={(e) => update("currentJobDesc", e.target.value.slice(0, 300))} maxLength={300} />
        <CharCount value={data.currentJobDesc ?? ""} max={300} />
      </label>
      <label><span>原有岗位掌握技能</span>
        <MultiTag tags={data.currentSkills ?? []} onChange={(v) => update("currentSkills", v)} />
      </label>
      <div className="xn-form-grid">
        <label><span>目标行业</span><input type="text" value={data.targetIndustry ?? ""} onChange={(e) => update("targetIndustry", e.target.value)} maxLength={30} /></label>
        <label><span>目标岗位</span><input type="text" value={data.targetJobTitle ?? ""} onChange={(e) => update("targetJobTitle", e.target.value)} maxLength={30} /></label>
      </div>
      <label><span>为转岗已做准备</span>
        <div className="xn-choice-pills">{(["自学课程", "做练习项目", "考取证书", "无准备"] as const).map((v) => (
          <button type="button" key={v} className={`xn-pill ${data.prepStatus === v ? "active" : ""}`} onClick={() => update("prepStatus", v)}>{v}</button>
        ))}</div>
      </label>
      <label><span>转岗原因</span>
        <textarea value={data.changeReason ?? ""} onChange={(e) => update("changeReason", e.target.value.slice(0, 200))} maxLength={200} />
        <CharCount value={data.changeReason ?? ""} max={200} />
      </label>
      <div className="xn-form-grid">
        <label><span>可接受的转型方式</span>
          <div className="xn-choice-pills">{(["直接跳槽", "先实习过渡", "兼职试手"] as const).map((v) => (
            <button type="button" key={v} className={`xn-pill ${data.transitionMethod === v ? "active" : ""}`} onClick={() => update("transitionMethod", v)}>{v}</button>
          ))}</div>
        </label>
        <label><span>期望薪资（提示：可接受降薪）</span><input type="text" value={data.salaryExpectation ?? ""} onChange={(e) => update("salaryExpectation", e.target.value)} maxLength={30} placeholder="例如：10-18k/月" /></label>
        <label><span>每周学习投入时长（小时）</span><input type="number" value={data.weeklyStudyHours ?? ""} onChange={(e) => update("weeklyStudyHours", e.target.value)} min={0} max={60} /></label>
        <label><span>对目标岗位了解程度</span>
          <div className="xn-choice-pills">{(["了解较多", "略有了解", "几乎不了解"] as const).map((v) => (
            <button type="button" key={v} className={`xn-pill ${data.targetAwareness === v ? "active" : ""}`} onClick={() => update("targetAwareness", v)}>{v}</button>
          ))}</div>
        </label>
      </div>
    </fieldset>
  );
}
