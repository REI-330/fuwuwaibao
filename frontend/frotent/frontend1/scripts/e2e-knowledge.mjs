#!/usr/bin/env node
/**
 * 知识库端到端测试：一条命令把「原始快照 → 分块 → 图谱 → 导出 → 三端消费」整条链跑一遍。
 *
 *   npm run e2e                  # 全链（含前端 dev server）
 *   npm run e2e -- --no-frontend # 只跑离线 + 后端 + MCP
 *   npm run e2e -- --with-suites  # 再带上既有 pytest / npm test / MCP verify
 *   npm run e2e:llm              # 再验模型接通性（调真实 LLM 端点，单次 10–40 s）
 *
 * 为什么要有它：仓库此前只有**分层**测试（后端 pytest 16 项、前端 node:test 12 项、
 * MCP verify 14+25 项、评测脚本若干）。分层测试各自绿，不代表整条链是通的 ——
 * 比如「导出里 226 条边」和「MCP 返回 226 条边」是两次独立的断言，没有任何一处
 * 断言过它们**读的是同一份字节**。这个脚本补的就是这一层：
 *
 *   阶段 A  产物自洽      导出 ↔ chunks.jsonl ↔ graph.json ↔ search-index ↔ raw 快照 sha256
 *   阶段 B  构建闸门可复跑 09/10/11 重跑一次，语义投影必须逐字不变（易变字段白名单见 VOLATILE_KEY）
 *   阶段 C  检索闸门      现役 BM25 在 dev 题集上的命中数不得低于钉住的基线
 *   阶段 D  后端 HTTP     真起 python 进程，走契约接口（含 /api/chat 的记忆注入、
 *                         /api/growth-records 的记录→候选、/api/resumes/extract 的
 *                         简历→画像草稿+候选、/api/v1/interviews 的模拟面试、
 *                         /api/v1/cross-role 的跨岗位沟通训练、/api/tasks 的任务实践；
 *                         本阶段显式关掉模型，所以钉住的是"没有模型也必须能用"的规则版路径）
 *   阶段 E  MCP HTTP      真起 mcp/http.ts 进程，三个工具各真调一次
 *   阶段 F  跨端同源      后端 /health、MCP dataVersion、导出文件三方版本与计数必须一致，
 *                         MCP 返回的每条 citation 都要能在导出里指回真实 chunk
 *   阶段 G  前端 dev server（可跳过）SSR 页面能出 HTML，未实现路由的现状被显式记录
 *   阶段 H  既有套件（可跳过）pytest / npm test / MCP verify / 题集硬校验
 *   阶段 I  模型接通性（--with-llm，可跳过）真调端点：记忆触发器的模型版
 *                         （generatedBy/generatedByModel）+ 真实对话（provider/injected），
 *                         降级时必须有原因
 *
 * 退出码 0 = 全过；1 = 至少一条失败（失败项打在 stderr 与 evidence/knowledge-e2e.json）。
 * 证据落盘：evidence/knowledge-e2e.json（含每条断言、耗时、原始响应片段）。
 *
 * 只依赖：node ≥ 22、python（后端）、本机 node_modules（MCP 用 tsx 起 TS）。
 * 不依赖 LLM / 嵌入服务 / Docker —— 需要外部服务的那几档不在本脚本里（见 knowledge/README.md）。
 */
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import process from "node:process";

/* ------------------------------------------------------------------ *
 * 0. 路径 / 开关 / 钉住的基线
 * ------------------------------------------------------------------ */

const HERE = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(HERE, ".."); // frontend/frotent/frontend1
const REPO_ROOT = resolve(PROJECT_ROOT, "..", "..", "..");
const KNOWLEDGE = resolve(REPO_ROOT, "knowledge");
const EVIDENCE_DIR = resolve(PROJECT_ROOT, "evidence");
const EVIDENCE_PATH = resolve(EVIDENCE_DIR, "knowledge-e2e.json");

const FLAGS = new Set(process.argv.slice(2).filter(arg => arg.startsWith("--")));
const WITH_FRONTEND = !FLAGS.has("--no-frontend");
const WITH_SUITES = FLAGS.has("--with-suites");
/* 模型相关验证默认不跑：它依赖外部端点、单次要 10–40 s，不能拖累"5 秒全绿"的默认体验。
   要验证「模型是否真的接上了」就显式加 --with-llm。 */
const WITH_LLM = FLAGS.has("--with-llm");

const PYTHON = process.env.PYTHON || "python";
const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT || 8123);
const MCP_PORT = Number(process.env.E2E_MCP_PORT || 8798);
const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT || 3111);
const LLM_PORT = Number(process.env.E2E_LLM_PORT || 8125);

/**
 * 钉住的基线。**都必须有出处**，不接受「业界惯例」这类依据：
 * 数字变了就说明语料或链路变了，要么修回归，要么连同本文档一起更新。
 */
const BASELINE = {
  /** 2026-09-29 本机实测：node knowledge/pipeline/bm25-run.mjs --questions knowledge/evaluations/questions-dev.json */
  bm25Dev: { hits: 25, total: 34, precisionAt3: 0.3529 },
  /** 交叉参考（题集不同，不可与上一行比大小）：node knowledge/pipeline/bm25-run.mjs → 10/14 */
  bm25V2: { hits: 10, total: 14 },
  /** knowledge/README.md「数据边界」一节声明的口径：25 份快照，逐字节可复现 20 份 */
  rawSnapshots: 25,
  rawSha256Matched: 20,
  /** 实测就是这 5 份对不上，值为「实际字节 − manifest 记录字节」 */
  rawMismatch: { S15: -230, S18: -1085, S19: -10, S20: -2400, S22: -1 },
  /** 09 步硬约束条数（14 项校验里 13 项是 hard） */
  hardChecks: "硬约束 13/13",
};

/** 重跑管线时必须忽略的易变字段：时间戳、耗时、运行次数。除此之外任何差异都算回归。 */
const VOLATILE_KEY = /^(generatedAt|updatedAt|startedAt|finishedAt|durationMs|elapsedMs|runCount)$/;

/* ------------------------------------------------------------------ *
 * 1. 断言与流程骨架
 * ------------------------------------------------------------------ */

const checks = [];
const warnings = [];
const metrics = {};
const stages = [];

function check(stageId, label, ok, detail = "") {
  const record = { stage: stageId, label, ok: Boolean(ok), detail: String(detail ?? "") };
  checks.push(record);
  console.log(`  [${record.ok ? "PASS" : "FAIL"}] ${label}${detail ? ` — ${detail}` : ""}`);
  return record.ok;
}

function warn(stageId, label, detail = "") {
  warnings.push({ stage: stageId, label, detail: String(detail ?? "") });
  console.log(`  [WARN] ${label}${detail ? ` — ${detail}` : ""}`);
}

function info(line) {
  console.log(`  · ${line}`);
}

/** 跑一个阶段；阶段内部抛错会被记下来并继续跑后面的阶段（一次跑完看到全貌）。 */
async function stage(id, title, fn) {
  const startedAt = Date.now();
  console.log(`\n── ${id} ${title} ──`);
  const before = checks.length;
  let failure = null;
  try {
    await fn();
  } catch (error) {
    failure = error;
    check(id, `${title}：阶段本身不能抛异常`, false, error?.message ?? String(error));
  }
  const stageChecks = checks.slice(before);
  const record = {
    id,
    title,
    durationMs: Date.now() - startedAt,
    checks: stageChecks.length,
    failed: stageChecks.filter(item => !item.ok).length,
    error: failure ? String(failure?.stack ?? failure) : null,
  };
  stages.push(record);
  console.log(`  └ ${record.checks} 项断言，失败 ${record.failed}，耗时 ${record.durationMs} ms`);
  return record;
}

const IS_WINDOWS = process.platform === "win32";

/** 起一个前台子进程；返回句柄与「进程结束后才能读到」的输出累积器。 */
function startProcess(command, args, options = {}) {
  const child = spawn(command, args, {
    cwd: options.cwd ?? PROJECT_ROOT,
    env: { ...process.env, ...(options.env ?? {}) },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  const log = { stdout: "", stderr: "" };
  child.stdout?.on("data", chunk => {
    log.stdout += chunk.toString("utf8");
  });
  child.stderr?.on("data", chunk => {
    log.stderr += chunk.toString("utf8");
  });
  return { child, log };
}

/** Windows 下必须连同子进程一起杀，否则 python/node 会挂在后台占端口。 */
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

/** 跑一条命令并等它结束（用于离线管线与既有测试套件）。 */
function runToCompletion(command, args, options = {}) {
  return new Promise(resolvePromise => {
    const startedAt = Date.now();
    const child = spawn(command, args, {
      cwd: options.cwd ?? REPO_ROOT,
      env: { ...process.env, ...(options.env ?? {}) },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", chunk => {
      stdout += chunk.toString("utf8");
    });
    child.stderr?.on("data", chunk => {
      stderr += chunk.toString("utf8");
    });
    const timeout = setTimeout(() => {
      killTree(child);
      resolvePromise({ code: -1, stdout, stderr, ms: Date.now() - startedAt, timedOut: true });
    }, options.timeoutMs ?? 300000);
    child.on("error", error => {
      clearTimeout(timeout);
      resolvePromise({ code: -1, stdout, stderr: `${stderr}\n${error.message}`, ms: Date.now() - startedAt, spawnError: true });
    });
    child.on("close", code => {
      clearTimeout(timeout);
      resolvePromise({ code, stdout, stderr, ms: Date.now() - startedAt });
    });
  });
}

async function waitForHttp(url, { attempts = 60, intervalMs = 250, timeoutMs = 2000 } = {}) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
      if (response.ok) return true;
    } catch {
      /* 还没起来 */
    }
    await new Promise(resolvePromise => setTimeout(resolvePromise, intervalMs));
  }
  return false;
}

async function httpJson(url, init = {}) {
  const response = await fetch(url, { ...init, signal: AbortSignal.timeout(init.timeoutMs ?? 30000) });
  const text = await response.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    /* 不是 JSON，保留 text */
  }
  return { status: response.status, json, text, headers: response.headers };
}

const readJson = path => JSON.parse(readFileSync(path, "utf8"));
const readJsonl = path =>
  readFileSync(path, "utf8")
    .split("\n")
    .filter(line => line.trim().length > 0)
    .map(line => JSON.parse(line));
const sha256File = path => createHash("sha256").update(readFileSync(path)).digest("hex");

/* ---- 语义投影：剥掉时间戳/耗时后做严格比对，用来验证「重跑只改时间戳」 ---- */

function stripVolatile(value) {
  if (Array.isArray(value)) return value.map(stripVolatile);
  if (value && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value)) {
      if (VOLATILE_KEY.test(key)) continue;
      out[key] = stripVolatile(value[key]);
    }
    return out;
  }
  return value;
}

function firstDiff(a, b, path = "$") {
  if (a === b) return null;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return `${path}.length: ${a.length} → ${b.length}`;
    for (let index = 0; index < a.length; index += 1) {
      const diff = firstDiff(a[index], b[index], `${path}[${index}]`);
      if (diff) return diff;
    }
    return null;
  }
  if (a && b && typeof a === "object" && typeof b === "object") {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const key of keys) {
      if (VOLATILE_KEY.test(key)) continue;
      const diff = firstDiff(a[key], b[key], `${path}.${key}`);
      if (diff) return diff;
    }
    return null;
  }
  return `${path}: ${JSON.stringify(a)} → ${JSON.stringify(b)}`;
}

/** 只忽略「N ms」与生成时间行，其余逐字比对。 */
function normalizeMarkdown(text) {
  return text
    .replace(/^.*生成时间：.*$/gm, "生成时间：<volatile>")
    .replace(/\d+(\.\d+)?\s*ms\b/g, "<n> ms")
    .replace(/\r\n/g, "\n");
}

/* ------------------------------------------------------------------ *
 * 2. 阶段 A：产物自洽（纯读盘）
 * ------------------------------------------------------------------ */

/** 三个阶段共用：读一次导出，记下数组长度（阶段 B 会重写导出，之后要重新读）。 */
function loadExport(context) {
  const exportPath = join(KNOWLEDGE, "exports", "career-graph.json");
  context.exportData = readJson(exportPath);
  context.arrayCounts = {
    sources: (context.exportData.sources ?? []).length,
    chunks: (context.exportData.chunks ?? []).length,
    nodes: (context.exportData.nodes ?? []).length,
    edges: (context.exportData.edges ?? []).length,
    wikiPages: (context.exportData.wikiPages ?? []).length,
    evaluationQuestions: (context.exportData.evaluationQuestions ?? []).length,
  };
  return context.exportData;
}

function stageA() {
  const exportPath = join(KNOWLEDGE, "exports", "career-graph.json");
  const chunksPath = join(KNOWLEDGE, "chunks", "chunks.jsonl");
  const graphPath = join(KNOWLEDGE, "graph", "graph.json");
  const indexPath = join(KNOWLEDGE, "graph", "search-index.json");
  const manifestPath = join(KNOWLEDGE, "raw", "manifest.json");

  check("A", "导出文件存在", existsSync(exportPath), exportPath);
  const exportData = readJson(exportPath);
  const meta = exportData.meta ?? {};
  check("A", "导出是第 11 步真实交付物（status=pipeline-export）", meta.status === "pipeline-export", String(meta.status));
  metrics.kbVersion = meta.kbVersion;
  metrics.graphVersion = meta.graphVersion;

  const counts = meta.counts ?? {};
  const arrayCounts = {
    sources: (exportData.sources ?? []).length,
    chunks: (exportData.chunks ?? []).length,
    nodes: (exportData.nodes ?? []).length,
    edges: (exportData.edges ?? []).length,
    wikiPages: (exportData.wikiPages ?? []).length,
    evaluationQuestions: (exportData.evaluationQuestions ?? []).length,
  };
  metrics.counts = arrayCounts;
  check(
    "A",
    "meta.counts 与各数组长度逐项一致",
    Object.entries(arrayCounts).every(([key, value]) => counts[key] === value),
    JSON.stringify(arrayCounts)
  );

  const chunks = readJsonl(chunksPath);
  check("A", "chunks.jsonl 段数 = 导出 chunks", chunks.length === arrayCounts.chunks, `${chunks.length} vs ${arrayCounts.chunks}`);
  const chunkIds = new Set(chunks.map(chunk => chunk.chunkId));
  check("A", "chunkId 无重复", chunkIds.size === chunks.length, `${chunkIds.size}/${chunks.length}`);

  const graph = readJson(graphPath);
  check(
    "A",
    "graph.json 与导出同源（节点/边数一致）",
    (graph.nodes ?? []).length === arrayCounts.nodes && (graph.edges ?? []).length === arrayCounts.edges,
    `graph ${(graph.nodes ?? []).length}/${(graph.edges ?? []).length}`
  );

  const searchIndex = readJson(indexPath);
  check(
    "A",
    "search-index.json 覆盖全部节点与 chunk",
    searchIndex.counts?.nodeDocs === arrayCounts.nodes && searchIndex.counts?.chunkDocs === arrayCounts.chunks,
    JSON.stringify(searchIndex.counts)
  );

  const nodeIds = new Set((exportData.nodes ?? []).map(node => node.id));
  const danglingEndpoints = [];
  for (const edge of exportData.edges ?? []) {
    for (const endpoint of [edge.from, edge.to]) {
      if (nodeIds.has(endpoint) || String(endpoint).startsWith("chunk:")) continue;
      danglingEndpoints.push(`${edge.id}:${endpoint}`);
    }
  }
  check("A", "每条边的两端都能解析（节点 id 或 chunk: 引用）", danglingEndpoints.length === 0, danglingEndpoints.slice(0, 5).join(", "));

  const unresolvedLinks = new Set();
  for (const page of exportData.wikiPages ?? []) {
    for (const link of page.internalLinks ?? []) if (!nodeIds.has(link)) unresolvedLinks.add(link);
  }
  check("A", "wiki 内链全部指向真实节点", unresolvedLinks.size === 0, [...unresolvedLinks].slice(0, 5).join(", "));

  const sourceIds = new Set((exportData.sources ?? []).map(source => source.sourceId));
  const unknownSource = (exportData.chunks ?? []).filter(chunk => !sourceIds.has(chunk.sourceId));
  check("A", "每段 chunk 都能指回已登记来源", unknownSource.length === 0, `${unknownSource.length} 段无来源`);
  const registeredChunks = (exportData.sources ?? []).reduce((sum, source) => sum + (source.chunkCount ?? 0), 0);
  check(
    "A",
    "来源登记的 chunkCount 之和 = 导出 chunks",
    registeredChunks === arrayCounts.chunks,
    `${registeredChunks} vs ${arrayCounts.chunks}`
  );

  /* ---- raw 快照逐字节可复现性（README 已如实声明只有 20/25） ---- */
  const manifest = readJson(manifestPath);
  const documents = manifest.documents ?? [];
  check("A", `raw 快照登记份数为 ${BASELINE.rawSnapshots}`, documents.length === BASELINE.rawSnapshots, String(documents.length));
  check("A", "抓取无失败（fetchFailed=0，failures 为空）", manifest.counts?.fetchFailed === 0 && (manifest.failures ?? []).length === 0, JSON.stringify(manifest.counts?.fetchFailed));

  const matched = [];
  const mismatched = {};
  for (const document of documents) {
    const snapshot = join(KNOWLEDGE, "raw", `${document.sourceId}.html`);
    if (!existsSync(snapshot)) {
      mismatched[document.sourceId] = "MISSING";
      continue;
    }
    const actual = sha256File(snapshot);
    if (actual === document.sha256) matched.push(document.sourceId);
    else mismatched[document.sourceId] = readFileSync(snapshot).length - (document.bytes ?? 0);
  }
  metrics.rawSha256 = { matched: matched.length, total: documents.length, mismatched };
  check(
    "A",
    `逐字节可复现份数 = ${BASELINE.rawSha256Matched}/${BASELINE.rawSnapshots}（README 声明的口径）`,
    matched.length === BASELINE.rawSha256Matched,
    `实测 ${matched.length}/${documents.length}`
  );
  const expectedMismatch = Object.keys(BASELINE.rawMismatch).sort();
  const actualMismatch = Object.keys(mismatched).sort();
  check(
    "A",
    "对不上的就是已知那 5 份，没有新增",
    JSON.stringify(expectedMismatch) === JSON.stringify(actualMismatch),
    `实测 ${actualMismatch.join(",")}`
  );
  check(
    "A",
    "5 份的字节差与 README 记录一致（-230/-1085/-10/-2400/-1）",
    Object.entries(BASELINE.rawMismatch).every(([sourceId, delta]) => mismatched[sourceId] === delta),
    Object.entries(mismatched)
      .map(([sourceId, delta]) => `${sourceId}:${delta}`)
      .join(" ")
  );

  const gitattributes = readFileSync(join(REPO_ROOT, ".gitattributes"), "utf8");
  check(
    "A",
    ".gitattributes 对 knowledge/raw/** 关闭行尾转换（否则 sha256 换了机器就变）",
    /knowledge\/raw\/\*\*\s+-text/.test(gitattributes),
    "缺少 -text 规则"
  );
}

/* ------------------------------------------------------------------ *
 * 3. 阶段 B：构建闸门可复跑（09/10/11）
 * ------------------------------------------------------------------ */

/** 会被 09/10/11 重写的产物：JSON 走语义投影，markdown 走正则归一化。 */
const PIPELINE_ARTIFACTS = [
  { path: join(KNOWLEDGE, "evidence", "integrity-report.json"), kind: "json" },
  { path: join(KNOWLEDGE, "evidence", "pipeline-log.json"), kind: "json" },
  { path: join(KNOWLEDGE, "graph", "graph.json"), kind: "json" },
  { path: join(KNOWLEDGE, "graph", "search-index.json"), kind: "json" },
  { path: join(KNOWLEDGE, "exports", "career-graph.json"), kind: "json" },
  { path: join(KNOWLEDGE, "evaluations", "results.json"), kind: "json" },
  { path: join(KNOWLEDGE, "evaluations", "report.md"), kind: "markdown" },
];

function snapshotArtifacts() {
  const snapshot = {};
  for (const artifact of PIPELINE_ARTIFACTS) {
    if (!existsSync(artifact.path)) continue;
    const raw = readFileSync(artifact.path, "utf8");
    snapshot[artifact.path] = {
      raw,
      projection: artifact.kind === "json" ? stripVolatile(JSON.parse(raw)) : normalizeMarkdown(raw),
    };
  }
  return snapshot;
}

async function stageB() {
  const before = snapshotArtifacts();

  const gate = await runToCompletion("node", ["knowledge/pipeline/09-integrity-check.mjs"]);
  const gateOutput = `${gate.stdout}\n${gate.stderr}`;
  check("B", "09 完整性校验退出码 0", gate.code === 0, `exit=${gate.code}`);
  check("B", `09 报出「${BASELINE.hardChecks}」`, gateOutput.includes(BASELINE.hardChecks), gateOutput.split("\n").find(line => line.includes("硬约束"))?.trim() ?? "");
  metrics.hardChecks = gateOutput.split("\n").find(line => line.includes("硬约束"))?.trim() ?? "";

  const layout = await runToCompletion("node", ["knowledge/pipeline/10-layout-index.mjs"]);
  check("B", "10 布局与索引退出码 0", layout.code === 0, `exit=${layout.code}`);

  const exported = await runToCompletion("node", ["knowledge/pipeline/11-export-eval.mjs"]);
  check("B", "11 导出与评测退出码 0", exported.code === 0, `exit=${exported.code}`);

  const after = snapshotArtifacts();
  const semanticDiffs = [];
  const rawChanged = [];
  const volatileOnly = [];
  for (const artifact of PIPELINE_ARTIFACTS) {
    const key = artifact.path;
    if (!(key in before) || !(key in after)) continue;
    const short = key.replace(REPO_ROOT, ".");
    if (before[key].raw !== after[key].raw) rawChanged.push(short);
    const diff =
      artifact.kind === "json"
        ? firstDiff(before[key].projection, after[key].projection, "$")
        : before[key].projection === after[key].projection
          ? null
          : "markdown 正文（非耗时行）有差异";
    if (diff) semanticDiffs.push(`${short} → ${diff}`);
    else if (before[key].raw !== after[key].raw) volatileOnly.push(short);
  }
  check(
    "B",
    "重跑 09–11 后，语义投影逐字不变（只允许时间戳/耗时变化）",
    semanticDiffs.length === 0,
    semanticDiffs.slice(0, 3).join(" | ")
  );
  metrics.pipelineRerun = { rawChanged, volatileOnly, semanticDiffs };
  info(`本次重跑改写了 ${rawChanged.length} 份产物；语义投影全部未变`);
  if (semanticDiffs.length === 0 && rawChanged.length > 0) {
    warn(
      "B",
      "口径提醒：重跑 09–11 改写的文件不止「只改 generatedAt」那三份",
      `本次改写：${rawChanged.map(item => item.split(/[\\/]/).slice(-2).join("/")).join(" ")}；` +
        "其中 evaluations/results.json、evaluations/report.md、evidence/pipeline-log.json 还含耗时/运行次数（每跑一次都变），" +
        "所以「产物完全确定性」只对图谱类产物成立 —— 本阶段按语义投影（剥掉时间戳与耗时）比对，语义字段必须逐字不变"
    );
  }
}

/* ------------------------------------------------------------------ *
 * 4. 阶段 C：检索闸门（现役 BM25，离线）
 * ------------------------------------------------------------------ */

async function stageC() {
  const questions = join(KNOWLEDGE, "evaluations", "questions-dev.json");
  /* 落到系统临时目录：跑批产物不进仓库（仓库里那份 bm25-topk.json 是给评测用的另一件事）。 */
  const out = join(tmpdir(), `e2e-bm25-dev-${process.pid}.json`);
  const run = await runToCompletion("node", [
    "knowledge/pipeline/bm25-run.mjs",
    "--questions",
    questions,
    "--out",
    out,
  ]);
  check("C", "BM25 跑批退出码 0", run.code === 0, `exit=${run.code}`);
  const line = run.stdout.split("\n").find(text => text.includes("命中"))?.trim() ?? "";
  const file = existsSync(out) ? readJson(out) : null;
  const hitRate = file?.metrics?.main?.hitRate ?? "";
  const [hits, total] = hitRate.split("/").map(Number);
  metrics.bm25Dev = { hitRate, meanPrecisionAtK: file?.metrics?.main?.meanPrecisionAtK, stdout: line };
  check("C", "跑批产物可解析出命中数", Number.isFinite(hits) && Number.isFinite(total), hitRate || line);
  check(
    "C",
    `dev 题集命中 ≥ 钉住的基线 ${BASELINE.bm25Dev.hits}/${BASELINE.bm25Dev.total}`,
    hits >= BASELINE.bm25Dev.hits,
    `实测 ${hitRate}`
  );
  const precision = Number(file?.metrics?.main?.meanPrecisionAtK);
  check(
    "C",
    `平均精确率@3 ≥ ${BASELINE.bm25Dev.precisionAt3}`,
    Number.isFinite(precision) && precision >= BASELINE.bm25Dev.precisionAt3 - 1e-6,
    `实测 ${precision}`
  );
  info(line);
}

/* ------------------------------------------------------------------ *
 * 5. 阶段 D：后端 HTTP（真进程）
 * ------------------------------------------------------------------ */

async function stageD(context) {
  const base = `http://127.0.0.1:${BACKEND_PORT}`;
  /* 记忆库落到临时文件：端到端跑不碰开发者的 backend/career.db，
     且每次都是干净的库，断言才可重复。
     `CAREER_LLM_DISABLED=1`：本机 knowledge/eval/.env 里有真 key，不关的话 /api/chat
     会真调端点（单次 10–40 s），默认 e2e 就不是"5 秒全绿"了。模型那条路在阶段 I
     （--with-llm）单独验；这里正好把「没有模型也必须能用」的规则版路径钉住。 */
  const memoryDb = join(tmpdir(), `e2e-memory-${process.pid}.db`);
  const started = startProcess(PYTHON, ["backend/run.py", "--port", String(BACKEND_PORT)], {
    cwd: PROJECT_ROOT,
    env: { CAREER_MEMORY_DB: memoryDb, CAREER_LLM_DISABLED: "1" },
  });
  try {
    const ready = await waitForHttp(`${base}/health`);
    check("D", `后端在 ${base} 就绪（真起 python 进程）`, ready);
    if (!ready) {
      warn("D", "后端未就绪，本阶段跳过", started.log.stderr.split("\n").slice(-3).join(" "));
      return;
    }

    const health = await httpJson(`${base}/health`);
    check("D", "/health 200", health.status === 200, String(health.status));
    check("D", "/health exportStatus=pipeline-export", health.json?.exportStatus === "pipeline-export", String(health.json?.exportStatus));
    context.backendHealth = health.json;
    check("D", "/health dataSource 指向同一份导出", String(health.json?.dataSource ?? "").includes("knowledge/exports/career-graph.json"), String(health.json?.dataSource));
    // 模型状态必须能在 /health 上看见（不配端点时更不能让人以为触发器在走模型）
    check("D", "/health 报出 LLM 配置状态（不含密钥）", typeof health.json?.llm?.configured === "boolean", JSON.stringify(health.json?.llm));
    check(
      "D",
      "/health 的 LLM 块不回显密钥",
      !JSON.stringify(health.json?.llm ?? {}).toLowerCase().includes("api_key"),
      JSON.stringify(health.json?.llm)
    );
    // 本阶段显式关了模型（见上面的 env）：接口必须如实报 false，而不是假装配了
    check("D", "显式 CAREER_LLM_DISABLED=1 时 /health 报 configured=false", health.json?.llm?.configured === false, JSON.stringify(health.json?.llm));

    const stats = await httpJson(`${base}/api/v1/catalog/stats`);
    const data = stats.json?.data ?? {};
    const exportData = context.exportData;
    const expectedOccupations = (exportData.nodes ?? []).filter(node => node.kind === "occupation").length;
    check("D", "通用响应包形状 {requestId,data,error}", stats.json && "requestId" in stats.json && "data" in stats.json && "error" in stats.json);
    check("D", `catalog/stats 职业数 = 导出 occupation 节点数（${expectedOccupations}）`, data.occupations === expectedOccupations, String(data.occupations));
    check("D", "catalog/stats 技能数 = 导出 skill 节点数", data.skills === (exportData.nodes ?? []).filter(node => node.kind === "skill").length, String(data.skills));
    check("D", "catalog/stats prerequisite 边数 = 导出 prerequisite 边数", data.prerequisites === (exportData.edges ?? []).filter(edge => edge.type === "prerequisite").length, String(data.prerequisites));

    const occupations = await httpJson(`${base}/api/v1/occupations`);
    const items = occupations.json?.data?.items ?? [];
    const exportOccupationIds = (exportData.nodes ?? []).filter(node => node.kind === "occupation").map(node => node.id.split(":")[1]).sort();
    check("D", "职业列表与导出的 occupation 节点一一对应", JSON.stringify(items.map(item => item.occupationId).sort()) === JSON.stringify(exportOccupationIds), items.map(item => item.occupationId).join(","));

    const detail = await httpJson(`${base}/api/v1/occupations/AI002`);
    const skills = detail.json?.data?.occupation?.skills ?? [];
    const requiresCountAI002 = (exportData.edges ?? []).filter(edge => edge.type === "requires" && edge.from === "occupation:AI002").length;
    check("D", "职业详情按 requires 边给出技能明细", skills.length === requiresCountAI002, `${skills.length} vs ${requiresCountAI002}`);
    check("D", "详情里的技能 id 都是导出的 skill 节点", skills.every(skill => (exportData.nodes ?? []).some(node => node.id === `skill:${skill.skillId}`)), skills.map(skill => skill.skillId).slice(0, 3).join(","));

    const missing = await httpJson(`${base}/api/v1/occupations/NOPE`);
    check("D", "未知职业 404 OCCUPATION_NOT_FOUND", missing.status === 404 && missing.json?.error?.code === "OCCUPATION_NOT_FOUND", `${missing.status} ${missing.json?.error?.code}`);

    /* ---- 画像 → 确认 → 推荐 这条真实链路 ---- */
    const profile = await httpJson(`${base}/api/profile`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ identity: "学生", skills: "Python, PyTorch, 模型量化", directions: "边缘AI", question: "我想做边缘 AI 工程师" }),
    });
    check("D", "PUT /api/profile 返回标准化画像", profile.status === 200 && profile.json?.data?.profile?.skills?.length >= 1, JSON.stringify(profile.json?.data?.profile?.skills ?? []));
    const confirmed = await httpJson(`${base}/api/profile/confirm`, { method: "POST" });
    check("D", "POST /api/profile/confirm 把画像置为 confirmed", confirmed.json?.data?.profile?.status === "confirmed", String(confirmed.json?.data?.profile?.status));

    /* ---- M1-2 访客会话：201 + HttpOnly Cookie；register/login 仍 501 ---- */
    const postJson = (body) => ({ method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    const guest = await httpJson(`${base}/api/auth/guest`, postJson({ displayName: "端到端访客" }));
    const guestUser = guest.json?.data?.user ?? {};
    const setCookie = guest.headers?.get?.("set-cookie") ?? "";
    check("D", "访客会话：POST /api/auth/guest 返回 201", guest.status === 201, String(guest.status));
    check("D", "访客会话：下发 HttpOnly 会话 Cookie", /career_session=user_[0-9a-f]{12}/.test(setCookie) && /HttpOnly/i.test(setCookie), setCookie.replace(/career_session=user_[0-9a-f]{12}/, "career_session=user_***"));
    check("D", "访客会话：返回 isGuest 用户，不落账号密码", guestUser.isGuest === true && typeof guestUser.userId === "string", JSON.stringify(guestUser));
    const guestSessionCookie = setCookie.split(";")[0];
    const guestProfile = await httpJson(`${base}/api/profile`, { headers: { cookie: guestSessionCookie } });
    check("D", "访客会话：带 Cookie 拿到的是自己的画像（与无 Cookie 的 user_local 隔离）", guestProfile.json?.data?.profile?.userId !== "user_local" && guestProfile.json?.data?.profile?.userId === guestUser.userId, `${guestProfile.json?.data?.profile?.userId} vs ${guestUser.userId}`);
    const forged = await httpJson(`${base}/api/profile`, { headers: { cookie: "career_session=user_deadbeefcaf" } });
    check("D", "访客会话：Cookie 值不合法时回落 user_local，不报 401", forged.status === 200 && forged.json?.data?.profile?.userId === "user_local", `${forged.status} ${forged.json?.data?.profile?.userId}`);
    for (const route of ["register", "login"]) {
      const res = await httpJson(`${base}/api/auth/${route}`, postJson({ email: "a@b.c", password: "12345678" }));
      check("D", `认证：/api/auth/${route} 仍是 501（本项目不存账号密码）`, res.status === 501 && res.json?.error?.code === "NOT_IMPLEMENTED", `${res.status} ${res.json?.error?.code}`);
    }

    const recommendations = await httpJson(`${base}/api/career/recommendations`);
    const rows = recommendations.json?.recommendations ?? [];
    check("D", "推荐接口按契约直出 recommendations（无信封）", Array.isArray(recommendations.json?.recommendations), Object.keys(recommendations.json ?? {}).join(","));
    check("D", "推荐条数 = 职业数", rows.length === expectedOccupations, String(rows.length));
    check("D", "有了画像后至少一条匹配度 > 0", rows.some(row => row.match_score > 0), rows.map(row => `${row.occupation_id}:${row.match_score}`).join(" "));
    check("D", "推荐里的职业 id 都在导出里", rows.every(row => exportOccupationIds.includes(row.occupation_id)), rows.map(row => row.occupation_id).join(","));
    context.backendRecommendations = rows;

    const notImplemented = await httpJson(`${base}/api/auth/login`, { method: "POST" });
    check("D", "未实现接口明确 501（不假装可用）", notImplemented.status === 501 && notImplemented.json?.error?.code === "NOT_IMPLEMENTED", `${notImplemented.status} ${notImplemented.json?.error?.code}`);
    const unknown = await httpJson(`${base}/api/does-not-exist`);
    check("D", "未知路由 404（与 501 区分开）", unknown.status === 404, String(unknown.status));

    /* ---- 职业匹配：曾经回 501（理由写的是「需要招聘数据源」，该归因已纠正）。
            排序依据全在图谱（requires 边带 importance/targetLevel）与已确认画像里，
            所以这一段**不需要任何外部数据源或凭证**，离线即可复现。 ---- */
    const matchGenerate = await httpJson(`${base}/api/career-matches/generate`, { method: "POST" });
    const matchRun = matchGenerate.json?.data?.run ?? {};
    const matchItems = matchRun.items ?? [];
    check("D", "职业匹配：不再 501，生成返回 201 且带 run", matchGenerate.status === 201 && typeof matchRun.runId === "string", `${matchGenerate.status} ${matchRun.runId}`);
    check("D", "职业匹配：候选职业数 = 图谱职业数", matchItems.length === expectedOccupations, String(matchItems.length));
    check("D",
      "职业匹配：按分数降序且 rank 连续",
      matchItems.every((item, index) => item.rank === index + 1)
        && matchItems.every((item, index) => index === 0 || matchItems[index - 1].matchScore >= item.matchScore),
      matchItems.map(item => `${item.rank}:${item.matchScore}`).join(" "));
    check("D",
      "职业匹配：每条理由都带出处（用户能复核）",
      matchItems.length > 0 && matchItems.every(item => (item.reasons ?? []).length > 0 && item.reasons.every(reason => reason.source && reason.detail)),
      matchItems.map(item => (item.reasons ?? []).length).join(","));
    check("D",
      "职业匹配：缺口来自图谱 requires 边（带 skillId / targetLevel / importance 刻度）",
      matchItems.every(item => (item.skillGaps ?? []).every(gap => gap.skillId && gap.targetLevel >= 1 && gap.targetLevel <= 5 && gap.importance >= 1 && gap.importance <= 5)),
      "");
    check("D", "职业匹配：职业 id 都在导出里", matchItems.every(item => exportOccupationIds.includes(item.occupationId)), matchItems.map(item => item.occupationId).join(","));

    const matchCurrent = await httpJson(`${base}/api/career-matches/current`);
    check("D", "职业匹配：生成后可读取（同一份 run）", matchCurrent.status === 200 && matchCurrent.json?.data?.run?.runId === matchRun.runId, `${matchCurrent.status}`);

    const matchSelect = await httpJson(`${base}/api/career-matches/select`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ occupationId: matchItems[0]?.occupationId }),
    });
    check("D", "职业匹配：选择目标职业落库", matchSelect.status === 200 && matchSelect.json?.data?.target?.occupationId === matchItems[0]?.occupationId, `${matchSelect.status}`);
    const badSelect = await httpJson(`${base}/api/career-matches/select`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ occupationId: "AI999" }),
    });
    check("D", "职业匹配：不存在的职业被拒 404（不静默接受）", badSelect.status === 404, String(badSelect.status));

    /* ---- M1-4 路径引擎：由图谱 requires / prerequisite 边算出，模型不参与 ---- */
    const pathBody = { target_job: "AI001", weekly_hours: 10 };
    const careerPath = await httpJson(`${base}/api/v1/career-path/generate`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(pathBody),
    });
    const pathData = careerPath.json?.data ?? {};
    check("D", "路径引擎已实现：POST 返回 201 而不是 501", careerPath.status === 201, String(careerPath.status));
    check("D", "路径覆盖 junior/intermediate/advanced 三阶段", (pathData.path ?? []).map(s => s.stage).join(",") === "junior,intermediate,advanced", (pathData.path ?? []).map(s => s.stage).join(","));
    check("D", "六项硬校验全 false（无环 / 等级有效 / 先修顺序正确）", Object.values(pathData.evaluation?.hard_checks ?? {}).every(v => v === false), JSON.stringify(pathData.evaluation?.hard_checks));
    check("D", "阶段不早于前置：拓扑序约束成立", (() => {
      const order = { junior: 0, intermediate: 1, advanced: 2 };
      const byId = Object.fromEntries((pathData.path ?? []).flatMap(s => s.skills.map(k => [k.skill_id, k])));
      return Object.values(byId).every(k => k.prerequisite_ids.every(p => !byId[p] || order[k.default_stage] >= order[byId[p].default_stage]));
    })(), "order");
    check("D", "同输入两次输出完全相同（结构确定，不含时钟字段）", JSON.stringify((await httpJson(`${base}/api/v1/career-path/generate`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(pathBody) })).json?.data) === JSON.stringify(pathData), "determinism");
    const pathMissing = await httpJson(`${base}/api/v1/career-path/generate`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ target_job: "AI999" }) });
    check("D", "路径引擎：未知职业 404 OCCUPATION_NOT_FOUND", pathMissing.status === 404 && pathMissing.json?.error?.code === "OCCUPATION_NOT_FOUND", `${pathMissing.status} ${pathMissing.json?.error?.code}`);
    check("D", "路径引擎：target_job_en 没有对应字段时留空、不臆造", pathData.target_job_en === "", String(pathData.target_job_en));
    check("D", "路径引擎如实回传 warnings（如工具归属只有职业级）", Array.isArray(pathData.warnings) && pathData.warnings.length > 0, JSON.stringify(pathData.warnings));

    /* ---- 对话：注入已确认记忆（本阶段无模型，走规则版；模型版在阶段 I） ---- */
    const chatInit = body => ({ method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    const firstMessage = "我该先补哪些技能？";
    const chatEmpty = await httpJson(`${base}/api/chat`, chatInit({ message: firstMessage }));
    const chatEmptyData = chatEmpty.json?.data ?? {};
    check("D", "POST /api/chat 200 且走通用响应包", chatEmpty.status === 200 && chatEmpty.json && "requestId" in chatEmpty.json && "data" in chatEmpty.json && "error" in chatEmpty.json, `${chatEmpty.status}`);
    check("D", "对话返回 message 与可复用的 conversationId", typeof chatEmptyData.message === "string" && chatEmptyData.message.length > 0 && String(chatEmptyData.conversationId ?? "").startsWith("conversation_"), String(chatEmptyData.conversationId));
    check("D", "没有模型时对话降级为规则版并如实标注", chatEmptyData.provider === "rule-based" && chatEmptyData.llm?.used === "rule-based", JSON.stringify(chatEmptyData.llm));
    check("D", "没有已确认记忆时不注入任何记忆（授权闸门在对话侧同样成立）", chatEmptyData.injected?.count === 0 && (chatEmptyData.injected?.memoryIds ?? []).length === 0, JSON.stringify(chatEmptyData.injected?.memoryIds));
    check("D", "检索用的提问就是用户原话（未被改写）", chatEmptyData.injected?.query === firstMessage, String(chatEmptyData.injected?.query));
    check("D", "规则版回答不是空串（没有模型也能用）", String(chatEmptyData.message).includes("规则版回答"), String(chatEmptyData.message).slice(0, 40));
    const followup = await httpJson(`${base}/api/chat`, chatInit({ message: "再具体一点", conversationId: chatEmptyData.conversationId }));
    check("D", "带上 conversationId 时同一会话被复用", followup.json?.data?.conversationId === chatEmptyData.conversationId, String(followup.json?.data?.conversationId));
    const badChat = await httpJson(`${base}/api/chat`, chatInit({}));
    check("D", "空消息被拒（400 INVALID_CHAT_MESSAGE）", badChat.status === 400 && badChat.json?.error?.code === "INVALID_CHAT_MESSAGE", `${badChat.status} ${badChat.json?.error?.code}`);

    /* ---- 记忆库全生命周期（候选闸门 → 确认 → 召回 → 消费 → 遗忘） ---- */
    const mem = `${base}/api/memories`;
    const jsonInit = (method, body) => ({
      method,
      headers: { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });

    // ① 写入：AI 观察先停在候选
    // 用「轻量级推理引擎集成」（skill:SK120，重要度 0.9、属于 AI004）作为被测事实：
    // 上面的画像只写了 Python / PyTorch / 模型量化，所以这条记忆确实能带来增量。
    const candidate = await httpJson(mem, jsonInit("POST", {
      category: "skill", content: "具备或正在学习：轻量级推理引擎集成", status: "candidate",
    }));
    const candidateId = candidate.json?.data?.item?.id;
    check("D", "记忆：候选写入返回 201", candidate.status === 201 && Boolean(candidateId), `${candidate.status} ${candidateId}`);
    check("D", "记忆：候选没有触发器（未确认不生成）", candidate.json?.data?.item?.triggersPending === true, String(candidate.json?.data?.item?.triggersPending));

    // ② 授权闸门：候选不进上下文、不进推荐
    const beforeContext = await httpJson(`${mem}/context?query=${encodeURIComponent("轻量级推理引擎集成")}`);
    check("D", "记忆：候选不进注入上下文（授权边界）", beforeContext.json?.data?.count === 0, String(beforeContext.json?.data?.count));
    const beforeHash = (await httpJson(`${base}/api/career/recommendations`)).json?.memory_hash ?? "";
    const beforeScores = Object.fromEntries(((await httpJson(`${base}/api/career/recommendations`)).json?.recommendations ?? []).map(row => [row.occupation_id, row.match_score]));

    // ③ 确认：那一刻生成触发器
    const confirmedItem = await httpJson(`${mem}/${candidateId}`, jsonInit("PATCH", { status: "confirmed" }));
    const triggers = confirmedItem.json?.data?.item?.triggers ?? [];
    check("D", "记忆：确认后立刻生成触发器（1–3 条）", triggers.length >= 1 && triggers.length <= 3, `${triggers.length} 条`);
    check("D", "记忆：触发器是规则版且带句式（generatedBy=rule-based）", triggers.every(t => t.generatedBy === "rule-based" && (t.activationPatterns ?? []).length === 3), JSON.stringify(triggers.map(t => t.concept)));

    // 生成器要能显式选、且如实回传这次走了哪条路（模型版在阶段 I 验证）
    const regenerate = await httpJson(`${mem}/${candidateId}/triggers?generator=rule-based`, { method: "POST" });
    check("D", "记忆：可显式选 generator=rule-based", regenerate.status === 200, `${regenerate.status}`);
    check(
      "D",
      "记忆：生成器如实回传 requested/used/model",
      regenerate.json?.data?.generator?.requested === "rule-based" && regenerate.json?.data?.generator?.used === "rule-based",
      JSON.stringify(regenerate.json?.data?.generator)
    );
    const badGenerator = await httpJson(`${mem}/${candidateId}/triggers?generator=magic`, { method: "POST" });
    check("D", "记忆：未知 generator 被拒（不静默当成 auto）", badGenerator.status === 400, `${badGenerator.status} ${badGenerator.json?.error?.code}`);

    // ④ 召回：persona 常驻 + 联想
    const targetMemory = await httpJson(mem, jsonInit("POST", { category: "career_target", content: "目标职业：边缘 AI 工程师" }));
    const targetMemoryId = targetMemory.json?.data?.item?.id;
    const recall = await httpJson(`${mem}/context?query=${encodeURIComponent("我轻量级推理引擎集成那块进展怎么样")}`);
    const contextData = recall.json?.data ?? {};
    check("D", "记忆：注入上下文含 persona 常驻与本次想起", (contextData.persona ?? []).length >= 1 && (contextData.recalled ?? []).length >= 1, `persona ${(contextData.persona ?? []).length} / recalled ${(contextData.recalled ?? []).length}`);
    check("D", "记忆：联想命中通道被标注（trigger:/word）", (contextData.recalled ?? []).every(entry => typeof entry.channel === "string" && entry.channel.length > 0), JSON.stringify((contextData.recalled ?? []).map(entry => entry.channel)));
    check("D", "记忆：注入预览不含未确认内容", !contextData.summaryText?.includes("candidate"), "summaryText 完整可展示");
    const irrelevant = await httpJson(`${mem}/context?query=${encodeURIComponent("量子计算芯片流片工艺")}`);
    check("D", "记忆：无关提问不硬塞记忆", (irrelevant.json?.data?.recalled ?? []).length === 0, `${(irrelevant.json?.data?.recalled ?? []).length} 条`);

    // ⑤ 消费：推荐被增强且逐条带证据
    const augmented = await httpJson(`${base}/api/career/recommendations`);
    const afterScores = Object.fromEntries((augmented.json?.recommendations ?? []).map(row => [row.occupation_id, row.match_score]));
    const evidence = (augmented.json?.recommendations ?? []).flatMap(row => row.confirmed_memory ?? []);
    check("D", "记忆：确认后 memory_hash 由空变为非空", beforeHash === "" && (augmented.json?.memory_hash ?? "") !== "", `${beforeHash} → ${augmented.json?.memory_hash}`);
    check("D", "记忆：推荐被真实增强（AI004 匹配度上升）", (afterScores.AI004 ?? 0) > (beforeScores.AI004 ?? 0), `AI004 ${beforeScores.AI004} → ${afterScores.AI004}`);
    check("D", "记忆：推荐结果逐条带 confirmed_memory 证据", evidence.length >= 1 && evidence.every(entry => entry.memoryId && entry.usedFor), JSON.stringify(evidence));

    // ⑤b 消费：**真实对话**注入（此前记忆只有预览，没有任何一条对话用到它）
    const chatWithMemory = await httpJson(`${base}/api/chat`, chatInit({ message: "我该先补哪些技能？" }));
    const chatData = chatWithMemory.json?.data ?? {};
    check("D", "对话：已确认记忆真的进了注入块", (chatData.injected?.count ?? 0) >= 1 && (chatData.injected?.memoryIds ?? []).includes(targetMemoryId), JSON.stringify(chatData.injected?.memoryIds));
    check("D", "对话：注入指纹与推荐接口的 memory_hash 一致（同一份已确认集合）", Boolean(chatData.injected?.memoryHash) && chatData.injected?.memoryHash === (augmented.json?.memory_hash ?? ""), `${chatData.injected?.memoryHash} vs ${augmented.json?.memory_hash}`);
    check("D", "对话：persona 常驻进了提示词（可审计的 summaryText）", String(chatData.injected?.summaryText ?? "").includes("边缘 AI 工程师"), JSON.stringify(chatData.injected?.summaryText ?? "").slice(0, 80));
    check("D", "对话：答案里真的出现了那条记忆（注入链路闭到用户可见文本）", String(chatData.message).includes("边缘 AI 工程师"), String(chatData.message).slice(0, 60));
    check("D", "对话：提示词模板可溯源", chatData.injected?.promptTemplate === "career-chat/v1", String(chatData.injected?.promptTemplate));

    // ⑥ 遗忘：删除即遗忘 + 推荐回落
    const deleted = await httpJson(`${mem}/${candidateId}`, { method: "DELETE" });
    check("D", "记忆：删除返回被删 id 与新指纹", deleted.status === 200 && deleted.json?.data?.deleted === candidateId, `${deleted.status} ${deleted.json?.data?.deleted}`);
    const afterDelete = await httpJson(`${base}/api/career/recommendations`);
    const afterDeleteScores = Object.fromEntries((afterDelete.json?.recommendations ?? []).map(row => [row.occupation_id, row.match_score]));
    check("D", "记忆：删除后推荐回落（遗忘是彻底的）", (afterDeleteScores.AI004 ?? 0) === (beforeScores.AI004 ?? 0), `AI004 ${afterScores.AI004} → ${afterDeleteScores.AI004}（基线 ${beforeScores.AI004}）`);
    const goneContext = await httpJson(`${mem}/context?query=${encodeURIComponent("轻量级推理引擎集成")}`);
    check("D", "记忆：删除后不再被想起", !(goneContext.json?.data?.summaryText ?? "").includes("轻量级推理引擎集成"), "注入预览已不含该记忆");

    /* ---- ⑦ 成长记录 → 待确认记忆候选（记忆库第二条写入通道） ---- */
    const growth = `${base}/api/growth-records`;
    const record = await httpJson(growth, jsonInit("POST", {
      kind: "任务行动", title: "完成「模型量化」任务", after: "把 YOLOv5 量化到 INT8", source: "用户提交的行动结果",
    }));
    const recordData = record.json?.data ?? {};
    const recordId = recordData.record?.id;
    check("D", "成长记录：写入返回 201 与记录 id", record.status === 201 && Boolean(recordId), `${record.status} ${recordId}`);
    check("D", "成长记录：派生的记忆全部停在候选（授权闸门）", (recordData.candidates ?? []).length >= 1 && recordData.candidates.every(item => item.status === "candidate" && item.sourceType === "growth_record"), JSON.stringify((recordData.candidates ?? []).map(item => item.status)));
    check("D", "成长记录：候选带可溯源的 sourceId（指回记录）", (recordData.candidates ?? []).every(item => String(item.sourceId).startsWith(`${recordId}:`)), JSON.stringify((recordData.candidates ?? []).map(item => item.sourceId)));
    check("D", "成长记录：候选内容来自图谱已知名词（规则版派生，不调模型）", (recordData.candidates ?? []).some(item => item.content === "具备或正在学习：模型量化与部署"), JSON.stringify((recordData.candidates ?? []).map(item => item.content)));
    const recordCandidateId = recordData.candidates?.[0]?.id;
    const recordContext = await httpJson(`${mem}/context?query=${encodeURIComponent("模型量化与部署")}`);
    check("D", "成长记录：候选不进注入上下文", !String(recordContext.json?.data?.summaryText ?? "").includes("模型量化与部署"), "候选未进入召回");

    const idempotent = await httpJson(growth, jsonInit("POST", { kind: "任务行动", title: "完成「模型量化」任务", after: "把 YOLOv5 量化到 INT8", recordId }));
    check("D", "成长记录：同一 recordId 重复提交是幂等的", idempotent.json?.data?.created === false && idempotent.json?.data?.record?.id === recordId, JSON.stringify(idempotent.json?.data?.note));

    const noTerm = await httpJson(growth, jsonInit("POST", { kind: "能力变化", title: "今天心情不错", after: "随便写写" }));
    check("D", "成长记录：认不出图谱名词时宁可不写候选", (noTerm.json?.data?.candidates ?? []).length === 0 && noTerm.json?.data?.note?.reason === "no_known_term", JSON.stringify(noTerm.json?.data?.note));

    await httpJson(`${mem}/${recordCandidateId}`, jsonInit("PATCH", { status: "confirmed" }));
    const afterRecordConfirm = await httpJson(`${mem}/context?query=${encodeURIComponent("模型量化与部署")}`);
    check("D", "成长记录：候选确认后才进召回", (afterRecordConfirm.json?.data?.recalled ?? []).some(entry => entry.memoryId === recordCandidateId), JSON.stringify((afterRecordConfirm.json?.data?.recalled ?? []).map(entry => entry.memoryId)));

    const removed = await httpJson(`${growth}/${recordId}`, { method: "DELETE" });
    check("D", "成长记录：删记录只清未确认候选、保留已确认记忆", removed.json?.data?.deleted === recordId && (removed.json?.data?.keptMemoryIds ?? []).includes(recordCandidateId), JSON.stringify(removed.json?.data));
    check("D", "成长记录：未知记录 404（与 501 / 未知路由区分开）", (await httpJson(`${growth}/growth_none`, { method: "GET" })).status === 404, "404");
    /* ---- M1-3 确认：候选 → 记录 → 证据 → 事件，一个事务且幂等 ---- */
    const confirmRecord = (await httpJson(growth, jsonInit("POST", {
      kind: "能力变化", title: "完成边缘 AI 推理优化", after: "在 RK3588 上把模型量化与部署跑通",
    }))).json?.data ?? {};
    const confirmRecordId = confirmRecord.record?.id;
    const confirmIds = (confirmRecord.candidates ?? []).map(item => item.id);
    const confirmBody = { recordId: confirmRecordId, memoryIds: confirmIds };
    const firstConfirm = await httpJson(`${base}/api/growth-records/confirm`, jsonInit("POST", confirmBody));
    const firstData = firstConfirm.json?.data ?? {};
    check("D", "确认：候选→记录→证据→事件一次写入", firstConfirm.status === 200 && (firstData.confirmedMemoryIds ?? []).length === confirmIds.length && (firstData.evidence ?? []).length === confirmIds.length && (firstData.events ?? []).length === confirmIds.length, JSON.stringify({ c: firstData.confirmedMemoryIds, e: (firstData.evidence ?? []).length, v: (firstData.events ?? []).length }));
    const secondConfirm = await httpJson(`${base}/api/growth-records/confirm`, jsonInit("POST", confirmBody));
    const secondData = secondConfirm.json?.data ?? {};
    check("D", "确认：重复 confirm 幂等（不产生重复行）", (secondData.confirmedMemoryIds ?? []).length === 0 && (secondData.alreadyConfirmedMemoryIds ?? []).length === confirmIds.length, JSON.stringify({ c: secondData.confirmedMemoryIds, a: secondData.alreadyConfirmedMemoryIds }));
    const confirmMissing = await httpJson(`${base}/api/growth-records/confirm`, jsonInit("POST", { recordId: "growth_missing", memoryIds: ["memory_x"] }));
    check("D", "确认：未知记录 404 GROWTH_RECORD_NOT_FOUND", confirmMissing.status === 404 && confirmMissing.json?.error?.code === "GROWTH_RECORD_NOT_FOUND", `${confirmMissing.status} ${confirmMissing.json?.error?.code}`);
    const confirmForeign = await httpJson(`${base}/api/growth-records/confirm`, jsonInit("POST", { recordId: confirmRecordId, memoryIds: [recordCandidateId] }));
    check("D", "确认：越权（拿别的记录的候选）必须被拒 400", confirmForeign.status === 400 && confirmForeign.json?.error?.code === "INVALID_GROWTH_CONFIRM", `${confirmForeign.status} ${confirmForeign.json?.error?.code}`);

    const earlyChat = await httpJson(`${base}/api/chat`, chatInit({ message: "模型量化与部署我学到哪了？" }));
    check("D", "成长记录：确认后的记忆真的进了对话注入", (earlyChat.json?.data?.injected?.memoryIds ?? []).includes(recordCandidateId), JSON.stringify(earlyChat.json?.data?.injected?.memoryIds));

    /* ---- ⑧ 简历解析：文本 → 画像草稿 + 待确认候选（零依赖；不支持的形态明确拒绝） ---- */
    const resumeEndpoint = `${base}/api/resumes/extract`;
    const resumeText = [
      "张小明",
      "教育背景",
      "2022.09-2026.06  浙江大学  自动化 专业  大三",
      "求职意向：边缘 AI 工程师",
      "专业技能",
      "Python（熟练）、模型量化与部署、轻量级推理引擎集成、炖菜",
      "项目经历",
      "项目名称：模型量化部署实践",
      "把 YOLOv5 量化到 INT8 并在 RK3588 上跑通。",
    ].join("\n");
    const parsed = await httpJson(resumeEndpoint, chatInit({ text: resumeText, useLlm: false }));
    const parsedData = parsed.json?.data ?? {};
    check("D", "简历：粘贴文本解析 200 且返回画像草稿", parsed.status === 200 && Boolean(parsedData.profileDraft), `${parsed.status}`);
    check("D", "简历：抽取出处写进画像证据表（M1-1，只写定位得到的）", parsedData.evidenceCount >= 1 && parsedData.evidenceCount <= (parsedData.evidence ?? []).length, `evidenceCount=${parsedData.evidenceCount} of ${(parsedData.evidence ?? []).length}`);
    check(
      "D",
      "简历：学校/专业/年级从原文抽到（反例保护：不能把「大三」当专业）",
      parsedData.profileDraft?.school === "浙江大学" && parsedData.profileDraft?.major === "自动化" && parsedData.profileDraft?.grade === "大三",
      JSON.stringify(parsedData.profileDraft)
    );
    check(
      "D",
      "简历：技能锚定图谱词条（nodeId 指向导出里的 skill 节点）",
      (parsedData.skills ?? []).length >= 1 && parsedData.skills.every(s => (exportData.nodes ?? []).some(n => n.id === s.nodeId && n.kind === "skill")),
      JSON.stringify((parsedData.skills ?? []).map(s => s.name))
    );
    check(
      "D",
      "简历：图谱不认识的技能词只进 unrecognizedSkills（不写画像技能表）",
      (parsedData.unrecognizedSkills ?? []).some(x => x.token === "Python") && !(parsedData.profileDraft?.skills ?? []).includes("Python"),
      JSON.stringify((parsedData.unrecognizedSkills ?? []).map(x => x.token))
    );
    check(
      "D",
      "简历：每条证据的 charRange 都定位到原文（可指回出处）",
      (parsedData.evidence ?? []).length >= 3 && parsedData.evidence.every(e => e.located === true && e.charRange[1] > e.charRange[0]),
      JSON.stringify((parsedData.evidence ?? []).map(e => e.field))
    );
    const resumeCandidates = parsedData.memoryCandidates ?? [];
    check(
      "D",
      "简历：抽取结果只落待确认候选（授权闸门）",
      resumeCandidates.length >= 2 && resumeCandidates.every(i => i.status === "candidate" && i.sourceType === "resume"),
      JSON.stringify(resumeCandidates.map(i => i.status))
    );
    const beforeResumeConfirm = await httpJson(`${mem}/context?query=${encodeURIComponent("模型量化与部署")}`);
    check(
      "D",
      "简历：候选确认前不进注入上下文",
      !(beforeResumeConfirm.json?.data?.recalled ?? []).some(entry => resumeCandidates.some(item => item.id === entry.memoryId)),
      JSON.stringify((beforeResumeConfirm.json?.data?.recalled ?? []).map(entry => entry.memoryId))
    );
    await httpJson(`${mem}/${resumeCandidates[0].id}`, jsonInit("PATCH", { status: "confirmed" }));
    const resumeKeyword = String(resumeCandidates[0].content).replace(/^[^：]*：/, "");
    const afterResumeConfirm = await httpJson(`${mem}/context?query=${encodeURIComponent(resumeKeyword)}`);
    check(
      "D",
      "简历：候选确认后进注入上下文",
      (afterResumeConfirm.json?.data?.recalled ?? []).some(entry => entry.memoryId === resumeCandidates[0].id),
      JSON.stringify((afterResumeConfirm.json?.data?.recalled ?? []).map(entry => entry.memoryId))
    );
    const reparse = await httpJson(resumeEndpoint, chatInit({ text: resumeText, useLlm: false }));
    check(
      "D",
      "简历：同一份简历重复上传是幂等的（不产生重复候选）",
      reparse.json?.data?.resumeId === parsedData.resumeId && reparse.json?.data?.candidateCount === 0,
      String(reparse.json?.data?.candidateCount)
    );
    // 形态处理不了的必须明确拒绝、给出可执行建议 —— 不假装解析、也不退回假数据。
    // PDF 现在是**可解析**的（本机装了哪个库就用哪个），所以这里拿一份截断的假 PDF 验证：
    // 装了解析库 → 415 RESUME_PDF_PARSE_FAILED；一个库都没装 → 415 RESUME_FORMAT_UNSUPPORTED。
    // 两种都必须是 415 + 一句能照做的建议，而不是 200 或一堆乱码当结果。
    const pdfForm = new FormData();
    pdfForm.append("file", new Blob([new TextEncoder().encode("%PDF-1.7\n")], { type: "application/pdf" }), "resume.pdf");
    const pdfResponse = await fetch(resumeEndpoint, { method: "POST", body: pdfForm });
    const pdfBody = await pdfResponse.json().catch(() => ({}));
    const pdfCode = pdfBody?.error?.code;
    const pdfMessage = String(pdfBody?.error?.message ?? "");
    check(
      "D",
      "简历：坏 PDF 明确 415 + 可执行建议（不假装解析）",
      pdfResponse.status === 415
        && ["RESUME_PDF_PARSE_FAILED", "RESUME_FORMAT_UNSUPPORTED"].includes(pdfCode)
        && /DOCX|pypdf|粘贴文本/.test(pdfMessage),
      `${pdfResponse.status} ${pdfCode}`
    );
    // PDF 能不能用不该靠猜：/health 必须报出探测结果与当前用的后端
    const healthForResume = await httpJson(`${base}/health`);
    check(
      "D",
      "简历：/health 报出 PDF 能力与后端（不支持时也能一眼看见）",
      typeof healthForResume.json?.resume?.pdfSupported === "boolean"
        && "pdfBackend" in (healthForResume.json?.resume ?? {}),
      `pdfSupported=${healthForResume.json?.resume?.pdfSupported} backend=${healthForResume.json?.resume?.pdfBackend}`
    );

    /* ---- ⑨ 模拟面试 / 跨岗位沟通训练：真链路（本阶段显式关模型 → 全部走规则版） ---- */
    const skillItems = (await httpJson(`${base}/api/v1/interview-skills`)).json?.data?.items ?? [];
    check(
      "D",
      `模拟面试：岗位列表 = 职业目录（${expectedOccupations}） + 自定义`,
      skillItems[0]?.roleId === "custom" && skillItems.length === expectedOccupations + 1,
      skillItems.map(item => item.roleId).join(",")
    );
    const interviewBody = { roleId: skillItems[1].roleId, difficulty: "mid", questionCount: 3, requestId: `e2e-iv-${process.pid}` };
    const created = await httpJson(`${base}/api/v1/interviews`, jsonInit("POST", interviewBody));
    const session = created.json?.data?.session;
    check("D", "模拟面试：创建 201 且题目数 = questionCount", created.status === 201 && session?.questions?.length === 3, `${created.status}/${session?.questions?.length}`);
    check("D", "模拟面试：未配模型时如实报 fallback（不假称模型出题）", session?.questionSource === "fallback", String(session?.questionSource));
    const createdAgain = await httpJson(`${base}/api/v1/interviews`, jsonInit("POST", interviewBody));
    check("D", "模拟面试：requestId 幂等（同一场，不产生重复记录）", createdAgain.json?.data?.session?.sessionId === session?.sessionId, String(createdAgain.json?.data?.session?.sessionId));

    const firstQuestion = session.questions[0];
    await httpJson(`${base}/api/v1/interviews/${session.sessionId}/answers`, jsonInit("POST", { questionId: firstQuestion.questionId, answer: "不知道" }));
    const finished = await httpJson(`${base}/api/v1/interviews/${session.sessionId}/complete`, { method: "POST" });
    const report = finished.json?.data?.report;
    const firstScore = (report?.questionDetails ?? []).find(item => item.questionId === firstQuestion.questionId)?.score;
    check("D", "模拟面试：交卷出报告，且「不知道」判 0 分（不拿字数换分）", finished.status === 200 && firstScore === 0, `score=${firstScore}`);
    const locked = await httpJson(`${base}/api/v1/interviews/${session.sessionId}/answers`, jsonInit("POST", { questionId: firstQuestion.questionId, answer: "改一下" }));
    check("D", "模拟面试：交卷后不能再改答案（409）", locked.status === 409, `${locked.status} ${locked.json?.error?.code}`);

    const crossRoles = await httpJson(`${base}/api/v1/cross-role/roles`);
    check(
      "D",
      "跨岗位训练：32 个岗位 / 320 题，且带题库来源与免责声明",
      crossRoles.json?.data?.count === 32 && crossRoles.json?.data?.questionCount === 320 && Boolean(crossRoles.json?.data?.disclaimer),
      `${crossRoles.json?.data?.count} 岗位 / ${crossRoles.json?.data?.questionCount} 题`
    );
    const crossCreated = await httpJson(`${base}/api/v1/cross-role/sessions`, jsonInit("POST", { roleId: "AI009", mode: "practice", requestId: `e2e-cr-${process.pid}` }));
    const crossSession = crossCreated.json?.data?.session;
    check("D", "跨岗位训练：创建 201 且每个岗位 10 个场景", crossCreated.status === 201 && crossSession?.questions?.length === 10, `${crossCreated.status}/${crossSession?.questions?.length}`);
    const crossQuestion = crossSession.questions[0];
    const crossAnswered = await httpJson(`${base}/api/v1/cross-role/sessions/${crossSession.sessionId}/answers`, jsonInit("POST", { questionId: crossQuestion.questionId, optionId: crossQuestion.options[0].optionId }));
    check("D", "跨岗位训练：练习模式作答后立刻给出推荐处理方式", Boolean(crossAnswered.json?.data?.session?.questions?.[0]?.feedback?.recommendedApproach), String(crossAnswered.status));
    const crossReport = (await httpJson(`${base}/api/v1/cross-role/sessions/${crossSession.sessionId}/complete`, { method: "POST" })).json?.data?.report;
    check(
      "D",
      "跨岗位训练：报告含四项协作维度与免责声明",
      (crossReport?.dimensions ?? []).length === 4 && Boolean(crossReport?.disclaimer),
      (crossReport?.dimensions ?? []).map(item => `${item.name}:${item.score}`).join(",")
    );
    // 就地清理：两条会话删掉，保证 e2e 可重复跑（简历存档落在临时库里，随 tmp 库一起丢弃）
    await httpJson(`${base}/api/v1/interviews/${session.sessionId}`, { method: "DELETE" });
    await httpJson(`${base}/api/v1/cross-role/sessions/${crossSession.sessionId}`, { method: "DELETE" });

    /* ---- ⑩ 任务实践：路径派生任务 → 提交 → 反馈（本阶段显式关模型 → 规则版） ---- */
    const tasksEndpoint = `${base}/api/tasks`;
    const taskList = await httpJson(tasksEndpoint);
    const taskItems = taskList.json?.data?.items ?? [];
    const taskCounts = taskList.json?.data?.counts ?? {};
    check(
      "D",
      "任务实践：任务清单由路径派生（每条都带阶段与出处）",
      taskList.status === 200 && taskItems.length > 0
        && taskItems.every(item => item.title && item.sourceRefs.length > 0 && item.sourcePath.stageName),
      `${taskItems.length} 条：${taskItems.map(item => `${item.taskId}/${item.status}`).join(",")}`
    );
    check(
      "D",
      "任务实践：图谱没有的字段如实为 null 且列进 unavailableFields",
      taskItems.every(item => item.difficulty === null && item.estimatedHours === null && item.unavailableFields.includes("difficulty")),
      `difficulty=${taskItems[0]?.difficulty} unavailable=${JSON.stringify(taskItems[0]?.unavailableFields)}`
    );
    check(
      "D",
      "任务实践：第一个未完成阶段的任务是 available",
      (taskCounts.available ?? 0) > 0 && taskItems.some(item => item.status === "available"),
      JSON.stringify(taskCounts)
    );
    const targetTask = taskItems.find(item => item.status === "available");
    context.taskId = targetTask.taskId;
    const taskDetail = await httpJson(`${tasksEndpoint}/${targetTask.taskId}`);
    check(
      "D",
      "任务实践：任务详情带历史提交（此刻为 0）",
      taskDetail.status === 200 && taskDetail.json?.data?.task?.taskId === targetTask.taskId && taskDetail.json?.data?.runCount === 0,
      `runCount=${taskDetail.json?.data?.runCount}`
    );

    const taskSkill = targetTask.requiredSkills[0]?.name ?? "模型量化与部署";
    const taskSubmission = `首先核对约束，接着按步骤验证，用到了 ${taskSkill}，例如实测指标提升 40%；最后写了交付说明。`;
    const taskRequestId = `e2e-task-${process.pid}`;
    const taskSubmit = await httpJson(`${tasksEndpoint}/${targetTask.taskId}/runs`, jsonInit("POST", {
      action: "先核对约束再逐项验证", submission: taskSubmission, requestId: taskRequestId,
    }));
    const taskRun = taskSubmit.json?.data?.run;
    check(
      "D",
      "任务实践：提交 201，且运行记录与成长记录用同一个 ID",
      taskSubmit.status === 201 && taskRun?.growthRecordId === taskRun?.runId && Boolean(taskRun?.runId),
      `${taskSubmit.status} ${taskRun?.growthRecordId}`
    );
    const taskCandidates = taskSubmit.json?.data?.candidates ?? [];
    check(
      "D",
      "任务实践：提交只产出待确认候选（没有一条 confirmed）",
      taskCandidates.length > 0 && taskCandidates.every(item => item.status === "candidate"),
      taskCandidates.map(item => item.status).join(",")
    );
    const taskSubmitAgain = await httpJson(`${tasksEndpoint}/${targetTask.taskId}/runs`, jsonInit("POST", {
      submission: taskSubmission, requestId: taskRequestId,
    }));
    check(
      "D",
      "任务实践：requestId 幂等（不重复写记录与候选）",
      taskSubmitAgain.json?.data?.created === false && taskSubmitAgain.json?.data?.run?.runId === taskRun?.runId,
      `created=${taskSubmitAgain.json?.data?.created}`
    );
    const taskEvaluate = await httpJson(`${base}/api/task-runs/${taskRun.runId}/evaluate`, { method: "POST" });
    const taskReport = taskEvaluate.json?.data?.report;
    check(
      "D",
      "任务实践：评估可达且走规则版（本阶段关了模型）",
      taskEvaluate.status === 200 && taskReport?.provider === "fallback" && (taskReport.improvements ?? []).length > 0,
      `${taskEvaluate.status} provider=${taskReport?.provider}`
    );
    check(
      "D",
      "任务实践：观察到的能力必须引用原文（逐字可查）",
      (taskReport?.observedAbilities ?? []).length > 0
        && taskReport.observedAbilities.every(item => item.evidence.length > 0
          && `先核对约束再逐项验证\n${taskSubmission}`.includes(item.evidence)),
      (taskReport?.observedAbilities ?? []).map(item => item.name).join(",")
    );
    const taskCandidateIds = new Set(taskCandidates.map(item => item.id));
    const memoriesNow = (await httpJson(mem)).json?.data?.items ?? [];
    check(
      "D",
      "任务实践：评估没有把这次提交的候选改成已确认",
      taskCandidateIds.size > 0 && memoriesNow.filter(item => taskCandidateIds.has(item.id)).every(item => item.status === "candidate"),
      memoriesNow.filter(item => taskCandidateIds.has(item.id)).map(item => item.status).join(",")
    );
    const taskListAfter = await httpJson(tasksEndpoint);
    check(
      "D",
      "任务实践：提交后该任务状态翻成 completed",
      (taskListAfter.json?.data?.counts?.completed ?? 0) === 1,
      JSON.stringify(taskListAfter.json?.data?.counts)
    );
    const taskDetailAfter = await httpJson(`${tasksEndpoint}/${targetTask.taskId}`);
    check(
      "D",
      "任务实践：详情里的历史提交带上了评估结果",
      taskDetailAfter.json?.data?.runCount === 1 && taskDetailAfter.json?.data?.latestFeedback?.provider === "fallback",
      `runCount=${taskDetailAfter.json?.data?.runCount}`
    );

    // 清理：把剩下的测试记忆（含候选）全删掉，保证 e2e 可重复跑（不污染本地 db）
    const leftover = await httpJson(mem);
    for (const item of leftover.json?.data?.items ?? []) await httpJson(`${mem}/${item.id}`, { method: "DELETE" });
    const sweep = await httpJson(mem);
    check("D", "记忆：e2e 结束后不留残余（可重复跑）", (sweep.json?.data?.count ?? -1) === 0, `${sweep.json?.data?.count} 条：${(sweep.json?.data?.items ?? []).map(item => `${item.sourceType}/${item.status}`).join(",")}`);
    const recordLeftover = await httpJson(growth);
    for (const item of recordLeftover.json?.data?.items ?? []) await httpJson(`${growth}/${item.id}`, { method: "DELETE" });
    const recordSweep = await httpJson(growth);
    check("D", "成长记录：e2e 结束后不留残余", (recordSweep.json?.data?.count ?? -1) === 0, `${recordSweep.json?.data?.count} 条`);
  } finally {
    killTree(started.child);
    context.backendLog = started.log.stderr.split("\n").filter(Boolean).slice(-5);
  }
}

/* ------------------------------------------------------------------ *
 * 6. 阶段 E：MCP HTTP（真进程）
 * ------------------------------------------------------------------ */

async function stageE(context) {
  const base = `http://127.0.0.1:${MCP_PORT}`;
  const started = startProcess(process.execPath, ["--import", "tsx", "mcp/http.ts"], {
    cwd: PROJECT_ROOT,
    env: { MCP_HTTP_PORT: String(MCP_PORT), MCP_HTTP_HOST: "127.0.0.1" },
  });
  try {
    const ready = await waitForHttp(`${base}/health`, { attempts: 120, timeoutMs: 3000 });
    check("E", `MCP HTTP 入口在 ${base} 就绪`, ready);
    if (!ready) {
      warn("E", "MCP 未就绪，本阶段跳过", started.log.stderr.split("\n").slice(-3).join(" "));
      return;
    }

    const mcpUrl = `${base}/mcp`;
    const post = async (payload, sessionId) => {
      const headers = { "content-type": "application/json", accept: "application/json, text/event-stream" };
      if (sessionId) headers["mcp-session-id"] = sessionId;
      const response = await fetch(mcpUrl, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(30000),
      });
      const raw = await response.text();
      const trimmed = raw.trim();
      let messages = [];
      if (trimmed.startsWith("{")) messages = [JSON.parse(trimmed)];
      else {
        messages = trimmed
          .split(/\r?\n/)
          .filter(line => line.startsWith("data:"))
          .map(line => line.slice(5).trim())
          .filter(Boolean)
          .map(line => JSON.parse(line));
      }
      return { status: response.status, headers: response.headers, messages };
    };

    const init = await post({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "e2e-knowledge", version: "0.1.0" } },
    });
    const sessionId = init.headers.get("mcp-session-id");
    check("E", "initialize 返回 session", init.status === 200 && Boolean(sessionId), `${init.status} session=${sessionId ? "有" : "无"}`);
    if (!sessionId) return;
    await post({ jsonrpc: "2.0", method: "notifications/initialized" }, sessionId);

    const listed = await post({ jsonrpc: "2.0", id: 2, method: "tools/list" }, sessionId);
    const tools = listed.messages[0]?.result?.tools ?? [];
    check("E", "tools/list 返回 3 个工具", tools.length === 3, tools.map(tool => tool.name).join(","));
    metrics.tools = tools.map(tool => tool.name);

    const callTool = async (name, args) => {
      const response = await post({ jsonrpc: "2.0", id: `${name}-${Date.now()}`, method: "tools/call", params: { name, arguments: args } }, sessionId);
      const message = response.messages[0];
      const payload = message?.result?.structuredContent ?? null;
      return { payload, isError: Boolean(message?.result?.isError), message };
    };

    const exportData = context.exportData;
    const nodeIds = new Set((exportData.nodes ?? []).map(node => node.id));
    const chunkIds = new Set((exportData.chunks ?? []).map(chunk => chunk.chunkId));
    const sourceIds = new Set((exportData.sources ?? []).map(source => source.sourceId));
    const expectedCounts = context.arrayCounts;

    /* ---- 工具 1：检索 ---- */
    const search = await callTool("search_career_knowledge", { query: "模型量化", limit: 5 });
    check("E", "search_career_knowledge 调用成功", search.payload?.ok === true && !search.isError, search.payload?.error ?? "");
    const matches = search.payload?.matches ?? [];
    const chunkMatches = search.payload?.chunkMatches ?? [];
    check("E", "检索有命中（节点或 chunk 至少一类）", matches.length + chunkMatches.length > 0, `节点 ${matches.length} / chunk ${chunkMatches.length}`);
    check("E", "命中的节点 id 都在导出里", matches.every(match => nodeIds.has(match.id)), matches.map(match => match.id).join(","));
    const citations = [...matches.flatMap(match => match.citations ?? []), ...chunkMatches.flatMap(match => match.citations ?? [])];
    check("E", "命中的 chunk 都在导出里", chunkMatches.every(match => chunkIds.has(match.chunkId)), chunkMatches.map(match => match.chunkId).join(","));
    check(
      "E",
      "每条 citation 都能指回真实 chunk 与来源",
      citations.every(citation => chunkIds.has(citation.chunkId) && sourceIds.has(citation.sourceId) && citation.text.length > 0),
      `${citations.length} 条引用`
    );
    metrics.searchCitations = citations.length;

    /* ---- 工具 2：技能缺口（图推理，不是关键词匹配） ---- */
    const gap = await callTool("get_skill_gap", { target: "边缘 AI 工程师", ownedSkills: ["Python", "C 语言与内存模型"] });
    check("E", "get_skill_gap 调用成功", gap.payload?.ok === true && !gap.isError, gap.payload?.error ?? "");
    check("E", "目标解析到真实节点", Boolean(gap.payload?.target?.id) && nodeIds.has(gap.payload.target.id), `${gap.payload?.targetResolution?.matchedBy} → ${gap.payload?.target?.id}`);
    const order = gap.payload?.learningOrder ?? [];
    check("E", "缺口非空（目标职业要求了技能）", order.length > 0, `${order.length} 步`);
    const seen = new Set();
    const orderViolations = [];
    for (const step of order) {
      for (const blocker of step.blockedBy ?? []) {
        if (blocker.id === step.id) continue;
        if (!seen.has(blocker.id) && !(gap.payload?.satisfiedSkills ?? []).some(item => item.id === blocker.id)) {
          orderViolations.push(`${step.id} 排在先修 ${blocker.id} 之前`);
        }
      }
      seen.add(step.id);
    }
    check("E", "学习序满足先修边（先修必须排在依赖方之前）", orderViolations.length === 0, orderViolations.slice(0, 3).join("; "));
    check("E", "已具备的技能被识别，不再出现在待学清单里", (gap.payload?.satisfiedSkills ?? []).length >= 1 && (gap.payload?.satisfiedSkills ?? []).every(item => !order.some(step => step.id === item.id)), (gap.payload?.satisfiedSkills ?? []).map(item => item.label).join(","));
    check("E", "无法解析的已有技能原样回显，不瞎猜", Array.isArray(gap.payload?.unmatchedOwnedSkills), JSON.stringify(gap.payload?.unmatchedOwnedSkills));
    check("E", "缺口步骤的 citations 可追溯", order.every(step => (step.citations ?? []).every(citation => chunkIds.has(citation.chunkId))), `${order.reduce((sum, step) => sum + (step.citations ?? []).length, 0)} 条引用`);

    /* ---- 工具 3：焦点图谱视图 ---- */
    const view = await callTool("get_career_graph_view", { focusId: "occupation:AI001", maxHop: 2 });
    check("E", "get_career_graph_view 调用成功", view.payload?.ok === true && !view.isError, view.payload?.error ?? "");
    const graphView = view.payload?.view;
    check("E", "视图节点数 = 导出节点数", graphView?.nodes?.length === expectedCounts.nodes, `${graphView?.nodes?.length} vs ${expectedCounts.nodes}`);
    const chunkyEdges = (exportData.edges ?? []).filter(edge => String(edge.from).startsWith("chunk:") || String(edge.to).startsWith("chunk:")).length;
    check("E", "chunk 引用边被排除在可渲染边之外（记入 skippedEdges）", (graphView?.skippedEdges ?? []).length === chunkyEdges, `${(graphView?.skippedEdges ?? []).length} vs ${chunkyEdges}`);
    check("E", "可渲染边数 = 总边数 − chunk 引用边", graphView?.counts?.edges === expectedCounts.edges - chunkyEdges, `${graphView?.counts?.edges} vs ${expectedCounts.edges - chunkyEdges}`);
    check("E", "焦点节点被标记为 focal", graphView?.nodes?.some(node => node.id === "occupation:AI001" && node.state === "focal"), (graphView?.nodes ?? []).filter(node => node.state === "focal").map(node => node.id).join(","));
    check("E", "摘要文案不含 undefined（此前是 undefined 的悬停文案）", typeof view.payload?.summaryZh === "string" && !view.payload.summaryZh.includes("undefined"), view.payload?.summaryZh ?? "");

    /* ---- 端到端问答探针：拿真实题集的题走完整条链，看能不能答出可追溯的引用 ---- */
    const questionsPath = join(KNOWLEDGE, "evaluations", "questions-dev.json");
    const rawQuestions = readJson(questionsPath);
    const questions = (Array.isArray(rawQuestions) ? rawQuestions : rawQuestions.questions ?? [])
      .filter(question => question.answerable !== false && (question.referenceChunks ?? []).length > 0)
      .slice(0, Number(process.env.E2E_QUESTION_PROBE ?? 12));
    const probe = [];
    for (const question of questions) {
      const response = await callTool("search_career_knowledge", { query: question.question, limit: 5 });
      const payload = response.payload ?? {};
      const returned = new Set([
        ...(payload.matches ?? []).map(match => match.id),
        ...(payload.chunkMatches ?? []).map(match => match.chunkId),
        ...(payload.matches ?? []).flatMap(match => (match.citations ?? []).map(citation => citation.chunkId)),
        ...(payload.chunkMatches ?? []).flatMap(match => (match.citations ?? []).map(citation => citation.chunkId)),
      ]);
      const reference = new Set(question.referenceChunks ?? []);
      const cited = [...returned].filter(id => reference.has(id));
      probe.push({ questionId: question.questionId, ok: payload.ok === true, returned: returned.size, citedChunks: cited });
    }
    const answered = probe.filter(item => item.returned > 0).length;
    const cited = probe.filter(item => item.citedChunks.length > 0).length;
    metrics.questionProbe = { asked: probe.length, answered, cited, detail: probe };
    check("E", `问答探针：${probe.length} 道真题都能拿到非空结果`, answered === probe.length, `${answered}/${probe.length}`);
    check(
      "E",
      `问答探针：至少 1 道题的引用命中参考答案段（端到端可追溯）`,
      cited >= 1,
      `引用命中 ${cited}/${probe.length}（MCP 检索是节点+chunk 双通道，非纯 BM25；此数只作链路信号，不作检索质量结论）`
    );
    info(`问答探针：${probe.length} 题，非空 ${answered}，引用命中 ${cited}`);

    context.mcp = { search, gap, view, probe };
  } finally {
    killTree(started.child);
    context.mcpLog = started.log.stderr.split("\n").filter(Boolean).slice(-5);
  }
}

/* ------------------------------------------------------------------ *
 * 7. 阶段 F：跨端同源 + 端到端问答可追溯
 * ------------------------------------------------------------------ */

async function stageF(context) {
  const exportMeta = context.exportData.meta ?? {};
  const health = context.backendHealth ?? {};
  const mcpCounts = context.mcp?.search?.payload?.dataVersion?.counts ?? null;

  check("F", "后端 /health 与导出 kbVersion 一致", health.kbVersion === exportMeta.kbVersion, `${health.kbVersion} vs ${exportMeta.kbVersion}`);
  check("F", "后端 /health 与导出 graphVersion 一致", health.graphVersion === exportMeta.graphVersion, `${health.graphVersion} vs ${exportMeta.graphVersion}`);
  check("F", "后端 /health 与导出 generatedAt 一致（同一份字节）", health.generatedAt === exportMeta.generatedAt, `${health.generatedAt} vs ${exportMeta.generatedAt}`);
  check(
    "F",
    "后端 counts 与导出数组长度逐项一致",
    health.counts && Object.entries(context.arrayCounts).every(([key, value]) => health.counts[key] === value),
    JSON.stringify(health.counts)
  );

  check("F", "MCP dataVersion.kbVersion 与导出一致", context.mcp?.search?.payload?.dataVersion?.kbVersion === exportMeta.kbVersion, String(context.mcp?.search?.payload?.dataVersion?.kbVersion));
  check("F", "MCP dataVersion.generatedAt 与导出一致（同一份字节）", context.mcp?.search?.payload?.dataVersion?.generatedAt === exportMeta.generatedAt, String(context.mcp?.search?.payload?.dataVersion?.generatedAt));
  /* 同一台机器上看「是不是同一个文件」：比较归一化后的路径（Windows 下 Python 给的是
     as_posix() 正斜杠，Node 给的是反斜杠，直接字符串比会假失败）。 */
  const normalizePath = value => String(value ?? "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  check(
    "F",
    "后端与 MCP 指向的导出文件是同一个路径",
    normalizePath(context.mcp?.search?.payload?.dataVersion?.exportPath) === normalizePath(health.dataSource),
    `${context.mcp?.search?.payload?.dataVersion?.exportPath} vs ${health.dataSource}`
  );
  check(
    "F",
    "MCP counts 与导出数组长度逐项一致",
    mcpCounts && mcpCounts.nodes === context.arrayCounts.nodes && mcpCounts.edges === context.arrayCounts.edges && mcpCounts.chunks === context.arrayCounts.chunks && mcpCounts.sources === context.arrayCounts.sources,
    JSON.stringify(mcpCounts)
  );

  /* ---- 一致性：MCP 命中的 chunk 文本与导出里的 chunk 文本必须逐字相同 ---- */
  const chunkTextById = new Map((context.exportData.chunks ?? []).map(chunk => [chunk.chunkId, chunk.text]));
  const sampleChunk = (context.mcp?.search?.payload?.chunkMatches ?? [])[0];
  if (sampleChunk) {
    check(
      "F",
      "MCP 返回的原文与导出里的 chunk 逐字相同",
      chunkTextById.get(sampleChunk.chunkId) === sampleChunk.text,
      sampleChunk.chunkId
    );
  } else {
    warn("F", "本次检索没有 chunk 命中，跳过原文逐字比对", "换检索词或提高 limit 后重跑可覆盖");
  }

  /* ---- 问答探针的引用也要能在导出里指回真实原文（跨阶段同源） ---- */
  const probe = context.mcp?.probe ?? [];
  const citedIds = probe.flatMap(item => item.citedChunks);
  check(
    "F",
    "问答探针给出的引用段全部存在于导出 chunks 里",
    citedIds.every(id => chunkTextById.has(id)),
    `${citedIds.length} 条引用`
  );
}

/* ------------------------------------------------------------------ *
 * 8. 阶段 G：前端 dev server（可跳过）
 * ------------------------------------------------------------------ */

async function stageG(context) {
  /* vinext dev 打印的是 http://localhost:PORT —— 实测它只监听 localhost 解析到的那个地址
     （本机是 ::1），拿 127.0.0.1 去 fetch 会直接 ECONNREFUSED，所以这里必须用 localhost。 */
  const base = `http://localhost:${FRONTEND_PORT}`;
  const started = startProcess(process.execPath, ["node_modules/vinext/dist/cli.js", "dev", "--port", String(FRONTEND_PORT)], {
    cwd: PROJECT_ROOT,
  });
  try {
    const ready = await waitForHttp(`${base}/auth`, { attempts: 160, intervalMs: 500, timeoutMs: 5000 });
    check("G", `前端 dev server 在 ${base} 就绪`, ready);
    if (!ready) {
      warn("G", "前端未就绪，本阶段跳过", started.log.stderr.split("\n").slice(-3).join(" "));
      return;
    }

    const entry = await httpJson(`${base}/auth`, { timeoutMs: 60000 });
    check("G", "/auth 返回 200 且能 SSR 出 HTML", entry.status === 200 && entry.text.includes("向新"), `${entry.status} ${entry.text.length}B`);

    const onboarding = await httpJson(`${base}/onboarding`, { timeoutMs: 60000 });
    check("G", "/onboarding 返回 200 且带表单内容", onboarding.status === 200 && onboarding.text.includes("技能"), `${onboarding.status} ${onboarding.text.length}B`);

    // 记忆库面板挂在 /growth：SSR 就该出现「记忆库」与三栏标题，
    // 这样「前端可发现性」不是靠人工点页面确认的。
    const growth = await httpJson(`${base}/growth`, { timeoutMs: 90000 });
    check("G", "/growth 返回 200（重定向后的落点）", growth.status === 200, String(growth.status));
    check(
      "G",
      "/growth 的 SSR 里出现记忆库面板（候选 / 已确认 / 注入预览三栏）",
      growth.text.includes("记忆库") && growth.text.includes("待确认") && growth.text.includes("注入预览"),
      growth.text.includes("记忆库") ? "已渲染" : "未见「记忆库」字样"
    );

    /* 职场模拟入口与它挂的两个真页面：以前 /actions 只有「内容筹备中」，
       现在必须是能 SSR 出流程标题的真页面（模拟面试 / 跨岗位沟通训练）。 */
    const actions = await httpJson(`${base}/actions`, { timeoutMs: 90000 });
    check(
      "G",
      "/actions 是模拟场景入口（三条链路都在，不再是「筹备中」占位页）",
      actions.status === 200 && actions.text.includes("模拟场景") && actions.text.includes("跨岗位沟通训练")
        && actions.text.includes("任务实践") && !actions.text.includes("内容筹备中"),
      `${actions.status} ${actions.text.length}B`
    );
    const interview = await httpJson(`${base}/mock-interview`, { timeoutMs: 90000 });
    check(
      "G",
      "/mock-interview 返回 200 且 SSR 出面试配置表单",
      interview.status === 200 && interview.text.includes("模拟面试") && interview.text.includes("目标岗位"),
      `${interview.status} ${interview.text.length}B`
    );
    const crossRole = await httpJson(`${base}/scenarios/cross-role`, { timeoutMs: 90000 });
    check(
      "G",
      "/scenarios/cross-role 返回 200 且 SSR 出岗位选择页",
      crossRole.status === 200 && crossRole.text.includes("跨岗位沟通") && crossRole.text.includes("选择你要扮演的岗位"),
      `${crossRole.status} ${crossRole.text.length}B`
    );

    /* 任务实践：清单页 SSR 就该出现筛选与口径入口；详情页只要求 200 + 渲染出加载态
       （数据是客户端取的，SSR 只会给出壳 —— 这里不断言具体任务内容，避免过度断言）。 */
    const tasksPage = await httpJson(`${base}/actions/tasks`, { timeoutMs: 90000 });
    check(
      "G",
      "/actions/tasks 返回 200 且 SSR 出任务清单页",
      tasksPage.status === 200 && tasksPage.text.includes("实践任务") && tasksPage.text.includes("现在可做"),
      `${tasksPage.status} ${tasksPage.text.length}B`
    );
    if (context?.taskId) {
      const taskDetailPage = await httpJson(`${base}/actions/tasks/${encodeURIComponent(context.taskId)}`, { timeoutMs: 90000 });
      check(
        "G",
        `/actions/tasks/<taskId> 返回 200（真任务 ID ${context.taskId} 可路由）`,
        taskDetailPage.status === 200 && taskDetailPage.text.includes("正在从图谱里取这条任务"),
        `${taskDetailPage.status} ${taskDetailPage.text.length}B`
      );
    }
    const pathPage = await httpJson(`${base}/path`, { timeoutMs: 90000 });
    check(
      "G",
      "/path 的 SSR 里不再出现「职场模拟将在后续开放」这句过期文案",
      pathPage.status === 200 && pathPage.text.includes("我的成长路径") && !pathPage.text.includes("职场模拟将在后续开放"),
      `${pathPage.status} ${pathPage.text.length}B`
    );

    /* M1-5 之后 /work-map 与 /catalog 都是真页面：必须 200 且 SSR 出内容，
       不能再是 redirect。`/chat` 仍是站内重定向（那是它的设计，与本次无关）。 */
    const productRoutes = ["/work-map", "/catalog", "/chat"];
    const realPages = ["/work-map", "/catalog"];
    const observed = {};
    for (const route of productRoutes) {
      const response = await fetch(`${base}${route}`, { redirect: "manual", signal: AbortSignal.timeout(60000) });
      observed[route] = { status: response.status, location: response.headers.get("location") };
      if (realPages.includes(route)) {
        const html = response.status === 200 ? await response.text() : "";
        const hasHeading = html.includes("未来工作地图") || html.includes("职业与技能目录");
        check(
          "G",
          `${route} 已是真页面（200 + SSR 出标题，不再是 redirect）`,
          response.status === 200 && hasHeading,
          `${response.status} ${hasHeading ? "标题已渲染" : "未见标题"}`
        );
      } else {
        check(
          "G",
          `${route} 的响应是 200 或站内重定向（chat 按设计走重定向）`,
          response.status === 200 || response.status === 307 || response.status === 308,
          `${response.status} ${response.headers.get("location") ?? ""}`
        );
      }
    }
    metrics.frontendRoutes = observed;
  } finally {
    killTree(started.child);
  }
}

/* ------------------------------------------------------------------ *
 * 9. 阶段 H：既有测试套件（--with-suites）
 * ------------------------------------------------------------------ */

async function stageH() {
  const suites = [
    { label: "前端 node:test（npm test）", command: process.execPath, args: ["--import", "tsx", "--test", "tests/*.test.ts"], cwd: PROJECT_ROOT },
    { label: "后端 pytest（backend/tests）", command: PYTHON, args: ["-m", "pytest", "backend/tests", "-q", "-p", "no:cacheprovider"], cwd: PROJECT_ROOT },
    { label: "MCP stdio 验证（mcp:verify）", command: process.execPath, args: ["--import", "tsx", "scripts/verify-mcp.ts"], cwd: PROJECT_ROOT },
    { label: "MCP HTTP 验证（mcp:verify-http）", command: process.execPath, args: ["--import", "tsx", "scripts/verify-mcp-http.ts"], cwd: PROJECT_ROOT },
  ];
  for (const suite of suites) {
    const run = await runToCompletion(suite.command, suite.args, { cwd: suite.cwd });
    const tail = `${run.stdout}\n${run.stderr}`.split("\n").filter(Boolean).slice(-1)[0] ?? "";
    check("H", `${suite.label} 退出码 0`, run.code === 0, `exit=${run.code} · ${tail.slice(0, 160)}`);
  }

  const questionset = join(KNOWLEDGE, "eval", "validate_questionset.py");
  if (existsSync(questionset)) {
    const run = await runToCompletion(PYTHON, [questionset, "--set", join(KNOWLEDGE, "evaluations", "questions-holdout.json"), "--min-gap", "3"]);
    check("H", "题集硬规则校验（holdout）通过", run.code === 0, `exit=${run.code}`);
  }
}

/* ------------------------------------------------------------------ *
 * 9. 阶段 I：模型接通性（--with-llm）
 * ------------------------------------------------------------------ */

async function stageI(context) {
  const base = `http://127.0.0.1:${LLM_PORT}`;
  const memoryDb = join(tmpdir(), `e2e-llm-memory-${process.pid}.db`);
  /* 真起一个后端：配置与产品/评测共用（CAREER_LLM_* > DEEPEVAL_* > knowledge/eval/.env）。
     `CAREER_LLM_DISABLED=1` 时这一段会看到 configured=false 并跳过 —— 也算断言通过。 */
  const started = startProcess(PYTHON, ["backend/run.py", "--port", String(LLM_PORT)], {
    cwd: PROJECT_ROOT,
    env: { CAREER_MEMORY_DB: memoryDb },
  });
  try {
    const ready = await waitForHttp(`${base}/health`);
    check("I", `后端在 ${base} 就绪`, ready);
    if (!ready) return;

    const health = await httpJson(`${base}/health`);
    const llm = health.json?.llm ?? {};
    info(`LLM：configured=${llm.configured} source=${llm.source} host=${llm.host} model=${llm.model}`);
    check("I", "/health 的 LLM 块结构完整", typeof llm.configured === "boolean" && "host" in llm && "model" in llm, JSON.stringify(llm));
    check("I", "/health 不回显密钥", !JSON.stringify(llm).toLowerCase().includes("api_key"), JSON.stringify(llm));
    if (!llm.configured) {
      warn("I", "未配置 LLM 端点，跳过模型版触发器验证", String(llm.note ?? ""));
      return;
    }

    const mem = `${base}/api/memories`;
    const jsonInit = (method, body) => ({
      method,
      headers: { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });

    // 记忆内容选图谱里真实存在的技能名，这样规则版也能拿到可读锚点，两边可比
    const created = await httpJson(mem, jsonInit("POST", {
      category: "skill", content: "具备或正在学习：轻量级推理引擎集成",
    }));
    const memoryId = created.json?.data?.item?.id;
    check("I", "记忆写入成功（写路径走规则版，不等模型）", created.status === 201 && Boolean(memoryId), String(memoryId));

    const startedAt = Date.now();
    const generated = await httpJson(`${mem}/${memoryId}/triggers?generator=llm`, { method: "POST", timeoutMs: 240000 });
    const note = generated.json?.data?.generator ?? {};
    const triggers = generated.json?.data?.triggers ?? [];
    metrics.llmTriggers = {
      latencyMs: Date.now() - startedAt,
      used: note.used,
      model: note.model,
      error: note.error,
      concepts: triggers.map(trigger => trigger.concept),
      generatedBy: triggers.map(trigger => trigger.generatedBy),
    };
    info(`模型版触发器：${Date.now() - startedAt} ms，used=${note.used}，概念 ${JSON.stringify(triggers.map(t => t.concept))}`);

    check("I", "接口 200（模型失败也不该报错，而是降级）", generated.status === 200, String(generated.status));
    check("I", "生成器回传 requested=llm 与最终 used", note.requested === "llm" && typeof note.used === "string", JSON.stringify(note));
    check("I", "请求了模型就有模型名可溯源", note.used !== "llm" || Boolean(note.model), String(note.model));

    if (note.used === "llm") {
      check("I", "模型版触发器带 generatedBy=llm 与模型名", triggers.length > 0 && triggers.every(t => t.generatedBy === "llm" && t.generatedByModel === note.model), JSON.stringify(triggers.map(t => [t.generatedBy, t.generatedByModel])));
      check("I", "模型版触发器的概念与句式都在字段约束内", triggers.every(t => t.concept.length > 0 && t.concept.length <= 60 && t.bridge.length <= 120 && t.activationPatterns.length === 3 && t.activationPatterns.every(p => p.length <= 80)), JSON.stringify(triggers.map(t => t.concept)));
      // 关键价值验证：用「模型自己编的提问句式」去问，应该能靠触发器命中这条记忆
      const probeQuery = triggers[0].activationPatterns[0];
      const recall = await httpJson(`${mem}/context?query=${encodeURIComponent(probeQuery)}`);
      const recalled = recall.json?.data?.recalled ?? [];
      check("I", `用它自己生成的问句「${probeQuery}」能召回该记忆`, recalled.some(entry => entry.memoryId === memoryId), JSON.stringify(recalled.map(entry => entry.channel)));
      check("I", "召回通道标注为触发器命中", recalled.some(entry => entry.memoryId === memoryId && entry.channel.startsWith("trigger:")), JSON.stringify(recalled.map(entry => entry.channel)));
    } else {
      // 降级本身不算失败（外部端点不可控），但必须**说得出原因**
      check("I", "降级时带 rule-based-fallback 标记", note.used === "rule-based-fallback", String(note.used));
      check("I", "降级时错误码与说明都在", Boolean(note.error?.code) && Boolean(note.error?.message), JSON.stringify(note.error));
      check("I", "降级后触发器仍可用（记忆不被阻断）", triggers.length > 0, String(triggers.length));
      warn("I", "模型版触发器本次降级到规则版", `${note.error?.code}：${note.error?.message}`);
    }

    /* 真实对话：模型 + 已确认记忆注入 —— 这是「产品可以用」这条链的最终验收。
       注入是否真的发生用 `injected` 自证（不靠模型"必须提到某个词"这种飘的断言）。 */
    const targetMemory = await httpJson(mem, jsonInit("POST", { category: "career_target", content: "目标职业：边缘 AI 工程师" }));
    const targetMemoryId = targetMemory.json?.data?.item?.id;
    const chatStartedAt = Date.now();
    const chat = await httpJson(`${base}/api/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: "我该先补哪块技能？" }),
      timeoutMs: 240000,
    });
    const chatData = chat.json?.data ?? {};
    metrics.llmChat = {
      latencyMs: Date.now() - chatStartedAt,
      provider: chatData.provider,
      injectedCount: chatData.injected?.count,
      memoryIds: chatData.injected?.memoryIds,
      promptBytes: chatData.injected?.prefixBytes,
      error: chatData.llm?.error ?? null,
      reply: String(chatData.message ?? "").slice(0, 200),
    };
    info(`真实对话：${Date.now() - chatStartedAt} ms，provider=${chatData.provider}，注入 ${chatData.injected?.count} 条，回答 ${String(chatData.message ?? "").length} 字`);

    check("I", "对话接口 200（模型失败也不该报错，而是降级）", chat.status === 200, String(chat.status));
    check("I", "对话注入了已确认记忆", (chatData.injected?.count ?? 0) >= 1 && (chatData.injected?.memoryIds ?? []).includes(targetMemoryId), JSON.stringify(chatData.injected?.memoryIds));
    check("I", "对话注入可审计（memoryHash / 模板 / 提示词字节数）", Boolean(chatData.injected?.memoryHash) && chatData.injected?.promptTemplate === "career-chat/v1" && (chatData.injected?.prefixBytes ?? 0) > (chatData.injected?.memoryBlockBytes ?? 0), JSON.stringify({ hash: chatData.injected?.memoryHash, template: chatData.injected?.promptTemplate }));
    check("I", "对话回答非空", typeof chatData.message === "string" && chatData.message.trim().length > 0, String(chatData.message ?? "").slice(0, 60));

    if (chatData.provider === "llm") {
      check("I", "对话真的走了模型（provider=llm 且带模型名）", chatData.llm?.used === "llm" && chatData.llm?.model === llm.model, JSON.stringify({ used: chatData.llm?.used, model: chatData.llm?.model }));
      check("I", "模型回答不是规则版占位文本", !String(chatData.message).startsWith("（规则版回答"), String(chatData.message).slice(0, 40));
    } else {
      check("I", "对话降级时带错误码与说明", Boolean(chatData.llm?.error?.code) && Boolean(chatData.llm?.error?.message), JSON.stringify(chatData.llm?.error));
      warn("I", "对话本次降级到规则版", `${chatData.llm?.error?.code}：${chatData.llm?.error?.message}`);
    }

    // 清理测试记忆
    await httpJson(`${mem}/${memoryId}`, { method: "DELETE" });
    if (targetMemoryId) await httpJson(`${mem}/${targetMemoryId}`, { method: "DELETE" });
  } finally {
    killTree(started.child);
  }
}

/* ------------------------------------------------------------------ *
 * 10. 主流程
 * ------------------------------------------------------------------ */

async function main() {
  const startedAt = Date.now();
  console.log("知识库端到端测试（frontend/frotent/frontend1/scripts/e2e-knowledge.mjs）");
  console.log(`  仓库根：${REPO_ROOT}`);
  console.log(`  开关：前端=${WITH_FRONTEND ? "跑" : "跳过"} 既有测试套件=${WITH_SUITES ? "跑" : "跳过"} 模型=${WITH_LLM ? "跑" : "跳过"}`);

  const context = {};
  loadExport(context);

  await stage("A", "知识库产物自洽（导出 ↔ 分块 ↔ 图谱 ↔ 索引 ↔ 原始快照）", stageA);
  await stage("B", "构建闸门可复跑（09/10/11 重跑后语义投影不变）", stageB);
  /* 阶段 B 会重写导出（generatedAt 变），后端/MCP 读到的是新字节 —— 重新读一次，
     否则阶段 F 的「同一份字节」断言会拿旧值跟新文件比，变成假失败。 */
  loadExport(context);
  await stage("C", "检索闸门（现役 BM25 / dev 题集）", stageC);
  await stage("D", "后端 HTTP 端到端（真起 python 进程）", () => stageD(context));
  await stage("E", "MCP HTTP 端到端（真起 mcp/http.ts，三个工具各调一次）", () => stageE(context));
  await stage("F", "跨端同源与引用可追溯（后端 = MCP = 导出）", () => stageF(context));
  if (WITH_FRONTEND) await stage("G", "前端 dev server（SSR 页面）", () => stageG(context));
  if (WITH_SUITES) await stage("H", "既有测试套件", stageH);
  if (WITH_LLM) await stage("I", "模型接通性（记忆触发器模型版 + 真实对话）", () => stageI(context));

  const failed = checks.filter(item => !item.ok);
  const durationMs = Date.now() - startedAt;

  mkdirSync(EVIDENCE_DIR, { recursive: true });
  writeFileSync(
    EVIDENCE_PATH,
    `${JSON.stringify(
      {
        schema: "career-graph-e2e/v1",
        generatedAt: new Date().toISOString(),
        durationMs,
        versions: {
          node: process.version,
          platform: process.platform,
          kbVersion: metrics.kbVersion,
          graphVersion: metrics.graphVersion,
        },
        flags: { withFrontend: WITH_FRONTEND, withSuites: WITH_SUITES, withLlm: WITH_LLM },
        baseline: BASELINE,
        metrics,
        stages,
        checks,
        warnings,
      },
      null,
      2
    )}\n`,
    "utf8"
  );

  console.log("\n──────── 汇总 ────────");
  for (const record of stages) {
    console.log(`  ${record.failed === 0 ? "PASS" : "FAIL"} ${record.id} ${record.title}（${record.checks} 项，${record.durationMs} ms）`);
  }
  console.log(`  断言：${checks.length - failed.length}/${checks.length} 通过，警告 ${warnings.length} 条，总耗时 ${(durationMs / 1000).toFixed(1)} s`);
  console.log(`  证据：${EVIDENCE_PATH}`);

  if (failed.length > 0) {
    console.error("\n失败项：");
    for (const item of failed) console.error(`  [${item.stage}] ${item.label}${item.detail ? ` — ${item.detail}` : ""}`);
    return 1;
  }
  if (warnings.length > 0) {
    console.log("\n警告（不阻断）：");
    for (const item of warnings) console.log(`  [${item.stage}] ${item.label}${item.detail ? ` — ${item.detail}` : ""}`);
  }
  console.log("\n端到端链全绿：原始快照 → 分块 → 图谱 → 导出 → 后端/MCP/前端三端读的是同一份字节。");
  return 0;
}

main()
  .then(code => process.exit(code))
  .catch(error => {
    console.error(error);
    process.exit(1);
  });
