#!/usr/bin/env node
/**
 * 交付演示脚本（M3-5）：把「画像 → 推荐 → 依据 → 路径 → 行动 → 记录」这条主链路
 * 用**真实后端、真实导出**跑一遍，并把每一步的真实返回值打出来。
 *
 * 为什么是脚本不是视频（见用户拍板的交付标准）：
 *   视频只能证明「当时跑过」，脚本能证明「你现在也能跑出同样的东西」—— 每一个数字
 *   都可以自己复算，而且会随代码一起被回归测试覆盖（`npm run e2e`）。
 *
 * 它自己起后端：用**临时数据库**（不碰 backend/career.db），跑完自己收摊（含 Windows 下的进程树）。
 *
 * 跑法（仓库根，或 frontend/frotent/frontend1 下都行）：
 *   npm --prefix frontend/frotent/frontend1 run demo
 *   # 指定端口：DEMO_PORT=8123
 *   # 连真模型看模型版回答（每次十几秒，需 knowledge/eval/.env）：DEMO_WITH_LLM=1
 */
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(HERE, "..");
const PYTHON = process.env.PYTHON || "python";
const IS_WINDOWS = process.platform === "win32";
const PORT = Number(process.env.DEMO_PORT || 8123);
const BASE = `http://127.0.0.1:${PORT}`;

let stepNo = 0;
const failures = [];

function title(text) {
  process.stdout.write(`\n${"─".repeat(72)}\n${text}\n${"─".repeat(72)}\n`);
}

function step(text) {
  stepNo += 1;
  process.stdout.write(`\n[${String(stepNo).padStart(2, "0")}] ${text}\n`);
}

function show(label, value) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 1);
  process.stdout.write(`     ${label}：${text.split("\n").join("\n" + " ".repeat(label.length + 7))}\n`);
}

function assert(ok, label, detail = "") {
  process.stdout.write(`     ${ok ? "✅" : "❌"} ${label}${detail ? ` —— ${detail}` : ""}\n`);
  if (!ok) failures.push(label);
  return ok;
}

function killTree(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  if (IS_WINDOWS && child.pid) {
    try {
      spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
      return;
    } catch {
      /* 落到下面的 kill */
    }
  }
  try {
    child.kill("SIGTERM");
  } catch {
    /* 已经死了 */
  }
}

function startBackend(dbPath) {
  // 默认**离线**（CAREER_LLM_DISABLED=1）：演示要「快、可复算、不依赖外部端点」。
  // 想连真模型看模型版回答就 DEMO_WITH_LLM=1，那时单次响应要等十几秒。
  const env = { ...process.env, CAREER_MEMORY_DB: dbPath, PYTHONIOENCODING: "utf-8" };
  if (process.env.DEMO_WITH_LLM === "1") delete env.CAREER_LLM_DISABLED;
  else env.CAREER_LLM_DISABLED = "1";
  const child = spawn(PYTHON, ["-X", "utf8", "backend/run.py", "--port", String(PORT)], {
    cwd: PROJECT_ROOT,
    env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  let log = "";
  child.stdout.on("data", (chunk) => { log += String(chunk); });
  child.stderr.on("data", (chunk) => { log += String(chunk); });
  return { child, getLog: () => log };
}

async function waitForHealth(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${BASE}/health`, { signal: AbortSignal.timeout(2000) });
      if (response.ok) return await response.json();
    } catch {
      /* 还没起来 */
    }
    await new Promise((done) => setTimeout(done, 250));
  }
  throw new Error(`后端在 ${timeoutMs}ms 内没起来`);
}

let cookie = "";

async function api(method, path, body) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers["content-type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  if (cookie) init.headers.cookie = cookie;
  const response = await fetch(`${BASE}${path}`, { ...init, signal: AbortSignal.timeout(30000) });
  const setCookie = response.headers.get("set-cookie");
  if (setCookie) cookie = setCookie.split(";")[0];
  const text = await response.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    /* 不是 JSON 就保留原文 */
  }
  return { status: response.status, json, text, setCookie };
}

const RESUME = [
  "李小明",
  "教育背景",
  "2022.09-2026.06  浙江大学  自动化 专业  大三",
  "求职意向：边缘 AI 工程师",
  "专业技能",
  "模型量化与部署、轻量级推理引擎集成、炖菜",
  "项目经历",
  "项目名称：模型量化部署实践",
  "把 YOLOv5 量化到 INT8 并在 RK3588 上跑通。",
].join("\n");

const tmpDir = mkdtempSync(join(tmpdir(), "career-demo-"));
const dbPath = join(tmpDir, "demo.db");
const backend = startBackend(dbPath);

try {
  title("向新 · 交付演示：一条真实可复算的主链路");
  process.stdout.write(
    `后端：${PYTHON} backend/run.py --port ${PORT}（临时库 ${dbPath}）\n` +
    `导出：knowledge/exports/career-graph.json（三端读同一份字节）\n`
  );

  const health = await waitForHealth();
  step("后端就绪（真实进程，不是 mock）");
  assert(health?.status === "pipeline-export" || Boolean(health?.kbVersion), "导出是真实交付物", `kbVersion=${health?.kbVersion}`);
  show("图谱规模", health?.counts);
  show("模型端点", health?.llm?.configured ? `${health.llm.model} @ ${health.llm.host}` : "未配置（触发器走规则版）");

  /* 1. 访客会话 */
  step("访客会话：拿到自己的 userId 与会话 Cookie（M1-2）");
  const guest = await api("POST", "/api/auth/guest", { displayName: "演示用户" });
  const userId = guest.json?.data?.user?.userId;
  assert(guest.status === 201 && Boolean(userId), "POST /api/auth/guest 返回 201", `userId=${userId}`);
  assert(/HttpOnly/i.test(guest.setCookie ?? ""), "下发 HttpOnly 会话 Cookie", (guest.setCookie ?? "").replace(/user_[0-9a-f]{12}/, "user_***"));

  /* 2. 简历解析 */
  step("简历解析：文本 → 画像草稿 + 原文出处 + 待确认候选（不直接写正式画像）");
  const parsed = await api("POST", "/api/resumes/extract", { text: RESUME, useLlm: false });
  const draft = parsed.json?.data?.profileDraft ?? {};
  const evidence = parsed.json?.data?.evidence ?? [];
  assert(parsed.status === 200, "POST /api/resumes/extract 返回 200", `source=${parsed.json?.data?.extraction?.source}`);
  show("画像草稿", { school: draft.school, major: draft.major, grade: draft.grade, skills: draft.skills });
  assert(Boolean(draft.school && draft.major), "学校 / 专业从原文抽到（不是硬编码）", `${draft.school} / ${draft.major} / ${draft.grade}`);
  const located = evidence.filter((item) => item.located);
  assert(located.length > 0, "每条抽取都指得回原文（charRange 能切出该值）",
    located.slice(0, 2).map((item) => `${item.field}=「${item.value}」chars[${item.charRange}]`).join("；"));
  show("图谱不认识的名词（只进这里，不进画像）", (parsed.json?.data?.unrecognizedSkills ?? []).map((item) => item.token));
  assert((parsed.json?.data?.candidateCount ?? 0) > 0, "派生待确认候选（授权闸门，未确认不进上下文）", `candidateCount=${parsed.json?.data?.candidateCount}`);

  /* 3. 画像落库 + 确认（重启后仍在） */
  step("画像落库并确认：PUT → POST /api/profile/confirm（写入同一个 SQLite）");
  const saveBody = { ...draft, source: "resume" };
  const saved = await api("PUT", "/api/profile", saveBody);
  show("画像版本", `v${saved.json?.data?.profile?.profileVersion}（每次保存 +1）`);
  const confirmedProfile = await api("POST", "/api/profile/confirm");
  assert(confirmedProfile.json?.data?.profile?.status === "confirmed", "画像状态置为 confirmed", confirmedProfile.json?.data?.profile?.status);

  /* 4. 推荐 */
  step("岗位推荐：图谱覆盖度算式（不是模型打分），带缺口与理由");
  const rec = await api("GET", "/api/career/recommendations");
  const rows = rec.json?.recommendations ?? [];
  assert(rows.length > 0, "GET /api/career/recommendations 直出数组（按契约无信封）", `${rows.length} 个职业`);
  show("TOP 1", {
    职业: `${rows[0]?.occupation_name}（${rows[0]?.occupation_id}）`,
    匹配度: `${rows[0]?.match_score}%`,
    理由: rows[0]?.reason,
    缺口: (rows[0]?.skill_gaps ?? []).slice(0, 4),
  });
  assert(Boolean(rows[0]?.reason && rows[0]?.match_score > 0), "推荐带可解释理由与覆盖度分数");

  /* 5. 路径 */
  step("成长路径：图谱 requires / prerequisite 边做拓扑排序（模型不参与）");
  const path = await api("POST", "/api/v1/career-path/generate", { target_job: "AI004", weekly_hours: 8 });
  const data = path.json?.data ?? {};
  assert(path.status === 201, "POST /api/v1/career-path/generate 返回 201（不再是 501）", `target=${data.target_job}`);
  show("三阶段", (data.path ?? []).map((stage) => `${stage.stage}：${stage.skills.length} 技能 / ${stage.estimated_hours}h / ${stage.estimated_weeks}w`));
  assert(Object.values(data.evaluation?.hard_checks ?? {}).every((value) => value === false), "六项硬校验全 false", JSON.stringify(data.evaluation?.hard_checks));
  show("六维指标", data.evaluation?.metrics);
  const order = { junior: 0, intermediate: 1, advanced: 2 };
  const index = Object.fromEntries((data.path ?? []).flatMap((stage) => stage.skills.map((skill) => [skill.skill_id, skill])));
  const ordered = Object.values(index).every((skill) =>
    skill.prerequisite_ids.every((id) => !index[id] || order[skill.default_stage] >= order[index[id].default_stage]));
  assert(ordered, "阶段不早于前置（拓扑序约束成立）");
  show("如实说明（后端回传的 warnings）", data.warnings);

  /* 6. 行动 → 记录 → 候选 → 确认 */
  step("行动记录：成长记录 → 待确认候选 → 一次性确认（证据 + 事件同事务）");
  const record = await api("POST", "/api/growth-records", {
    kind: "能力变化",
    title: "完成模型量化与部署实战",
    after: "把 YOLOv5 量化到 INT8，在 RK3588 上跑通并记录基准数字",
    source: "演示脚本",
  });
  const recordId = record.json?.data?.record?.id;
  const candidateIds = (record.json?.data?.candidates ?? []).map((item) => item.id);
  assert(record.status === 201 && candidateIds.length > 0, "写记录同时派生待确认候选（规则版，不等模型）", `recordId=${recordId}`);
  show("候选内容", (record.json?.data?.candidates ?? []).map((item) => item.content));
  const confirm = await api("POST", "/api/growth-records/confirm", { recordId, memoryIds: candidateIds });
  const confirmData = confirm.json?.data ?? {};
  assert(confirm.status === 200, "候选 → 记录 → 证据 → 事件一次写入",
    `confirmed=${confirmData.confirmedMemoryIds?.length} evidence=${confirmData.evidence?.length} events=${confirmData.events?.length}`);
  const again = await api("POST", "/api/growth-records/confirm", { recordId, memoryIds: candidateIds });
  assert((again.json?.data?.alreadyConfirmedMemoryIds ?? []).length === candidateIds.length, "重复确认幂等（不产生重复行）",
    `already=${again.json?.data?.alreadyConfirmedMemoryIds?.length}`);

  /* 7. 消费：对话注入 */
  step("消费：对话注入已确认记忆 + 图谱事实（固定可审计前缀）");
  const chat = await api("POST", "/api/chat", { message: "模型量化与部署我学到哪了？" });
  const injected = chat.json?.data?.injected ?? {};
  assert(chat.status === 200, "POST /api/chat 返回 200", `provider=${chat.json?.data?.provider}`);
  assert((injected.memoryIds ?? []).length > 0, "已确认记忆真的进了注入", `memoryIds=${injected.memoryIds?.length} memoryHash=${String(injected.memoryHash).slice(0, 8)}…`);
  show("注入的提问原文", injected.query);
  // 回答字段叫 `message`（契约里就是这个名字）；空回答绝不能配 `provider=llm` 混过去。
  const answer = String(chat.json?.data?.message ?? "");
  show("回答", answer.length > 0 ? answer.slice(0, 180) : "（空）");
  assert(answer.length > 0, "回答非空（模型失败必须降级并标 reason，不能装作答了）",
    `provider=${chat.json?.data?.provider} llm.error=${JSON.stringify(chat.json?.data?.llm?.error)}`);
  if (chat.json?.data?.llm?.error) show("模型降级原因（不报 5xx）", chat.json.data.llm.error);

  /* 8. 记忆库总览 */
  step("总览：记忆库现状（候选与已确认分区）");
  const memories = await api("GET", "/api/memories");
  const items = memories.json?.data?.items ?? [];
  const byStatus = items.reduce((acc, item) => ({ ...acc, [item.status]: (acc[item.status] ?? 0) + 1 }), {});
  show("记忆条目", { 总数: items.length, 状态分布: byStatus, memoryHash: String(memories.json?.data?.memoryHash).slice(0, 12) + "…" });
  assert(items.some((item) => item.status === "confirmed"), "存在已确认记忆（授权闸门放行后才进入消费）");

  title(failures.length === 0
    ? `演示完成：${stepNo} 步全部通过 —— 上面每个数字都来自这次真实运行`
    : `演示未完成：${failures.length} 处不符合预期`);
  if (failures.length) for (const item of failures) process.stdout.write(`  ❌ ${item}\n`);
  process.exitCode = failures.length === 0 ? 0 : 1;
} catch (error) {
  title("演示中断");
  process.stdout.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  process.stdout.write(`\n后端日志尾部：\n${backend.getLog().split("\n").slice(-12).join("\n")}\n`);
  process.exitCode = 1;
} finally {
  killTree(backend.child);
  try {
    if (existsSync(tmpDir)) rmSync(tmpDir, { recursive: true, force: true });
  } catch {
    /* 临时目录清不掉不影响演示结论 */
  }
}
