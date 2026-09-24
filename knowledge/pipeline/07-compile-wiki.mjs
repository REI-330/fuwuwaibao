#!/usr/bin/env node
/**
 * 步骤 07：把 accepted.jsonl 编译成 Wiki 草稿（knowledge/wiki/*.md + index.json）
 *
 * 这是「离线 LLM 编译 + 人工审核」里的前半段（设计文档 §6 L242、§10 决定一 A 方案）。
 * 它**不产生任何新知识**：页面的每一行都必须能落回 L1 已有的边或 L0 已有的原文。
 * 这是 §4.3 的铁律，也是第 08 步能逐页驳回、第 09 步能反向校验的前提。
 *
 * 三条自我约束：
 *   1) 编页范围只取 occupation / skill / knowledge —— 4 + 25 + 9 = 38 页，落在
 *      taxonomy.scaleTargets.wikiPages [30,40] 内。task / tool / trend / credential / domain
 *      只作为「被投影到的对象」出现在别人页面上，不单独成页。
 *   2) 正文里出现的每个 [[entityId]] 都必须由一条真实存在的边推出（08/09 反向校验）。
 *   3) 需要 L0 原文支撑、而语料里没有对应段落的章节（如「常见误区」）明确留空并写清原因，
 *      不拿模型的想象力填空。
 *
 * 产出：
 *   knowledge/wiki/<kind>/<ref>.md   每实体一页，front-matter 按 §4.1 L160-183 的 8 键
 *   knowledge/wiki/index.json        机器可读页目录（front-matter + 内链 + 投影的边），供 08/09/11 复用
 */
import { existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

import { CHUNKS_FILE, SOURCES_FILE, WIKI_DIR, WIKI_INDEX_FILE, relPath } from "./lib/paths.mjs";
import { fail, logLine, nowIso, readJson, readJsonl, recordStep, writeJson } from "./lib/log.mjs";
import {
  KIND_ORDER,
  describeCounts,
  directedEdges,
  isChunkTarget,
  loadAccepted,
  safeFileSegment,
  shortRef,
} from "./lib/graph.mjs";

const STEP = "07";
const TITLE = "编译 Wiki 草稿";
const COMMAND = "node knowledge/pipeline/07-compile-wiki.mjs";
const SCHEMA = "career-graph-wiki-draft/v1";
const DRAFT = "llm-draft";

/** 编页范围。页数 = 4 + 25 + 9 = 38，正好压在 scaleTargets.wikiPages [30,40] 里。 */
const PAGE_KINDS = ["occupation", "skill", "knowledge"];

/**
 * 关系文案与前端 `lib/client/career-graph.ts` 的 RELATION_LABELS 同源。
 * Wiki 正文、图谱、MCP 返回值必须共用一套中文，否则「wiki 页」和「图上的这些节点」
 * 又会变成两套说法（§9 的目标就是让它们看起来是同一份知识）。
 */
const RELATION_LABELS = {
  requires: "需要技能",
  prerequisite: "前置技能",
  belongs_to: "属于",
  trains: "训练",
  uses: "使用工具",
  learning_unit: "学习单元",
  transitions_to: "转型路径",
  emerging_in: "涌现于",
  evidenced_by: "证据来源",
};

const startedAt = nowIso();
const { nodes, edges } = loadAccepted();

if (!existsSync(CHUNKS_FILE)) {
  fail(`缺少 ${relPath(CHUNKS_FILE)}，Wiki 的「引用来源」段落没有原文可查，先跑 03-chunk-topics.mjs`);
  process.exit(1);
}
if (!existsSync(SOURCES_FILE)) {
  fail(`缺少 ${relPath(SOURCES_FILE)}，先跑 01-register-sources.mjs`);
  process.exit(1);
}

const chunks = readJsonl(CHUNKS_FILE);
const chunkById = new Map(chunks.map((chunk) => [chunk.chunkId, chunk]));
const sourceDoc = readJson(SOURCES_FILE);
const sourceById = new Map((sourceDoc.sources ?? []).map((source) => [source.sourceId, source]));

const nodeById = new Map(nodes.map((node) => [node.id, node]));

/**
 * `evidenced_by` 的 to 是 `chunk:<chunkId>`，不是节点（§3.2 L144）。
 * 它不参与实体审查、也不贡献正文内链，但**贡献引用来源** —— 页面的「可点开原文」正是靠它。
 */
const authoredEdges = edges.filter((edge) => !isChunkTarget(edge.to));
const evidenceEdges = edges.filter((edge) => isChunkTarget(edge.to));
const { out: edgesOut, in: edgesIn } = directedEdges(authoredEdges);

const version = startedAt.slice(0, 10);
const pageNodes = nodes
  .filter((node) => PAGE_KINDS.includes(node.kind))
  .sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind) || a.id.localeCompare(b.id));

// ---------- 投影小工具（只做格式化，不做判断） ----------

const link = (id) => `[[${id}]]`;
const titleOf = (id) => nodeById.get(id)?.label ?? shortRef(id);
const linkWithTitle = (id) => `${link(id)} ${titleOf(id)}`;
const num = (value) => (typeof value === "number" ? String(Math.round(value * 1000) / 1000) : "—");
const listOf = (values) => (Array.isArray(values) ? values.map((value) => String(value)) : []);

/** 每条断言后面都挂上它依据的 chunkId —— 引用不是装饰，是「沿 evidenced_by 回到 L0」的入口。 */
function refSuffix(edge) {
  const refs = listOf(edge.sourceRefs);
  return refs.length > 0 ? `（依据 ${refs.join("、")}）` : "（缺 sourceRefs，第 09 步会因此失败）";
}

/** 按 importance / targetLevel 降序，再按 id —— 排序必须确定，否则同一份输入每次生成的页面都不一样。 */
function byImportanceDesc(a, b) {
  const ia = typeof a.importance === "number" ? a.importance : -1;
  const ib = typeof b.importance === "number" ? b.importance : -1;
  if (ia !== ib) return ib - ia;
  const la = typeof a.targetLevel === "number" ? a.targetLevel : -1;
  const lb = typeof b.targetLevel === "number" ? b.targetLevel : -1;
  if (la !== lb) return lb - la;
  return a.to.localeCompare(b.to);
}

const byTo = (a, b) => a.to.localeCompare(b.to);
const byFrom = (a, b) => a.from.localeCompare(b.from);
const byPosition = (a, b) => (a.position ?? 0) - (b.position ?? 0) || a.from.localeCompare(b.from);

/** 一行的定义：抽出节点自带的 description（原文侧文字），不在这里编新句子。 */
function collapse(text) {
  return String(text ?? "").replace(/\s+/g, " ").trim();
}

/** 需要 L0 原文、而语料里没有对应段落的章节：留空 + 说明原因，绝不编。 */
function gapSection(title, reason) {
  return { title, lines: [`（本节留空：${reason}。按设计文档 §4.3 铁律，Wiki 页不得给出 L0 支持不了的结论。）`] };
}

function emptyNote(sentence) {
  return [`（图谱中暂无${sentence}）`];
}

// ---------- 分类型投影 ----------

function occupationSections(node) {
  const out = edgesOut.get(node.id) ?? [];
  const sections = [];

  const requires = out.filter((edge) => edge.type === "requires").sort(byImportanceDesc);
  sections.push({
    title: "需要的能力",
    lines: requires.length
      ? requires.map(
          (edge) =>
            `- ${linkWithTitle(edge.to)} —— 目标等级 ${num(edge.targetLevel)}/5、重要度 ${num(edge.importance)}${refSuffix(edge)}`,
        )
      : emptyNote(`指向「${node.label}」的 requires 边`),
  });

  const units = out.filter((edge) => edge.type === "learning_unit").sort(byPosition);
  sections.push({
    title: "学习单元",
    lines: units.length
      ? units.map((edge) => `- 第 ${num(edge.position)} 单元：${linkWithTitle(edge.to)}${refSuffix(edge)}`)
      : emptyNote(`从「${node.label}」出发的 learning_unit 边`),
  });

  const uses = out.filter((edge) => edge.type === "uses").sort(byTo);
  sections.push({
    title: "常用工具",
    lines: uses.length
      ? uses.map((edge) => `- ${linkWithTitle(edge.to)}${refSuffix(edge)}`)
      : emptyNote(`从「${node.label}」出发的 uses 边`),
  });

  const transitions = out.filter((edge) => edge.type === "transitions_to").sort(byTo);
  sections.push({
    title: "相邻职业",
    lines: transitions.length
      ? transitions.map((edge) => {
          const delta = listOf(edge.deltaSkills);
          const linked = delta.filter((id) => nodeById.has(id)).map(link);
          const tail = linked.length > 0 ? `，需补 ${linked.join(" ")}` : "";
          const horizon = typeof edge.horizon === "number" ? `${edge.horizon} 年` : "周期未标注";
          return `- ${linkWithTitle(edge.to)} —— ${horizon}${tail}${refSuffix(edge)}`;
        })
      : emptyNote(`从「${node.label}」出发的 transitions_to 边`),
  });

  sections.push(
    gapSection("常见误区", `L0 语料里没有可直接引用的「误区 / 反面经验」段落，不能由模型代写`),
  );
  return sections;
}

function skillSections(node) {
  const out = edgesOut.get(node.id) ?? [];
  const inbound = edgesIn.get(node.id) ?? [];
  const sections = [];

  const domains = out.filter((edge) => edge.type === "belongs_to").sort(byTo);
  sections.push({
    title: "能力域",
    lines: domains.length
      ? domains.map((edge) => `- ${linkWithTitle(edge.to)}${refSuffix(edge)}`)
      : emptyNote(`从「${node.label}」出发的 belongs_to 边`),
  });

  const prereqIn = inbound.filter((edge) => edge.type === "prerequisite").sort(byFrom);
  const prereqOut = out.filter((edge) => edge.type === "prerequisite").sort(byTo);
  sections.push({
    title: "先修提示",
    lines: [
      prereqIn.length
        ? `学习本技能前先掌握：${prereqIn.map((edge) => linkWithTitle(edge.from)).join("、")}${refSuffix(prereqIn[0])}`
        : "图谱中没有指向本技能的 prerequisite 边，可直接上手。",
      prereqOut.length
        ? `掌握本技能后可继续：${prereqOut.map((edge) => linkWithTitle(edge.to)).join("、")}${refSuffix(prereqOut[0])}`
        : "图谱中没有从本技能出发的 prerequisite 边。",
    ],
  });

  const requiredBy = inbound.filter((edge) => edge.type === "requires").sort(byFrom);
  sections.push({
    title: "被以下职业要求",
    lines: requiredBy.length
      ? requiredBy.map(
          (edge) => `- ${linkWithTitle(edge.from)} —— 目标等级 ${num(edge.targetLevel)}/5、重要度 ${num(edge.importance)}${refSuffix(edge)}`,
        )
      : emptyNote(`指向「${node.label}」的 requires 边`),
  });

  const trainedBy = inbound.filter((edge) => edge.type === "trains").sort(byFrom);
  sections.push({
    title: "相关训练任务",
    lines: trainedBy.length
      ? trainedBy.map((edge) => `- ${linkWithTitle(edge.from)}${refSuffix(edge)}`)
      : emptyNote(`指向「${node.label}」的 trains 边`),
  });

  const emerging = inbound.filter((edge) => edge.type === "emerging_in").sort(byFrom);
  sections.push({
    title: "趋势信号",
    lines: emerging.length
      ? emerging.map((edge) => {
          const year = edge.year === undefined ? "" : `，${edge.year} 年`;
          const direction = edge.direction ? `，方向${edge.direction}` : "";
          return `- ${linkWithTitle(edge.from)}${year}${direction}${refSuffix(edge)}`;
        })
      : emptyNote(`指向「${node.label}」的 emerging_in 边`),
  });

  sections.push(gapSection("常见误区", `L0 语料里没有可直接引用的「误区 / 反面经验」段落，不能由模型代写`));
  return sections;
}

function knowledgeSections(node) {
  const inbound = edgesIn.get(node.id) ?? [];
  const sections = [];

  const units = inbound.filter((edge) => edge.type === "learning_unit").sort(byPosition);
  sections.push({
    title: "所属职业",
    lines: units.length
      ? units.map((edge) => `- ${linkWithTitle(edge.from)} —— 第 ${num(edge.position)} 单元${refSuffix(edge)}`)
      : emptyNote(`指向「${node.label}」的 learning_unit 边`),
  });

  sections.push(gapSection("常见误区", `L0 语料里没有可直接引用的「误区 / 反面经验」段落，不能由模型代写`));
  return sections;
}

const SECTION_BUILDERS = {
  occupation: occupationSections,
  skill: skillSections,
  knowledge: knowledgeSections,
};

/** 从节点与它身上所有作者边的 sourceRefs 求并集 —— 页面投影了哪些边，那些边的证据就是这页的来源。 */
function citationIds(node) {
  const refs = new Set(listOf(node.sourceRefs));
  for (const edge of [...(edgesOut.get(node.id) ?? []), ...(edgesIn.get(node.id) ?? [])]) {
    for (const ref of listOf(edge.sourceRefs)) refs.add(ref);
  }
  for (const edge of evidenceEdges) {
    if (edge.from === node.id) for (const ref of listOf(edge.sourceRefs)) refs.add(ref);
  }
  return [...refs].sort();
}

function citationLine(ref) {
  const chunk = chunkById.get(ref);
  if (!chunk) return `- \`${ref}\`（chunks.jsonl 中找不到该 chunkId，第 09 步会因此失败）`;
  const source = sourceById.get(chunk.sourceId);
  const title = source?.title ?? chunk.sourceId;
  const heading = chunk.heading ? ` · ${chunk.heading}` : "";
  return `- \`${ref}\` 《${title}》${heading}（可点开原文）`;
}

/** 页面投影到的边：只有真实存在、且真的会被写进正文的边才登记，08/09 用它做双向校验。 */
const PROJECTED_EDGE_TYPES = new Set([
  "requires",
  "learning_unit",
  "uses",
  "transitions_to",
  "belongs_to",
  "prerequisite",
  "trains",
  "emerging_in",
]);

function projectedEdgeKeys(nodeId) {
  const keys = new Set();
  for (const edge of [...(edgesOut.get(nodeId) ?? []), ...(edgesIn.get(nodeId) ?? [])]) {
    if (PROJECTED_EDGE_TYPES.has(edge.type)) keys.add(edge.edgeKey ?? `${edge.type}|${edge.from}|${edge.to}`);
  }
  return [...keys].sort();
}

// ---------- front-matter 与页面落盘 ----------

/** 块映射里的 plain scalar 只要不出现「冒号+空格 / # / 引号 / 流式符号」就安全，否则加双引号。 */
function yamlScalar(value) {
  const text = String(value ?? "");
  return /^[A-Za-z0-9\u4e00-\u9fff._\- :]+$/.test(text) && !/:\s/.test(text) ? text : JSON.stringify(text);
}

function frontMatter(page) {
  return [
    "---",
    `entityId: ${page.entityId}`,
    `entityType: ${page.entityType}`,
    `title: ${yamlScalar(page.title)}`,
    `aliases: [${page.aliases.map((alias) => JSON.stringify(alias)).join(", ")}]`,
    `version: ${page.version}`,
    `review: ${page.review}`,
    `reviewedBy: ${page.reviewedBy ?? "null"}`,
    `sources: [${page.sources.map((ref) => JSON.stringify(ref)).join(", ")}]`,
    "---",
    "",
  ].join("\n");
}

function buildPage(node) {
  const description = collapse(node.description);
  const definition = {
    title: "一句话定义",
    lines: [description || `（L0 语料没有给出「${node.label}」的定义段落，不代写。）`],
  };
  const sections = [definition, ...SECTION_BUILDERS[node.kind](node)];
  const sources = citationIds(node);
  const citations = { title: "引用来源", lines: sources.length ? sources.map(citationLine) : ["（本实体暂时没有可引用的 chunkId）"] };
  const allSections = [...sections, citations];

  const internalLinks = new Set();
  for (const section of sections) {
    for (const line of section.lines) {
      for (const match of line.matchAll(/\[\[([^\]]+)\]\]/g)) internalLinks.add(match[1]);
    }
  }

  const relativePath = `wiki/${node.kind}/${safeFileSegment(node.id)}.md`;
  const body = allSections.map((section) => `## ${section.title}\n\n${section.lines.join("\n")}`).join("\n\n");

  return {
    entityId: node.id,
    entityType: node.kind,
    title: node.label,
    path: relativePath,
    version,
    review: DRAFT,
    reviewedBy: null,
    aliases: listOf(node.aliases),
    sources,
    internalLinks: [...internalLinks].sort(),
    sections: allSections.map((section) => section.title),
    edgeKeys: projectedEdgeKeys(node.id),
    body: `${frontMatter({
      entityId: node.id,
      entityType: node.kind,
      title: node.label,
      aliases: listOf(node.aliases),
      version,
      review: DRAFT,
      reviewedBy: null,
      sources,
    })}${body}\n`,
  };
}

// ---------- 主流程 ----------

const pages = pagesFromNodes();

function pagesFromNodes() {
  const built = [];
  for (const node of pageNodes) built.push(buildPage(node));
  return built;
}

/** 只写不删：如果 wiki/ 下有本次没生成的 .md，那是上一轮留下的残留页，必须让人来处理，脚本不擅自删文件。 */
const expected = new Set(pages.map((page) => page.path));
const stale = [];
for (const kind of PAGE_KINDS) {
  const dir = resolve(WIKI_DIR, kind);
  if (!existsSync(dir)) continue;
  for (const name of readdirSync(dir)) {
    if (!name.endsWith(".md")) continue;
    const relativePath = `wiki/${kind}/${name}`;
    if (!expected.has(relativePath)) stale.push(relativePath);
  }
}

for (const page of pages) {
  const file = resolve(WIKI_DIR, page.path.slice("wiki/".length));
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, page.body, "utf8");
}

const byKind = {};
for (const page of pages) byKind[page.entityType] = (byKind[page.entityType] ?? 0) + 1;
const internalLinkTotal = pages.reduce((sum, page) => sum + page.internalLinks.length, 0);
const usedSources = new Set(pages.flatMap((page) => page.sources.map((ref) => ref.split("#")[0])));

writeJson(WIKI_INDEX_FILE, {
  schema: SCHEMA,
  generatedAt: startedAt,
  step: STEP,
  note: "Wiki 草稿页目录。review 全部是 llm-draft —— 只有第 08 步（人工审核）能把它们改成 reviewed。",
  counts: {
    pages: pages.length,
    internalLinks: internalLinkTotal,
    citedChunks: new Set(pages.flatMap((page) => page.sources)).size,
    sourcesUsed: usedSources.size,
    byKind,
  },
  pages: pages.map(({ body, ...page }) => page),
});

recordStep({
  step: STEP,
  title: TITLE,
  command: COMMAND,
  inputs: ["knowledge/extract/accepted.jsonl", "knowledge/chunks/chunks.jsonl", "knowledge/sources/sources.json"],
  outputs: [WIKI_DIR, WIKI_INDEX_FILE],
  counts: {
    pages: pages.length,
    internalLinks: internalLinkTotal,
    edgeRefs: pages.reduce((sum, page) => sum + page.edgeKeys.length, 0),
    byKind,
  },
  notes: [
    `编页范围 ${PAGE_KINDS.join(" / ")}，共 ${pages.length} 页（${describeCounts(byKind, PAGE_KINDS)}），落在 scaleTargets.wikiPages [30,40] 内。`,
    `正文内链 ${internalLinkTotal} 条，全部由已有边推出；反向校验在第 09 步。`,
    "「常见误区」等需要 L0 原文而语料没有的章节留空并注明原因，不生成无依据结论（§4.3）。",
    stale.length > 0
      ? `⚠ wiki/ 下有 ${stale.length} 个本次未生成的残留页：${stale.join("、")} —— 请确认后手工删除。`
      : "wiki/ 下没有残留页。",
  ],
  startedAt,
});

logLine(`${TITLE}：${pages.length} 页 → ${relPath(WIKI_DIR)}（${describeCounts(byKind, PAGE_KINDS)}）`);
logLine(`  正文内链 ${internalLinkTotal} 条，引用 chunk ${new Set(pages.flatMap((page) => page.sources)).size} 段，review 全部为 ${DRAFT}`);
for (const page of stale) logLine(`  ⚠ 残留页未处理：${page}`);
