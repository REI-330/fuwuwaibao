#!/usr/bin/env node
/**
 * 04 实体与关系抽取
 *
 * 设计文档 §6 第 4 步：抽取实体与关系 → candidates.jsonl。
 * 本仓库没有外部 LLM 调用（零依赖、可离线复跑），所以这一步拆成两半：
 *   - 语义部分：knowledge/extract/seed.json，由人/LLM 按 §3.1 §3.2 的契约写出来；
 *   - 机械部分：本脚本。它做四件事，且每件都是硬校验 ——
 *       1) 词表校验：kind 必须是 taxonomy 的 8 类之一，edge type 必须是 8 种之一，
 *          from/to 的 kind 组合必须符合 taxonomy 的 from/to 声明；
 *       2) 引文校验：每条 node / edge 的每个 evidence.quote 必须在对应 chunk 的
 *          原文里逐字符找得到（lib/corpus.mjs 的子串检查）。写不出出处就构建失败 ——
 *          这是设计文档 L0 铁律「不输出无出处结论」在执行层的落点；
 *       3) 补全 evidenced_by：每个有出处的节点自动生成 node → chunk:<chunkId> 的边，
 *          免去手工维护「查看依据」入口，也保证它不可能漏；
 *       4) 稳定编号：节点 id 用 kind 前缀 + 短编号，边 id 用 e-0001 递增，保证同输入同输出。
 *       5) 补全 learning_unit：knowledge 节点的 ref 形如 `<职业短编号>:stageN`，据此补出
 *          occupation → knowledge 的入边（设计文档 §10 L413 的缺口），否则学习单元在图上永远是孤点。
 *
 * 输入：knowledge/extract/seed.json、knowledge/chunks/chunks.jsonl、knowledge/pipeline/taxonomy.json
 * 输出：knowledge/extract/candidates.jsonl、knowledge/evidence/pipeline-log.json
 *
 * 跑法（仓库根目录）：
 *   node knowledge/pipeline/04-extract-candidates.mjs
 */
import { existsSync } from "node:fs";

import { nowIso, writeJsonl, recordStep, logLine, fail, readJson } from "./lib/log.mjs";
import {
  CANDIDATES_FILE,
  EXTRACT_SEED_FILE,
  relPath,
} from "./lib/paths.mjs";
import { findPrerequisiteCycle, loadChunkIndex, loadTaxonomy, checkEvidence } from "./lib/corpus.mjs";

const STEP = "04";
const TITLE = "实体与关系抽取";
const COMMAND = "node knowledge/pipeline/04-extract-candidates.mjs";

const startedAt = nowIso();
const problems = [];

if (!existsSync(EXTRACT_SEED_FILE)) {
  fail(`缺少抽取种子 ${relPath(EXTRACT_SEED_FILE)}。这一步的语义输入必须有人/LLM 先写出来。`);
  process.exit(1);
}

const taxonomy = loadTaxonomy();
const kindById = new Map(taxonomy.nodeKinds.map((kind) => [kind.id, kind]));
const edgeTypeById = new Map(taxonomy.edgeTypes.map((type) => [type.id, type]));
const { index: chunkIndex } = loadChunkIndex();
const seed = readJson(EXTRACT_SEED_FILE);

const today = startedAt.slice(0, 10);
const annotatedBy = seed.annotatedBy ?? "llm-draft";
if (!taxonomy.annotatedByValues.includes(annotatedBy)) {
  problems.push(`seed.annotatedBy="${annotatedBy}" 不在词表里（${taxonomy.annotatedByValues.join(" / ")}）`);
}

// ---------- 节点 ----------

const nodes = [];
const refToId = new Map();
const byId = new Map();

for (const raw of seed.nodes ?? []) {
  const kind = kindById.get(raw.kind);
  if (!kind) {
    problems.push(`节点 ref="${raw.ref}" 的 kind="${raw.kind}" 不在词表里`);
    continue;
  }
  const id = raw.id ?? `${kind.prefix}${raw.ref}`;
  if (!id.startsWith(kind.prefix)) {
    problems.push(`节点 ${id} 的 id 前缀与 kind=${kind.id} 不一致（应为 ${kind.prefix}…）`);
  }
  if (byId.has(id)) {
    problems.push(`节点 id 重复：${id}`);
    continue;
  }
  if (!raw.label || String(raw.label).trim().length < 2) {
    problems.push(`节点 ${id} 缺少 label`);
  }
  const { refs, problems: evidenceProblems } = checkEvidence(raw.evidence, chunkIndex, `节点 ${id}`);
  problems.push(...evidenceProblems);

  const node = {
    recordType: "node",
    id,
    kind: kind.id,
    label: String(raw.label ?? "").trim(),
    aliases: Array.isArray(raw.aliases) ? raw.aliases.filter(Boolean) : [],
    description: String(raw.description ?? "").trim(),
    direction: raw.direction ?? null,
    sourceRefs: refs,
    // §3.3 硬约束 2：annotatedBy 必须存在于每个节点与每条边。节点同样默认草稿态，
    // 只有第 06/08 步的人工裁定才能把它升级为 human / llm-reviewed。
    annotatedBy,
  };
  nodes.push(node);
  byId.set(id, node);
  if (raw.ref) {
    if (refToId.has(raw.ref)) problems.push(`节点短编号重复：${raw.ref}`);
    refToId.set(raw.ref, id);
  }
}

/** 允许在 seed 里用短编号（SK215）或完整 id（skill:SK215）引用节点。 */
function resolveNodeRef(token, owner) {
  if (!token) {
    problems.push(`${owner}：缺少节点引用`);
    return null;
  }
  if (byId.has(token)) return token;
  if (refToId.has(token)) return refToId.get(token);
  problems.push(`${owner}：引用不到节点「${token}」`);
  return null;
}

// ---------- 边 ----------

const edges = [];
let edgeSeq = 0;

function pushEdge(row) {
  edgeSeq += 1;
  edges.push({ recordType: "edge", id: `e-${String(edgeSeq).padStart(4, "0")}`, updatedAt: today, ...row });
}

const authored = seed.edges ?? [];
const seenEdgeKeys = new Set();

for (const raw of authored) {
  const type = edgeTypeById.get(raw.type);
  if (!type) {
    problems.push(`边 type="${raw.type}" 不在词表里（${[...edgeTypeById.keys()].join(" / ")}）`);
    continue;
  }
  if (type.id === "evidenced_by") {
    problems.push(`不要手写 evidenced_by 边（由本步按 evidence 自动生成），出错条目 from=${raw.from} to=${raw.to}`);
    continue;
  }

  const from = resolveNodeRef(raw.from, `边 ${type.id} from`);
  const to = resolveNodeRef(raw.to, `边 ${type.id} to`);
  if (!from || !to) continue;

  if (from === to) problems.push(`边 ${type.id} 自指：${from}`);

  const fromKind = byId.get(from).kind;
  const toKind = byId.get(to).kind;
  if (!type.from.includes(fromKind)) {
    problems.push(`边 ${type.id} 的 from 只能是 ${type.from.join("/")}，实际是 ${fromKind}（${from}）`);
  }
  if (!type.to.includes(toKind)) {
    problems.push(`边 ${type.id} 的 to 只能是 ${type.to.join("/")}，实际是 ${toKind}（${to}）`);
  }
  if (type.id === "requires") {
    if (!Number.isFinite(raw.targetLevel)) problems.push(`边 requires ${from}→${to} 缺 targetLevel(1-5)`);
    if (!Number.isFinite(raw.importance)) problems.push(`边 requires ${from}→${to} 缺 importance(0-1)`);
  }
  if (type.id === "transitions_to" && !Number.isFinite(raw.horizon)) {
    problems.push(`边 transitions_to ${from}→${to} 缺 horizon(年)`);
  }
  if (type.id === "emerging_in" && !Number.isFinite(raw.year)) {
    problems.push(`边 emerging_in ${from}→${to} 缺 year`);
  }

  const key = `${type.id}|${from}|${to}`;
  if (seenEdgeKeys.has(key)) problems.push(`重复边：${key}`);
  seenEdgeKeys.add(key);

  const { refs, problems: evidenceProblems } = checkEvidence(raw.evidence, chunkIndex, `边 ${type.id} ${from}→${to}`);
  problems.push(...evidenceProblems);

  const row = {
    type: type.id,
    from,
    to,
    weight: Number.isFinite(raw.weight) ? raw.weight : undefined,
    sourceRefs: refs,
    annotatedBy,
  };
  for (const field of type.fields) {
    if (raw[field] !== undefined && raw[field] !== null) row[field] = raw[field];
  }
  pushEdge(row);
}

// ---------- evidenced_by：按 evidence 自动补齐 ----------

let evidenceEdges = 0;
for (const node of nodes) {
  for (const chunkId of node.sourceRefs) {
    pushEdge({
      type: "evidenced_by",
      from: node.id,
      to: `chunk:${chunkId}`,
      sourceRefs: [chunkId],
      annotatedBy,
    });
    evidenceEdges += 1;
  }
}

// ---------- learning_unit：按 knowledge 节点的 ref 结构补齐入边 ----------

/**
 * 设计文档 §10 L413 留下的缺口：knowledge（学习单元）节点一个入边都没有，图上永远是孤点。
 * 词表里补 `learning_unit`（occupation → knowledge）后，这里按 ref 的结构补齐：
 * knowledge 节点的 ref 形如 `<职业短编号>:stageN`，前缀就是它归属的职业。
 *
 * 这不是「新造事实」：归属关系本来就写在 ref 里，而且边的 sourceRefs 直接继承该学习单元
 * 自己的 sourceRefs，所以每条边仍然指得到真实 chunk（§3.3 硬约束 1 不因此松口）。
 */
let learningUnitEdges = 0;
for (const node of nodes) {
  if (node.kind !== "knowledge") continue;
  const [ownerRef, stage] = String(node.id).slice("knowledge:".length).split(":");
  const owner = refToId.get(ownerRef);
  if (!owner) {
    problems.push(`学习单元 ${node.id} 的前缀「${ownerRef}」对应不到任何职业节点`);
    continue;
  }
  if (byId.get(owner).kind !== "occupation") {
    problems.push(`学习单元 ${node.id} 的前缀「${ownerRef}」指向的不是职业节点（${byId.get(owner).kind}）`);
    continue;
  }
  const position = Number(String(stage ?? "").replace(/[^0-9]/g, ""));
  pushEdge({
    type: "learning_unit",
    from: owner,
    to: node.id,
    ...(Number.isFinite(position) && position > 0 ? { position } : {}),
    sourceRefs: [...node.sourceRefs],
    annotatedBy,
  });
  learningUnitEdges += 1;
}

// ---------- 环路预警（第 09 步会再硬校验一次并直接失败） ----------

const cycle = findPrerequisiteCycle(edges);

if (problems.length > 0) {
  fail(`${problems.length} 条校验不通过，未写出 ${relPath(CANDIDATES_FILE)}：`);
  for (const problem of problems.slice(0, 40)) logLine(`  - ${problem}`);
  if (problems.length > 40) logLine(`  …还有 ${problems.length - 40} 条`);
  process.exit(1);
}

const rows = [...nodes, ...edges];
writeJsonl(CANDIDATES_FILE, rows);

const byKind = {};
for (const node of nodes) byKind[node.kind] = (byKind[node.kind] ?? 0) + 1;
const byType = {};
for (const edge of edges) byType[edge.type] = (byType[edge.type] ?? 0) + 1;

const counts = {
  nodes: nodes.length,
  edges: edges.length,
  evidencedByEdges: evidenceEdges,
  learningUnitEdges,
  authoredEdges: edges.length - evidenceEdges - learningUnitEdges,
  nodesByKind: byKind,
  edgesByType: byType,
  distinctChunksCited: new Set(nodes.flatMap((node) => node.sourceRefs)).size,
  prerequisiteCycle: cycle.length > 0 ? cycle.join(" → ") : null,
};

const notes = [
  `从 seed.json 校验出 ${nodes.length} 个节点、${edges.length} 条边（其中 ${evidenceEdges} 条 evidenced_by、${learningUnitEdges} 条 learning_unit 由结构自动生成）。`,
  `每条结论都挂到了具体 chunk：引文逐字符子串校验通过率 100%（不通过会直接构建失败）。`,
  `节点分布：${Object.entries(byKind).map(([k, v]) => `${k} ${v}`).join("，")}。`,
  `边分布：${Object.entries(byType).map(([k, v]) => `${k} ${v}`).join("，")}。`,
  cycle.length > 0
    ? `⚠ prerequisite 存在环路：${cycle.join(" → ")}，第 09 步会直接失败。`
    : "prerequisite 无环。",
  `annotatedBy 统一标为 ${annotatedBy}：人工裁定前都只是草稿，第 06/08 步才会升级为 human / llm-reviewed。`,
];

recordStep({
  step: STEP,
  title: TITLE,
  command: COMMAND,
  inputs: [EXTRACT_SEED_FILE, "knowledge/chunks/chunks.jsonl", "knowledge/pipeline/taxonomy.json"],
  outputs: [CANDIDATES_FILE],
  counts,
  notes,
  startedAt,
});

logLine(`${TITLE}：${counts.nodes} 个节点 / ${counts.edges} 条边（${evidenceEdges} 条依据边），写入 ${relPath(CANDIDATES_FILE)}`);
logLine(`  节点：${Object.entries(byKind).map(([k, v]) => `${k} ${v}`).join("，")}`);
logLine(`  边：${Object.entries(byType).map(([k, v]) => `${k} ${v}`).join("，")}`);
if (cycle.length > 0) logLine(`  ⚠ prerequisite 环路：${cycle.join(" → ")}`);
