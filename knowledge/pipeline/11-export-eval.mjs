#!/usr/bin/env node
/**
 * 步骤 11：版本化导出与效果评测（设计文档 §6 L245、§7 L255-276、§12 L419-421）
 *
 * 本步做两件事，且刻意都不产生新的业务结论：
 *
 *   1) 把 05–10 的产物**按契约投影**成一份交付物 `knowledge/exports/career-graph.json`。
 *      07 的 wiki 页、10 的 x/y 与 requires.weight、09 的硬校验结果，到这里都只是被「抄」进来：
 *      节点/边只保留 §3.1/§3.2 的契约字段（中间态 labelKey / aliasKeys / edgeKey / adjudication
 *      一律丢掉），wikiPage 严格 9 键，evalQuestion 严格 6 键，顶层 7 个键与 sample 同形。
 *      本步**不碰 annotatedBy** —— llm-draft → llm-reviewed 的唯一通道仍是第 06 步（L251）。
 *
 *   2) 在同一份导出上跑 §7 的 24 题评测（四类各 6 题）与 5 档消融。
 *      检索用 10 建好的同一套零依赖 BM25（lib/graph.mjs 的 tokenize/buildBm25），
 *      不调模型 —— 所以这里报的是**检索中间态**（Top-3 chunk、分值、命中与否、耗时），
 *      不是「回答质量」。§7 的「引用支持度」需要人工逐条判，报告里明确标注为未做，
 *      不拿机器代理指标冒充人工结论。
 *
 * 产物：
 *   knowledge/exports/career-graph.json
 *   knowledge/evaluations/questions.json   24 题（与导出里的 evaluationQuestions 同一份）
 *   knowledge/evaluations/results.json     逐题 × 5 档配置的原始命中结果（失败案例也在里面）
 *   knowledge/evaluations/report.md        人读报告
 *
 * 复现：node knowledge/pipeline/11-export-eval.mjs
 */
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

import {
  ADJUDICATION_FILE,
  CHUNKS_FILE,
  EVAL_QUESTIONS_FILE,
  EVAL_REPORT_FILE,
  EVAL_RESULTS_FILE,
  EXPORT_FILE,
  GRAPH_FILE,
  SOURCES_FILE,
  TAXONOMY_FILE,
  WIKI_INDEX_FILE,
  relPath,
} from "./lib/paths.mjs";
import { fail, logLine, nowIso, readJson, readJsonl, recordStep, writeJson } from "./lib/log.mjs";
import {
  DEFAULT_CANVAS,
  EDGE_ORDER,
  GRAPH_VERSION,
  KB_VERSION,
  KIND_ORDER,
  WEIGHT_RULE,
  bm25Search,
  buildBm25,
  chunkIdOf,
  describeCounts,
  describeTokenizer,
  exportEdge,
  exportNode,
  isChunkTarget,
  sortEdges,
  sortNodes,
  tokenize,
} from "./lib/graph.mjs";

const STEP = "11";
const TITLE = "版本化导出与效果评测";
const COMMAND = "node knowledge/pipeline/11-export-eval.mjs";

const startedAt = nowIso();

/** 交付物状态：必须区别于 sample 的 `contract-sample`，见设计文档 §12 L426-435。 */
const EXPORT_STATUS = "pipeline-export";

/** 评测的四类问题（§7 L257：职业要求 / 技能差距与先修 / 情境建议 / 超范围与信息不足）。 */
const CATEGORIES = ["occupation-requirement", "skill-gap-prerequisite", "scenario-advice", "out-of-scope"];

/** 消融 5 档（§7 L268-274）。 */
const CONFIGS = [
  { id: "A-bm25-only", label: "BM25 only", purpose: "基线：只看问题文本的 chunk 级 BM25。", graph: false, wiki: "none" },
  { id: "B-graph-1hop", label: "+ 图遍历（一跳邻居）", purpose: "图结构是否带来增益：用节点索引定位种子，把一跳邻居的词面并进查询。", graph: true, wiki: "none" },
  { id: "C-wiki-reviewed", label: "+ wiki 页（reviewed）", purpose: "Wiki 层是否带来增益：对 reviewed 页引用的 chunk 加权。", graph: true, wiki: "reviewed" },
  { id: "D-wiki-with-draft", label: "+ wiki 页（含 llm-draft 降权）", purpose: "降权策略是否有效：reviewed 页 ×1.0、llm-draft 页 ×0.6。", graph: true, wiki: "weighted" },
  { id: "E-full", label: "全量", purpose: "完整链路：图遍历 + 全部 wiki 页加权 + 别名/描述扩展。", graph: true, wiki: "weighted" },
];

// ---------- 0. 前置文件 ----------

function requireFile(file, hint) {
  if (!existsSync(file)) {
    fail(`缺少 ${relPath(file)}，${hint}`);
    process.exit(1);
  }
}

requireFile(GRAPH_FILE, "请先跑 10-layout-index.mjs");
requireFile(CHUNKS_FILE, "请先跑 03-chunk-topics.mjs");
requireFile(SOURCES_FILE, "请先跑 01-register-sources.mjs");
requireFile(WIKI_INDEX_FILE, "请先跑 07-compile-wiki.mjs");
requireFile(TAXONOMY_FILE, "它是 kind / 边类型顺序的唯一来源");

const graph = readJson(GRAPH_FILE);
const chunks = readJsonl(CHUNKS_FILE);
const wiki = readJson(WIKI_INDEX_FILE);
const sourcesFile = readJson(SOURCES_FILE);
const taxonomy = readJson(TAXONOMY_FILE);
const adjudication = existsSync(ADJUDICATION_FILE) ? readJson(ADJUDICATION_FILE) : {};

const kindOrder = taxonomy.nodeKinds.map((spec) => spec.id);
const edgeOrder = taxonomy.edgeTypes.map((spec) => spec.id);

// ---------- 1. 契约投影 ----------

/** 字段白名单：只从这里取字段，其余中间态一律不带进交付物。 */
const SOURCE_FIELDS = sourcesFile.contractFields ?? [
  "sourceId",
  "title",
  "publisher",
  "url",
  "publishedAt",
  "publishedAtSource",
  "sourceUpdatedAt",
  "sourceUpdatedAtSource",
  "collectedAt",
  "license",
  "scopeZh",
  "sha256",
  "chunkCount",
];
const CHUNK_FIELDS = ["chunkId", "sourceId", "heading", "sectionPath", "charRange", "text", "tags", "version"];
/** §4.1 L160-183：wiki 页前置区的 8 个键 + `path`，导出里一共 9 个键。 */
const WIKI_PAGE_FIELDS = ["entityId", "entityType", "title", "path", "version", "review", "reviewedBy", "sources", "internalLinks"];
/** sample 里 evalQuestion 的 6 个键。 */
const EVAL_FIELDS = ["questionId", "category", "question", "answerable", "referenceChunks", "gradingNotes"];

function pick(row, fields) {
  const out = {};
  for (const field of fields) if (row[field] !== undefined) out[field] = row[field];
  return out;
}

const exportNodes = sortNodes(graph.nodes).map(exportNode);
const exportEdges = sortEdges(graph.edges).map(exportEdge);
const exportChunks = chunks.map((chunk) => pick(chunk, CHUNK_FIELDS));
const exportSources = sourcesFile.sources.map((source) => pick(source, SOURCE_FIELDS));
const exportWikiPages = [...wiki.pages]
  .map((page) => pick(page, WIKI_PAGE_FIELDS))
  .sort((a, b) => a.entityId.localeCompare(b.entityId));

const chunkIds = new Set(exportChunks.map((chunk) => chunk.chunkId));
const nodeIds = new Set(exportNodes.map((node) => node.id));
const draftEdges = exportEdges.filter((edge) => edge.type === "evidenced_by" || isChunkTarget(edge.to) || isChunkTarget(edge.from));
const renderableEdges = exportEdges.filter((edge) => !draftEdges.includes(edge));

/**
 * 引用的完整性：交付物里**每一处** chunk 引用都必须能在同一份 `chunks[]` 里查到，
 * 否则前端「查看依据」会点开空气。这里在落盘前先自己卡一道（09 的同类校验针对 graph.json，
 * 本步针对最终交付物）。
 */
const missingRefs = [];
for (const node of exportNodes) for (const ref of node.sourceRefs ?? []) if (!chunkIds.has(ref)) missingRefs.push(`${node.id} → ${ref}`);
for (const edge of exportEdges) for (const ref of edge.sourceRefs ?? []) if (!chunkIds.has(ref)) missingRefs.push(`${edge.id} → ${ref}`);
for (const page of exportWikiPages) for (const ref of page.sources ?? []) if (!chunkIds.has(ref)) missingRefs.push(`${page.entityId} → ${ref}`);
if (missingRefs.length > 0) {
  fail(`交付物存在 ${missingRefs.length} 处悬空 chunk 引用（示例：${missingRefs[0]}）`);
  process.exit(1);
}

const byKind = Object.fromEntries(
  kindOrder.filter((kind) => exportNodes.some((node) => node.kind === kind)).map((kind) => [kind, exportNodes.filter((node) => node.kind === kind).length]),
);
const byType = Object.fromEntries(
  edgeOrder.filter((type) => exportEdges.some((edge) => edge.type === type)).map((type) => [type, exportEdges.filter((edge) => edge.type === type).length]),
);
const byAnnotation = {};
for (const row of [...exportNodes, ...exportEdges]) byAnnotation[row.annotatedBy] = (byAnnotation[row.annotatedBy] ?? 0) + 1;

const reviewCounts = { reviewed: 0, "llm-draft": 0, other: 0 };
for (const page of exportWikiPages) {
  if (page.review === "reviewed") reviewCounts.reviewed += 1;
  else if (page.review === "llm-draft") reviewCounts["llm-draft"] += 1;
  else reviewCounts.other += 1;
}

// ---------- 2. 评测：24 题 ----------

/**
 * 题目规格。`nodes` / `edges` / `select` 都指向**真实存在的**节点、边或边族，
 * `referenceChunks` 由它们反查出来（节点自身 sourceRefs ∪ 挂在其上的 evidenced_by 目标 ∪ 边的 sourceRefs），
 * 所以「标准答案」不是手抄的，而是从图上算出来的 —— 题目换了，答案自己跟着走。
 */
const QUESTION_SPECS = [
  // —— 职业要求（6）——
  {
    questionId: "Q01",
    category: "occupation-requirement",
    question: "嵌入式开发工程师主要需要哪些能力？",
    nodes: ["occupation:AI001"],
    select: [{ type: "requires", from: "occupation:AI001" }],
    gradingNotes: "需覆盖外设驱动、实时内核与内存/DMA 处理，并给出每条能力的 sourced 依据。",
  },
  {
    questionId: "Q02",
    category: "occupation-requirement",
    question: "机器视觉工程师需要掌握哪些能力？",
    nodes: ["occupation:AI002"],
    select: [{ type: "requires", from: "occupation:AI002" }],
    gradingNotes: "需落到机器视觉链路（数据/训练/评估/部署）而不是泛泛的「会用深度学习」。",
  },
  {
    questionId: "Q03",
    category: "occupation-requirement",
    question: "自动化测试工程师的能力要求有哪些？",
    nodes: ["occupation:AI003"],
    select: [{ type: "requires", from: "occupation:AI003" }],
    gradingNotes: "需包含自动化框架与稳定性实践（显式等待、夹具复用）。",
  },
  {
    questionId: "Q04",
    category: "occupation-requirement",
    question: "边缘 AI 工程师这个岗位要求哪些技能？",
    nodes: ["occupation:AI004"],
    select: [{ type: "requires", from: "occupation:AI004" }],
    gradingNotes: "需包含模型量化/端侧部署以及其前置的 C 语言与内存模型。",
  },
  {
    questionId: "Q05",
    category: "occupation-requirement",
    question: "嵌入式开发工程师日常用到哪些工具和平台？",
    nodes: ["occupation:AI001", "tool:esp-idf", "tool:freertos"],
    edges: ["e-0078", "e-0079"],
    gradingNotes: "需点出 ESP-IDF 与 FreeRTOS，并说明各自在开发流程中的位置。",
  },
  {
    questionId: "Q06",
    category: "occupation-requirement",
    question: "机器视觉工程师与边缘 AI 工程师在能力要求上有什么重叠、又差在哪？",
    nodes: ["occupation:AI002", "occupation:AI004"],
    select: [
      { type: "requires", from: "occupation:AI002" },
      { type: "requires", from: "occupation:AI004" },
    ],
    gradingNotes: "重叠在视觉/深度学习，差异在边缘 AI 侧的量化与端侧部署。",
  },

  // —— 技能差距与先修（6）——
  {
    questionId: "Q07",
    category: "skill-gap-prerequisite",
    question: "想学「模型量化与部署」（SK215），需要先补哪些前置技能？",
    nodes: ["skill:SK215", "skill:SK101", "skill:SK090"],
    edges: ["e-0036", "e-0029"],
    gradingNotes: "顺序必须是 C 语言与内存模型 → 微控制器外设驱动 → 模型量化与部署（沿 prerequisite 边）。",
  },
  {
    questionId: "Q08",
    category: "skill-gap-prerequisite",
    question: "从「C 语言与内存模型」（SK090）到「微控制器外设驱动」（SK101）再到上层技能，先后顺序是怎样的？",
    nodes: ["skill:SK090", "skill:SK101", "skill:SK102", "skill:SK103"],
    edges: ["e-0029", "e-0030", "e-0031", "e-0032", "e-0034", "e-0033"],
    gradingNotes: "需给出拓扑序，而不是把 6 条 prerequisite 边平铺成列表。",
  },
  {
    questionId: "Q09",
    category: "skill-gap-prerequisite",
    question: "自动化测试方向的技能缺口怎么补，先修顺序是什么？",
    nodes: ["skill:SK130", "skill:SK131", "skill:SK132", "skill:SK133", "skill:SK134", "skill:SK136", "skill:SK137"],
    edges: ["e-0047", "e-0048", "e-0049", "e-0050", "e-0051", "e-0052"],
    gradingNotes: "需从测试基础（SK130）出发，分叉到 pytest 夹具与 Selenium/端到端两条支线。",
  },
  {
    questionId: "Q10",
    category: "skill-gap-prerequisite",
    question: "机器视觉方向（SK110–SK115、SK122–SK123）的技能先修关系是怎样的？",
    nodes: ["skill:SK110", "skill:SK111", "skill:SK112", "skill:SK113", "skill:SK114", "skill:SK115", "skill:SK122", "skill:SK123"],
    edges: ["e-0039", "e-0040", "e-0041", "e-0038", "e-0037", "e-0043", "e-0042"],
    gradingNotes: "需说明 SK111/SK115 落在 SK110 之前，SK122/SK123 落在 SK113 之后。",
  },
  {
    questionId: "Q11",
    category: "skill-gap-prerequisite",
    question: "边缘 AI 方向（SK215、SK120、SK121、SK123）技能之间谁先谁后？",
    nodes: ["skill:SK215", "skill:SK120", "skill:SK121", "skill:SK123"],
    edges: ["e-0044", "e-0045", "e-0046"],
    gradingNotes: "需给出 SK215 → SK120 → SK121 与 SK215 → SK123 两条支线。",
  },
  {
    questionId: "Q12",
    category: "skill-gap-prerequisite",
    question: "嵌入式开发工程师的学习单元按什么顺序学？",
    nodes: ["occupation:AI001", "knowledge:AI001:stage1", "knowledge:AI001:stage2", "knowledge:AI001:stage3", "knowledge:AI001:stage4"],
    edges: ["e-0218", "e-0219", "e-0220", "e-0221"],
    gradingNotes: "顺序来自 learning_unit 边的 position（1→4），不得按文件名或印象排序。",
  },

  // —— 情境建议（6）——
  {
    questionId: "Q13",
    category: "scenario-advice",
    question: "从嵌入式开发工程师转到边缘 AI 工程师，需要补哪些技能？",
    nodes: ["occupation:AI001", "occupation:AI004"],
    edges: ["e-0088"],
    gradingNotes: "答案必须落到 deltaSkills（SK215、SK123）与其先修，而不是泛泛的职业建议。",
  },
  {
    questionId: "Q14",
    category: "scenario-advice",
    question: "嵌入式开发工程师转到机器视觉工程师，路径和技能增量是什么？",
    nodes: ["occupation:AI001", "occupation:AI002"],
    edges: ["e-0086"],
    gradingNotes: "需引用 transitions_to 的 horizon（3 年）与 deltaSkills（SK110、SK115）。",
  },
  {
    questionId: "Q15",
    category: "scenario-advice",
    question: "机器视觉工程师转边缘 AI 工程师要怎么走？",
    nodes: ["occupation:AI002", "occupation:AI004"],
    edges: ["e-0087"],
    gradingNotes: "需给 horizon（2 年）与 deltaSkills（SK215、SK120）。",
  },
  {
    questionId: "Q16",
    category: "scenario-advice",
    question: "2026 年端侧部署这条线在涨，具体是哪几项技能在上升？",
    nodes: ["trend:edge-ai-2026", "skill:SK123", "skill:SK215"],
    edges: ["e-0089", "e-0090"],
    gradingNotes: "需说明 trend:edge-ai-2026 指向 SK123 与 SK215，并标注这些是 llm-draft 标注（未人工复核）。",
  },
  {
    questionId: "Q17",
    category: "scenario-advice",
    question: "计算机硬件工程与软件质量保证这两个方向的前景怎么样？",
    nodes: ["trend:hw-bright-outlook", "trend:sqa-bright-outlook"],
    edges: ["e-0093", "e-0094", "e-0091", "e-0092"],
    gradingNotes: "一个「平稳」一个「上升」，需分开陈述并给出技能指向。",
  },
  {
    questionId: "Q18",
    category: "scenario-advice",
    question: "浏览器自动化协议标准化（WebDriver BiDi）对测试工程师的技能有什么影响？",
    nodes: ["trend:webdriver-bidi", "skill:SK132", "skill:SK137"],
    edges: ["e-0096", "e-0095"],
    gradingNotes: "需说明它对 Selenium 与端到端测试技能是加分项，并标注 llm-draft 状态。",
  },

  // —— 超范围与信息不足（6）——
  {
    questionId: "Q19",
    category: "out-of-scope",
    question: "这几个岗位目前的薪资区间是多少？",
    nodes: [],
    edges: [],
    gradingNotes: "知识库不含薪资数据：正确行为是明确说资料不足并追问岗位与地区，不得编造数字。",
  },
  {
    questionId: "Q20",
    category: "out-of-scope",
    question: "现在有哪些公司在招这些岗位，招聘量有多大？",
    nodes: [],
    edges: [],
    gradingNotes: "知识库不含招聘量与在招企业信息；应说明资料不足。",
  },
  {
    questionId: "Q21",
    category: "out-of-scope",
    question: "我应该报考哪所大学的哪个专业？",
    nodes: [],
    edges: [],
    gradingNotes: "院校与招生建议超出知识库范围；应说明资料不足并追问目标城市/学历。",
  },
  {
    questionId: "Q22",
    category: "out-of-scope",
    question: "到 2030 年这些岗位的确切人才缺口人数是多少？",
    nodes: [],
    edges: [],
    gradingNotes: "知识库只给方向（上升/平稳），不给人数；不得把方向结论外推成数字。",
  },
  {
    questionId: "Q23",
    category: "out-of-scope",
    question: "请直接给我一份可以投递的简历模板。",
    nodes: [],
    edges: [],
    gradingNotes: "模板生成不是知识库职责；应说明资料不足并给出可复用的能力项清单来源。",
  },
  {
    questionId: "Q24",
    category: "out-of-scope",
    question: "某个证书的考试报名费是多少？",
    nodes: [],
    edges: [],
    gradingNotes: "知识库只登记证书与其来源，不含费用；应说明资料不足。",
  },
];

const nodeByIdExport = new Map(exportNodes.map((node) => [node.id, node]));
const edgeByIdExport = new Map(exportEdges.map((edge) => [edge.id, edge]));

/** 节点能回溯到的 chunk：自身 sourceRefs ∪ 挂在自己身上的 evidenced_by 目标。 */
const nodeRefs = new Map();
for (const node of exportNodes) nodeRefs.set(node.id, new Set(node.sourceRefs ?? []));
for (const edge of exportEdges) {
  if (edge.type !== "evidenced_by") continue;
  const chunkId = chunkIdOf(edge.to);
  if (chunkId && nodeRefs.has(edge.from)) nodeRefs.get(edge.from).add(chunkId);
}

const specProblems = [];
function resolveRefs(spec) {
  const refs = new Set();
  for (const nodeId of spec.nodes ?? []) {
    if (!nodeByIdExport.has(nodeId)) specProblems.push(`${spec.questionId}: 节点不存在 ${nodeId}`);
    else for (const ref of nodeRefs.get(nodeId)) refs.add(ref);
  }
  for (const edgeId of spec.edges ?? []) {
    const edge = edgeByIdExport.get(edgeId);
    if (!edge) specProblems.push(`${spec.questionId}: 边不存在 ${edgeId}`);
    else for (const ref of edge.sourceRefs ?? []) refs.add(ref);
  }
  for (const selector of spec.select ?? []) {
    const matched = exportEdges.filter(
      (edge) => edge.type === selector.type && (!selector.from || edge.from === selector.from) && (!selector.to || edge.to === selector.to),
    );
    if (matched.length === 0) specProblems.push(`${spec.questionId}: 边族为空 ${JSON.stringify(selector)}`);
    for (const edge of matched) for (const ref of edge.sourceRefs ?? []) refs.add(ref);
  }
  return [...refs].sort();
}

const questions = QUESTION_SPECS.map((spec) => ({
  questionId: spec.questionId,
  category: spec.category,
  question: spec.question,
  answerable: (spec.nodes?.length ?? 0) + (spec.edges?.length ?? 0) + (spec.select?.length ?? 0) > 0,
  referenceChunks: resolveRefs(spec),
  gradingNotes: spec.gradingNotes,
  // 报告与复算用，导出时按 EVAL_FIELDS 过滤掉。
  referenceNodes: [...(spec.nodes ?? [])],
  referenceEdges: [
    ...(spec.edges ?? []),
    ...(spec.select ?? []).flatMap((selector) =>
      exportEdges
        .filter((edge) => edge.type === selector.type && (!selector.from || edge.from === selector.from) && (!selector.to || edge.to === selector.to))
        .map((edge) => edge.id),
    ),
  ],
}));

if (specProblems.length > 0) {
  fail(`评测题目引用了不存在的图元素：${specProblems.join("；")}`);
  process.exit(1);
}

const categoryCounts = Object.fromEntries(CATEGORIES.map((category) => [category, questions.filter((q) => q.category === category).length]));
const emptyRefQuestions = questions.filter((q) => q.answerable && q.referenceChunks.length === 0);
if (emptyRefQuestions.length > 0) {
  fail(`可回答问题缺少 referenceChunks：${emptyRefQuestions.map((q) => q.questionId).join("、")}`);
  process.exit(1);
}
const unanswerableWithRefs = questions.filter((q) => !q.answerable && q.referenceChunks.length > 0);
if (unanswerableWithRefs.length > 0) {
  fail(`超范围问题不该有 referenceChunks：${unanswerableWithRefs.map((q) => q.questionId).join("、")}`);
  process.exit(1);
}

const questionsFile = {
  schema: "career-graph-eval-questions/v1",
  step: STEP,
  generatedAt: startedAt,
  kbVersion: KB_VERSION,
  graphVersion: GRAPH_VERSION,
  note: "§7 的 24 题（四类各 6）。referenceChunks 由 referenceNodes / referenceEdges 反查得出，不是手抄：题目换了，答案自己跟着图走。导出里的 evaluationQuestions 是本文件按 6 键契约投影的结果。",
  categories: CATEGORIES,
  counts: { total: questions.length, ...categoryCounts, answerable: questions.filter((q) => q.answerable).length },
  questions,
};

// ---------- 3. 评测执行（5 档消融） ----------

const chunkDocs = exportChunks.map((chunk) => ({
  id: chunk.chunkId,
  text: [chunk.heading, chunk.sectionPath, (chunk.tags ?? []).join(" "), chunk.text].filter(Boolean).join(" "),
  meta: { sourceId: chunk.sourceId, title: chunk.heading || chunk.sectionPath || chunk.chunkId },
}));
const nodeDocs = exportNodes.map((node) => ({
  id: node.id,
  text: [node.label, ...(node.aliases ?? []), node.description ?? "", node.kind, node.id].filter(Boolean).join(" "),
  meta: { kind: node.kind, title: node.label },
}));
const chunkIndex = buildBm25(chunkDocs);
const nodeIndex = buildBm25(nodeDocs);

/** 一跳邻域（只用**可渲染**的节点-节点边，`chunk:` 引用不参与遍历 —— §3.2 L144-146）。 */
const adjacency = new Map();
function link(a, b) {
  if (!adjacency.has(a)) adjacency.set(a, new Set());
  adjacency.get(a).add(b);
}
for (const edge of renderableEdges) {
  link(edge.from, edge.to);
  link(edge.to, edge.from);
}

/** 别名倒排的「确定性入口」：实体识别先查别名，不靠模型猜（§4.1 L188）。 */
const aliasToNode = new Map();
for (const node of exportNodes) {
  for (const surface of [node.label, ...(node.aliases ?? []), node.id]) {
    if (!surface) continue;
    const key = String(surface).trim().toLowerCase();
    if (!aliasToNode.has(key)) aliasToNode.set(key, node.id);
  }
}

/** wiki 页 → 它引用的 chunk，以及它的复核级别（§4.2 L192-196：全审 reviewed / 抽审 llm-draft）。 */
const wikiChunks = new Map();
const wikiReview = new Map();
for (const page of exportWikiPages) {
  wikiChunks.set(page.entityId, new Set(page.sources ?? []));
  wikiReview.set(page.entityId, page.review);
}

const GRAPH_BONUS = 0.5;
const WIKI_BONUS = 0.5;
const DRAFT_FACTOR = 0.6;
/**
 * ⚠️ 这两个加成在**当前量纲下等于没生效**：BM25 分数在 200–300 量级，绝对加 0.5 只占 0.2%，
 * 永远改变不了排序。所以消融里 C/D/E 三档与 B 完全相同 —— 那不是「Wiki 层没用」的证据，
 * 是「这段权重根本没参与排序」。
 *
 * 已测过修法：把它们换成**相对倍数**（被邻域节点引用过的段落 ×1.3）后，命中率从 4/18 跳到 12/18
 * （见 `probe-retrieval.mjs` 的 V8）。**但那个数字含同源水分**：实测参考答案 134 段
 * 100% 都落在「图的引用集合」里，18 题全覆盖 —— 对引用集合加权，等于在答案集合内部排序。
 *
 * 所以这里**先不修**：修了之后报告里就会出现一个说不清的成绩。正确顺序是
 * ① 先做一份「答案不来自构图语料」的独立评测集，② 再改这两个常量，③ 用新评测集判断值不值。
 */

/** 用节点索引 + 别名倒排定位种子节点：问题里提到哪个实体，就从哪个实体开始走图。 */
function seedNodes(question, limit = 3) {
  const scored = bm25Search(nodeIndex, question, limit);
  const found = scored.map((hit) => hit.id);
  const lowered = question.toLowerCase();
  for (const [surface, nodeId] of aliasToNode) {
    if (lowered.includes(surface) && !found.includes(nodeId)) found.push(nodeId);
  }
  return found.slice(0, limit);
}

function neighborhood(seeds) {
  const out = new Set(seeds);
  for (const seed of seeds) for (const next of adjacency.get(seed) ?? []) out.add(next);
  return out;
}

/** 一档配置的完整打分：chunk BM25 + 图加权 + wiki 加权，全部走同一份 lib/graph.mjs。 */
function rankChunks(config, question) {
  const seeds = config.graph || config.id === "E-full" ? seedNodes(question) : [];
  const hood = neighborhood(seeds);

  let query = question;
  if (config.graph) {
    // 扩展词 = 邻居节点的标签 + 别名 + **描述**。
    // 加上描述是实测结论：`probe-retrieval.mjs` 的 A/B 里，只扩标签与别名是 4/18，
    // 加上 description 变成 6/18（top10 从 11/18 到 12/18）。描述里有「把训练好的模型搬到端侧设备上跑」
    // 这类原词里没有的表述，正是查询与原文用词对不上的那块缺口。
    const expansion = [...hood]
      .map((id) => nodeByIdExport.get(id))
      .filter(Boolean)
      .flatMap((node) => [node.label, ...(node.aliases ?? []), node.description ?? ""])
      .filter(Boolean);
    if (expansion.length > 0) query = `${question} ${expansion.join(" ")}`;
  }

  const base = new Map(bm25Search(chunkIndex, query, chunkIndex.entries.length).map((hit) => [hit.id, hit.score]));
  const scored = exportChunks.map((chunk) => {
    let score = base.get(chunk.chunkId) ?? 0;
    if (config.graph) {
      for (const id of hood) {
        const refs = nodeRefs.get(id);
        if (refs && refs.has(chunk.chunkId)) {
          score += GRAPH_BONUS;
          break;
        }
      }
    }
    if (config.wiki !== "none") {
      for (const [entityId, refs] of wikiChunks) {
        if (!hood.has(entityId) || !refs.has(chunk.chunkId)) continue;
        const review = wikiReview.get(entityId);
        if (config.wiki === "reviewed") {
          if (review === "reviewed") score += WIKI_BONUS;
        } else {
          score += review === "reviewed" ? WIKI_BONUS : WIKI_BONUS * DRAFT_FACTOR;
        }
        break;
      }
    }
    return { chunkId: chunk.chunkId, score: Math.round(score * 1000) / 1000 };
  });

  scored.sort((a, b) => b.score - a.score || a.chunkId.localeCompare(b.chunkId));
  return scored.filter((row) => row.score > 0).slice(0, 3);
}

/** 无依据问题的判定阈值：真实问题 Top-1 分的下四分位（低于它就算「确实没料」）。 */
function percentile(values, ratio) {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(ratio * (sorted.length - 1)))];
}

const answerable = questions.filter((q) => q.answerable);
const unanswerable = questions.filter((q) => !q.answerable);

const runs = [];
for (const config of CONFIGS) {
  const perQuestion = [];
  const elapsedStart = Date.now();
  for (const question of questions) {
    const top = rankChunks(config, question.question);
    const ids = top.map((row) => row.chunkId);
    const hits = question.referenceChunks.filter((ref) => ids.includes(ref));
    perQuestion.push({
      questionId: question.questionId,
      category: question.category,
      answerable: question.answerable,
      referenceChunks: question.referenceChunks,
      top3: top,
      hit: question.answerable ? hits.length > 0 : null,
      precisionAt3: question.answerable ? Math.round((hits.length / 3) * 1000) / 1000 : null,
      hits,
    });
  }
  const elapsedMs = Date.now() - elapsedStart;

  const answered = perQuestion.filter((row) => row.answerable);
  const answeredHit = answered.filter((row) => row.hit);
  const top1 = answered.map((row) => row.top3[0]?.score ?? 0);
  const threshold = Math.round(percentile(top1, 0.25) * 1000) / 1000;
  const handled = perQuestion.filter((row) => !row.answerable).filter((row) => (row.top3[0]?.score ?? 0) < threshold);

  runs.push({
    id: config.id,
    label: config.label,
    purpose: config.purpose,
    threshold,
    metrics: {
      hitRate: `${answeredHit.length}/${answered.length}`,
      hitRateValue: Math.round((answeredHit.length / answered.length) * 1000) / 1000,
      meanPrecisionAt3: Math.round((answered.reduce((sum, row) => sum + row.precisionAt3, 0) / answered.length) * 1000) / 1000,
      insufficientDataHandling: `${handled.length}/${unanswerable.length}`,
      insufficientDataHandlingValue: Math.round((handled.length / unanswerable.length) * 1000) / 1000,
      elapsedMs,
    },
    perQuestion,
  });
}

const resultsFile = {
  schema: "career-graph-eval-results/v1",
  step: STEP,
  generatedAt: startedAt,
  kbVersion: KB_VERSION,
  graphVersion: GRAPH_VERSION,
  note: "逐题 × 5 档配置的原始命中结果。检索用 10 的同一套零依赖 BM25（lib/graph.mjs），不调模型，所以只有检索耗时；「引用支持度」需人工判定，本文件不冒充。失败案例原样保留。",
  corpus: { chunks: exportChunks.length, nodes: exportNodes.length, edges: exportEdges.length, wikiPages: exportWikiPages.length },
  scoring: {
    graphBonus: GRAPH_BONUS,
    wikiBonus: WIKI_BONUS,
    draftFactor: DRAFT_FACTOR,
    topK: 3,
    thresholdRule: "每档配置取「可回答问题 Top-1 分」的下四分位作为阈值；无依据问题的 Top-1 分低于该阈值即判为「正确说明资料不足」。",
    tokenizer: describeTokenizer(),
  },
  configs: CONFIGS,
  summary: runs.map((run) => ({ id: run.id, label: run.label, threshold: run.threshold, metrics: run.metrics })),
  runs,
};

// ---------- 4. 导出组装 ----------

const exportQuestions = questions.map((question) => pick(question, EVAL_FIELDS));

const exportDoc = {
  meta: {
    kbVersion: KB_VERSION,
    graphVersion: GRAPH_VERSION,
    generatedAt: startedAt,
    status: EXPORT_STATUS,
    note: `由 knowledge/pipeline/11-export-eval.mjs 从第 01–10 步产物投影生成的真实交付物（status=${EXPORT_STATUS}，非 contract-sample）。节点/边只保留契约字段，x/y 与 requires.weight 来自第 10 步，annotatedBy 来自第 06 步裁定，本次导出未改动任何标注。`,
    adapterNotes: [
      "edges[].type 在前端适配层映射为 lib/client/career-graph.ts 的 GraphEdge.relation。",
      "nodes[].kind 覆盖 8 类（含 task / trend / credential / domain），前端 GraphNode.kind 已拓宽，不再过滤。",
      "nodes[].x / nodes[].y 是第 10 步产出的确定性布局坐标（画布 1050×820），前端不得重算力导向布局。",
      "evidenced_by 的 to 是 chunk:<chunkId> 引用而非节点 id：前端 buildGraphView 必须过滤这类边，否则会产生幽灵节点并污染 BFS。",
      "weight 只出现在 requires 边上且等于 importance；其余边类型没有量化来源，前端对 weight === undefined 的边不做线宽映射。",
    ],
    counts: {
      sources: exportSources.length,
      chunks: exportChunks.length,
      nodes: exportNodes.length,
      edges: exportEdges.length,
      wikiPages: exportWikiPages.length,
      evaluationQuestions: exportQuestions.length,
    },
  },
  sources: exportSources,
  chunks: exportChunks,
  nodes: exportNodes,
  edges: exportEdges,
  wikiPages: exportWikiPages,
  evaluationQuestions: exportQuestions,
};

writeJson(EXPORT_FILE, exportDoc);
writeJson(EVAL_QUESTIONS_FILE, questionsFile);
writeJson(EVAL_RESULTS_FILE, resultsFile);

// ---------- 5. 人读报告 ----------

const bestRun = runs.reduce((best, run) => (run.metrics.hitRateValue > best.metrics.hitRateValue ? run : best), runs[0]);
const worstRun = runs.reduce((worst, run) => (run.metrics.hitRateValue < worst.metrics.hitRateValue ? run : worst), runs[0]);
const ablatedByGraph = runs.find((run) => run.id === "B-graph-1hop");
const baseline = runs.find((run) => run.id === "A-bm25-only");
const full = runs.find((run) => run.id === "E-full");
const failureRows = [];
for (const run of runs) {
  for (const row of run.perQuestion) {
    if (row.answerable && row.hit === false) failureRows.push({ config: run.label, questionId: row.questionId, question: questions.find((q) => q.questionId === row.questionId).question, top3: row.top3, referenceChunks: row.referenceChunks });
  }
}

function sign(value) {
  return `${value >= 0 ? "+" : ""}${value}`;
}
function pct(value) {
  return `${(value * 100).toFixed(1)}%`;
}

const tableRows = runs
  .map(
    (run) =>
      `| ${run.label} | ${run.metrics.hitRate} | ${pct(run.metrics.hitRateValue)} | ${run.metrics.meanPrecisionAt3} | ${run.metrics.insufficientDataHandling} | ${pct(run.metrics.insufficientDataHandlingValue)} | ${run.metrics.elapsedMs} ms |`,
  )
  .join("\n");

const perQuestionRows = questions
  .map((question) => {
    const cells = runs
      .map((run) => {
        const row = run.perQuestion.find((item) => item.questionId === question.questionId);
        if (!question.answerable) {
          const ok = (row.top3[0]?.score ?? 0) < run.threshold;
          return `${ok ? "✅ 说资料不足" : "⚠️ 未说不足"}（Top-1 ${row.top3[0]?.score ?? 0}）`;
        }
        return row.hit ? `✅ ${row.hits.join(", ")}` : `❌ ${row.top3.map((item) => item.chunkId).join(", ") || "（无命中）"}`;
      })
      .join(" | ");
    return `| ${question.questionId} | ${question.category} | ${question.question} | ${cells} |`;
  })
  .join("\n");

const report = `# 知识库效果评估报告（第 11 步产物）

- 生成方式：\`${COMMAND}\`
- 生成时间：${startedAt}
- 知识库版本：kbVersion \`${KB_VERSION}\` / graphVersion \`${GRAPH_VERSION}\`
- 评测语料：${exportChunks.length} 段 chunk、${exportNodes.length} 个节点、${exportEdges.length} 条边、${exportWikiPages.length} 个 wiki 页（**就是同一份导出**，不是另抄一份）
- 题目集：${questions.length} 题，四类各 ${categoryCounts[CATEGORIES[0]]} 题（${CATEGORIES.join(" / ")}）

## 一、这份报告处于什么状态

| 部分 | 状态 | 依据 |
|---|---|---|
| 导出与评测的**脚本、输入、输出、复算路径** | **实际实现 + 实际验证** | 本文件与 \`knowledge/exports/career-graph.json\`、\`knowledge/evaluations/{questions,results}.json\` 都是本步真实跑出来的产物，命令即上文一行 |
| **检索指标**（命中率 / 平均 Top-3 精确率 / 信息不足处理率 / 检索耗时） | **实际验证** | 逐题原始命中结果（含失败案例）全部落在 \`results.json\`，任何人重跑同一条命令可复算同一组数字 |
| **引用支持度**与**回答质量** | **未做（需人工 + 模型调用）** | §7 L262 要求逐条人工判「回答的关键结论是否被引用内容支持」。本步不调模型，所以**不报**这个指标，也不拿 Top-3 精确率冒充它 |
| 知识库内容本身 | **原型模拟** | 来源是公开资料，图谱 ${exportNodes.length} 节点 / ${exportEdges.length} 边，规模远小于生产知识库；小样本不作普遍准确率承诺 |
| 与百宝箱的对照实验 | **设计方案（未做）** | §11 待确认 #1：平台侧知识库导入权限与检索中间态仍未核验，故对照实验暂不做 |

> 措辞提醒（§12 L426-435）：本报告的「实际验证」只覆盖**检索链路与指标口径**这一层，不等于「知识内容真实」，也不等于百宝箱平台已连入。

## 二、消融结果（§7 L268-274 的 5 档）

| 配置 | 命中率 | 命中率值 | 平均 Top-3 精确率 | 信息不足处理率 | 处理率值 | 检索耗时 |
|---|---|---|---|---|---|---|
${tableRows}

- 命中率分母是 ${answerable.length} 道**可回答**题；信息不足处理率分母是 ${unanswerable.length} 道**超范围**题。
- 每档阈值（可回答问题 Top-1 分的下四分位）：${runs.map((run) => `${run.label} \`${run.threshold}\``).join("，")}。
- 图遍历增益（B − A）：命中率 ${sign(ablatedByGraph.metrics.hitRateValue - baseline.metrics.hitRateValue)}，平均精确率 ${sign(Math.round((ablatedByGraph.metrics.meanPrecisionAt3 - baseline.metrics.meanPrecisionAt3) * 1000) / 1000)}。
- 全量相对基线（E − A）：命中率 ${sign(full.metrics.hitRateValue - baseline.metrics.hitRateValue)}，平均精确率 ${sign(Math.round((full.metrics.meanPrecisionAt3 - baseline.metrics.meanPrecisionAt3) * 1000) / 1000)}。
- 最好：${bestRun.label}（${bestRun.metrics.hitRate}）；最差：${worstRun.label}（${worstRun.metrics.hitRate}）。

## 三、逐题结果

「✅」= Top-3 命中标注的相关 chunk（后列为命中的 chunkId）；「❌」= 未命中（后列为实际 Top-3）。
超范围题不判命中率，改判「是否明确说资料不足」。

| 题号 | 类别 | 问题 | ${CONFIGS.map((config) => config.label).join(" | ")} |
|---|---|---|---|---|
${perQuestionRows}

## 四、失败案例（原样保留）

共 ${failureRows.length} 条「可回答问题未命中」记录（5 档 × ${answerable.length} 题 = ${runs.length * answerable.length} 次作答中的未命中）：

${
  failureRows.length === 0
    ? "本次运行没有未命中记录。"
    : failureRows
        .map(
          (row) =>
            `- **${row.questionId}**（${row.config}）：${row.question}\n  - 标注相关 chunk：${row.referenceChunks.join(", ")}\n  - 实际 Top-3：${row.top3.map((item) => `${item.chunkId}(${item.score})`).join(", ") || "（无命中）"}`,
        )
        .join("\n")
}

修改方式与复测结果：修完题目措辞、参考 chunk 标注或检索权重后，重跑 \`${COMMAND}\`，把新的 \`results.json\` 覆盖上来并在此处追加一行「修改内容 / 修改前后命中率」。只放成功截图的报告不可信（§7 L276）。

## 五、指标口径与局限

1. **命中率**：Top-3 结果里是否含**至少一个**标注相关 chunk，报分子/分母，不报单一名次。
2. **平均 Top-3 精确率**：Top-3 中命中标注 chunk 的条数 / 3，取 18 题均值 —— 这是**机器代理指标**，只用于看排序质量，**不等于** §7 的「引用支持度」。
3. **信息不足处理率**：${unanswerable.length} 道超范围题的 Top-1 分低于阈值即算正确说「资料不足」。阈值是机器启发式，不是人工判断。
4. **检索耗时**：只统计本地 BM25/图遍历耗时，不含模型调用（本步没有模型调用）。
5. **未做**：引用支持度、回答质量、多轮追问路径、真实用户满意度。
6. **不作承诺**：${questions.length} 题是小样本，且题目与标注由同一批人产出，存在自证偏差；不对普适准确率做任何承诺。

## 六、复现

\`\`\`bash
node knowledge/pipeline/11-export-eval.mjs
# 产物：
#   ${relPath(EXPORT_FILE)}
#   ${relPath(EVAL_QUESTIONS_FILE)}
#   ${relPath(EVAL_RESULTS_FILE)}
#   ${relPath(EVAL_REPORT_FILE)}
\`\`\`

前提：先跑完 05 → 10（\`graph.json\` 里的 x/y 与 requires.weight 来自第 10 步）。
`;

mkdirSync(dirname(EVAL_REPORT_FILE), { recursive: true });
writeFileSync(EVAL_REPORT_FILE, report, "utf8");

// ---------- 6. 日志 ----------

logLine(`${TITLE}：${relPath(EXPORT_FILE)} + ${relPath(EVAL_REPORT_FILE)}`);
logLine(`  导出：${describeCounts(byKind, kindOrder)}；${describeCounts(byType, edgeOrder)}`);
logLine(`  标注：${describeCounts(byAnnotation, ["human", "llm-reviewed", "llm-draft"])}（本步未改动任何 annotatedBy）`);
logLine(`  画布：x/y 取自第 10 步（${DEFAULT_CANVAS.width}×${DEFAULT_CANVAS.height}），weight 规则：${WEIGHT_RULE}`);
logLine(`  评测：${questions.length} 题（${describeCounts(categoryCounts, CATEGORIES)}），5 档消融`);
logLine(`  消融命中率：${runs.map((run) => `${run.label} ${run.metrics.hitRate}`).join("；")}`);

recordStep({
  step: STEP,
  title: TITLE,
  command: COMMAND,
  inputs: [GRAPH_FILE, CHUNKS_FILE, SOURCES_FILE, WIKI_INDEX_FILE, TAXONOMY_FILE, ADJUDICATION_FILE],
  outputs: [EXPORT_FILE, EVAL_QUESTIONS_FILE, EVAL_RESULTS_FILE, EVAL_REPORT_FILE],
  counts: {
    sources: exportSources.length,
    chunks: exportChunks.length,
    nodes: exportNodes.length,
    edges: exportEdges.length,
    renderableEdges: renderableEdges.length,
    wikiPages: exportWikiPages.length,
    evaluationQuestions: questions.length,
    answerable: answerable.length,
    ...Object.fromEntries(CATEGORIES.map((category) => [`category_${category}`, categoryCounts[category]])),
  },
  notes: [
    `导出 ${relPath(EXPORT_FILE)}：status=${EXPORT_STATUS}（≠ contract-sample），顶层 7 键与 sample 同形；节点/边/来源/chunk 全部只保留契约字段。`,
    `引用完整性：节点/边/wiki 页共 ${exportNodes.length + exportEdges.length + exportWikiPages.length} 条记录的 chunk 引用全部能在同一份 chunks[] 里查到，悬空 0 处。`,
    `weight 按 WEIGHT_RULE 只出现在 requires 上；weight 的值来自第 10 步，未在本步重算或改写。`,
    `本步不碰 annotatedBy：human ${byAnnotation.human ?? 0} / llm-reviewed ${byAnnotation["llm-reviewed"] ?? 0} / llm-draft ${byAnnotation["llm-draft"] ?? 0}，llm-draft → llm-reviewed 的唯一通道仍是第 06 步（L251）。`,
    `评测 ${questions.length} 题（${describeCounts(categoryCounts, CATEGORIES)}），消融 5 档：${runs.map((run) => `${run.label} 命中 ${run.metrics.hitRate}`).join("；")}。`,
    `评测只报检索中间态（Top-3 + 分值 + 耗时），不调模型；「引用支持度」需人工判定，报告已明确标注为未做（§7 L262 / L276）。`,
  ],
  startedAt,
});

if (specProblems.length > 0) fail(specProblems.join("；"));
