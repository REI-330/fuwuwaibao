/**
 * HTTP 传输端到端验证：真实拉起 `mcp/http.ts` → 裸 HTTP（fetch，不借 SDK 客户端）
 * 走 initialize → notifications/initialized → tools/list → tools/call × 3 → 落盘原始报文。
 *
 * 用法：
 *   npm run mcp:verify-http
 *   MCP_HTTP_VERIFY_TARGET=https://<公网隧道域名> npm run mcp:verify-http   # 同一套断言打公网，证据存 mcp-public-verify.json
 *
 * 为什么用裸 fetch 而不是 SDK 的 StreamableHTTPClientTransport：百宝箱接的是
 * 「URL + Header」这一层，任何第三方客户端都会直接看到 SSE 帧。这里假设最少，
 * 只按 MCP 规范拼 JSON-RPC、解析 `data:` 行，因此验证结果对 curl 也可复现。
 *
 * 退出码 0 = 全部断言通过；1 = 至少一条失败（失败项打在 stderr）。
 */
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { TOOL_NAMES } from "../mcp/career-graph-server";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const evidenceDir = resolve(projectRoot, "evidence");

/** 换个端口，避免跟手动 `npm run mcp:http` 的 8787 打架。 */
const PORT = Number(process.env.MCP_HTTP_VERIFY_PORT ?? 8799);
const HOST = "127.0.0.1";
/**
 * 设了 `MCP_HTTP_VERIFY_TARGET`（公网隧道给出的 base，例如 https://xxx.life）就不再本机
 * spawn，直接打这个 base —— 用同一套断言验证「经公网之后」的接口，证据另存为
 * evidence/mcp-public-verify.json，不覆盖本机那份。
 */
const EXTERNAL_BASE = (process.env.MCP_HTTP_VERIFY_TARGET ?? "").replace(/\/+$/, "");
const BASE = EXTERNAL_BASE || `http://${HOST}:${PORT}`;
const MCP_URL = `${BASE}/mcp`;
const EVIDENCE_NAME = EXTERNAL_BASE ? "mcp-public-verify.json" : "mcp-http-verify.json";

const failures: string[] = [];
function check(label: string, condition: boolean, detail = ""): void {
  console.log(`[${condition ? "PASS" : "FAIL"}] ${label}${detail ? ` — ${detail}` : ""}`);
  if (!condition) failures.push(label);
}

/** 每次交互的原始报文都留档，报告里可以直接引用，不必让人复跑。 */
type Exchange = {
  step: string;
  method: string;
  request: { method: string; url: string; headers: Record<string, string>; body?: unknown };
  response: { status: number; headers: Record<string, string>; rawBody: string; messages: unknown[] };
};
const exchanges: Exchange[] = [];

/**
 * 服务器默认按规范回 SSE（`text/event-stream`），但 `enableJsonResponse` 为真时
 * 会直接回 JSON。两种都吃掉，脚本不会因为服务端换了编码方式而假失败。
 */
function parseMessages(raw: string): unknown[] {
  const trimmed = raw.trim();
  if (!trimmed) return [];
  if (trimmed.startsWith("{")) return [JSON.parse(trimmed)];
  return trimmed
    .split(/\r?\n/)
    .filter(line => line.startsWith("data:"))
    .map(line => line.slice("data:".length).trim())
    .filter(Boolean)
    .map(line => JSON.parse(line));
}

/**
 * 递归按键名排序后再序列化。两条链路的 tools/list 都出自同一个 SDK 序列化器，
 * 归一化键序只为排除「同一份内容、不同键序」造成的假失败，不改动任何值。
 */
function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      out[key] = sortKeys((value as Record<string, unknown>)[key]);
    }
    return out;
  }
  return value;
}
function canonical(value: unknown): string {
  return JSON.stringify(sortKeys(value));
}

async function post(
  step: string,
  payload: unknown,
  sessionId?: string
): Promise<{ status: number; headers: Record<string, string>; messages: unknown[]; raw: string }> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    // 规范要求客户端同时接受两种；只发 application/json 会被严格实现拒掉。
    accept: "application/json, text/event-stream"
  };
  if (sessionId) headers["mcp-session-id"] = sessionId;

  const response = await fetch(MCP_URL, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(20000)
  });

  const raw = await response.text();
  const collected: Record<string, string> = {};
  for (const key of ["content-type", "mcp-session-id"]) {
    const value = response.headers.get(key);
    if (value) collected[key] = value;
  }

  exchanges.push({
    step,
    method: (payload as { method?: string }).method ?? "n/a",
    request: { method: "POST", url: MCP_URL, headers, body: payload },
    response: { status: response.status, headers: collected, rawBody: raw, messages: parseMessages(raw) }
  });

  return { status: response.status, headers: collected, messages: parseMessages(raw), raw };
}

let child: ChildProcess | undefined;

async function waitForHealth(attempts = EXTERNAL_BASE ? 6 : 80): Promise<boolean> {
  // 公网隧道首次回源可能慢：外部目标模式放宽单次超时、同时收紧重试次数，避免隧道挂掉时干等。
  const perAttemptMs = EXTERNAL_BASE ? 8000 : 1000;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(`${BASE}/health`, { signal: AbortSignal.timeout(perAttemptMs) });
      if (response.ok) return true;
    } catch {
      // 还没起来，继续等
    }
    await new Promise(resolvePromise => setTimeout(resolvePromise, 250));
  }
  return false;
}

try {
  /* ---------------- ⓪ 拉起真实 server（外部目标模式则跳过） ---------------- */
  if (EXTERNAL_BASE) {
    console.log(`\n外部目标模式：不本机 spawn，直接打 ${BASE}`);
  } else {
    child = spawn(process.execPath, ["--import", "tsx", "mcp/http.ts"], {
      cwd: projectRoot,
      env: { ...process.env, MCP_HTTP_PORT: String(PORT), MCP_HTTP_HOST: HOST },
      stdio: ["ignore", "pipe", "pipe"]
    });
    child.stderr?.on("data", (chunk: Buffer) => process.stderr.write(`[server] ${chunk}`));
    child.stdout?.on("data", (chunk: Buffer) => process.stderr.write(`[server:stdout] ${chunk}`));
  }

  const healthy = await waitForHealth();
  check(`HTTP 入口在 ${BASE} 就绪`, healthy);
  if (!healthy) throw new Error("server 未在预期时间内就绪，后续步骤跳过");

  const healthResponse = await fetch(`${BASE}/health`, { signal: AbortSignal.timeout(5000) });
  const health = (await healthResponse.json()) as { tools?: string[]; kbVersion?: string };
  console.log(`\nhealth → ${JSON.stringify(health)}`);
  check("/health 报出 3 个工具", (health.tools ?? []).length === TOOL_NAMES.length);
  check("/health 的 kbVersion 与导出 JSON 同源", health.kbVersion === "2026.09.15", String(health.kbVersion));

  /* ---------------- ① initialize → 拿 session ---------------- */
  const init = await post("initialize", {
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "verify-mcp-http", version: "0.1.0" }
    }
  });

  const sessionId = init.headers["mcp-session-id"];
  const initMessage = init.messages[0] as { result?: { serverInfo?: { name?: string } } } | undefined;
  console.log(`\n──────── initialize ────────`);
  console.log(`HTTP ${init.status}; content-type=${init.headers["content-type"]}; mcp-session-id=${sessionId ?? "(无)"}`);
  console.log(`serverInfo=${JSON.stringify(initMessage?.result?.serverInfo)}`);

  check("initialize 返回 200", init.status === 200, String(init.status));
  check("响应是 SSE 帧或 JSON", Boolean(init.headers["content-type"]), String(init.headers["content-type"]));
  check("initialize 分配了 mcp-session-id", Boolean(sessionId), sessionId ?? "(缺)");
  check(
    "serverInfo.name = career-graph-mcp",
    initMessage?.result?.serverInfo?.name === "career-graph-mcp",
    String(initMessage?.result?.serverInfo?.name)
  );

  if (!sessionId) throw new Error("没有 session 就无法继续验证有状态链路");

  /* ---------------- ② notifications/initialized ---------------- */
  const notified = await post("notifications/initialized", { jsonrpc: "2.0", method: "notifications/initialized" }, sessionId);
  check("initialized 通知被接受（202 或 200）", notified.status === 202 || notified.status === 200, String(notified.status));

  /* ---------------- ③ tools/list ---------------- */
  const listed = await post("tools/list", { jsonrpc: "2.0", id: 2, method: "tools/list" }, sessionId);
  const listMessage = listed.messages[0] as { result?: { tools?: Array<{ name: string; inputSchema?: { properties?: Record<string, unknown> } }> } };
  const tools = listMessage?.result?.tools ?? [];
  const toolNames = tools.map(tool => tool.name).sort();
  console.log(`\n──────── tools/list ────────`);
  console.log(`tools → ${toolNames.join(", ")}`);

  check("tools/list 返回 3 个工具", toolNames.length === 3, `实际 ${toolNames.length}`);
  check("工具名与 server 注册一致", toolNames.join(",") === [...TOOL_NAMES].sort().join(","), [...TOOL_NAMES].sort().join(","));
  for (const tool of tools) {
    check(`  ${tool.name} 带 inputSchema.properties`, Boolean(tool.inputSchema?.properties), Object.keys(tool.inputSchema?.properties ?? {}).join("/"));
  }

  /* ---------------- ④ tools/call × 3 ---------------- */
  const calls = [
    { name: "search_career_knowledge", args: { query: "模型量化", limit: 3 } },
    { name: "get_skill_gap", args: { target: "occupation:AI004", ownedSkills: [] } },
    { name: "get_career_graph_view", args: { focusId: "occupation:AI001", maxHop: 2 } }
  ] as const;

  const callResults: Array<{ name: string; args: unknown; payload: unknown; text: string; isError: boolean }> = [];

  for (const [index, call] of calls.entries()) {
    const response = await post(
      `tools/call ${call.name}`,
      { jsonrpc: "2.0", id: 10 + index, method: "tools/call", params: { name: call.name, arguments: call.args } },
      sessionId
    );
    const message = response.messages[0] as {
      result?: { isError?: boolean; content?: Array<{ text?: string }>; structuredContent?: unknown };
    };
    const text = (message?.result?.content ?? []).map(part => part.text ?? "").join("");
    callResults.push({
      name: call.name,
      args: call.args,
      payload: message?.result?.structuredContent,
      text,
      isError: message?.result?.isError === true
    });

    console.log(`\n──────── tools/call ${call.name} ────────`);
    console.log(text.length > 1200 ? `${text.slice(0, 1200)}\n…（截断，完整见 evidence/${EVIDENCE_NAME}，共 ${text.length} 字符）` : text);
    check(`${call.name} isError !== true`, !callResults[index].isError);
    check(`${call.name} 经 HTTP 也返回 structuredContent`, Boolean(message?.result?.structuredContent));
  }

  const byName = new Map(callResults.map(entry => [entry.name, JSON.stringify(entry.payload ?? {})]));

  /* ---------------- ⑤ 「一份数据两端消费」交叉核对 ---------------- */
  check(
    "检索结果出现前端 RELATION 的中文关系标签",
    /需要技能|前置技能|学习单元|使用工具/.test(byName.get("search_career_knowledge") ?? ""),
    "来自 lib/client/graph-view.ts 的 RELATION"
  );
  check(
    "缺口结果沿 prerequisite 给出先修语义",
    /blockedBy|neededFor|先补|先修|前置技能/.test(byName.get("get_skill_gap") ?? "")
  );
  const viewPayload = callResults.find(entry => entry.name === "get_career_graph_view")?.payload as
    | {
        view?: {
          nodes?: unknown[];
          edges?: unknown[];
          counts?: { nodes?: number; edges?: number; edgesSkipped?: number; prerequisiteChain?: number };
        };
      }
    | undefined;

  check(
    "图谱视图节点数 = 63（与导出 JSON、前端单测一致）",
    viewPayload?.view?.counts?.nodes === 63 && viewPayload?.view?.nodes?.length === 63,
    `counts.nodes=${viewPayload?.view?.counts?.nodes}, nodes.length=${viewPayload?.view?.nodes?.length}`
  );
  check(
    "图谱视图可渲染边 = 117（226 条边中 109 条 chunk: 证据边按设计跳过）",
    viewPayload?.view?.counts?.edges === 117 && viewPayload?.view?.counts?.edgesSkipped === 109,
    `counts.edges=${viewPayload?.view?.counts?.edges}, edgesSkipped=${viewPayload?.view?.counts?.edgesSkipped}`
  );
  check(
    "图谱视图先修链 = 18 段（prerequisite 边走通）",
    viewPayload?.view?.counts?.prerequisiteChain === 18,
    `counts.prerequisiteChain=${viewPayload?.view?.counts?.prerequisiteChain}`
  );

  /* ---------------- ⑥ 与 stdio 链路比对：同一个 server 工厂 ---------------- */
  const stdioEvidencePath = resolve(evidenceDir, "mcp-verify.json");
  let crossCheck = "evidence/mcp-verify.json 不存在，跳过与 stdio 链路比对（先跑 npm run mcp:verify）";
  if (existsSync(stdioEvidencePath)) {
    const stdioEvidence = JSON.parse(readFileSync(stdioEvidencePath, "utf8")) as {
      toolsList?: { tools?: Array<{ name: string }> };
    };
    // 两侧都用同一投影：整条 tool 对象（name/title/description/inputSchema/annotations/execution），
    // 只按 name 排序 + 归一化键序，不做任何字段裁剪，避免「只比了子集」的假通过。
    const stdioTools = [...(stdioEvidence.toolsList?.tools ?? [])].sort((a, b) => a.name.localeCompare(b.name));
    const httpTools = [...tools].sort((a, b) => a.name.localeCompare(b.name));

    const identical = canonical(stdioTools) === canonical(httpTools);
    check("stdio 与 HTTP 两条链路的 tools/list 逐字相同（同一个 createCareerGraphServer）", identical);
    crossCheck = identical
      ? "两条链路 tools/list（整条 tool 对象：名称+描述+inputSchema+annotations+execution）逐字一致"
      : "两条链路 tools/list 不一致 —— 见 evidence/mcp-http-verify.json 与 evidence/mcp-verify.json";
  } else {
    console.log(`\n[SKIP] ${crossCheck}`);
  }

  /* ---------------- ⑦ 落盘原始证据 ---------------- */
  mkdirSync(evidenceDir, { recursive: true });
  const evidencePath = resolve(evidenceDir, EVIDENCE_NAME);
  writeFileSync(
    evidencePath,
    `${JSON.stringify(
      {
        capturedAt: new Date().toISOString(),
        command: EXTERNAL_BASE
          ? `MCP_HTTP_VERIFY_TARGET=${BASE} npm run mcp:verify-http  →  node --import tsx scripts/verify-mcp-http.ts`
          : "npm run mcp:verify-http  →  node --import tsx scripts/verify-mcp-http.ts",
        server: {
          transport: "streamable-http",
          entry: "mcp/http.ts",
          spawn: EXTERNAL_BASE ? null : `${process.execPath} --import tsx mcp/http.ts`,
          externalTarget: EXTERNAL_BASE || null,
          endpoint: MCP_URL,
          healthEndpoint: `${BASE}/health`
        },
        health,
        sessionId,
        toolsList: { tools },
        calls: callResults,
        exchanges,
        crossCheck,
        failures
      },
      null,
      2
    )}\n`,
    "utf8"
  );
  console.log(`\n原始报文已写入 ${evidencePath}`);
} finally {
  if (child && child.exitCode === null) {
    child.kill("SIGTERM");
  }
  interactionsCleanup();
}

function interactionsCleanup(): void {
  // 占位：退出前无需额外清理（会话随进程终止关闭）。
}

if (failures.length > 0) {
  console.error(`\n${failures.length} 项断言失败：\n - ${failures.join("\n - ")}`);
  process.exit(1);
}
console.log(`\nHTTP 传输端到端验证通过（tools/list ${TOOL_NAMES.length} 项，tools/call ${TOOL_NAMES.length} 次）。`);
