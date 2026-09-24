#!/usr/bin/env node
/**
 * 步骤 10：布局与索引（设计文档 §6 L245、§3.1 L110）
 *
 * 只做两件事，都不产生新的业务结论：
 *
 *   1) 给每个节点算一对**确定性的** x/y。设计文档 §3.1 L110 说得很直白：「x/y 是预算好的
 *      确定性布局坐标，由构建流水线第 10 步产出并版本化……不要每次渲染跑力导向布局。」
 *      所以这里没有随机数、没有 Date、没有迭代收敛 —— 同一份 accepted.jsonl 跑一万次，
 *      坐标一模一样，录屏与截图才稳。
 *   2) 建检索索引：节点级 BM25 倒排（MCP 的 `search_career_knowledge` 是图感知检索，
 *      命中的是节点）+ chunk 级 BM25 倒排（第 11 步的检索命中率要用）+ 别名倒排
 *      （§4.1 L188：`aliases` 是实体识别的确定性入口，先查别名词典，不靠模型猜）。
 *
 * 产物：
 *   knowledge/graph/graph.json        含 x/y 的图（第 09 步的 09-10 / 09-11 就从这里取坐标与 weight）
 *   knowledge/graph/search-index.json 检索索引
 *
 * 关于 weight：设计文档 §3.2 的边结构里有 `weight`，但抽取层唯一给出的量化字段是 `requires`
 * 上的 `importance`（0–1，「这项技能对这个岗位有多重要」）。两者本来就是同一个数字，所以按
 * WEIGHT_RULE 只给 `requires` 写 weight = importance，其余边类型宁缺不造（§9.4 明确「缺 weight
 * 时不要用线宽表示重要度」，前端对 weight === undefined 的边不做线宽映射）。
 *
 * 本步不碰 annotatedBy，也就不可能把 llm-draft 洗成 llm-reviewed —— 06 仍是唯一通道（L251）。
 */
import { existsSync } from "node:fs";

import {
  ACCEPTED_FILE,
  ADJUDICATION_FILE,
  ALIASES_FILE,
  CHUNKS_FILE,
  GRAPH_FILE,
  SEARCH_INDEX_FILE,
  TAXONOMY_FILE,
  relPath,
} from "./lib/paths.mjs";
import { fail, logLine, nowIso, readJson, readJsonl, recordStep, writeJson } from "./lib/log.mjs";
import {
  DEFAULT_CANVAS,
  EDGE_ORDER,
  GRAPH_VERSION,
  KB_VERSION,
  KIND_ORDER,
  NODE_HALF_HEIGHT,
  NODE_HALF_WIDTH,
  WEIGHT_RULE,
  buildBm25,
  chunkIdOf,
  describeCounts,
  describeTokenizer,
  edgeWeight,
  exportEdge,
  exportNode,
  loadAccepted,
  shortRef,
  sortEdges,
  sortNodes,
} from "./lib/graph.mjs";

const STEP = "10";
const TITLE = "布局与索引";
const COMMAND = "node knowledge/pipeline/10-layout-index.mjs";

const startedAt = nowIso();

if (!existsSync(ACCEPTED_FILE)) fail(`缺少 ${relPath(ACCEPTED_FILE)}，请先跑 06-adjudicate.mjs`);
if (!existsSync(CHUNKS_FILE)) fail(`缺少 ${relPath(CHUNKS_FILE)}，请先跑 03-chunk-topics.mjs`);
if (!existsSync(TAXONOMY_FILE)) fail(`缺少 ${relPath(TAXONOMY_FILE)}：它是节点 kind 顺序的唯一来源`);

const { nodes, edges } = loadAccepted();
const chunks = readJsonl(CHUNKS_FILE);
const taxonomy = readJson(TAXONOMY_FILE);
const aliases = existsSync(ALIASES_FILE) ? readJson(ALIASES_FILE) : { entries: [] };
const adjudication = existsSync(ADJUDICATION_FILE) ? readJson(ADJUDICATION_FILE) : { aliasDecisions: [] };

const kindOrder = taxonomy.nodeKinds.map((spec) => spec.id);

// ---------- 1. 布局 ----------

/**
 * 网格几何（刻意与前端 `lib/client/graph-view.ts` 的胶囊尺寸对齐，常量从 lib/graph.mjs 取）：
 *   胶囊 210×58 → 5 列 × 210 = 1050 = 画布宽，横向正好铺满且互不重叠；
 *   8 类节点各占一条横向色带（顺序 = taxonomy.nodeKinds），带内按 id 升序、每行 5 个。
 * 纵向行数是唯一必须妥协的维度：8 条色带共 15 行，画布只给得起 54px 行距（比胶囊高度少 4px），
 * 相邻两行会有轻微重叠 —— 这是画布配额下的取舍，写进 graph.json 的 layout.note 备查。
 */
const COLUMNS = 5;
const COLUMN_STEP = 2 * NODE_HALF_WIDTH;
const TOP_Y = NODE_HALF_HEIGHT;
const BOTTOM_Y = DEFAULT_CANVAS.height - NODE_HALF_HEIGHT;

const orderedNodes = sortNodes(nodes);
const bands = [];
for (const kind of kindOrder) {
  const list = orderedNodes.filter((node) => node.kind === kind);
  if (list.length === 0) continue;
  bands.push({ kind, count: list.length, rows: Math.ceil(list.length / COLUMNS), nodes: list });
}
const totalRows = bands.reduce((sum, band) => sum + band.rows, 0);
const rowStep =
  totalRows > 1 ? Math.min(2 * NODE_HALF_HEIGHT, Math.floor((BOTTOM_Y - TOP_Y) / (totalRows - 1))) : 2 * NODE_HALF_HEIGHT;

const coordinates = new Map();
let rowCursor = 0;
for (const band of bands) {
  band.startRow = rowCursor;
  band.nodes.forEach((node, index) => {
    const column = index % COLUMNS;
    const row = rowCursor + Math.floor(index / COLUMNS);
    coordinates.set(node.id, { x: NODE_HALF_WIDTH + column * COLUMN_STEP, y: TOP_Y + row * rowStep });
  });
  rowCursor += band.rows;
}

const xs = [...coordinates.values()].map((point) => point.x);
const ys = [...coordinates.values()].map((point) => point.y);
const xRange = [Math.min(...xs), Math.max(...xs)];
const yRange = [Math.min(...ys), Math.max(...ys)];

const layoutNodes = orderedNodes.map((node) => exportNode({ ...node, ...coordinates.get(node.id) }));
const withWeights = edges.map((edge) => {
  const weight = edgeWeight(edge);
  return weight === null ? edge : { ...edge, weight };
});
const layoutEdges = sortEdges(withWeights).map(exportEdge);

const requires = edges.filter((edge) => edge.type === "requires");
const requiresWithWeight = requires.filter((edge) => edgeWeight(edge) !== null).length;

const byKind = Object.fromEntries(
  kindOrder.filter((kind) => nodes.some((node) => node.kind === kind)).map((kind) => [kind, nodes.filter((node) => node.kind === kind).length]),
);
const byType = Object.fromEntries(
  EDGE_ORDER.filter((type) => edges.some((edge) => edge.type === type)).map((type) => [type, edges.filter((edge) => edge.type === type).length]),
);

const graph = {
  schema: "career-graph-layout/v1",
  step: STEP,
  generatedAt: startedAt,
  kbVersion: KB_VERSION,
  graphVersion: GRAPH_VERSION,
  note: "第 10 步产物：确定性 x/y + requires.weight。坐标与 weight 都由本文件落盘，第 09 步按 id 覆盖后做硬校验，第 11 步按契约投影成 exports/career-graph.json。",
  canvas: { ...DEFAULT_CANVAS },
  capsule: { width: 2 * NODE_HALF_WIDTH, height: 2 * NODE_HALF_HEIGHT },
  layout: {
    rule: "按 taxonomy.nodeKinds 的顺序把各类节点排成横向色带；带内按 id 升序、每行 5 个左对齐；行距取画布容差内的最大值。纯确定性：无随机数、无迭代收敛、与运行时间无关。",
    columns: COLUMNS,
    columnStep: COLUMN_STEP,
    rowStep,
    rows: totalRows,
    origin: { x: NODE_HALF_WIDTH, y: TOP_Y },
    bands: bands.map((band) => ({ kind: band.kind, count: band.count, rows: band.rows, startRow: band.startRow })),
    xRange,
    yRange,
    note: `${nodes.length} 个节点要铺在 ${DEFAULT_CANVAS.width}×${DEFAULT_CANVAS.height} 内：胶囊 ${2 * NODE_HALF_WIDTH}×${2 * NODE_HALF_HEIGHT}，横向 5 列 = ${COLUMNS * COLUMN_STEP} 正好等于画布宽；纵向 ${totalRows} 行只给得起 ${rowStep}px 行距（比胶囊高度少 ${2 * NODE_HALF_HEIGHT - rowStep}px，相邻两行轻微重叠）。前端不得每次渲染重跑力导向布局（§3.1 L110）。`,
  },
  counts: { nodes: layoutNodes.length, edges: layoutEdges.length, bands: bands.length, rows: totalRows, requiresWithWeight, byKind, byType },
  nodes: layoutNodes,
  edges: layoutEdges,
};

writeJson(GRAPH_FILE, graph);

// ---------- 2. 检索索引 ----------

const nodeDocs = orderedNodes.map((node) => ({
  id: node.id,
  text: [node.label, ...(node.aliases ?? []), node.description ?? "", node.kind, shortRef(node.id)].filter(Boolean).join(" "),
  meta: { kind: node.kind, title: node.label },
}));
const chunkDocs = chunks.map((chunk) => ({
  id: chunk.chunkId,
  text: [chunk.heading, chunk.sectionPath, (chunk.tags ?? []).join(" "), chunk.text].filter(Boolean).join(" "),
  meta: { sourceId: chunk.sourceId, title: chunk.heading || chunk.sectionPath || chunk.chunkId },
}));

const nodeIndex = buildBm25(nodeDocs);
const chunkIndex = buildBm25(chunkDocs);

/**
 * 倒排落盘格式：`terms[词] = [[docPos, tf], ...]`，docPos 指向同级的 `docs[]` 下标。
 * 不存 `df`（等于数组长度）、不存 Map（JSON 里只能退化成对象数组）—— 纯粹是为了让交付物小一半。
 * 复原方式：`idf = ln(1 + (N - df + 0.5) / (df + 0.5))`，`df = postings.length`，与 lib/graph.mjs 的 bm25Search 同源。
 */
function serializeIndex(index) {
  const docPos = new Map(index.entries.map((entry, position) => [entry.id, position]));
  const postings = new Map();
  for (const entry of index.entries) {
    for (const [term, tf] of entry.tf) {
      if (!postings.has(term)) postings.set(term, []);
      postings.get(term).push([docPos.get(entry.id), tf]);
    }
  }
  const terms = {};
  for (const term of [...postings.keys()].sort()) terms[term] = postings.get(term);
  return {
    k1: index.k1,
    b: index.b,
    N: index.N,
    avgLen: Math.round(index.avgLen * 1000) / 1000,
    docs: index.entries.map((entry) => ({ id: entry.id, length: entry.length, ...entry.meta })),
    termCount: postings.size,
    terms,
  };
}

const decisionByKey = new Map((adjudication.aliasDecisions ?? []).map((decision) => [decision.key, decision]));
const aliasIndex = (aliases.entries ?? [])
  .map((entry) => {
    const decision = decisionByKey.get(entry.key);
    let primary = entry.primary ?? null;
    let resolvedBy = entry.resolvedBy ?? (entry.status === "unique" ? "unique-surface" : null);
    if (decision) {
      if (decision.decision === "keep-separate") {
        primary = null;
        resolvedBy = "adjudicated-keep-separate";
      } else {
        primary = decision.decision;
        resolvedBy = "adjudicated-merge";
      }
    }
    return {
      key: entry.key,
      surfaces: entry.surfaces ?? [],
      kinds: entry.kinds ?? [],
      ids: entry.ids ?? [],
      status: entry.status,
      primary,
      resolvedBy,
    };
  })
  .sort((a, b) => a.key.localeCompare(b.key));

/** 节点 → 它能回溯到的 chunkId（自身 sourceRefs ∪ 挂在自己身上的 evidenced_by 目标），"查看依据"入口用。 */
const chunkRefsByNode = {};
for (const node of orderedNodes) chunkRefsByNode[node.id] = new Set(node.sourceRefs ?? []);
for (const edge of edges) {
  if (edge.type !== "evidenced_by") continue;
  const chunkId = chunkIdOf(edge.to);
  if (chunkId && chunkRefsByNode[edge.from]) chunkRefsByNode[edge.from].add(chunkId);
}
for (const id of Object.keys(chunkRefsByNode)) chunkRefsByNode[id] = [...chunkRefsByNode[id]].sort();

const searchIndex = {
  schema: "career-graph-search-index/v1",
  step: STEP,
  generatedAt: startedAt,
  kbVersion: KB_VERSION,
  graphVersion: GRAPH_VERSION,
  note: "第 10 步产物：零依赖 BM25 倒排（同一份 lib/graph.mjs 的 tokenize/buildBm25 生成，第 11 步评测复算同一份数字）。节点级索引服务 MCP 的 search_career_knowledge；chunk 级索引服务 §7 的「可回答问题 Top-3 是否含标注的相关 chunk」。",
  tokenizer: describeTokenizer(),
  counts: {
    nodeDocs: nodeDocs.length,
    nodeTerms: nodeIndex.df.size,
    chunkDocs: chunkDocs.length,
    chunkSources: new Set(chunks.map((chunk) => chunk.sourceId)).size,
    aliasKeys: aliasIndex.length,
    ambiguousKeys: aliasIndex.filter((item) => item.status === "ambiguous").length,
    resolvedAmbiguousKeys: aliasIndex.filter((item) => item.status === "ambiguous" && item.primary).length,
    nodesWithChunkRefs: Object.values(chunkRefsByNode).filter((refs) => refs.length > 0).length,
  },
  nodeIndex: serializeIndex(nodeIndex),
  chunkIndex: serializeIndex(chunkIndex),
  aliasIndex,
  chunkRefsByNode,
};

writeJson(SEARCH_INDEX_FILE, searchIndex);

// ---------- 3. 日志 ----------

logLine(`${TITLE}：${relPath(GRAPH_FILE)} + ${relPath(SEARCH_INDEX_FILE)}`);
logLine(`  布局：${bands.length} 条色带 / ${totalRows} 行 × 最多 ${COLUMNS} 列；x ∈ [${xRange[0]}, ${xRange[1]}]，y ∈ [${yRange[0]}, ${yRange[1]}]（画布 ${DEFAULT_CANVAS.width}×${DEFAULT_CANVAS.height}）`);
logLine(`  weight：requires ${requiresWithWeight}/${requires.length} 条 —— ${WEIGHT_RULE}`);
logLine(`  索引：节点 ${nodeDocs.length} 篇 / chunk ${chunkDocs.length} 篇 / 别名 ${aliasIndex.length} 条（歧义 ${searchIndex.counts.ambiguousKeys}，已裁定 ${searchIndex.counts.resolvedAmbiguousKeys}）`);
logLine(`  图：${describeCounts(byKind, kindOrder)}；${describeCounts(byType, EDGE_ORDER)}`);

recordStep({
  step: STEP,
  title: TITLE,
  command: COMMAND,
  inputs: [ACCEPTED_FILE, CHUNKS_FILE, TAXONOMY_FILE, ALIASES_FILE, ADJUDICATION_FILE],
  outputs: [GRAPH_FILE, SEARCH_INDEX_FILE],
  counts: {
    nodes: layoutNodes.length,
    edges: layoutEdges.length,
    bands: bands.length,
    rows: totalRows,
    requires: requires.length,
    requiresWithWeight,
    nodeDocs: nodeDocs.length,
    chunkDocs: chunkDocs.length,
    aliasKeys: aliasIndex.length,
  },
  notes: [
    `确定性布局：${bands.length} 条色带 / ${totalRows} 行，行距 ${rowStep}px，x ∈ [${xRange[0]}, ${xRange[1]}]，y ∈ [${yRange[0]}, ${yRange[1]}]，全部落在 ${DEFAULT_CANVAS.width}×${DEFAULT_CANVAS.height} 内；无随机数、与运行时间无关。`,
    `weight 按 WEIGHT_RULE 只写 requires：${requiresWithWeight}/${requires.length} 条 = importance（同一份数字）。`,
    `检索索引：节点 BM25（${searchIndex.counts.nodeTerms} 个词） + chunk BM25（${searchIndex.counts.chunkDocs} 篇） + 别名倒排 ${aliasIndex.length} 条。`,
    `本步不改 annotatedBy：llm-draft → llm-reviewed 的唯一通道仍是第 06 步（设计文档 L251）。`,
  ],
  startedAt,
});
