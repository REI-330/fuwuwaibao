#!/usr/bin/env node
/**
 * 合并外部职业库并生成交付物 → `knowledge/exports/external-career-library.json`
 *
 *   node knowledge/pipeline/import/build-library.mjs
 *
 * 干四件事，每一件都会在出错时直接退出（外部层没有「warn 一下继续跑」的余地）：
 *
 *   ① 读 `build/onet.jsonl` 与 `build/dadian.jsonl`，合并成一份节点表 ——
 *      跨库 id 撞车要炸，两个库各自内部就已经查过一遍，这里是第三道。
 *   ② 校验：每个 kind / edge type 都必须在 `knowledge/pipeline/taxonomy.json` 里，
 *      每条边的端点都必须能在节点表里找到（`aligned_with` 除外，它的 from 是自建层职业）。
 *   ③ 生成 `aligned_with` 桥边：从 `knowledge/import/mappings/occupation-alignments.json` 读人工配对，
 *      并去 `knowledge/exports/career-graph.json` 核对 occupation id 真的存在。
 *   ④ 写交付物。**不把 2,692 个节点全塞进 JSON**（那会是一份 20MB 的文件），
 *      交付物只放：版本、来源、计数、校验结果、以及 id/kind/label 索引。
 *      全量数据留在 `build/*.jsonl`，按行读、可 diff、可重跑。
 *
 * 为什么外部层不进 `exports/career-graph.json`：那份文件里每个节点都有指回自己 chunk 的
 * `sourceRefs`，这是步骤 04 的子串级引文校验在守的。外部节点没有 chunk 可指，
 * 掺进去会让这条对所有节点成立的性质变成「对 63 个成立、对 2,692 个不成立」。
 * 详见 `knowledge/import/README.md`。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { sortRecords, toJsonl } from "./lib/external.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = resolve(HERE, "..", "..", "..");
const KNOWLEDGE_DIR = resolve(WORKSPACE_ROOT, "knowledge");
const BUILD_DIR = resolve(KNOWLEDGE_DIR, "import", "build");
const MAPPINGS_DIR = resolve(KNOWLEDGE_DIR, "import", "mappings");
const EXPORTS_DIR = resolve(KNOWLEDGE_DIR, "exports");

const TAXONOMY_FILE = resolve(KNOWLEDGE_DIR, "pipeline", "taxonomy.json");
const ALIGNMENTS_FILE = resolve(MAPPINGS_DIR, "occupation-alignments.json");
const BUILTIN_GRAPH_FILE = resolve(EXPORTS_DIR, "career-graph.json");
const OUT_FILE = resolve(EXPORTS_DIR, "external-career-library.json");

const LIBRARIES = [
  { id: "onet-29.1", name: "O*NET 29.1", file: resolve(BUILD_DIR, "onet.jsonl"), summary: resolve(BUILD_DIR, "onet.summary.json") },
  { id: "cn-dadian", name: "中华人民共和国职业分类大典", file: resolve(BUILD_DIR, "dadian.jsonl"), summary: resolve(BUILD_DIR, "dadian.summary.json") },
  {
    id: "cn-osta-standards",
    name: "国家职业技能标准（工作内容 → 技能）",
    file: resolve(BUILD_DIR, "dadian-skills.jsonl"),
    summary: resolve(BUILD_DIR, "dadian-skills.summary.json"),
  },
];

const readJsonl = (file) => readFileSync(file, "utf8").split("\n").filter((line) => line.trim().length > 0).map((line) => JSON.parse(line));

// ---------- ① 合并 ----------

const taxonomy = JSON.parse(readFileSync(TAXONOMY_FILE, "utf8"));
const kindIds = new Set(taxonomy.nodeKinds.map((spec) => spec.id));
const edgeIds = new Set(taxonomy.edgeTypes.map((spec) => spec.id));

const nodes = [];
const edges = [];
const perLibrary = [];
const idOwner = new Map();
const collisions = [];

for (const library of LIBRARIES) {
  if (!existsSync(library.file)) {
    throw new Error(`缺少 ${library.file}：请先按顺序跑 import-onet.mjs / import-dadian.mjs`);
  }
  const rows = readJsonl(library.file);
  const libraryNodes = rows.filter((row) => row.recordType === "node");
  const libraryEdges = rows.filter((row) => row.recordType === "edge");
  for (const node of libraryNodes) {
    if (idOwner.has(node.id)) collisions.push(`${node.id}（${idOwner.get(node.id)} 与 ${library.id}）`);
    idOwner.set(node.id, library.id);
  }
  nodes.push(...libraryNodes);
  edges.push(...libraryEdges);
  const summary = existsSync(library.summary) ? JSON.parse(readFileSync(library.summary, "utf8")) : null;
  perLibrary.push({
    id: library.id,
    name: library.name,
    version: summary?.version ?? null,
    sourceId: summary?.sourceId ?? null,
    nodes: libraryNodes.length,
    edges: libraryEdges.length,
  });
}
if (collisions.length > 0) {
  throw new Error(`${collisions.length} 个节点 id 在两个库之间撞车：${collisions.slice(0, 3).join("；")}`);
}

// ---------- ② 校验 ----------

const problems = [];
for (const node of nodes) {
  if (!kindIds.has(node.kind)) problems.push(`节点的 kind 不在词表里：${node.id} → ${node.kind}`);
}
for (const edge of edges) {
  if (!edgeIds.has(edge.type)) problems.push(`边的 type 不在词表里：${edge.id} → ${edge.type}`);
}

/**
 * 两个中国系统各自更新，编号对不齐是常态：国家职业技能标准是按颁布批次追加的，
 * 而大典分类树取的是某个 versionId 的快照 —— 会出现「标准里有这个职业编号、大典树里还没有」。
 * 这不是错误，是数据现状，但不能静默丢掉：把这类边挑出来单独计数、写进交付物，
 * 数量一旦异常增长，报告里一眼就能看见。除了这一类，其余悬空边仍然硬失败。
 */
const nodeIds = new Set(nodes.map((node) => node.id));
const unmatchedStandardEdges = [];
const keptEdges = [];
for (const edge of edges) {
  const isFromStandard = String(edge.from).startsWith("standard:cn-") && edge.type === "specifies";
  if (isFromStandard && !nodeIds.has(edge.from)) {
    unmatchedStandardEdges.push(edge);
    continue;
  }
  keptEdges.push(edge);
}
edges.length = 0;
edges.push(...keptEdges);

const unmatchedCodes = new Map();
for (const edge of unmatchedStandardEdges) {
  const code = String(edge.from).replace("standard:cn-", "");
  unmatchedCodes.set(code, (unmatchedCodes.get(code) ?? 0) + 1);
}

for (const edge of edges) {
  for (const side of ["from", "to"]) {
    if (!nodeIds.has(edge[side])) problems.push(`边的端点在节点表里不存在：${edge.id} ${side}=${edge[side]}`);
  }
}
if (problems.length > 0) {
  throw new Error(`外部层自洽检查未通过（${problems.length} 条）：\n  ${problems.slice(0, 5).join("\n  ")}`);
}

// ---------- ③ 桥边（自建职业 → 标准条目） ----------

if (!existsSync(BUILTIN_GRAPH_FILE)) {
  throw new Error(`缺少 ${BUILTIN_GRAPH_FILE}：桥边的 from 要对着自建层的 occupation id 核对，没有它就没法查`);
}
const builtin = JSON.parse(readFileSync(BUILTIN_GRAPH_FILE, "utf8"));
const builtinOccupationIds = new Set((builtin.nodes ?? []).filter((node) => node.kind === "occupation").map((node) => node.id));

const alignments = JSON.parse(readFileSync(ALIGNMENTS_FILE, "utf8")).alignments ?? [];
const bridge = [];
for (const item of alignments) {
  if (!item.occupationId || !item.targetUri || !item.alignment) {
    throw new Error(`occupation-alignments.json 的每条都要有 occupationId / targetUri / alignment：${JSON.stringify(item)}`);
  }
  if (!["full", "partial", "related"].includes(item.alignment)) {
    throw new Error(`alignment 只能是 full / partial / related，收到 ${item.alignment}`);
  }
  if (!builtinOccupationIds.has(item.occupationId)) {
    throw new Error(`映射指向了不存在的自建职业：${item.occupationId}（自建层现有 ${[...builtinOccupationIds].join("、")}）`);
  }
  if (!nodeIds.has(item.targetUri)) {
    throw new Error(`映射指向了不存在的标准条目：${item.targetUri}`);
  }
  const target = nodes.find((node) => node.id === item.targetUri);
  bridge.push({
    recordType: "edge",
    id: `bridge-${String(bridge.length + 1).padStart(3, "0")}`,
    type: "aligned_with",
    from: item.occupationId,
    to: item.targetUri,
    code: item.code ?? target.standardRef,
    alignment: item.alignment,
    note: item.note,
    standardRef: item.code ?? target.standardRef,
    sourceId: target.sourceId,
    layer: "bridge",
  });
}

const EDGE_FIELDS = ["recordType", "id", "type", "from", "to", "code", "alignment", "note", "standardRef", "sourceId", "layer"];
writeFileSync(resolve(BUILD_DIR, "bridge.jsonl"), toJsonl(bridge, { edge: EDGE_FIELDS }), "utf8");

// ---------- ④ 交付物 ----------

/**
 * 索引不再是「按 kind 列全量」—— 22,000 个节点这么列出来是 4.2 MB 的 JSON，
 * 既没人会去翻，也违背「交付物只放索引与计数、全量留在 JSONL」的初衷。
 * 现在每个 kind 只放计数 + 最多 50 条样例，够看清这一层长什么样；要全量去 build/*.jsonl。
 */
const INDEX_SAMPLE = 50;
const index = new Map();
for (const node of sortRecords(nodes)) {
  if (!index.has(node.kind)) index.set(node.kind, []);
  const list = index.get(node.kind);
  if (list.length < INDEX_SAMPLE) list.push({ id: node.id, label: node.label, standardRef: node.standardRef });
}

const byKind = {};
for (const node of nodes) byKind[node.kind] = (byKind[node.kind] ?? 0) + 1;
const byType = {};
for (const edge of edges) byType[edge.type] = (byType[edge.type] ?? 0) + 1;
const byLibraryKind = {};
for (const node of nodes) {
  const library = idOwner.get(node.id);
  byLibraryKind[library] = byLibraryKind[library] ?? {};
  byLibraryKind[library][node.kind] = (byLibraryKind[library][node.kind] ?? 0) + 1;
}

const bridgeByAlignment = {};
for (const edge of bridge) bridgeByAlignment[edge.alignment] = (bridgeByAlignment[edge.alignment] ?? 0) + 1;

const exportFile = {
  schema: "career-graph-external-library/v1",
  generatedAt: new Date().toISOString(),
  note: "外部职业库（O*NET + 中国职业分类大典）的合并视图。只放索引与计数，全量数据在 knowledge/import/build/*.jsonl。这一层没有 chunk 引文，出处一律是 standardRef。",
  layers: {
    builtin: { file: "knowledge/exports/career-graph.json", nodes: builtin.nodes?.length ?? 0, edges: builtin.edges?.length ?? 0 },
    external: { files: ["knowledge/import/build/onet.jsonl", "knowledge/import/build/dadian.jsonl"], nodes: nodes.length, edges: edges.length },
    bridge: { file: "knowledge/import/build/bridge.jsonl", edges: bridge.length },
  },
  libraries: perLibrary,
  counts: {
    nodes: nodes.length,
    edges: edges.length,
    bridge: bridge.length,
    byKind,
    byType,
    byLibraryKind,
    bridgeByAlignment,
    unmatchedStandardEdges: unmatchedStandardEdges.length,
  },
  validation: {
    kindVocabulary: [...kindIds],
    edgeVocabulary: [...edgeIds],
    passed: true,
    checked: [
      "每个节点的 kind 在 taxonomy.nodeKinds 里",
      "每条边的 type 在 taxonomy.edgeTypes 里",
      "每条边的 from / to 都在合并后的节点表里（「标准编号未进大典」这一类另有记录，见 unmatchedStandards）",
      "节点 id 跨库不重复",
      "桥边的 occupationId 在自建层 exports/career-graph.json 里真实存在",
      "桥边的 targetUri 在外部节点表里真实存在",
    ],
    unmatchedStandards: {
      codes: [...unmatchedCodes.keys()].sort(),
      edges: unmatchedStandardEdges.length,
      note: "国家职业技能标准里出现了、但《职业分类大典》分类树里没有的职业编号。两个系统按各自的批次更新，编号对不齐属正常；这些边被挡在交付物之外并在此计数，不是被静默丢弃。",
    },
  },
  index: Object.fromEntries(
    [...index.entries()].map(([kind, sample]) => [kind, { count: byKind[kind] ?? 0, sampleSize: sample.length, sample }]),
  ),
};

mkdirSync(EXPORTS_DIR, { recursive: true });
writeFileSync(OUT_FILE, `${JSON.stringify(exportFile, null, 2)}\n`, "utf8");

console.log("外部职业库合并完成 → knowledge/exports/external-career-library.json");
console.log(`  节点 ${nodes.length}（${Object.entries(byKind).map(([k, v]) => `${k} ${v}`).join("，")}）`);
console.log(`  边   ${edges.length}（${Object.entries(byType).map(([k, v]) => `${k} ${v}`).join("，")}）`);
console.log(`  桥边 ${bridge.length}（${Object.entries(bridgeByAlignment).map(([k, v]) => `${k} ${v}`).join("，")}）`);
console.log(`  自洽检查：kind 词表 ${kindIds.size} 类、边词表 ${edgeIds.size} 类，${nodes.length} 节点 / ${edges.length} 边全部通过`);
if (unmatchedStandardEdges.length > 0) {
  console.log(`  注意：${unmatchedStandardEdges.length} 条边因「标准编号未进大典树」被挡下，涉及 ${unmatchedCodes.size} 个编号（见交付物 validation.unmatchedStandards）`);
}
