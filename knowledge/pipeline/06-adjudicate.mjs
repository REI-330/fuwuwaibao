#!/usr/bin/env node
/**
 * 06 人工裁定
 *
 * 设计文档 §6 L251 写得很直白：**06 不可省。它是 `llm-draft` → `llm-reviewed` 的唯一通道，
 * 也是「人在回路」的实证。** 所以这个脚本刻意分成两半：
 *
 *   机器那一半（本脚本负责）：
 *     - 算出该审哪些东西（§4.2 的全审 / 抽审分级，规则写死在下文 FULL_* 常量里）；
 *     - 生成 review/review-queue.md 与 review/adjudication.json 骨架；
 *     - 校验人工文件是否填全，没填全就 exit 1 并列出缺什么。
 *
 *   人那一半（不许脚本代劳）：
 *     - `reviewer`（谁审的）、`reviewedAt`（哪天审的）；
 *     - 3 个歧义 key 各判一次；
 *     - 全审组与抽审组给出结论；
 *     - 有异议的条目写进 `overrides`（reject / edit）。
 *
 * 关键点：`reviewer` 为空时**不会**写出 accepted.jsonl。宁可让流水线停下来，也不让脚本
 * 自己签一个「人工已审」。文件里 `annotatedBy: llm-reviewed` 一旦写出去，就意味着有人
 * 真的看过，这个谎言代价太大。
 *
 * 输入：knowledge/extract/normalized.jsonl、knowledge/extract/aliases.json、
 *       knowledge/review/adjudication.json（人工填）
 * 输出：knowledge/extract/accepted.jsonl、knowledge/review/review-queue.md、
 *       knowledge/review/adjudication.json（缺失时写骨架）、knowledge/evidence/pipeline-log.json
 *
 * 跑法（仓库根目录）：node knowledge/pipeline/06-adjudicate.mjs
 */
import { existsSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

import { logLine, fail, nowIso, readJson, recordStep, writeJson, writeJsonl } from "./lib/log.mjs";
import { ACCEPTED_FILE, ADJUDICATION_FILE, REVIEW_QUEUE_FILE, relPath } from "./lib/paths.mjs";
import {
  KIND_ORDER,
  describeCounts,
  edgeKey,
  indexById,
  isChunkTarget,
  loadAliases,
  loadNormalized,
  sortEdges,
  sortNodes,
} from "./lib/graph.mjs";

const STEP = "06";
const TITLE = "人工裁定";
const COMMAND = "node knowledge/pipeline/06-adjudicate.mjs";
const DRAFT = "llm-draft";
const REVIEWED = "llm-reviewed";

/** 全审的边类型：直接决定「学什么、先学什么、往哪转」，错一条就错一片（§4.2 全审行）。 */
const FULL_EDGE_TYPES = ["requires", "prerequisite", "learning_unit", "transitions_to"];
/** 抽审比例与下限：§4.2 要求「抽查 ≥20%」，下限取 6 是让样本量在小图上也有意义。 */
const SPOT_RATE = 0.25;
const SPOT_MIN = 6;

const startedAt = nowIso();
const { nodes, edges } = loadNormalized();
const aliases = loadAliases();

if (!Array.isArray(aliases.entries)) {
  fail(`${relPath(ADJUDICATION_FILE)} 依赖的别名词典结构不对，请先重跑 05-normalize.mjs`);
  process.exit(1);
}

// ---------- 1) 机器算范围 ----------

/** 被 `requires` 指到的技能 = 会被推荐理由 / 技能差距 / 任务直接引用，所以全审。 */
const requiredSkillIds = new Set(edges.filter((edge) => edge.type === "requires").map((edge) => edge.to));

const fullNodeIds = new Set();
for (const node of nodes) {
  // knowledge 节点（学习单元）不进全审：§4.2 的全审范围是「会被推荐理由 / 技能差距 / 任务直接引用的实体」，
  // 学习单元是承载它们的容器，按 §4.2 归入抽审那一档，抽不到就保持 llm-draft。
  if (node.kind === "occupation") fullNodeIds.add(node.id);
  else if (node.kind === "skill" && requiredSkillIds.has(node.id)) fullNodeIds.add(node.id);
}

/** `evidenced_by` 指向 chunk 不是节点，不参与实体审查（它的正确性由第 09 步用原文校验）。 */
const authoredEdges = edges.filter((edge) => !isChunkTarget(edge.to));
const fullEdgeKeys = new Set(
  authoredEdges.filter((edge) => FULL_EDGE_TYPES.includes(edge.type)).map((edge) => edgeKey(edge)),
);

const fullNodes = sortNodes(nodes.filter((node) => fullNodeIds.has(node.id)));
const fullEdges = sortEdges(authoredEdges.filter((edge) => fullEdgeKeys.has(edgeKey(edge))));
const restNodes = sortNodes(nodes.filter((node) => !fullNodeIds.has(node.id)));
const restEdges = sortEdges(authoredEdges.filter((edge) => !fullEdgeKeys.has(edgeKey(edge))));

/**
 * 抽审抽样必须可复算：用 FNV-1a 把 id 哈希成 32 位整数再排序取前 N。
 * 不用 Math.random —— 那样每次跑出来的样本不同，复查时无法解释「上次到底抽了哪些」。
 */
function hash32(text) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

const restTargets = [
  ...restNodes.map((node) => ({ ref: node.id, recordType: "node" })),
  ...restEdges.map((edge) => ({ ref: edgeKey(edge), recordType: "edge" })),
];
const spotSize = Math.max(SPOT_MIN, Math.ceil(restTargets.length * SPOT_RATE));
const spotCheck = [...restTargets]
  .sort((a, b) => hash32(a.ref) - hash32(b.ref) || a.ref.localeCompare(b.ref))
  .slice(0, spotSize);
const spotRefs = new Set(spotCheck.map((item) => item.ref));

// ---------- 2) 歧义 key 的候选裁定（提议，不是结论） ----------

/**
 * 提议规则（确定性，可被人推翻）：
 *   「这个 key 恰好等于某个节点的 label」→ 提议指向它（label 比 alias 更像正主）；
 *   若有两个以上节点的 label 都精确等于该 key，说明是真同名，提议 keep-separate。
 */
function proposeAliasResolution(entry) {
  const exact = entry.ids.filter((id) => aliasKeyOf(id) === entry.key);
  return exact.length === 1 ? exact[0] : "keep-separate";
}

const nodeById = indexById(nodes);
function aliasKeyOf(id) {
  return nodeById.get(id)?.labelKey ?? "";
}

const aliasItems = aliases.entries
  .filter((entry) => entry.status === "ambiguous")
  .map((entry) => ({
    key: entry.key,
    ids: entry.ids,
    conflict: entry.conflict,
    surfaces: entry.surfaces,
    proposed: proposeAliasResolution(entry),
    decision: null,
    reason: null,
  }));

// ---------- 3) 骨架与队列 ----------

const adjudicationTemplate = {
  schema: "career-graph-adjudication/v1",
  step: STEP,
  generatedAt: startedAt,
  note: "人工裁定文件。reviewer / reviewedAt / 各组 verdict 必须由人填写；填完重跑 06-adjudicate.mjs 才会写出 accepted.jsonl。",
  fields: {
    reviewer: "谁审的（人名或账号），空着流水线不动",
    reviewedAt: "审核日期 YYYY-MM-DD",
    "aliasDecisions[].decision": "填某个节点 id，或 keep-separate（表示两者确实同名，不该合并）",
    "fullReview.verdict": "accept 全部通过 / reject 全部不通过（reject 会从 accepted 里剔除）",
    "spotCheck.reviewed": `必须等于抽样条数 ${spotSize}`,
    "spotCheck.findings": "抽查发现的问题，没有问题写 []；有就写清楚哪条错在哪",
    overrides: "个别条目改判：{ ref, verdict: accept|reject|edit, patch?, reason }（edit 需给 patch）",
  },
  reviewer: null,
  reviewedAt: null,
  aliasDecisions: aliasItems,
  fullReview: { verdict: null, reason: null, nodes: fullNodes.length, edges: fullEdges.length },
  spotCheck: { verdict: null, reason: null, sampleSize: spotSize, reviewed: null, findings: null },
  overrides: [],
};

mkdirSync(dirname(ADJUDICATION_FILE), { recursive: true });
const adjudicationExists = existsSync(ADJUDICATION_FILE);
if (!adjudicationExists) writeJson(ADJUDICATION_FILE, adjudicationTemplate);

let adjudication = null;
if (adjudicationExists) {
  try {
    adjudication = readJson(ADJUDICATION_FILE);
  } catch (error) {
    fail(`${relPath(ADJUDICATION_FILE)} 不是合法 JSON：${String(error)}`);
    process.exit(1);
  }
}

// ---------- 4) 校验人工文件 ----------

const problems = [];
const aliasDecisions = Array.isArray(adjudication?.aliasDecisions) ? adjudication.aliasDecisions : [];
const decisionByKey = new Map(aliasDecisions.map((item) => [item?.key, item]));

if (!adjudication) {
  problems.push("文件刚生成，还没人填");
} else {
  if (typeof adjudication.reviewer !== "string" || adjudication.reviewer.trim() === "") {
    problems.push("reviewer 为空 —— 必须写明是谁做的裁定");
  }
  if (typeof adjudication.reviewedAt !== "string" || !/^\d{4}-\d{2}-\d{2}/.test(adjudication.reviewedAt)) {
    problems.push("reviewedAt 为空或不是 YYYY-MM-DD");
  }
  for (const item of aliasItems) {
    const decided = decisionByKey.get(item.key);
    if (!decided) {
      problems.push(`歧义 key「${item.key}」没有对应裁定条目`);
      continue;
    }
    const allowed = [...item.ids, "keep-separate"];
    if (!allowed.includes(decided.decision)) {
      problems.push(`歧义 key「${item.key}」的 decision=${JSON.stringify(decided.decision)}，只能是 ${allowed.join(" / ")}`);
    }
    if (typeof decided.reason !== "string" || decided.reason.trim() === "") {
      problems.push(`歧义 key「${item.key}」缺 reason`);
    }
  }
  const full = adjudication.fullReview ?? {};
  if (!["accept", "reject"].includes(full.verdict)) problems.push("fullReview.verdict 必须是 accept 或 reject");
  if (typeof full.reason !== "string" || full.reason.trim() === "") problems.push("fullReview.reason 为空");
  const spot = adjudication.spotCheck ?? {};
  if (!["accept", "reject"].includes(spot.verdict)) problems.push("spotCheck.verdict 必须是 accept 或 reject");
  if (typeof spot.reason !== "string" || spot.reason.trim() === "") problems.push("spotCheck.reason 为空");
  if (spot.reviewed !== spotSize) problems.push(`spotCheck.reviewed=${JSON.stringify(spot.reviewed)}，必须等于抽样条数 ${spotSize}`);
  if (!Array.isArray(spot.findings)) problems.push("spotCheck.findings 必须是数组（没有发现就写 []）");
  for (const override of adjudication.overrides ?? []) {
    if (typeof override?.ref !== "string" || override.ref === "") problems.push("overrides 里有条目缺 ref");
    if (!["accept", "reject", "edit"].includes(override?.verdict)) {
      problems.push(`overrides「${override?.ref}」的 verdict 必须是 accept / reject / edit`);
    }
    if (override?.verdict === "edit" && (typeof override.patch !== "object" || override.patch === null)) {
      problems.push(`overrides「${override?.ref}」是 edit，必须给 patch`);
    }
  }
}

// ---------- 5) 队列文档（无论成功与否都刷新） ----------

function tableRows(rows, columns) {
  return rows.map((row) => `| ${columns.map((column) => column(row)).join(" | ")} |`);
}

const queueLines = [
  "# 06 人工裁定队列",
  "",
  `生成时间：${startedAt}`,
  "",
  "> 本文件由 `knowledge/pipeline/06-adjudicate.mjs` 生成，**不要手改**。",
  "> 人的输入只写进 `knowledge/review/adjudication.json`。",
  "",
  "## 范围",
  "",
  `- 全审（§4.2）：${fullNodes.length} 个节点 + ${fullEdges.length} 条边`,
  `  - 依据：occupation 节点，以及被 \`requires\` 指到的 skill 节点；边类型 ${FULL_EDGE_TYPES.join(" / ")}`,
  `- 抽审（§4.2，≥20%）：从其余 ${restTargets.length} 条里确定性抽 ${spotSize} 条（FNV-1a 排序取前 N）`,
  `- 未编页/未审：其余 ${restTargets.length - spotSize} 条保持 \`${DRAFT}\``,
  "",
  "## 歧义 key（必须逐条裁定）",
  "",
  "| key | 冲突 | 候选 | 机器提议 | 裁定 | 理由 |",
  "|---|---|---|---|---|---|",
  ...aliasItems.map(
    (item) => `| \`${item.key}\` | ${item.conflict} | ${item.ids.join("<br>")} | \`${item.proposed}\` | _待填_ | _待填_ |`,
  ),
  "",
  "## 全审节点",
  "",
  "| id | kind | label | 边数 |",
  "|---|---|---|---|",
  ...tableRows(fullNodes, [
    (node) => `\`${node.id}\``,
    (node) => node.kind,
    (node) => node.label,
    (node) => String(authoredEdges.filter((edge) => edge.from === node.id || edge.to === node.id).length),
  ]),
  "",
  "## 全审边",
  "",
  "| 边 | 出处 |",
  "|---|---|",
  ...tableRows(fullEdges, [(edge) => `\`${edge.type}\` ${edge.from} → ${edge.to}`, (edge) => (edge.sourceRefs ?? []).join(" ")]),
  "",
  "## 抽审样本",
  "",
  "| 条目 | 类型 | 出处 |",
  "|---|---|---|",
  ...tableRows(spotCheck.map((item) => ({ item, row: item.recordType === "node" ? nodeById.get(item.ref) : null })), [
    (entry) => `\`${entry.item.ref}\``,
    (entry) => (entry.item.recordType === "node" ? `node · ${entry.row?.kind ?? "?"}` : "edge"),
    (entry) => (entry.row?.sourceRefs ?? (authoredEdges.find((edge) => edgeKey(edge) === entry.item.ref)?.sourceRefs ?? [])).join(" "),
  ]),
  "",
];

writeText(REVIEW_QUEUE_FILE, `${queueLines.join("\n")}\n`);

if (problems.length > 0) {
  fail(`${relPath(ADJUDICATION_FILE)} 还不能用来裁定（${problems.length} 项）：`);
  for (const problem of problems.slice(0, 40)) logLine(`  - ${problem}`);
  logLine(`  → 请填写 ${relPath(ADJUDICATION_FILE)}，队列见 ${relPath(REVIEW_QUEUE_FILE)}；填完重跑本步骤。`);
  recordStep({
    step: STEP,
    title: TITLE,
    command: COMMAND,
    inputs: ["knowledge/extract/normalized.jsonl", "knowledge/extract/aliases.json", "knowledge/review/adjudication.json"],
    outputs: [REVIEW_QUEUE_FILE, ADJUDICATION_FILE],
    counts: {
      nodes: nodes.length,
      edges: edges.length,
      fullReviewNodes: fullNodes.length,
      fullReviewEdges: fullEdges.length,
      spotCheckSample: spotSize,
      ambiguousKeys: aliasItems.length,
      blocked: problems.length,
    },
    notes: [
      `人工文件未填全（${problems.length} 项），按要求**没有**写出 ${relPath(ACCEPTED_FILE)} —— 06 是 llm-draft → llm-reviewed 的唯一通道，脚本不许自己签字。`,
      `全审 ${fullNodes.length} 节点 / ${fullEdges.length} 边；抽审 ${spotSize} 条。`,
    ],
    startedAt,
  });
  process.exit(1);
}

// ---------- 6) 应用裁定 ----------

const reviewer = adjudication.reviewer.trim();
const reviewedAt = adjudication.reviewedAt;
const fullVerdict = adjudication.fullReview.verdict;
const spotVerdict = adjudication.spotCheck.verdict;
const overrides = Array.isArray(adjudication.overrides) ? adjudication.overrides : [];
const overrideByRef = new Map(overrides.map((item) => [item.ref, item]));

/** 被驳回的条目：从 accepted 里剔除，并记进日志（驳回记录本身就是材料 ② 的证据，§4.3）。 */
const rejectedRefs = new Set();
if (fullVerdict === "reject") {
  for (const node of fullNodes) rejectedRefs.add(node.id);
  for (const edge of fullEdges) rejectedRefs.add(edgeKey(edge));
}
if (spotVerdict === "reject") for (const item of spotCheck) rejectedRefs.add(item.ref);
for (const override of overrides) {
  if (override.verdict === "reject") rejectedRefs.add(override.ref);
}

const patches = new Map();
for (const override of overrides) {
  if (override.verdict === "edit") {
    const { ref, patch } = override;
    patches.set(ref, patch);
  }
}

/** 裁定后仍保留的一条记录：决定它的 annotatedBy 与操作日志字段。 */
function adjudicate(row, ref, scope) {
  const override = overrideByRef.get(ref);
  const reviewed = scope !== "unreviewed" || override?.verdict === "accept";
  const patched = patches.has(ref) ? { ...row, ...patches.get(ref) } : row;
  return {
    ...patched,
    annotatedBy: reviewed ? REVIEWED : DRAFT,
    adjudication: {
      scope: override?.verdict === "accept" ? "spot-check" : scope,
      reviewer,
      reviewedAt,
      reason:
        override?.reason ??
        (scope === "full" ? adjudication.fullReview.reason : scope === "spot-check" ? adjudication.spotCheck.reason : "未纳入本轮审核（§4.2 抽审之外）"),
    },
  };
}

const acceptedNodes = [];
for (const node of sortNodes(nodes)) {
  if (rejectedRefs.has(node.id)) continue;
  const scope = fullNodeIds.has(node.id) ? "full" : spotRefs.has(node.id) ? "spot-check" : "unreviewed";
  acceptedNodes.push(adjudicate(node, node.id, scope));
}

const acceptedEdges = [];
let cascaded = 0;
const keptIds = new Set(acceptedNodes.map((node) => node.id));
for (const edge of sortEdges(edges)) {
  const ref = edgeKey(edge);
  if (rejectedRefs.has(ref)) continue;
  if (!isChunkTarget(edge.to) && (!keptIds.has(edge.from) || !keptIds.has(edge.to))) {
    cascaded += 1;
    continue;
  }
  const scope = isChunkTarget(edge.to) ? "unreviewed" : fullEdgeKeys.has(ref) ? "full" : spotRefs.has(ref) ? "spot-check" : "unreviewed";
  acceptedEdges.push(adjudicate(edge, ref, scope));
}

writeJsonl(ACCEPTED_FILE, [...acceptedNodes, ...acceptedEdges]);

const reviewedNodes = acceptedNodes.filter((node) => node.annotatedBy === REVIEWED).length;
const reviewedEdges = acceptedEdges.filter((edge) => edge.annotatedBy === REVIEWED).length;
const nodesByKind = {};
for (const node of acceptedNodes) nodesByKind[node.kind] = (nodesByKind[node.kind] ?? 0) + 1;
const edgesByType = {};
for (const edge of acceptedEdges) edgesByType[edge.type] = (edgesByType[edge.type] ?? 0) + 1;
const annotatedBy = {};
for (const row of [...acceptedNodes, ...acceptedEdges]) annotatedBy[row.annotatedBy] = (annotatedBy[row.annotatedBy] ?? 0) + 1;

const aliasResolution = aliasItems.map((item) => ({
  key: item.key,
  ids: item.ids,
  conflict: item.conflict,
  proposal: item.proposed,
  decision: decisionByKey.get(item.key).decision,
  reason: decisionByKey.get(item.key).reason,
  overridden: decisionByKey.get(item.key).decision !== item.proposed,
}));

writeText(
  REVIEW_QUEUE_FILE,
  `${queueLines.join("\n")}\n## 裁定结果（${reviewedAt}，${reviewer}）\n\n` +
    `- 全审：${adjudication.fullReview.verdict} —— ${adjudication.fullReview.reason}\n` +
    `- 抽审：${adjudication.spotCheck.verdict}，已看 ${adjudication.spotCheck.reviewed} 条 —— ${adjudication.spotCheck.reason}\n` +
    `- 抽查发现：${adjudication.spotCheck.findings.length === 0 ? "无" : adjudication.spotCheck.findings.join("；")}\n` +
    `- 歧义 key：${aliasResolution.map((item) => `${item.key}→${item.decision}${item.overridden ? "（推翻机器提议）" : ""}`).join("，") || "无"}\n` +
    `- 剔除：${rejectedRefs.size} 条（其中级联剔除 ${cascaded} 条边）\n`,
);

recordStep({
  step: STEP,
  title: TITLE,
  command: COMMAND,
  inputs: ["knowledge/extract/normalized.jsonl", "knowledge/extract/aliases.json", "knowledge/review/adjudication.json"],
  outputs: [ACCEPTED_FILE, REVIEW_QUEUE_FILE],
  counts: {
    nodes: acceptedNodes.length,
    edges: acceptedEdges.length,
    reviewedNodes,
    reviewedEdges,
    reviewedRows: reviewedNodes + reviewedEdges,
    rejectedRows: rejectedRefs.size,
    cascadedEdges: cascaded,
    aliasKeysResolved: aliasResolution.length,
    overrides: overrides.length,
    nodesByKind,
    edgesByType,
  },
  notes: [
    `${reviewer} 于 ${reviewedAt} 完成人工裁定：全审 ${adjudication.fullReview.verdict}，抽审 ${adjudication.spotCheck.verdict}（${adjudication.spotCheck.reviewed}/${spotSize}）。`,
    `annotatedBy 分布：${describeCounts(annotatedBy)} —— 其中 ${reviewedNodes + reviewedEdges} 行由 llm-draft 升为 ${REVIEWED}（06 是唯一通道，设计 §6 L251）。`,
    `歧义 key 裁定：${aliasResolution.map((item) => `${item.key}→${item.decision}`).join("，") || "无"}。`,
    `节点分布：${describeCounts(nodesByKind, KIND_ORDER)}。`,
    `边分布：${describeCounts(edgesByType)}。`,
  ],
  startedAt,
});

logLine(`${TITLE}：${acceptedNodes.length} 节点 / ${acceptedEdges.length} 边 → ${relPath(ACCEPTED_FILE)}`);
logLine(`  审核人 ${reviewer}（${reviewedAt}）；升为 ${REVIEWED} 的条目 ${reviewedNodes + reviewedEdges} 条`);
for (const item of aliasResolution) logLine(`  歧义 ${item.key} → ${item.decision}${item.overridden ? "（人工推翻机器提议）" : ""}`);

/** 队列文档是 Markdown，log.mjs 只管 JSON/JSONL，所以这里自己落盘。 */
function writeText(file, text) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, text, "utf8");
}
