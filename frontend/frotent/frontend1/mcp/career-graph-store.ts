/**
 * MCP 侧的数据访问层。
 *
 * 铁律②「一份数据两端消费」：这里读的仍然是 `knowledge/exports/career-graph.json`（第 11 步产出的真实导出），
 * 英文 `type` → 中文 relation 的翻译也仍然调用 `lib/client/career-graph.ts` 的同一份实现。
 * 本文件不复制粘贴任何前端逻辑，只做「读盘 + 建索引」。
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { adaptCareerGraph, relationFromType, type AdaptReport, type CareerGraph, type GraphEdge } from "../lib/client/career-graph";
import { buildAdjacency, type GraphAdjacency } from "../lib/client/graph-view";

/* ------------------------------------------------------------------ *
 * 1. 导出的字段形状（L1 契约）
 * ------------------------------------------------------------------ */

export type MarkdownSource = {
  sourceId: string;
  title: string;
  publisher: string;
  url: string;
  publishedAt: string | null;
  collectedAt: string;
  license: string;
  /** 这份资料能支持 / 不能支持什么结论，检索时用来挡越界提问。 */
  scopeZh: string;
  sha256: string;
  chunkCount: number;
};

export type KnowledgeChunk = {
  chunkId: string;
  sourceId: string;
  heading: string;
  sectionPath: string;
  charRange: [number, number];
  text: string;
  tags: string[];
  version: number;
};

export type WikiPage = {
  entityId: string;
  entityType: string;
  title: string;
  path: string;
  version: string;
  review: string;
  reviewedBy: string | null;
  sources: string[];
  internalLinks: string[];
};

export type EvaluationQuestion = {
  questionId: string;
  category: string;
  question: string;
  answerable: boolean;
  referenceChunks: string[];
  gradingNotes: string;
};

export type KnowledgeNode = {
  id: string;
  kind: string;
  label: string;
  aliases?: string[];
  description?: string;
  x: number;
  y: number;
  sourceRefs?: string[];
};

/** 原始边。`type` 是英文标识，`weight` 等字段只有部分边有，所以除主键外全部可选。 */
export type KnowledgeEdge = {
  id?: string;
  type: string;
  from: string;
  to: string;
  weight?: number;
  targetLevel?: number;
  importance?: number;
  sourceRefs?: string[];
  annotatedBy?: string;
  updatedAt?: string;
  deliverable?: string;
  assessmentPoints?: string[];
  deltaSkills?: string[];
  horizon?: number;
  year?: number;
  direction?: string;
};

export type KnowledgeMeta = {
  kbVersion: string;
  graphVersion: string;
  generatedAt: string;
  status: string;
  note?: string;
  adapterNotes?: string[];
  counts?: Record<string, number>;
};

export type CareerKnowledgeExport = {
  meta: KnowledgeMeta;
  sources: MarkdownSource[];
  chunks: KnowledgeChunk[];
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
  wikiPages: WikiPage[];
  evaluationQuestions: EvaluationQuestion[];
};

/* ------------------------------------------------------------------ *
 * 2. 读盘
 * ------------------------------------------------------------------ */

/** 从本文件位置推回仓库根：`<root>/frontend/frotent/frontend1/mcp/career-graph-store.ts`。 */
const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = resolve(MODULE_DIR, "../../../..");
export const EXPORT_RELATIVE_PATH = "knowledge/exports/career-graph.json";

/**
 * 默认导出路径：第 11 步产出的真实导出 `knowledge/exports/career-graph.json`。
 * 允许 `CAREER_GRAPH_EXPORT` 覆盖（指向别的版本或临时产物）。
 * 用 `globalThis.process` 取值，避免依赖 Node 全局类型声明。
 */
export function defaultExportPath(): string {
  const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env;
  const override = env?.CAREER_GRAPH_EXPORT?.trim();
  return override ? resolve(override) : resolve(REPO_ROOT, EXPORT_RELATIVE_PATH);
}

export function readExport(path: string = defaultExportPath()): CareerKnowledgeExport {
  if (!existsSync(path)) {
    throw new Error(`找不到图谱导出文件：${path}（可用环境变量 CAREER_GRAPH_EXPORT 覆盖路径）`);
  }
  const parsed = JSON.parse(readFileSync(path, "utf8")) as Partial<CareerKnowledgeExport>;
  if (!parsed.meta || !Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) {
    throw new Error(`图谱导出文件缺少 meta / nodes / edges 三个必需段落：${path}`);
  }
  return {
    meta: parsed.meta,
    sources: parsed.sources ?? [],
    chunks: parsed.chunks ?? [],
    nodes: parsed.nodes,
    edges: parsed.edges,
    wikiPages: parsed.wikiPages ?? [],
    evaluationQuestions: parsed.evaluationQuestions ?? []
  };
}

/* ------------------------------------------------------------------ *
 * 3. 索引
 * ------------------------------------------------------------------ */

export type CareerKnowledgeStore = {
  path: string;
  raw: CareerKnowledgeExport;
  /** 与前端组件同一份适配结果：`type` 已翻译成 `relation`。 */
  graph: CareerGraph;
  /** 适配时的丢弃记录（未知 kind / 缺坐标），报告里直接引用。 */
  adaptReport: AdaptReport;
  nodeById: Map<string, KnowledgeNode>;
  rawEdgeById: Map<string, KnowledgeEdge>;
  /** 与前端 `buildGraphView` 共用的邻接表（已排除 `chunk:` 与悬空边）。 */
  adjacency: GraphAdjacency;
  chunkById: Map<string, KnowledgeChunk>;
  sourceById: Map<string, MarkdownSource>;
  wikiByEntityId: Map<string, WikiPage>;
  wikiByPath: Map<string, WikiPage>;
  questions: EvaluationQuestion[];
};

export function createStore(path: string = defaultExportPath()): CareerKnowledgeStore {
  const raw = readExport(path);
  const { graph, report } = adaptCareerGraph(raw);

  const nodeById = new Map<string, KnowledgeNode>();
  for (const node of raw.nodes) nodeById.set(node.id, node);

  const rawEdgeById = new Map<string, KnowledgeEdge>();
  for (const edge of raw.edges) if (edge.id) rawEdgeById.set(edge.id, edge);

  const chunkById = new Map<string, KnowledgeChunk>();
  for (const chunk of raw.chunks) chunkById.set(chunk.chunkId, chunk);

  const sourceById = new Map<string, MarkdownSource>();
  for (const source of raw.sources) sourceById.set(source.sourceId, source);

  const wikiByEntityId = new Map<string, WikiPage>();
  const wikiByPath = new Map<string, WikiPage>();
  for (const page of raw.wikiPages) {
    wikiByEntityId.set(page.entityId, page);
    wikiByPath.set(page.path, page);
  }

  return {
    path,
    raw,
    graph,
    adaptReport: report,
    nodeById,
    rawEdgeById,
    adjacency: buildAdjacency(graph),
    chunkById,
    sourceById,
    wikiByEntityId,
    wikiByPath,
    questions: raw.evaluationQuestions
  };
}

let cachedDefaultStore: CareerKnowledgeStore | null = null;

/** MCP server 启动时用；同一进程内只解析一次 JSON。 */
export function getDefaultStore(): CareerKnowledgeStore {
  if (!cachedDefaultStore) cachedDefaultStore = createStore();
  return cachedDefaultStore;
}

/* ------------------------------------------------------------------ *
 * 4. 名字 → 节点 id
 * ------------------------------------------------------------------ */

export type NodeResolution = { id: string | null; matchedBy: "id" | "label" | "alias" | "ref" | "substring" | "none" | "ambiguous" };

function normalize(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, "");
}

/**
 * 让 LLM 可以按 id / 中文名 / 别名 / 裸编号（`SK090`）任意一种方式指代同一个节点。
 * 命中多个时返回 `ambiguous`，绝不瞎猜一个。
 */
export function resolveNodeId(store: CareerKnowledgeStore, reference: string): NodeResolution {
  const raw = reference.trim();
  if (!raw) return { id: null, matchedBy: "none" };

  if (store.nodeById.has(raw)) return { id: raw, matchedBy: "id" };

  const needle = normalize(raw);
  const exact: { id: string; matchedBy: NodeResolution["matchedBy"] }[] = [];
  const loose: string[] = [];

  for (const node of store.raw.nodes) {
    if (normalize(node.id) === needle) exact.push({ id: node.id, matchedBy: "id" });
    else if (normalize(node.label) === needle) exact.push({ id: node.id, matchedBy: "label" });
    else if ((node.aliases ?? []).some(alias => normalize(alias) === needle)) exact.push({ id: node.id, matchedBy: "alias" });
    else if (normalize(node.id.slice(node.id.indexOf(":") + 1)) === needle) exact.push({ id: node.id, matchedBy: "ref" });
    else if (
      normalize(node.label).includes(needle) ||
      normalize(node.id).includes(needle) ||
      (node.aliases ?? []).some(alias => normalize(alias).includes(needle))
    ) {
      loose.push(node.id);
    }
  }

  const id = exact[0]?.id ?? (loose.length === 1 ? loose[0] : null);
  if (exact[0]) return { id, matchedBy: exact[0].matchedBy };
  if (loose.length === 1) return { id: loose[0], matchedBy: "substring" };
  if (loose.length > 1) return { id: null, matchedBy: "ambiguous" };
  return { id: null, matchedBy: "none" };
}

/* ------------------------------------------------------------------ *
 * 5. 溯源
 * ------------------------------------------------------------------ */

export type Citation = {
  chunkId: string;
  heading: string;
  text: string;
  tags: string[];
  sourceId: string;
  sourceTitle: string;
  sourceUrl: string;
  sourceScopeZh: string;
  sourceLicense: string;
  /** 来源的定版日期；导出里 26 份**全部为 null**（公开文档站多无明确定版日期）。 */
  sourcePublishedAt: string | null;
  /**
   * 时效的人话说明。`publishedAt` 为 null 时明确写「缺少发布时间」——
   * 缺字段不等于「没有时效这回事」，调用方不该把它当成空字符串忽略掉。
   * 也绝不拿 `collectedAt`（我们什么时候抓的）顶上，那是两件事。
   */
  sourceTimeNote: string;
};

export function citationForChunk(store: CareerKnowledgeStore, chunkId: string): Citation | null {
  const chunk = store.chunkById.get(chunkId);
  if (!chunk) return null;
  const source = store.sourceById.get(chunk.sourceId);
  const publishedAt = source?.publishedAt ?? null;
  return {
    chunkId: chunk.chunkId,
    heading: chunk.heading,
    text: chunk.text,
    tags: chunk.tags,
    sourceId: chunk.sourceId,
    sourceTitle: source?.title ?? chunk.sourceId,
    sourceUrl: source?.url ?? "",
    sourceScopeZh: source?.scopeZh ?? "",
    sourceLicense: source?.license ?? "",
    sourcePublishedAt: publishedAt,
    sourceTimeNote: publishedAt
      ? `来源定版于 ${publishedAt}`
      : "缺少发布时间：来源站未标明定版日期（按设计留空，不拿采集日期顶上）"
  };
}

/**
 * 一个节点「凭什么这么说」：先看 `evidenced_by` 边指向的 chunk，再兜底 `sourceRefs`。
 * 只有真的读到 chunk 文本才算证据，找不到就返回空数组而不是编造。
 */
export function citationsForNode(store: CareerKnowledgeStore, nodeId: string): Citation[] {
  const chunkIds: string[] = [];
  for (const edge of store.raw.edges) {
    if (edge.from !== nodeId) continue;
    if (edge.type !== "evidenced_by") continue;
    if (edge.to.startsWith("chunk:")) chunkIds.push(edge.to.slice("chunk:".length));
  }
  for (const chunkId of store.nodeById.get(nodeId)?.sourceRefs ?? []) chunkIds.push(chunkId);

  const seen = new Set<string>();
  const citations: Citation[] = [];
  for (const chunkId of chunkIds) {
    if (seen.has(chunkId)) continue;
    seen.add(chunkId);
    const citation = citationForChunk(store, chunkId);
    if (citation) citations.push(citation);
  }
  return citations;
}

/** 边的中文标签：适配层已经翻好了，这里只做防御性兜底，避免出现 `undefined`。 */
export function edgeRelationLabel(edge: GraphEdge): string {
  const relation = edge.relation?.trim();
  if (relation) return relation;
  return relationFromType(edge.type ?? "");
}
