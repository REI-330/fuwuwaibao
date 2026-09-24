/**
 * Streamable HTTP 入口：`npm run mcp:http`（默认 http://127.0.0.1:8787/mcp）。
 *
 * 为什么需要它：stdio 传输要求宿主能在本机 spawn 一个子进程，而云端百宝箱
 * （b.tbox.cn）的「创建 MCP 服务 → 自部署 MCP」只开放两种连接方式：`sse` 与
 * `streamableHttp`（实测见 evidence/tbox-mcp-transport.md）。所以要被平台接入，
 * 同一套工具必须再挂一层 HTTP 传输。
 *
 * 铁律②不变：这里只负责 HTTP 编解码与会话管理，server 仍由
 * `createCareerGraphServer(store)` 构建 —— 与前端同一份 JSON、同一套纯函数。
 * 三个工具的名字、schema、返回结构在 stdio 与 HTTP 两条链路上逐字相同。
 *
 * 会话策略（两条路都兜住，实测过的客户端行为不确定时不必二选一）：
 *   - 收到 `initialize`                    → 有状态：生成 mcp-session-id，之后的请求按该头复用。
 *   - 其余请求带已登记的 mcp-session-id    → 有状态：复用同一 transport。
 *   - 其余请求不带 session / session 未知  → 无状态：本次请求一次性 transport，用完即弃。
 *     （SDK 明确要求无状态 transport 不可跨请求复用，见 webStandardStreamableHttp.js:172）
 */
import { randomUUID } from "node:crypto";
import http from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import process from "node:process";

import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";

import { createCareerGraphServer, SERVER_INFO, TOOL_NAMES } from "./career-graph-server";
import { getDefaultStore } from "./career-graph-store";

const MCP_PATH = "/mcp";
const HEALTH_PATH = "/health";

const PORT = Number(process.env.MCP_HTTP_PORT ?? 8787);
const HOST = process.env.MCP_HTTP_HOST ?? "127.0.0.1";

/** 有状态会话：sessionId → transport。仅承载 `initialize` 起的会话。 */
const sessions = new Map<string, StreamableHTTPServerTransport>();

/** 与 stdio 入口共用同一个缓存 store —— 同一份版本化 JSON，不各读一份。 */
const store = getDefaultStore();

function headerValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function log(line: string): void {
  process.stderr.write(`[${SERVER_INFO.name}] ${line}\n`);
}

function sendJson(res: ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload);
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "content-length": Buffer.byteLength(body) });
  res.end(body);
}

/** JSON-RPC 错误用规范里的错误码，便于客户端区分「协议错」和「业务错」。 */
function sendRpcError(res: ServerResponse, status: number, code: number, message: string): void {
  sendJson(res, status, { jsonrpc: "2.0", error: { code, message }, id: null });
}

async function readBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(chunk as Buffer);
  }
  if (chunks.length === 0) return undefined;
  const text = Buffer.concat(chunks).toString("utf8");
  return text.trim() ? JSON.parse(text) : undefined;
}

/** JSON-RPC 的 `initialize` 是会话起点，只有它能开有状态会话。 */
function isInitializeRequest(body: unknown): boolean {
  return (
    typeof body === "object" &&
    body !== null &&
    (body as { method?: unknown }).method === "initialize" &&
    "id" in (body as object)
  );
}

async function handleMcp(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const sessionId = headerValue(req.headers["mcp-session-id"]);
  const existing = sessionId ? sessions.get(sessionId) : undefined;

  if (existing) {
    await existing.handleRequest(req, res);
    return;
  }

  // GET 是 SSE 长连接、DELETE 是结束会话，两者都必须绑定一个已登记的会话。
  if (req.method === "GET" || req.method === "DELETE") {
    sendRpcError(res, 400, -32000, "Bad Request: 缺失或未知的 mcp-session-id");
    return;
  }

  if (req.method !== "POST") {
    sendRpcError(res, 405, -32000, `Method Not Allowed: ${req.method ?? "unknown"}`);
    return;
  }

  let body: unknown;
  try {
    body = await readBody(req);
  } catch (error) {
    sendRpcError(res, 400, -32700, `Parse error: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  const stateful = isInitializeRequest(body);

  const transport = new StreamableHTTPServerTransport(
    stateful
      ? {
          sessionIdGenerator: () => randomUUID(),
          onsessioninitialized: (id) => {
            sessions.set(id, transport);
            log(`会话建立 ${id}（当前 ${sessions.size} 个）`);
          },
          onsessionclosed: (id) => {
            sessions.delete(id);
            log(`会话结束 ${id}（当前 ${sessions.size} 个）`);
          }
        }
      : { sessionIdGenerator: undefined }
  );

  transport.onerror = (error: Error) => {
    log(`传输错误：${error.message}`);
  };

  const server = createCareerGraphServer(store);

  if (stateful) {
    transport.onclose = () => {
      // sessionId 在 initialize 处理时才赋值，所以这里必须惰性读取。
      const id = transport.sessionId;
      if (id) sessions.delete(id);
    };
  } else {
    // 无状态：本请求就是它的完整生命周期。等响应真正写完再回收，
    // 否则可能截断 SSE 尾部（SDK 官方示例也是挂在 res 的 close 上）。
    res.on("close", () => {
      void transport.close().catch(() => undefined);
      void server.close().catch(() => undefined);
    });
  }

  await server.connect(transport);
  await transport.handleRequest(req, res, body);
}

async function main(): Promise<void> {
  const httpServer = http.createServer((req, res) => {
    const url = new URL(req.url ?? "/", `http://${req.headers.host ?? `${HOST}:${PORT}`}`);
    const route = url.pathname.replace(/\/+$/, "") || "/";

    if (route === HEALTH_PATH) {
      sendJson(res, 200, {
        ok: true,
        server: SERVER_INFO,
        tools: TOOL_NAMES,
        dataSource: store.path,
        kbVersion: store.raw.meta.kbVersion,
        activeSessions: sessions.size
      });
      return;
    }

    if (route === MCP_PATH) {
      void handleMcp(req, res).catch((error: unknown) => {
        log(`请求处理失败：${error instanceof Error ? error.stack ?? error.message : String(error)}`);
        if (!res.headersSent) {
          sendRpcError(res, 500, -32603, `Internal error: ${error instanceof Error ? error.message : String(error)}`);
        } else {
          res.end();
        }
      });
      return;
    }

    sendJson(res, 404, { ok: false, error: `未知路径 ${url.pathname}；MCP 端点在 ${MCP_PATH}，健康检查在 ${HEALTH_PATH}` });
  });

  await new Promise<void>((resolve, reject) => {
    httpServer.once("error", reject);
    httpServer.listen(PORT, HOST, () => {
      httpServer.off("error", reject);
      resolve();
    });
  });

  log(
    `streamable HTTP 就绪：http://${HOST}:${PORT}${MCP_PATH}（健康检查 ${HEALTH_PATH}）；` +
      `数据源 ${store.path}（${store.raw.meta.kbVersion}）；工具 ${TOOL_NAMES.join(" / ")}。`
  );

  const shutdown = (signal: string): void => {
    log(`收到 ${signal}，关闭 ${sessions.size} 个会话。`);
    for (const transport of sessions.values()) {
      void transport.close().catch(() => undefined);
    }
    sessions.clear();
    httpServer.close(() => process.exit(0));
    // 兜底：SSE 长连接可能拖住 close 回调。
    setTimeout(() => process.exit(0), 1500).unref();
  };

  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));
}

main().catch((error: unknown) => {
  process.stderr.write(
    `[${SERVER_INFO.name}] HTTP 入口启动失败：${error instanceof Error ? error.message : String(error)}\n`
  );
  process.exit(1);
});
