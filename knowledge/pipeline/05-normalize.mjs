#!/usr/bin/env node
/**
 * 05 术语归一与消歧
 *
 * 设计文档 §6 第 5 步：归一化与别名消解 → aliases.json（+ 归一化后的图谱行）。
 * 这一步只做「把同一个东西的不同写法收成一把钥匙」，不判断谁对谁错 ——
 * 冲突留给第 06 步的人工裁定去裁决（§6 明确 06 是唯一能把 llm-draft 升级为 llm-reviewed 的通道）。
 *
 * 三件事：
 *   1) 别名倒排：label + aliases 全部过 normalizeAlias（去空白、统一全/半角括号与连字符、小写），
 *      同一个 key 命中多个不同节点 = 真歧义，必须显式记录，不能悄悄选一个；
 *   2) 归一化行：给每个节点补 labelKey / aliasKeys、给每条边补 edgeKey，
 *      下游（07 编译 Wiki、10 建索引、11 评测）都靠这些 key 做匹配，避免各自再写一套比较逻辑；
 *   3) 补齐 §3.3 硬约束 2 的 annotatedBy：节点与边在这里统一标成 llm-draft，
 *      直到第 06 步裁定才升级。
 *
 * 明确不做的事：不发明 `weight`。设计文档 §3.2 把它列在边结构里，但 §10 L371 也写明
 * 「weight 缺失时不要用线宽表示重要度」，前端 career-graph-store.ts L76 同样注明「只有部分边有」。
 * 抽取层没给出这个数字，这里就不替它编一个 —— 宁可字段缺席，也不造数据。
 *
 * 输入：knowledge/extract/candidates.jsonl、knowledge/pipeline/taxonomy.json
 * 输出：knowledge/extract/aliases.json、knowledge/extract/normalized.jsonl、knowledge/evidence/pipeline-log.json
 *
 * 跑法（仓库根目录）：
 *   node knowledge/pipeline/05-normalize.mjs
 */
import { nowIso, writeJson, writeJsonl, recordStep, logLine, fail } from "./lib/log.mjs";
import { ALIASES_FILE, NORMALIZED_FILE, relPath } from "./lib/paths.mjs";
import { KIND_ORDER, describeCounts, loadCandidates, normalizeAlias, sortEdges, sortNodes } from "./lib/graph.mjs";
import { loadTaxonomy } from "./lib/corpus.mjs";

const STEP = "05";
const TITLE = "术语归一与消歧";
const COMMAND = "node knowledge/pipeline/05-normalize.mjs";

const startedAt = nowIso();
const problems = [];

const { nodes, edges } = loadCandidates();
const taxonomy = loadTaxonomy();
const DRAFT = "llm-draft";

if (!taxonomy.annotatedByValues.includes(DRAFT)) {
  fail(`词表里没有 ${DRAFT}，taxonomy.annotatedByValues=${taxonomy.annotatedByValues.join(" / ")}`);
  process.exit(1);
}

// ---------- 别名倒排 ----------

/** key → { key, surfaces:Set, kinds:Set, ids:Set } */
const buckets = new Map();

function addSurface(surface, node) {
  const text = String(surface ?? "").trim();
  if (!text) return;
  const key = normalizeAlias(text);
  if (!key) return;
  if (!buckets.has(key)) buckets.set(key, { key, surfaces: new Set(), kinds: new Set(), ids: new Set() });
  const bucket = buckets.get(key);
  bucket.surfaces.add(text);
  bucket.kinds.add(node.kind);
  bucket.ids.add(node.id);
}

for (const node of nodes) {
  addSurface(node.label, node);
  for (const alias of node.aliases ?? []) addSurface(alias, node);
}

/** 别名词典条目。`status` 只有两类：unique（唯一指向）与 ambiguous（需人工消歧）。 */
const entries = [...buckets.values()]
  .map((bucket) => {
    const ids = [...bucket.ids].sort();
    const kinds = [...bucket.kinds].sort((a, b) => KIND_ORDER.indexOf(a) - KIND_ORDER.indexOf(b));
    const ambiguous = ids.length > 1;
    return {
      key: bucket.key,
      surfaces: [...bucket.surfaces].sort(),
      kinds,
      ids,
      status: ambiguous ? "ambiguous" : "unique",
      // 唯一的 key 直接给出指向；歧义的必须留给 06，这里不许猜。
      primary: ambiguous ? null : ids[0],
      conflict: ambiguous ? (kinds.length > 1 ? "cross-kind" : "same-kind") : null,
      resolvedBy: ambiguous ? "pending-adjudication" : "unique-surface",
    };
  })
  .sort((a, b) => a.key.localeCompare(b.key));

const entryByKey = new Map(entries.map((entry) => [entry.key, entry]));
const ambiguous = entries.filter((entry) => entry.status === "ambiguous");

const byKey = {};
for (const entry of entries) byKey[entry.key] = entry.ids;

/** 节点 id → 它的钥匙集合，供 07/10/11 反查。 */
const byId = {};
/** 一个节点参与的所有 key 里，哪些是有歧义的。 */
const conflictingKeysByNode = new Map();

for (const node of sortNodes(nodes)) {
  const surfaces = [node.label, ...(node.aliases ?? [])];
  const aliasKeys = [];
  for (const surface of surfaces) {
    const key = normalizeAlias(surface);
    if (key && !aliasKeys.includes(key)) aliasKeys.push(key);
  }
  const key = normalizeAlias(node.label);
  if (!key) problems.push(`节点 ${node.id} 的 label「${node.label}」归一化后为空`);
  if (!node.annotatedBy) problems.push(`节点 ${node.id} 缺 annotatedBy（§3.3 硬约束 2）`);

  const conflicts = [];
  for (const aliasKey of aliasKeys) {
    const entry = entryByKey.get(aliasKey);
    if (!entry || entry.status !== "ambiguous") continue;
    conflicts.push({ key: aliasKey, others: entry.ids.filter((id) => id !== node.id), conflict: entry.conflict });
  }
  if (conflicts.length > 0) conflictingKeysByNode.set(node.id, conflicts);

  byId[node.id] = { label: node.label, key, aliasKeys, conflicts };
}

// ---------- 归一化行 ----------

const normalizedNodes = sortNodes(nodes).map((node) => ({
  recordType: "node",
  id: node.id,
  kind: node.kind,
  label: node.label,
  labelKey: normalizeAlias(node.label),
  aliases: node.aliases ?? [],
  aliasKeys: byId[node.id].aliasKeys,
  description: node.description ?? "",
  direction: node.direction ?? null,
  sourceRefs: node.sourceRefs ?? [],
  annotatedBy: node.annotatedBy ?? DRAFT,
  normalize: {
    status: conflictingKeysByNode.has(node.id) ? "ambiguous" : "unique",
    conflicts: conflictingKeysByNode.get(node.id) ?? [],
  },
}));

const normalizedEdges = sortEdges(edges).map((edge) => {
  if (!edge.annotatedBy) problems.push(`边 ${edge.id}（${edge.type}）缺 annotatedBy（§3.3 硬约束 2）`);
  if (!Array.isArray(edge.sourceRefs) || edge.sourceRefs.length === 0) {
    problems.push(`边 ${edge.id}（${edge.type}）缺 sourceRefs（§3.3 硬约束 1，第 09 步会再拦一次）`);
  }
  return {
    ...edge,
    annotatedBy: edge.annotatedBy ?? DRAFT,
    edgeKey: `${edge.type}|${edge.from}|${edge.to}`,
  };
});

if (problems.length > 0) {
  fail(`${problems.length} 条校验不通过，未写出 ${relPath(NORMALIZED_FILE)}：`);
  for (const problem of problems.slice(0, 40)) logLine(`  - ${problem}`);
  if (problems.length > 40) logLine(`  …还有 ${problems.length - 40} 条`);
  process.exit(1);
}

const nodesByKind = {};
for (const node of normalizedNodes) nodesByKind[node.kind] = (nodesByKind[node.kind] ?? 0) + 1;
const edgesByType = {};
for (const edge of normalizedEdges) edgesByType[edge.type] = (edgesByType[edge.type] ?? 0) + 1;

const aliasesDoc = {
  schema: "career-graph-aliases/v1",
  generatedAt: startedAt,
  note: "归一化只做确定性变换（去空白、统一全/半角括号与各类连字符、去间隔号、小写）。歧义 key 不猜指向，交给第 06 步人工裁定。",
  rules: [
    "key = label/alias 去掉全部空白 + 统一连字符与括号 + 去掉 ·・ + 转小写",
    "status=unique：该 key 只命中一个节点，primary 直接给出指向",
    "status=ambiguous：该 key 命中多个节点，primary=null，必须由 06-adjudicate.mjs 裁决",
    "cross-kind：歧义跨越节点类型（例如同时命中 skill 与 tool），比同类型歧义更需要人工确认",
  ],
  counts: {
    keys: entries.length,
    uniqueKeys: entries.length - ambiguous.length,
    ambiguousKeys: ambiguous.length,
    surfaces: entries.reduce((sum, entry) => sum + entry.surfaces.length, 0),
    nodes: nodes.length,
    nodesWithAliases: nodes.filter((node) => (node.aliases ?? []).length > 0).length,
    nodesInAmbiguity: conflictingKeysByNode.size,
  },
  entries,
  byKey,
  byId,
};

writeJson(ALIASES_FILE, aliasesDoc);
writeJsonl(NORMALIZED_FILE, [...normalizedNodes, ...normalizedEdges]);

const notes = [
  `把 ${nodes.length} 个节点、${edges.length} 条边归一化；别名倒排 ${entries.length} 个 key（${entries.length - ambiguous.length} 个唯一、${ambiguous.length} 个歧义）。`,
  `节点分布：${describeCounts(nodesByKind, KIND_ORDER)}。`,
  `边分布：${describeCounts(edgesByType)}。`,
  ambiguous.length > 0
    ? `歧义 key：${ambiguous.map((entry) => `${entry.key}(${entry.conflict}:${entry.ids.join("/")})`).join("，")} —— 已写进 06 的待裁定清单。`
    : "没有歧义 key。",
  "不发明 weight：设计文档 §3.2 把它列在边结构里，§10 L371 明确「缺失时不要用线宽表示重要度」，所以抽取层没给就保持缺席。",
  `节点与边的 annotatedBy 统一为 ${DRAFT}，等第 06 步裁定升级。`,
];

recordStep({
  step: STEP,
  title: TITLE,
  command: COMMAND,
  inputs: ["knowledge/extract/candidates.jsonl", "knowledge/pipeline/taxonomy.json"],
  outputs: [ALIASES_FILE, NORMALIZED_FILE],
  counts: {
    nodes: nodes.length,
    edges: edges.length,
    aliasKeys: entries.length,
    ambiguousKeys: ambiguous.length,
    nodesByKind,
    edgesByType,
  },
  notes,
  startedAt,
});

logLine(`${TITLE}：${nodes.length} 节点 / ${edges.length} 边 → ${relPath(NORMALIZED_FILE)}`);
logLine(`  别名倒排 ${entries.length} 个 key，其中 ${ambiguous.length} 个歧义`);
for (const entry of ambiguous) logLine(`    ⚠ ${entry.key} → ${entry.ids.join(" / ")}（${entry.conflict}）`);
logLine(`  别名词典：${relPath(ALIASES_FILE)}`);
