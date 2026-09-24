/**
 * stdio 入口：`node --import tsx mcp/stdio.ts`（见 package.json 的 `npm run mcp`）。
 *
 * 注意：stdout 是 JSON-RPC 通道，唯一能写日志的地方是 stderr。
 * 任何 `console.log` 都会污染协议流，让客户端解析失败。
 */
import process from "node:process";

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { createCareerGraphServer, SERVER_INFO, TOOL_NAMES } from "./career-graph-server";
import { getDefaultStore } from "./career-graph-store";

async function main(): Promise<void> {
  const store = getDefaultStore();
  const server = createCareerGraphServer(store);
  await server.connect(new StdioServerTransport());
  process.stderr.write(
    `[${SERVER_INFO.name}@${SERVER_INFO.version}] stdio 就绪；数据源 ${store.path}（${store.raw.meta.kbVersion}）；` +
      `工具 ${TOOL_NAMES.join(" / ")}。\n`
  );
}

main().catch((error: unknown) => {
  process.stderr.write(`[${SERVER_INFO.name}] 启动失败：${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
});
