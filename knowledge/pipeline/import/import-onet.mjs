#!/usr/bin/env node
/**
 * 导入 O*NET 29.1 职业库 → `knowledge/import/build/onet.jsonl`
 *
 *   node knowledge/pipeline/import/import-onet.mjs
 *
 * 这张表里的每个数字都来自官方文件本身，脚本不做任何「推断」：
 *
 *   职业（standard）  ← Occupation Data.txt      O*NET-SOC Code / Title / Description
 *   技能（skill）     ← Skills.txt               Element ID / Element Name（35 项）
 *   知识领域（domain）← Knowledge.txt            Element ID / Element Name（33 项）
 *   工具（tool）      ← Technology Skills.txt    只取 Hot Technology = Y 的（170 个）
 *
 * 边：
 *   specifies   standard → skill|domain   importance = IM / 5（与 requires.importance 同口径的 0–1）
 *                                         level      = LV 原值（0–7）
 *   uses        standard → tool           只对 Hot Technology 建边；In Demand 记在边上
 *
 * 刻意不做的三件事：
 *   1. 不把 O*NET 职业当成我们自建的 `occupation` 节点。它们是 `standard` 节点 ——
 *      「公开标准里的职业条目」，与「我们编的职业」是两个东西，混在一起就再也分不开了。
 *   2. 不导入 Task Statements（18,797 条）。见 ../import/README.md 的取舍第 2 条。
 *   3. 不做跨库合并、不做机器翻译。
 *
 * 复算：同一份 raw 跑两次，输出文件逐字节相同（排序与字段顺序都固定）。可 diff 验证。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { assertUnique, onetStandardId, parseTsv, round, slugName, sortRecords, toJsonl } from "./lib/external.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = resolve(HERE, "..", "..", "..");
const RAW_DIR = resolve(WORKSPACE_ROOT, "knowledge", "import", "raw", "onet-29.1");
const BUILD_DIR = resolve(WORKSPACE_ROOT, "knowledge", "import", "build");

/** O*NET 的正式发布版本。写进产物，避免以后分不清哪份数据是 29.1 还是 30.x。 */
const ONET_VERSION = "29.1";
const SOURCE_ID = "onet-29.1";
const SOURCE_URL = "https://www.onetcenter.org/dl_files/database/db_29_1_text.zip";

const REQUIRED = ["Occupation Data.txt", "Skills.txt", "Knowledge.txt", "Technology Skills.txt"];
for (const name of REQUIRED) {
  if (!existsSync(resolve(RAW_DIR, name))) {
    throw new Error(`缺少 ${RAW_DIR}\\${name}：请先从 ${SOURCE_URL} 下载并解包到该目录`);
  }
}

const readTsv = (name) => parseTsv(readFileSync(resolve(RAW_DIR, name), "utf8"));

// ---------- 1. 职业 → standard 节点 ----------

const occupations = readTsv("Occupation Data.txt").rows.map((row) => ({
  recordType: "node",
  id: onetStandardId(row["O*NET-SOC Code"]),
  kind: "standard",
  label: row.Title.trim(),
  description: row.Description.trim(),
  standardRef: `O*NET-SOC ${row["O*NET-SOC Code"].trim()}`,
  standardScheme: "onet-soc",
  standardVersion: ONET_VERSION,
  sourceId: SOURCE_ID,
  layer: "external",
}));

// ---------- 2. 技能要素 / 知识领域 → skill / domain 节点 ----------

/**
 * 要素 id 直接用 O*NET 的 Element ID（`2.A.1.a`）。
 * 用编号而不是名字：名字会随版本微调，编号不会，重跑时才不会整批换 id。
 */
function elementNodes(file, kind) {
  const { rows } = readTsv(file);
  const byId = new Map();
  for (const row of rows) {
    const elementId = row["Element ID"].trim();
    if (!elementId || byId.has(elementId)) continue;
    byId.set(elementId, {
      recordType: "node",
      id: `${kind}:onet-${elementId.toLowerCase()}`,
      kind,
      label: row["Element Name"].trim(),
      standardRef: `O*NET Content Model ${elementId}`,
      standardScheme: "onet-content-model",
      standardVersion: ONET_VERSION,
      sourceId: SOURCE_ID,
      layer: "external",
    });
  }
  return [...byId.values()];
}

const skillNodes = elementNodes("Skills.txt", "skill");
const domainNodes = elementNodes("Knowledge.txt", "domain");

// ---------- 3. Hot Technology → tool 节点 ----------

const hotTechRows = readTsv("Technology Skills.txt").rows.filter((row) => row["Hot Technology"].trim() === "Y");
const toolBySlug = new Map();
/** 名字 → slug 的反向表：只用来在撞车时报出是哪两个名字撞的，不在产物里。 */
const toolNameBySlug = new Map();
const toolCollisions = [];
for (const row of hotTechRows) {
  const name = row.Example.trim();
  const id = `tool:onet-${slugName(name)}`;
  const previous = toolNameBySlug.get(id);
  if (previous !== undefined && previous !== name) {
    toolCollisions.push({ id, names: [previous, name] });
    continue;
  }
  toolNameBySlug.set(id, name);
  if (toolBySlug.has(id)) continue;
  toolBySlug.set(id, {
    recordType: "node",
    id,
    kind: "tool",
    label: name,
    description: row["Commodity Title"] ? `${row["Commodity Title"].trim()}（UNSPSC ${row["Commodity Code"].trim()}）` : undefined,
    standardRef: `O*NET Hot Technology ${name}`,
    standardScheme: "onet-hot-technology",
    standardVersion: ONET_VERSION,
    sourceId: SOURCE_ID,
    layer: "external",
  });
}
if (toolCollisions.length > 0) {
  const sample = toolCollisions.slice(0, 3).map((item) => `${item.names.join(" / ")} → ${item.id}`).join("；");
  throw new Error(`${toolCollisions.length} 个技术名折算成同一个 id，会让不同技术合成一个节点：${sample}`);
}
const toolNodes = [...toolBySlug.values()];

// ---------- 4. 要素评分 → specifies 边 ----------

/**
 * 一个要素在一个职业上有两条记录：IM（重要性 0–5）与 LV（等级 0–7）。
 * O*NET 允许标注「不适用」：`Recommend Suppress = Y`（统计上不可靠）与
 * `Not Relevant = Y`（这一条对这个职业没有意义）。两种都跳过，宁可少一条边也不写一个假的数字。
 */
function specifiesEdges(file, kind) {
  const { rows } = readTsv(file);
  const pairs = new Map();
  let suppressed = 0;
  let irrelevant = 0;
  for (const row of rows) {
    if (row["Recommend Suppress"].trim() === "Y") {
      suppressed += 1;
      continue;
    }
    if (row["Not Relevant"].trim() === "Y") {
      irrelevant += 1;
      continue;
    }
    const key = `${row["O*NET-SOC Code"].trim()}|${row["Element ID"].trim()}`;
    const entry = pairs.get(key) ?? { soc: row["O*NET-SOC Code"].trim(), elementId: row["Element ID"].trim() };
    const value = Number.parseFloat(row["Data Value"]);
    if (!Number.isFinite(value)) continue;
    if (row["Scale ID"].trim() === "IM") entry.importance = round(value / 5, 3);
    if (row["Scale ID"].trim() === "LV") entry.level = round(value, 2);
    pairs.set(key, entry);
  }
  const edges = [];
  for (const entry of pairs.values()) {
    if (entry.importance === undefined && entry.level === undefined) continue;
    edges.push({
      recordType: "edge",
      type: "specifies",
      from: onetStandardId(entry.soc),
      to: `${kind}:onet-${entry.elementId.toLowerCase()}`,
      importance: entry.importance,
      level: entry.level,
      standardRef: `O*NET ${entry.soc} × ${entry.elementId}`,
      sourceId: SOURCE_ID,
      layer: "external",
    });
  }
  return { edges, suppressed, irrelevant };
}

const skillSpecifies = specifiesEdges("Skills.txt", "skill");
const domainSpecifies = specifiesEdges("Knowledge.txt", "domain");

// ---------- 5. Hot Technology → uses 边 ----------

const usesEdges = [];
const seenUse = new Set();
for (const row of hotTechRows) {
  const from = onetStandardId(row["O*NET-SOC Code"]);
  const to = `tool:onet-${slugName(row.Example.trim())}`;
  const key = `${from}|${to}`;
  if (seenUse.has(key)) continue;
  seenUse.add(key);
  usesEdges.push({
    recordType: "edge",
    type: "uses",
    from,
    to,
    inDemand: row["In Demand"].trim() === "Y",
    standardRef: `O*NET ${row["O*NET-SOC Code"].trim()} × ${row.Example.trim()}`,
    sourceId: SOURCE_ID,
    layer: "external",
  });
}

// ---------- 6. 跨库连线（aligned_with）由 build-library.mjs 统一生成 ----------

/**
 * 这里**故意不生成 `aligned_with` 边**。
 *
 * 「自建职业 → 标准条目」的配对写在 `knowledge/import/mappings/occupation-alignments.json`，
 * 那个文件同时覆盖 O*NET 与职业分类大典两个目标，而且要去自建层核对 occupation id 是否真的存在。
 * 谁都能读的文件就只在一个地方读：那一步放在 `build-library.mjs`（合并两库时一起做）。
 * 在这里顺手写一份，会让同一份映射有两个出口，早晚对不上。
 */

// ---------- 7. 装配、校验、落盘 ----------

const nodes = assertUnique(sortRecords([...occupations, ...skillNodes, ...domainNodes, ...toolNodes]), "O*NET 节点");
const edges = sortRecords([...skillSpecifies.edges, ...domainSpecifies.edges, ...usesEdges]);

const nodeIds = new Set(nodes.map((node) => node.id));
const dangling = edges.filter((edge) => !nodeIds.has(edge.from) || !nodeIds.has(edge.to));
if (dangling.length > 0) {
  const sample = dangling.slice(0, 3).map((edge) => `${edge.type} ${edge.from} → ${edge.to}`).join("；");
  throw new Error(`有 ${dangling.length} 条边的端点在节点表里不存在：${sample}`);
}

/** 边 id 由排序后的位置决定：同输入同输出，diff 才看得出来哪一条真的变了。 */
const prefix = "onet";
edges.forEach((edge, index) => {
  edge.id = `${prefix}-e-${String(index + 1).padStart(6, "0")}`;
});
for (const [index, node] of nodes.entries()) node.externalIndex = index + 1;

const NODE_FIELDS = [
  "recordType", "id", "kind", "label", "description",
  "standardRef", "standardScheme", "standardVersion", "sourceId", "layer", "externalIndex",
];
const EDGE_FIELDS = ["recordType", "id", "type", "from", "to", "importance", "level", "inDemand", "code", "alignment", "note", "standardRef", "sourceId", "layer"];

mkdirSync(BUILD_DIR, { recursive: true });
const jsonl = toJsonl([...nodes, ...edges], { node: NODE_FIELDS, edge: EDGE_FIELDS });
writeFileSync(resolve(BUILD_DIR, `${prefix}.jsonl`), jsonl, "utf8");

const byKind = {};
for (const node of nodes) byKind[node.kind] = (byKind[node.kind] ?? 0) + 1;
const byType = {};
for (const edge of edges) byType[edge.type] = (byType[edge.type] ?? 0) + 1;

const summary = {
  schema: "career-graph-external-import/v1",
  library: "O*NET",
  version: ONET_VERSION,
  sourceId: SOURCE_ID,
  sourceUrl: SOURCE_URL,
  rawDir: "knowledge/import/raw/onet-29.1",
  output: "knowledge/import/build/onet.jsonl",
  note: "由 import-onet.mjs 从官方文本库换算，无人工推断；importance = IM / 5 归一，level = LV 原值。",
  counts: { nodes: nodes.length, edges: edges.length, byKind, byType },
  skipped: {
    recommendSuppress: skillSpecifies.suppressed + domainSpecifies.suppressed,
    notRelevant: skillSpecifies.irrelevant + domainSpecifies.irrelevant,
    note: "Recommend Suppress = Y（统计不可靠）与 Not Relevant = Y（对本职业无意义）的评分行被跳过，不计入 specifies 边。",
  },
  notImported: [
    "Task Statements（18,797 条任务陈述）",
    "Alternate Titles（55,121 条岗位别名）",
    "Tools Used（41,663 行非技术类工具）",
    "Abilities / Work Activities / Work Context / Work Styles / Interests",
  ],
};
writeFileSync(resolve(BUILD_DIR, `${prefix}.summary.json`), `${JSON.stringify(summary, null, 2)}\n`, "utf8");

const size = (jsonl.length / 1024 / 1024).toFixed(1);
console.log(`O*NET ${ONET_VERSION} 导入完成 → knowledge/import/build/${prefix}.jsonl（${size} MB）`);
console.log(`  节点 ${nodes.length}：${Object.entries(byKind).map(([k, v]) => `${k} ${v}`).join("，")}`);
console.log(`  边   ${edges.length}：${Object.entries(byType).map(([k, v]) => `${k} ${v}`).join("，")}`);
