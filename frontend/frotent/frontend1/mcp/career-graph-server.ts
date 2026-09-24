/**
 * 职业图谱 MCP server（TypeScript，stdio 传输）。
 *
 * 与前端同一份数据、同一套纯函数（铁律②）：
 *   knowledge/exports/career-graph.json
 *     └─ lib/client/career-graph.ts   (adaptCareerGraph：英文 type → 中文 relation)
 *     └─ lib/client/graph-view.ts     (buildAdjacency / hopDistances / buildGraphView / describeGraphView)
 *
 * 为什么值得做 MCP 而不是再包一层检索：这里的三个工具回答的是「图上能推出来、
 * 但单条文本答不了」的问题 —— 缺口怎么排学习序、某项能力挂在哪段原文、哪个节点是焦点。
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { GRAPH_NODE_KINDS, type GraphNodeKind } from "../lib/client/career-graph";
import { getDefaultStore, type CareerKnowledgeStore } from "./career-graph-store";
import { getCareerGraphView, getSkillGap, searchCareerKnowledge } from "./career-graph-tools";

export const SERVER_INFO = { name: "career-graph-mcp", version: "0.1.0" } as const;

export const TOOL_NAMES = ["search_career_knowledge", "get_skill_gap", "get_career_graph_view"] as const;

/** zod 需要非空元组，而 `GRAPH_NODE_KINDS` 是 readonly 数组 —— 只在这里做一次窄化。 */
const KIND_TUPLE = GRAPH_NODE_KINDS as unknown as [GraphNodeKind, ...GraphNodeKind[]];

type ToolPayload = { ok: boolean; error?: string };

/**
 * 统一返回结构：`content` 给人/LLM 看（JSON 文本），`structuredContent` 给程序断言。
 * `ok === false` 时按 MCP 规范置 `isError`，让客户端知道这次调用是失败而不是空结果。
 */
function jsonResult<T extends ToolPayload>(payload: T) {
  const failed = payload.ok === false;
  return {
    content: [
      {
        type: "text" as const,
        text: failed
          ? `调用失败：${payload.error ?? "未知错误"}\n\n${JSON.stringify(payload, null, 2)}`
          : JSON.stringify(payload, null, 2)
      }
    ],
    structuredContent: payload as unknown as Record<string, unknown>,
    isError: failed
  };
}

export function createCareerGraphServer(store: CareerKnowledgeStore = getDefaultStore()): McpServer {
  const server = new McpServer(SERVER_INFO, {
    instructions:
      "职业/技能图谱知识库。回答职业能力、技能先修、技能缺口这类问题时优先用这里的工具，" +
      "而不是凭印象作答；每个结果都带 sourceRefs → chunk → 资料出处，引用时请保留出处。"
  });

  server.registerTool(
    "search_career_knowledge",
    {
      title: "检索职业图谱知识",
      description:
        "在同一份版本化图谱（63 个节点 / 226 条边 / 494 段原文）里做图感知检索：命中节点会连同图邻域关系、" +
        "chunk 级引用与 wiki 路径一起返回，而不是只甩一段文本。用于「某职业需要什么」「某技能属于哪个能力域」这类问题。",
      inputSchema: {
        query: z.string().min(1).describe("检索词，例如「模型量化」「边缘 AI 工程师」「SK090」。中英文均可。"),
        limit: z.number().int().min(1).max(20).optional().describe("最多返回几条命中，默认 5。"),
        kinds: z.array(z.enum(KIND_TUPLE)).optional().describe("只在这些节点类型里检索，默认全部 8 类。")
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false }
    },
    async ({ query, limit, kinds }) => jsonResult(searchCareerKnowledge(store, { query, limit, kinds }))
  );

  server.registerTool(
    "get_skill_gap",
    {
      title: "算技能缺口与学习顺序",
      description:
        "给定目标职业/技能和你已有的技能，算出缺口，并沿 prerequisite 边做先修闭包，给出「先学什么再学什么」的顺序。" +
        "这一步是图推理，不是关键词检索 —— 它回答的是「想学模型量化，得先补什么」。",
      inputSchema: {
        target: z.string().min(1).describe("目标职业或技能：可用节点 id（occupation:AI004 / skill:SK215）、中文名或别名。"),
        ownedSkills: z.array(z.string()).optional().describe("已具备的技能，支持 id / 中文名 / 裸编号（SK090），无法解析的会原样回显在 unmatchedOwnedSkills。"),
        maxHop: z.number().int().min(1).max(4).optional().describe("邻域半径，默认 2。")
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false }
    },
    async ({ target, ownedSkills, maxHop }) => jsonResult(getSkillGap(store, { target, ownedSkills, maxHop }))
  );

  server.registerTool(
    "get_career_graph_view",
    {
      title: "取焦点图谱视图",
      description:
        "返回前端组件真正渲染的那份视图模型（lib/client/graph-view.ts 的 buildGraphView 输出）：每个节点的 focal/hop1/hop2/context/dimmed 状态、" +
        "每条边的 prerequisite-active 等状态、跳数表、包围盒与相机。用于回答「以某职业为焦点，图上会亮哪些东西」。",
      inputSchema: {
        focusId: z.string().optional().describe("焦点节点 id / 中文名；不传则返回无焦点的全局视图。"),
        maxHop: z.number().int().min(1).max(4).optional().describe("描边半径，默认 2。"),
        animate: z.boolean().optional().describe("是否输出逐跳动画参数（delayMs / drawOn），默认 false，便于机器比对。")
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false }
    },
    async ({ focusId, maxHop, animate }) => jsonResult(getCareerGraphView(store, { focusId, maxHop, animate }))
  );

  return server;
}
