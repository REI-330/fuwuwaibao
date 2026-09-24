#!/usr/bin/env node
/**
 * 步骤 09：完整性校验（设计文档 §3.3 三条硬约束 + 页面契约的机器校验）
 *
 * 这一步是「构建流水线里的质检工位」，只做判定、不改数据：
 *   - 硬约束不通过 → 退出码 1，构建失败（设计文档 L150/L152 明确要求「缺失即构建失败」「环即失败」）；
 *   - 规模/风格类问题 → 记为 warn，不阻断，但要写进报告，供材料 ② 说明取舍。
 *
 * 校验项（level = hard 表示违反即失败）：
 *   1 每条边有 sourceRefs 且每个引用都是真实 chunkId                 hard  §3.3-1 / L150
 *   2 每个节点有 sourceRefs 且每个引用都是真实 chunkId               hard  §3.1 节点公共字段
 *   3 annotatedBy 合法（human / llm-reviewed / llm-draft）           hard  §3.3-2 / L151
 *   4 词表里 acyclic=true 的边类型（prerequisite / transitions_to）无环        hard  §3.3-3 / L152
 *   5 边端点类型与 §3.2 的 from→to 表一致，且端点节点真实存在         hard  §3.2 L131-140
 *   6 evidenced_by 的 to 是 `chunk:<真实 chunkId>`，且写进了 sourceRefs hard §3.2 L144
 *   7 节点 id / 边 id / edgeKey 唯一                                  hard
 *   8 Wiki 正文内链都能在图谱里找到对应边；图谱里该实体的可投影边都落到页面上 hard §4.3 L187
 *   9 Wiki front-matter 8 键齐全、review/reviewedBy 自洽、sources 都是真实 chunkId hard §4.1 L160-183
 *  10 x/y 若存在必须是有限数字且全量一致                              hard  §3.1 L110
 *  11 weight 若存在必须是 [0,1] 有限数，且只出现在 requires 上（= importance） hard
 *  12 别名冲突必须已在 06 的人工裁定里处理过                          hard  §4.1 L188
 *  13 规模落在 taxonomy.scaleTargets 区间内                           warn  §1 规模目标
 *
 * 注意 10/11 的窗口期：x/y 由第 10 步产出、weight 也由第 10 步按 WEIGHT_RULE 写入。
 * 在第 10 步之前跑本步，这两项记为 pending（不算失败，也不假装通过）；跑完 10 再跑一次即可全绿。
 */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { ACCEPTED_FILE, ADJUDICATION_FILE, CHUNKS_FILE, GRAPH_FILE, INTEGRITY_FILE, TAXONOMY_FILE, WIKI_DIR, WIKI_INDEX_FILE, relPath } from "./lib/paths.mjs";
import { fail, logLine, nowIso, readJson, readJsonl, recordStep, writeJson } from "./lib/log.mjs";
import { DEFAULT_CANVAS, EDGE_ORDER, KIND_ORDER, WEIGHT_RULE, chunkIdOf, describeCounts, isChunkTarget, loadAccepted } from "./lib/graph.mjs";

const STEP = "09";
const TITLE = "完整性校验";
const COMMAND = "node knowledge/pipeline/09-integrity-check.mjs";

/** §4.1 L160-183 front-matter 的 8 个键，缺一不可。 */
const FRONTMATTER_KEYS = ["entityId", "entityType", "title", "aliases", "version", "review", "reviewedBy", "sources"];

/** §4.2 L194-196 的 review 取值。 */
const REVIEW_VALUES = ["reviewed", "llm-draft", "runtime"];

const startedAt = nowIso();
const checks = [];

function round(value) {
  return Number.isInteger(value) ? value : Number(value.toFixed(3));
}

function check(id, title, level, run) {
  const record = { id, title, level, status: "pass", observed: "", detail: [], note: "" };
  try {
    const result = run() ?? {};
    record.status = result.status ?? "pass";
    record.observed = result.observed ?? "";
    record.detail = (result.detail ?? []).slice(0, 10);
    record.note = result.note ?? "";
    if (result.truncated) record.detail.push(`…… 另有 ${result.truncated} 条同类问题未列出`);
  } catch (error) {
    record.status = "fail";
    record.observed = error instanceof Error ? error.message : String(error);
  }
  checks.push(record);
  return record;
}

/** 把「不符合预期的东西」收敛成一行报告：前 N 条样本 + 总数。 */
function summarize(violations, label) {
  return {
    status: violations.length === 0 ? "pass" : "fail",
    observed: violations.length === 0 ? `0 处` : `${violations.length} 处${label}`,
    detail: violations.slice(0, 10).map((item) => (typeof item === "string" ? item : JSON.stringify(item))),
    truncated: Math.max(0, violations.length - 10),
  };
}

if (!existsSync(ACCEPTED_FILE)) fail(`缺少 ${relPath(ACCEPTED_FILE)}，请先跑 06-adjudicate.mjs`);
if (!existsSync(CHUNKS_FILE)) fail(`缺少 ${relPath(CHUNKS_FILE)}，请先跑 03-chunk-topics.mjs`);

const { nodes, edges } = loadAccepted();
const chunks = readJsonl(CHUNKS_FILE);
const chunkIds = new Set(chunks.map((chunk) => chunk.chunkId));

/**
 * 第 10 步的产物（graph.json）是 x/y 与 weight 的唯一落盘处 —— 设计文档 L110 要求坐标由第 10 步
 * 版本化产出，第 09 步只负责校验，不自己造坐标。所以这里按 id 把坐标与 weight 覆盖回内存里的
 * nodes/edges（accepted.jsonl 本身不带这两个字段）。graph.json 不存在时保持原样：09-10 / 09-11
 * 自然落到 pending（见 windowNote），既不失败也不假装通过。
 */
if (existsSync(GRAPH_FILE)) {
  const layout = readJson(GRAPH_FILE);
  const coordById = new Map((layout.nodes ?? []).map((node) => [node.id, node]));
  for (const node of nodes) {
    const coord = coordById.get(node.id);
    if (coord && coord.x !== undefined && coord.y !== undefined) {
      node.x = coord.x;
      node.y = coord.y;
    }
  }
  const weightById = new Map((layout.edges ?? []).map((edge) => [edge.id, edge.weight]));
  for (const edge of edges) {
    const weight = weightById.get(edge.id);
    if (weight !== undefined) edge.weight = weight;
  }
}

const taxonomy = existsSync(TAXONOMY_FILE) ? readJson(TAXONOMY_FILE) : null;
if (!taxonomy) fail(`缺少 ${relPath(TAXONOMY_FILE)}：它是节点 kind / 边类型 / 端点契约的唯一来源，不能省`);

const annotatedByValues = new Set(taxonomy.annotatedByValues);
const nodeKinds = new Set(taxonomy.nodeKinds.map((spec) => spec.id));
const edgeTypes = new Set(taxonomy.edgeTypes.map((spec) => spec.id));

/**
 * §3.2 L131-140 的 from→to 契约，在 taxonomy.edgeTypes 里已经逐条声明。
 * 这里直接读词表，而不是在脚本里再抄一份 —— 两处各写一份迟早会漂移。
 * `evidenced_by` 排除在外：它的 to 是 chunk 引用而不是节点，由第 6 项单独校验。
 */
const EDGE_SPEC_BY_ID = new Map(taxonomy.edgeTypes.filter((spec) => spec.id !== "evidenced_by").map((spec) => [spec.id, spec]));
/** 词表里 `acyclic: true` 的类型都要做环检查（本图谱是 prerequisite 与 transitions_to）。 */
const ACYCLIC_TYPES = taxonomy.edgeTypes.filter((spec) => spec.acyclic === true).map((spec) => spec.id);

const nodeById = new Map(nodes.map((node) => [node.id, node]));

// ---------- 1 / 2：sourceRefs 必须指向真实 chunkId（§3.3 L150） ----------

function missingSourceRefs(rows, label) {
  const violations = [];
  for (const row of rows) {
    const refs = row.sourceRefs;
    if (!Array.isArray(refs) || refs.length === 0) {
      violations.push(`${row.id}（${label}）没有 sourceRefs`);
      continue;
    }
    for (const ref of refs) {
      if (typeof ref !== "string" || ref.trim() === "") violations.push(`${row.id} 的 sourceRefs 含空值`);
      else if (!chunkIds.has(ref)) violations.push(`${row.id} 引用了不存在的 chunkId「${ref}」`);
    }
  }
  return violations;
}

check("09-01", "每条边都有 sourceRefs 且指向真实 chunkId", "hard", () => summarize(missingSourceRefs(edges, "边"), "（边）"));
check("09-02", "每个节点都有 sourceRefs 且指向真实 chunkId", "hard", () => summarize(missingSourceRefs(nodes, "节点"), "（节点）"));

// ---------- 3：annotatedBy 必须合法（§3.3 L151） ----------

check("09-03", "annotatedBy 全部合法（human / llm-reviewed / llm-draft）", "hard", () => {
  const violations = [];
  for (const row of [...nodes, ...edges]) {
    if (typeof row.annotatedBy !== "string") violations.push(`${row.id} 没有 annotatedBy`);
    else if (!annotatedByValues.has(row.annotatedBy)) violations.push(`${row.id} 的 annotatedBy=「${row.annotatedBy}」不在枚举内`);
    if (row.annotatedBy === "llm-reviewed" && !row.adjudication) {
      violations.push(`${row.id} 标了 llm-reviewed，却没有第 06 步的人工裁定记录`);
    }
  }
  return summarize(violations, "（标注不合法）");
});

// ---------- 4：无环类型（prerequisite 等）不许出现环（§3.3 L152） ----------

check("09-04", `${ACYCLIC_TYPES.join(" / ")} 子图无环`, "hard", () => {
  const violations = [];
  const notes = [];
  for (const type of ACYCLIC_TYPES) {
    const subset = edges.filter((edge) => edge.type === type);
    const adj = new Map();
    const indeg = new Map();
    for (const edge of subset) {
      if (!adj.has(edge.from)) adj.set(edge.from, []);
      adj.get(edge.from).push(edge.to);
      indeg.set(edge.to, (indeg.get(edge.to) ?? 0) + 1);
      if (!indeg.has(edge.from)) indeg.set(edge.from, indeg.get(edge.from) ?? 0);
    }
    const queue = [...indeg.entries()].filter(([, degree]) => degree === 0).map(([id]) => id);
    while (queue.length > 0) {
      const id = queue.shift();
      for (const next of adj.get(id) ?? []) {
        indeg.set(next, indeg.get(next) - 1);
        if (indeg.get(next) === 0) queue.push(next);
      }
    }
    const inCycle = [...indeg.entries()].filter(([, degree]) => degree > 0).map(([id]) => id);
    if (inCycle.length === 0) {
      notes.push(`${type}：${subset.length} 条边 / ${indeg.size} 个节点全部出拓扑序`);
      continue;
    }
    // 只为把环打印出来：顺着剩下的节点走，回到访问过的点即为环。
    const start = inCycle[0];
    const path = [];
    const onPath = new Set();
    let cursor = start;
    while (!onPath.has(cursor)) {
      onPath.add(cursor);
      path.push(cursor);
      cursor = (adj.get(cursor) ?? []).find((next) => inCycle.includes(next)) ?? start;
    }
    violations.push(`${type} 有环：${[...path.slice(path.indexOf(cursor)), cursor].join(" → ")}`);
    violations.push(...inCycle.filter((id) => id !== cursor).slice(0, 9).map((id) => `${type} 环上节点 ${id}`));
  }
  return { ...summarize(violations, "（成环）"), note: `${notes.join("；")}。方向即「先…再…」的学习顺序。` };
});

// ---------- 5：边端点与类型匹配（§3.2 L131-140） ----------

check("09-05", "边端点类型匹配且节点真实存在", "hard", () => {
  const violations = [];
  const seenTypes = new Set();
  for (const edge of edges) {
    if (!edgeTypes.has(edge.type)) {
      violations.push(`${edge.id} 的 type「${edge.type}」不在 taxonomy.edgeTypes 内`);
      continue;
    }
    seenTypes.add(edge.type);
    if (edge.type === "evidenced_by") continue; // to 是 chunk 引用，第 6 项单独校验
    const spec = EDGE_SPEC_BY_ID.get(edge.type);
    if (!spec) {
      violations.push(`${edge.id} 的 type「${edge.type}」没有登记 from→to 约束`);
      continue;
    }
    const from = nodeById.get(edge.from);
    const to = nodeById.get(edge.to);
    if (!from) violations.push(`${edge.id} 的 from「${edge.from}」不是节点`);
    else if (!spec.from.includes("*") && !spec.from.includes(from.kind)) violations.push(`${edge.id}（${edge.type}）的 from 是 ${from.kind}，应为 ${spec.from.join("/")}`);
    if (!to) violations.push(`${edge.id} 的 to「${edge.to}」不是节点`);
    else if (!spec.to.includes("*") && !spec.to.includes(to.kind)) violations.push(`${edge.id}（${edge.type}）的 to 是 ${to.kind}，应为 ${spec.to.join("/")}`);
  }
  for (const node of nodes) if (!nodeKinds.has(node.kind)) violations.push(`${node.id} 的 kind「${node.kind}」不在 taxonomy.nodeKinds 内`);
  const unused = [...edgeTypes].filter((type) => !seenTypes.has(type));
  return {
    ...summarize(violations, "（端点/类型不符）"),
    note: unused.length === 0 ? `taxonomy 里 ${edgeTypes.size} 种边类型全部被用到（端点约束直接读词表，不另抄一份）。` : `本轮没用到的边类型：${unused.join("、")}`,
  };
});

// ---------- 6：evidenced_by 的 to 是 chunk 引用（§3.2 L144） ----------

check("09-06", "evidenced_by 的 to 是 chunk:<真实 chunkId> 且写进 sourceRefs", "hard", () => {
  const violations = [];
  const targets = new Set();
  for (const edge of edges.filter((item) => item.type === "evidenced_by")) {
    if (!isChunkTarget(edge.to)) {
      violations.push(`${edge.id} 的 to「${edge.to}」不是 chunk: 引用`);
      continue;
    }
    const chunkId = chunkIdOf(edge.to);
    targets.add(chunkId);
    if (!chunkIds.has(chunkId)) violations.push(`${edge.id} 指向不存在的 chunkId「${chunkId}」`);
    if (!Array.isArray(edge.sourceRefs) || !edge.sourceRefs.includes(chunkId)) {
      violations.push(`${edge.id} 的 to「${edge.to}」没有写进自己的 sourceRefs —— 依据链断了`);
    }
    const owner = nodeById.get(edge.from);
    if (!owner) violations.push(`${edge.id} 的 from「${edge.from}」不是节点`);
  }
  return {
    ...summarize(violations, "（依据链问题）"),
    note: `覆盖 ${targets.size} 段原文；这类目标不是图节点，没有 x/y，前端必须过滤（§3.2 L144-146）。`,
  };
});

// ---------- 7：主键唯一 ----------

check("09-07", "节点 id / 边 id / edgeKey 唯一", "hard", () => {
  const violations = [];
  const dup = (rows, keyOf, label) => {
    const seen = new Map();
    for (const row of rows) {
      const key = keyOf(row);
      if (key === undefined || key === null || key === "") {
        violations.push(`${label} ${row.id ?? "(无 id)"} 缺少主键`);
        continue;
      }
      if (seen.has(key)) violations.push(`${label}主键重复：${key}（${seen.get(key)} 与 ${row.id}）`);
      else seen.set(key, row.id);
    }
    return seen.size;
  };
  const nodeKeys = dup(nodes, (node) => node.id, "节点");
  const edgeIds = dup(edges, (edge) => edge.id, "边");
  const edgeKeys = dup(edges, (edge) => edge.edgeKey, "边");
  return { ...summarize(violations, "（主键重复）"), note: `${nodeKeys} 个节点 id / ${edgeIds} 个边 id / ${edgeKeys} 个 edgeKey。` };
});

// ---------- 8 / 9：Wiki 页（§4.1 L160-183、§4.3 L187） ----------

if (!existsSync(WIKI_INDEX_FILE)) {
  fail(`缺少 ${relPath(WIKI_INDEX_FILE)}，请先跑 07-compile-wiki.mjs 与 08-review-wiki.mjs`);
}
const wikiIndex = readJson(WIKI_INDEX_FILE);
const wikiPages = wikiIndex.pages ?? [];
const pageIds = new Set(wikiPages.map((page) => page.entityId));

/**
 * 可投影成内链的边类型 = 除 `evidenced_by` 之外的全部。
 * `evidenced_by` 是唯一「to 不是节点」的关系（§3.2 L144），所以它只出现在「引用来源」小节，不算内链。
 */
const PROJECTED_EDGE_TYPES = EDGE_ORDER.filter((type) => type !== "evidenced_by");
const edgeByKey = new Map(edges.map((edge) => [edge.edgeKey, edge]));

check("09-08", "Wiki 内链与图谱边双向对应", "hard", () => {
  const violations = [];
  let links = 0;
  for (const page of wikiPages) {
    const own = new Set(page.edgeKeys ?? []);
    for (const key of own) {
      const edge = edgeByKey.get(key);
      if (!edge) {
        violations.push(`${page.entityId} 的 edgeKeys 里有图谱中不存在的边「${key}」`);
        continue;
      }
      if (edge.from !== page.entityId && edge.to !== page.entityId) {
        violations.push(`${page.entityId} 的 edgeKeys 引用了跟它无关的边「${key}」`);
      }
    }
    for (const target of page.internalLinks ?? []) {
      links += 1;
      if (!pageIds.has(target) && !nodeById.has(target)) {
        violations.push(`${page.entityId} 内链指向不存在的实体「${target}」`);
        continue;
      }
      const backed = edges.some(
        (edge) => PROJECTED_EDGE_TYPES.includes(edge.type) && ((edge.from === page.entityId && edge.to === target) || (edge.to === page.entityId && edge.from === target)),
      );
      if (!backed) violations.push(`${page.entityId} 的正文内链「${target}」在图谱里找不到对应边`);
    }
    for (const edge of edges) {
      if (!PROJECTED_EDGE_TYPES.includes(edge.type)) continue;
      if (edge.from !== page.entityId && edge.to !== page.entityId) continue;
      if (!own.has(edge.edgeKey)) violations.push(`${page.entityId} 漏投影了边「${edge.edgeKey}」`);
    }
  }
  return { ...summarize(violations, "（内链/漏投影）"), note: `${wikiPages.length} 页，正文内链 ${links} 条；「图谱里的边都应能在页面上体现」（§4.3 L187）。` };
});

check("09-09", "Wiki front-matter 8 键齐全且 review/reviewedBy/sources 自洽", "hard", () => {
  const violations = [];
  for (const page of wikiPages) {
    if (!REVIEW_VALUES.includes(page.review)) violations.push(`${page.entityId} 的 review=「${page.review}」不在 ${REVIEW_VALUES.join(" / ")} 内`);
    if (page.review === "reviewed" && !page.reviewedBy) violations.push(`${page.entityId} 标了 reviewed 却没有 reviewedBy`);
    if (page.review !== "reviewed" && page.reviewedBy) violations.push(`${page.entityId} 的 review=${page.review} 却带了 reviewedBy`);
    for (const ref of page.sources ?? []) if (!chunkIds.has(ref)) violations.push(`${page.entityId} 的 sources 引用了不存在的 chunkId「${ref}」`);
    if ((page.sources ?? []).length === 0) violations.push(`${page.entityId} 的 sources 为空`);

    const file = resolve(WIKI_DIR, page.path.slice("wiki/".length));
    if (!existsSync(file)) {
      violations.push(`${page.entityId} 的页面文件 ${page.path} 不存在`);
      continue;
    }
    const raw = readFileSync(file, "utf8");
    const end = raw.indexOf("\n---", 4);
    if (!raw.startsWith("---") || end < 0) {
      violations.push(`${page.entityId} 的 ${page.path} 没有 front-matter`);
      continue;
    }
    const keys = raw
      .slice(4, end)
      .split("\n")
      .map((line) => line.split(":")[0].trim())
      .filter(Boolean);
    const missing = FRONTMATTER_KEYS.filter((key) => !keys.includes(key));
    if (missing.length > 0) violations.push(`${page.entityId} 的 front-matter 缺键：${missing.join("、")}`);
    const extra = keys.filter((key) => !FRONTMATTER_KEYS.includes(key));
    if (extra.length > 0) violations.push(`${page.entityId} 的 front-matter 多出键：${extra.join("、")}`);
    const body = raw.slice(4, end);
    if (!body.includes(`review: ${page.review}`)) violations.push(`${page.entityId} 的 front-matter review 与 index.json 不一致`);
    if (!body.includes(`reviewedBy: ${page.reviewedBy ?? "null"}`)) violations.push(`${page.entityId} 的 front-matter reviewedBy 与 index.json 不一致`);
  }
  const reviewed = wikiPages.filter((page) => page.review === "reviewed").length;
  return {
    ...summarize(violations, "（页面契约问题）"),
    note: `${reviewed} 页 reviewed / ${wikiPages.length - reviewed} 页 llm-draft；未审核内容按 §5 降权（×0.6）。`,
  };
});

// ---------- 10 / 11：第 10 步的产物（x/y、weight） ----------

function windowNote() {
  return "x/y 与 weight 由第 10 步产出；第 10 步之前本项记 pending，跑完 10 再跑一次即可全绿。";
}

check("09-10", "x/y 是有限数字且全量一致", "hard", () => {
  const withXY = nodes.filter((node) => node.x !== undefined || node.y !== undefined);
  if (withXY.length === 0) return { status: "pending", observed: `0/${nodes.length} 个节点有坐标`, note: windowNote() };
  const violations = [];
  for (const node of nodes) {
    const hasX = node.x !== undefined;
    const hasY = node.y !== undefined;
    if (hasX !== hasY) {
      violations.push(`${node.id} 只有 x/y 中的一个`);
      continue;
    }
    if (!hasX) {
      violations.push(`${node.id} 缺 x/y（坐标必须全量，否则前端要自己补位置）`);
      continue;
    }
    for (const [axis, value] of [["x", node.x], ["y", node.y]]) {
      if (typeof value !== "number" || !Number.isFinite(value)) violations.push(`${node.id} 的 ${axis}=${JSON.stringify(value)} 不是有限数字`);
      else if (value < 0) violations.push(`${node.id} 的 ${axis}=${value} 为负`);
    }
    if (Number.isFinite(node.x) && node.x > DEFAULT_CANVAS.width) violations.push(`${node.id} 的 x=${node.x} 超出画布宽度 ${DEFAULT_CANVAS.width}`);
    if (Number.isFinite(node.y) && node.y > DEFAULT_CANVAS.height) violations.push(`${node.id} 的 y=${node.y} 超出画布高度 ${DEFAULT_CANVAS.height}`);
  }
  const xs = nodes.map((node) => node.x);
  const ys = nodes.map((node) => node.y);
  return {
    ...summarize(violations, "（坐标问题）"),
    note: `${nodes.length} 个节点都有坐标；x ∈ [${round(Math.min(...xs))}, ${round(Math.max(...xs))}]，y ∈ [${round(Math.min(...ys))}, ${round(Math.max(...ys))}]，画布 ${DEFAULT_CANVAS.width}×${DEFAULT_CANVAS.height}。`,
  };
});

check("09-11", "weight 只出现在 requires 上且等于 importance", "hard", () => {
  const withWeight = edges.filter((edge) => edge.weight !== undefined);
  if (withWeight.length === 0) return { status: "pending", observed: `0/${edges.length} 条边有 weight`, note: `${WEIGHT_RULE} ${windowNote()}` };
  const violations = [];
  for (const edge of withWeight) {
    if (typeof edge.weight !== "number" || !Number.isFinite(edge.weight)) {
      violations.push(`${edge.id} 的 weight 不是有限数字`);
      continue;
    }
    if (edge.weight < 0 || edge.weight > 1) violations.push(`${edge.id} 的 weight=${edge.weight} 超出 [0,1]`);
    if (edge.type !== "requires") {
      violations.push(`${edge.id}（${edge.type}）不该有 weight —— 该类型没有量化来源`);
      continue;
    }
    if (edge.weight !== edge.importance) violations.push(`${edge.id} 的 weight=${edge.weight} 与 importance=${edge.importance} 不一致`);
  }
  const requires = edges.filter((edge) => edge.type === "requires");
  const covered = requires.filter((edge) => edge.weight !== undefined).length;
  return {
    ...summarize(violations, "（weight 问题）"),
    note: `${WEIGHT_RULE} 覆盖率 ${covered}/${requires.length} 条 requires，占全部 ${edges.length} 条边的 ${round((covered / edges.length) * 100)}%。`,
  };
});

// ---------- 12：别名冲突必须已人工裁定（§4.1 L188） ----------

check("09-12", "别名冲突都在 06 的人工裁定里处理过", "hard", () => {
  const owners = new Map();
  for (const node of nodes) {
    for (const key of node.aliasKeys ?? []) {
      if (!owners.has(key)) owners.set(key, []);
      owners.get(key).push(node.id);
    }
  }
  const collisions = [...owners.entries()].filter(([, ids]) => ids.length > 1);
  if (collisions.length === 0) return { status: "pass", observed: `0 处冲突（${owners.size} 个别名 key）` };

  const adjudication = existsSync(ADJUDICATION_FILE) ? readJson(ADJUDICATION_FILE) : null;
  const decisions = new Map((adjudication?.aliasDecisions ?? []).map((item) => [item.key, item]));
  const violations = [];
  const handled = [];
  for (const [key, ids] of collisions) {
    const decision = decisions.get(key);
    if (!decision) {
      violations.push(`「${key}」同时属于 ${ids.join(" 与 ")}，但 adjudication.json 里没有对应裁定`);
      continue;
    }
    handled.push(`${key} → ${decision.decision}（${ids.join(" / ")}）`);
  }
  return {
    ...summarize(violations, "（未裁定冲突）"),
    detail: handled.slice(0, 10),
    note: "保留同名实体是有意的：查询时靠 kind 过滤区分（见 adjudication.json 的 aliasDecisions）。",
  };
});

// ---------- 13：规模目标（warn） ----------

check("09-13", "规模落在 taxonomy.scaleTargets 区间内", "warn", () => {
  const targets = taxonomy?.scaleTargets ?? {};
  const byKind = Object.fromEntries(KIND_ORDER.map((kind) => [kind, nodes.filter((node) => node.kind === kind).length]));
  const inRange = (value, range) => (Array.isArray(range) ? value >= range[0] && value <= range[1] : value === range);
  const items = [
    { label: "职业节点", value: byKind.occupation, target: targets.occupations },
    { label: "技能节点", value: byKind.skill, target: targets.skillNodes },
    { label: "Wiki 页", value: wikiPages.length, target: targets.wikiPages },
    { label: "来源", value: new Set(chunks.map((chunk) => chunk.sourceId)).size, target: targets.sources },
  ];
  const violations = items.filter((item) => item.target !== undefined && !inRange(item.value, item.target));
  return {
    ...summarize(violations.map((item) => `${item.label}=${item.value}，目标 ${JSON.stringify(item.target)}`), "（规模偏离）"),
    note: items.map((item) => `${item.label} ${item.value}（目标 ${JSON.stringify(item.target)}）`).join("；"),
    status: violations.length === 0 ? "pass" : "warn",
  };
});

// ---------- 汇总 ----------

const hard = checks.filter((item) => item.level === "hard");
const hardFailed = hard.filter((item) => item.status === "fail");
const pending = checks.filter((item) => item.status === "pending");
const warned = checks.filter((item) => item.status === "warn" || (item.level === "warn" && item.status === "fail"));
const annotated = {};
for (const row of [...nodes, ...edges]) annotated[row.annotatedBy] = (annotated[row.annotatedBy] ?? 0) + 1;
const byKind = Object.fromEntries(KIND_ORDER.filter((kind) => nodes.some((node) => node.kind === kind)).map((kind) => [kind, nodes.filter((node) => node.kind === kind).length]));
const byType = Object.fromEntries(EDGE_ORDER.filter((type) => edges.some((edge) => edge.type === type)).map((type) => [type, edges.filter((edge) => edge.type === type).length]));

const report = {
  schema: "career-graph-integrity/v1",
  step: STEP,
  generatedAt: startedAt,
  note: "第 09 步产物：设计文档 §3.3 三条硬约束 + 页面契约的机器校验结果。硬约束失败即构建失败（退出码 1）。",
  inputs: [ACCEPTED_FILE, CHUNKS_FILE, WIKI_INDEX_FILE, TAXONOMY_FILE].map(relPath),
  counts: {
    nodes: nodes.length,
    edges: edges.length,
    chunks: chunks.length,
    wikiPages: wikiPages.length,
    wikiReviewed: wikiPages.filter((page) => page.review === "reviewed").length,
    sources: new Set(chunks.map((chunk) => chunk.sourceId)).size,
    byKind,
    byType,
    annotatedBy: annotated,
  },
  summary: {
    checks: checks.length,
    hardChecks: hard.length,
    hardFailed: hardFailed.length,
    pending: pending.length,
    warned: warned.length,
    verdict: hardFailed.length === 0 ? "pass" : "fail",
  },
  checks,
};

writeJson(INTEGRITY_FILE, report);

logLine(`${TITLE}：${relPath(INTEGRITY_FILE)}`);
logLine(`  ${checks.length} 项校验：硬约束 ${hard.length - hardFailed.length}/${hard.length} 通过，pending ${pending.length}，warn ${warned.length}`);
for (const item of checks) {
  if (item.status === "fail" || item.status === "warn" || item.status === "pending") {
    logLine(`  ${item.status === "fail" ? "FAIL" : item.status === "warn" ? "WARN" : "PEND"}  ${item.title} —— ${item.observed}`);
  }
}
logLine(
  `  规模：${nodes.length} 节点（${describeCounts(byKind, KIND_ORDER)}）；${edges.length} 条边（${describeCounts(byType, EDGE_ORDER)}）`,
);
logLine(`  标注：${describeCounts(annotated, ["human", "llm-reviewed", "llm-draft"].filter((key) => annotated[key] !== undefined))}`);

recordStep({
  step: STEP,
  title: TITLE,
  command: COMMAND,
  inputs: report.inputs,
  outputs: [INTEGRITY_FILE],
  counts: { nodes: nodes.length, edges: edges.length, checks: checks.length, hardFailed: hardFailed.length, pending: pending.length, warned: warned.length },
  notes: [
    hardFailed.length === 0 ? `硬约束全部通过（${hard.length} 项）。` : `硬约束失败 ${hardFailed.length} 项：${hardFailed.map((item) => item.title).join("；")}`,
    pending.length > 0 ? `${pending.map((item) => item.title).join("；")} 待第 10 步产物，跑完 10 需再跑一次本步。` : "无 pending 项。",
    warned.length > 0 ? `非阻断提醒：${warned.map((item) => `${item.title}（${item.observed}）`).join("；")}` : "无非阻断提醒。",
    `标注分布：${describeCounts(annotated, ["human", "llm-reviewed", "llm-draft"].filter((key) => annotated[key] !== undefined))}`,
  ],
  startedAt,
});

if (hardFailed.length > 0) {
  fail(`完整性校验未通过（${hardFailed.length} 项硬约束失败）：`);
  for (const item of hardFailed) {
    process.stderr.write(`  - ${item.title}：${item.observed}\n`);
    for (const line of item.detail) process.stderr.write(`      · ${line}\n`);
  }
  process.exit(1);
}
