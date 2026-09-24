/**
 * 端到端验证脚本：真实拉起 stdio MCP server → tools/list → tools/call × 3 → 落盘原始结果。
 *
 * 用法：
 *   npm run mcp:verify
 *
 * 它不做任何 mock：客户端真的通过 stdio 子进程跟 `mcp/stdio.ts` 说 JSON-RPC，
 * server 真的从 knowledge/exports/career-graph.json 读盘（与前端同一份数据）。
 *
 * 退出码 0 = 全部断言通过；1 = 至少一条失败（失败项会打在 stderr）。
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

import { TOOL_NAMES } from "../mcp/career-graph-server";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const evidenceDir = resolve(projectRoot, "evidence");

const failures: string[] = [];
function check(label: string, condition: boolean, detail = ""): void {
  const mark = condition ? "PASS" : "FAIL";
  console.log(`[${mark}] ${label}${detail ? ` — ${detail}` : ""}`);
  if (!condition) failures.push(label);
}

/** 客户端与 server 是两条进程，server 的日志只能走 stderr —— 原样转发，别吞掉。 */
const transport = new StdioClientTransport({
  command: process.execPath,
  args: ["--import", "tsx", "mcp/stdio.ts"],
  cwd: projectRoot,
  stderr: "pipe"
});
transport.stderr?.on("data", (chunk: Buffer) => process.stderr.write(`[server] ${chunk}`));

const client = new Client({ name: "career-graph-verify", version: "0.1.0" });

await client.connect(transport);
console.log(`connected: server=${JSON.stringify(client.getServerVersion())}`);

/* ---------------- ① tools/list ---------------- */
const listed = await client.listTools();
const toolNames = listed.tools.map(tool => tool.name).sort();
console.log(`\ntools/list → ${toolNames.length} tools: ${toolNames.join(", ")}`);

check("tools/list 返回 3 个工具", toolNames.length === 3, `实际 ${toolNames.length}`);
check(
  "工具名与 server 注册一致",
  toolNames.join(",") === [...TOOL_NAMES].sort().join(","),
  [...TOOL_NAMES].sort().join(",")
);
for (const tool of listed.tools) {
  check(`  ${tool.name} 有 inputSchema`, Boolean(tool.inputSchema?.properties), Object.keys(tool.inputSchema?.properties ?? {}).join("/"));
}

/* ---------------- ② tools/call ---------------- */
const calls = [
  {
    name: "search_career_knowledge",
    // 「模型量化」= skill:SK215（中文名），图感知检索应该连它的邻域一起吐出来
    args: { query: "模型量化", limit: 3 }
  },
  {
    name: "get_skill_gap",
    // 目标 occupation:AI004，一个技能都不具备 → 缺口必须包含先修链
    args: { target: "occupation:AI004", ownedSkills: [] }
  },
  {
    name: "get_career_graph_view",
    // 前端 knowledge-graph.tsx 渲染同一份视图模型
    args: { focusId: "occupation:AI001", maxHop: 2 }
  }
] as const;

const results: Array<{ name: string; args: unknown; result: unknown }> = [];

for (const call of calls) {
  const result = await client.callTool({ name: call.name, arguments: call.args as Record<string, unknown> });
  results.push({ name: call.name, args: call.args, result });
  const payload = (result as { structuredContent?: unknown }).structuredContent;
  const text = ((result as { content?: Array<{ text?: string }> }).content ?? []).map(part => part.text ?? "").join("");
  const bytes = text.length;
  console.log(`\n──────── tools/call ${call.name} ────────`);
  console.log(text.length > 1600 ? `${text.slice(0, 1600)}\n…（截断，完整内容见 evidence/mcp-verify.json，共 ${bytes} 字符）` : text);
  check(`${call.name} isError !== true`, (result as { isError?: boolean }).isError !== true);
  check(`${call.name} 返回 structuredContent`, Boolean(payload));
}

const byName = new Map(results.map(entry => [entry.name, entry.result as Record<string, unknown>]));

/* ---------------- ③ 「一份数据两端消费」交叉核对 ---------------- */
const searchPayload = byName.get("search_career_knowledge")?.structuredContent as Record<string, unknown> | undefined;
const gapPayload = byName.get("get_skill_gap")?.structuredContent as Record<string, unknown> | undefined;
const viewPayload = byName.get("get_career_graph_view")?.structuredContent as Record<string, unknown> | undefined;

const searchText = JSON.stringify(searchPayload ?? {});
const gapText = JSON.stringify(gapPayload ?? {});

// 前端 lib/client/graph-view.ts 的 RELATION 映射：英文 type 必须已经被适配成这些中文标签
check(
  "检索结果出现前端 RELATION 的中文关系标签",
  /需要技能|前置技能|学习单元|使用工具/.test(searchText),
  "来自 graph-view.ts RELATION"
);
check("缺口结果带先修关系语义", /blockedBy|neededFor|先补|先修|前置技能/.test(gapText));
const viewCounts = (viewPayload?.view as
  | { counts?: { nodes?: number; edges?: number; edgesSkipped?: number; prerequisiteChain?: number } }
  | undefined)?.counts;
check(
  "图谱视图节点数 = 63（与导出 JSON / 前端单测一致）",
  viewCounts?.nodes === 63,
  `counts.nodes=${viewCounts?.nodes}, counts.edges=${viewCounts?.edges}, edgesSkipped=${viewCounts?.edgesSkipped}, prerequisiteChain=${viewCounts?.prerequisiteChain}`
);

/* ---------------- ④ 落盘原始证据 ---------------- */
const evidence = {
  capturedAt: new Date().toISOString(),
  command: "npm run mcp:verify  →  node --import tsx scripts/verify-mcp.ts",
  server: { transport: "stdio", entry: "mcp/stdio.ts", spawn: `${process.execPath} --import tsx mcp/stdio.ts` },
  clientInfo: client.getServerVersion(),
  dataSource: "knowledge/exports/career-graph.json",
  toolsList: listed,
  calls: results,
  failures
};
mkdirSync(evidenceDir, { recursive: true });
const evidencePath = resolve(evidenceDir, "mcp-verify.json");
writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
console.log(`\n原始结果已写入 ${evidencePath}`);

await client.close();

if (failures.length > 0) {
  console.error(`\n${failures.length} 项断言失败：\n - ${failures.join("\n - ")}`);
  process.exit(1);
}
console.log(`\nMCP 端到端验证通过（tools/list ${toolNames.length} 项，tools/call ${results.length} 次）。`);
