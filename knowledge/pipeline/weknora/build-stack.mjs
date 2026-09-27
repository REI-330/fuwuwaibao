#!/usr/bin/env node
/**
 * 按 Tencent/WeKnora 的思路重建一套检索栈，并在我们自己的两套评测集上验证。
 *
 *   node knowledge/pipeline/weknora/build-stack.mjs
 *
 * ## 照搬了它哪几样
 *
 * 全部来自 `docs/CHUNKING.md`（MIT），**只借思路与参数，不复制代码**：
 *
 *   1. **自适应分块（auto strategy）** —— 先给文档做画像，数结构性信号，再选分层策略：
 *        画像 → heading 层（Markdown 式标题）→ 递归层（无结构时的兜底）
 *      每一块前面挂一行面包屑上下文头（`# 顶层 > ## 本节`），只在建索引时用。
 *   2. **父子块** —— 子块（小）用于匹配，父块（大）返回给下游。父块目标 4096 字符。
 *   3. **递归切分 + 重叠** —— 兜底层用 512 字符、80 字符重叠（15%）。
 *
 * ## 没有照搬、也没法照搬的
 *
 *   · Dense 向量召回 —— 它靠 embedding 服务；我们零依赖、离线，只用 BM25。
 *   · GraphRAG / Neo4j —— 我们的图谱是自建层，不是文档抽出来的实体图。
 *   · 所以下面是**稀疏检索版**的 WeKnora 思路：结构感知分块 + 父子块 + BM25。
 *
 * ## 验证怎么做才算数
 *
 * 换分块 = 换掉评测标注（参考答案是旧 chunk 的 id），所以靠**字符位置对齐**：
 * 新旧分块都在 `raw/<来源>.txt` 的同一套坐标里（旧 chunk 的 `charRange` 与
 * `blocks.json` 的 `ranges` 对齐一致，已核对）。判据用**尺寸中立**的「检索段中点落在参考段内」，
 * 另附「覆盖/落入」两列暴露偏差方向。两套题集都跑，**默认接现役题集**
 * （`--main` 缺省 questions-dev.json、`--heldout` 缺省 questions-test.json）：
 *   · 主集（dev 34 题）—— 参考答案同样不在图的引用集合里，可反复跑、用于调参
 *   · 留出集（test 32 题）—— 冻结集，只跑一次；**改动配置后不得再用它验收**
 * 历史数字（18 题主集 / 8 题留出集）出自已删除的旧题集，不可复算，只有相对参考价值。
 */
import { readdirSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { bm25Search, buildBm25, TOKENIZER_MODE } from "../lib/graph.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = resolve(HERE, "..", "..", "..");
const KNOWLEDGE = resolve(WORKSPACE_ROOT, "knowledge");
const OUT_DIR = resolve(KNOWLEDGE, "weknora");

// ---------- WeKnora 的参数（照抄默认值） ----------

const CHUNK_SIZE = 512; // chars
const CHUNK_OVERLAP = 80; // chars ≈ 15%
const PARENT_SIZE = 4096; // chars
const CONTEXT_DELIMITER = " > "; // 面包屑用 ` > ` 连接
const HEADING_TIER_MIN_HEADINGS = 3; // 低于这个数就落到递归层

const SEPARATORS = ["\n\n", "\n", "。", "！", "？", ";", "；"];

// ---------- 1. 文档画像 ----------

function profile(blocks) {
  const headings = blocks.filter((block) => block.kind === "heading");
  const levels = new Set(headings.map((heading) => heading.level));
  return {
    blocks: blocks.length,
    headings: headings.length,
    levels: levels.size,
    /** 有足够标题才用 heading 层；否则退到递归层。这是 WeKnora「文档画像挑层」的简化版。 */
    tier: headings.length >= HEADING_TIER_MIN_HEADINGS ? "heading" : "recursive",
  };
}

// ---------- 2. 两种分块策略 ----------

/** 递归层：按分隔符优先级尽量合并，单块超长就降级到下一个分隔符；都用尽就硬切。 */
function splitRecursive(text, size, depth = 0) {
  if (text.length <= size) return text ? [text] : [];
  if (depth >= SEPARATORS.length) {
    // 分隔符全用尽还是超长（例如没有标点的长表格行），必须硬切。
    // 少了这一步会把整段原样返回 —— 实测出现过 11759 字符的「512 字符块」，
    // 那等于自适应分块根本没生效，还会把结果搞好看。
    const out = [];
    for (let index = 0; index < text.length; index += size) out.push(text.slice(index, index + size));
    return out;
  }
  const separator = SEPARATORS[depth];
  const pieces = text.split(separator);
  if (pieces.length === 1) return splitRecursive(text, size, depth + 1);
  const out = [];
  let buffer = "";
  for (let index = 0; index < pieces.length; index += 1) {
    const piece = index < pieces.length - 1 ? pieces[index] + separator : pieces[index];
    if (buffer.length + piece.length <= size) {
      buffer += piece;
      continue;
    }
    if (buffer) out.push(buffer);
    if (piece.length > size) out.push(...splitRecursive(piece, size, depth + 1));
    else buffer = piece;
    if (piece.length > size) buffer = "";
  }
  if (buffer) out.push(buffer);
  return out;
}

/** 递归层 + 重叠：每块开头回接上一块尾部 overlap 个字符。 */
function chunkRecursive(text, size, overlap) {
  const start = text.length - text.trimStart().length; // 跳过开头空白，别把空段算进去
  const pieces = splitRecursive(text, size);
  const spans = [];
  let cursor = start;
  for (const piece of pieces) {
    const at = text.indexOf(piece, cursor);
    const from = at >= 0 ? at : cursor;
    spans.push({ start: from, end: from + piece.length });
    cursor = from + piece.length;
  }
  if (overlap <= 0) return spans;
  return spans.map((span, index) => (index === 0 ? span : { start: Math.max(spans[index - 1].start, spans[index - 1].end - overlap), end: span.end }));
}

/**
 * heading 层：按标题切，标题栈维护面包屑。
 * 切出来的小节如果远大于目标尺寸，再下沉到递归层细分 —— 这一点 WeKnora 也这么做
 * （「validator 拒绝明显坏掉的输出，落到下一层」）。
 */
function chunkByHeading(blocks, ranges, text, size, overlap) {
  const sections = [];
  let breadcrumb = [];
  let current = null;
  for (let index = 0; index < blocks.length; index += 1) {
    const block = blocks[index];
    const [start, end] = ranges[index];
    if (block.kind === "heading") {
      breadcrumb = breadcrumb.slice(0, Math.max(0, block.level - 1));
      breadcrumb[block.level - 1] = block.text.trim();
      if (current) sections.push(current);
      current = { start, end, header: breadcrumb.filter(Boolean).join(CONTEXT_DELIMITER), pieces: [] };
      continue;
    }
    if (!current) current = { start, end, header: "", pieces: [] };
    current.end = end;
    current.pieces.push({ start, end });
  }
  if (current) sections.push(current);

  const out = [];
  for (const section of sections) {
    if (section.end - section.start <= size * 2) {
      out.push({ start: section.start, end: section.end, header: section.header });
      continue;
    }
    for (const span of chunkRecursive(text.slice(section.start, section.end), size, overlap)) {
      out.push({ start: section.start + span.start, end: section.start + span.end, header: section.header });
    }
  }
  return out;
}

// ---------- 3. 父子块 ----------

function toParentChild(children, parentSize) {
  const parents = [];
  let id = 0;
  for (const sourceId of [...new Set(children.map((child) => child.sourceId))].sort()) {
    const list = children.filter((child) => child.sourceId === sourceId).sort((a, b) => a.start - b.start);
    let current = [];
    for (const child of list) {
      current.push(child);
      if (current[current.length - 1].end - current[0].start >= parentSize) {
        parents.push({ parentId: `W-P${String(++id).padStart(4, "0")}`, sourceId, children: current.map((c) => c.id) });
        current = [];
      }
    }
    if (current.length) parents.push({ parentId: `W-P${String(++id).padStart(4, "0")}`, sourceId, children: current.map((c) => c.id) });
  }
  return parents;
}

// ---------- 建栈 ----------

const blockFiles = readdirSync(resolve(KNOWLEDGE, "raw")).filter((name) => name.endsWith(".blocks.json"));
const documents = [];
for (const name of blockFiles) {
  const raw = JSON.parse(readFileSync(resolve(KNOWLEDGE, "raw", name), "utf8"));
  const text = readFileSync(resolve(KNOWLEDGE, "raw", `${raw.sourceId}.txt`), "utf8");
  documents.push({ sourceId: raw.sourceId, blocks: raw.blocks, ranges: raw.ranges ?? [], text });
}

const children = [];
const profiles = [];
for (const document of documents) {
  const info = profile(document.blocks);
  profiles.push({ sourceId: document.sourceId, ...info });
  const spans =
    info.tier === "heading"
      ? chunkByHeading(document.blocks, document.ranges, document.text, CHUNK_SIZE, CHUNK_OVERLAP)
      : chunkRecursive(document.text, CHUNK_SIZE, CHUNK_OVERLAP).map((span) => ({ ...span, header: "" }));
  for (const span of spans) {
    children.push({
      id: `W-${document.sourceId}-${String(children.length + 1).padStart(4, "0")}`,
      sourceId: document.sourceId,
      start: span.start,
      end: span.end,
      header: span.header,
      text: document.text.slice(span.start, span.end),
    });
  }
}

const parents = toParentChild(children, PARENT_SIZE);
const childById = new Map(children.map((child) => [child.id, child]));

const headingTier = profiles.filter((p) => p.tier === "heading").length;
console.log("=== WeKnora 思路重建：自适应分块 ===");
console.log(`文档画像：${profiles.length} 份来源，heading 层 ${headingTier} 份 / 递归层 ${profiles.length - headingTier} 份`);
console.log(`  走了递归层的：${profiles.filter((p) => p.tier === "recursive").map((p) => `${p.sourceId}(标题${p.headings})`).join(" ")}`);
console.log(`子块 ${children.length} 个（目标 ${CHUNK_SIZE} 字符 / 重叠 ${CHUNK_OVERLAP}）｜ 父块 ${parents.length} 个（目标 ${PARENT_SIZE} 字符）`);
const sizes = children.map((child) => child.text.length).sort((a, b) => a - b);
console.log(`  子块中位长度 ${sizes[Math.floor(sizes.length / 2)]}，最长 ${sizes[sizes.length - 1]}`);

// ---------- 索引：索引文本 = 面包屑 + 正文（WeKnora 的 embedding-time context header） ----------

const indexText = (child) => (child.header ? `${child.header}\n${child.text}` : child.text);
const childIndex = buildBm25(children.map((child) => ({ id: child.id, text: indexText(child), meta: {} })));

// ---------- 现有图谱（查询扩展用，与线上一致） ----------

const graph = JSON.parse(readFileSync(resolve(KNOWLEDGE, "exports", "career-graph.json"), "utf8"));
const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
const nodeIndex = buildBm25(graph.nodes.map((node) => ({
  id: node.id,
  text: [node.label, ...(node.aliases ?? []), node.description ?? ""].filter(Boolean).join(" "),
  meta: {},
})));
const adjacency = new Map();
for (const edge of graph.edges) {
  if (String(edge.to).startsWith("chunk:")) continue;
  if (!adjacency.has(edge.from)) adjacency.set(edge.from, new Set());
  if (!adjacency.has(edge.to)) adjacency.set(edge.to, new Set());
  adjacency.get(edge.from).add(edge.to);
  adjacency.get(edge.to).add(edge.from);
}
function expandedQuery(question) {
  const hood = new Set(bm25Search(nodeIndex, question, 3).map((hit) => hit.id));
  for (const neighbor of [...hood]) for (const next of adjacency.get(neighbor) ?? []) hood.add(next);
  const words = [...hood].map((id) => nodeById.get(id)).filter(Boolean)
    .flatMap((node) => [node.label, ...(node.aliases ?? []), node.description ?? ""]).filter(Boolean);
  return `${question} ${words.join(" ")}`;
}

// ---------- 评测 ----------

const oldChunks = readFileSync(resolve(KNOWLEDGE, "chunks", "chunks.jsonl"), "utf8")
  .split("\n").filter(Boolean).map((line) => JSON.parse(line));
const oldById = new Map(oldChunks.map((chunk) => [chunk.chunkId, chunk]));

// 两套题集都可覆盖：--main / --heldout 传路径。
// 历史上的 questions.json（24 题主集，可由第 11 步重新生成）与
// heldout-questions.json（8 题留出集，已删除、不在任何步骤产出里）都不再作为默认输入。
function parseSetArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--main") out.main = argv[++i];
    else if (argv[i] === "--heldout") out.heldout = argv[++i];
  }
  return out;
}
const setArgs = parseSetArgs(process.argv.slice(2));
const mainPath = resolve(setArgs.main ?? resolve(KNOWLEDGE, "evaluations", "questions-dev.json"));
const heldPath = resolve(setArgs.heldout ?? resolve(KNOWLEDGE, "evaluations", "questions-test.json"));
function readSet(path) {
  const raw = JSON.parse(readFileSync(path, "utf8"));
  const list = Array.isArray(raw) ? raw : (raw.questions ?? []);
  if (list.length === 0) throw new Error(`题库为空或字段结构不符：${path}`);
  return list.filter((q) => q.answerable !== false);
}
const mainQuestions = readSet(mainPath);
const heldoutQuestions = readSet(heldPath);
console.log(`主集：${mainPath}（${mainQuestions.length} 题）`);
console.log(`留出集：${heldPath}（${heldoutQuestions.length} 题）`);
console.log("");

/** 尺寸中立：给定字符区间，中点是否落在某条参考答案段内（先比来源，charRange 是按来源各自计数的）。 */
function centerHits(span, sourceId, referenceIds, ratio) {
  for (const refId of referenceIds) {
    const ref = oldById.get(refId);
    if (!ref || ref.sourceId !== sourceId) continue;
    const [refStart, refEnd] = ref.charRange;
    const hit = Math.max(0, Math.min(span.end, refEnd) - Math.max(span.start, refStart));
    if (ratio === "center") {
      const middle = Math.floor((span.start + span.end) / 2);
      if (middle >= refStart && middle < refEnd) return true;
    } else if (hit / (ratio === "cover" ? refEnd - refStart : span.end - span.start) >= 0.5) {
      return true;
    }
  }
  return false;
}

function runStack(questions, { useParent, topN = 3 }) {
  const score = { exact3: 0, center3: 0, center10: 0, cover3: 0 };
  let budgetSum = 0;
  for (const question of questions) {
    const refs = question.referenceChunks;
    const ranked = bm25Search(childIndex, expandedQuery(question.question), children.length);
    const scoreOf = new Map(ranked.map((hit) => [hit.id, hit.score]));

    let picked3;
    let picked10;
    if (useParent) {
      // 父块打分 = 其子块里的最高分；取前 3 个父块，展开成它们的全部子块作为「返回集」
      const scored = parents
        .map((parent) => ({ parent, score: Math.max(...parent.children.map((id) => scoreOf.get(id) ?? 0)) }))
        .filter((row) => row.score > 0)
        .sort((a, b) => b.score - a.score || a.parent.parentId.localeCompare(b.parent.parentId));
      picked3 = scored.slice(0, 3).flatMap((row) => row.parent.children.map((id) => childById.get(id))).filter(Boolean);
      picked10 = picked3;
    } else {
      const spans = ranked.map((hit) => childById.get(hit.id)).filter(Boolean);
      picked3 = spans.slice(0, topN);
      picked10 = spans.slice(0, 10);
    }
    budgetSum += picked3.length;

    const hit = (span, mode) => centerHits(span, span.sourceId, refs, mode);
    if (picked3.some((span) => hit(span, "center"))) score.center3 += 1;
    if (picked10.some((span) => hit(span, "center"))) score.center10 += 1;
    if (picked3.some((span) => hit(span, "cover"))) score.cover3 += 1;
  }
  score.avgBudget = Math.round((budgetSum / questions.length) * 10) / 10;
  return score;
}

/** 对照：现有栈（494 段，无结构感知、无父子）。分块未变，所以额外报「精确 chunk 命中」作锚点。 */
function runBaseline(questions) {
  const index = buildBm25(oldChunks.map((chunk) => ({
    id: chunk.chunkId,
    text: [chunk.heading, chunk.sectionPath, (chunk.tags ?? []).join(" "), chunk.text].filter(Boolean).join(" "),
    meta: {},
  })));
  const score = { exact3: 0, center3: 0, center10: 0, cover3: 0 };
  for (const question of questions) {
    const ranked = bm25Search(index, expandedQuery(question.question), oldChunks.length);
    const picked = ranked.slice(0, 10).map((hit) => oldById.get(hit.id)).filter(Boolean);
    const refs = new Set(question.referenceChunks);
    const span = (chunk) => ({ start: chunk.charRange[0], end: chunk.charRange[1] });
    if (picked.slice(0, 3).some((chunk) => refs.has(chunk.chunkId))) score.exact3 += 1;
    if (picked.slice(0, 3).some((chunk) => centerHits(span(chunk), chunk.sourceId, question.referenceChunks, "center"))) score.center3 += 1;
    if (picked.slice(0, 10).some((chunk) => centerHits(span(chunk), chunk.sourceId, question.referenceChunks, "center"))) score.center10 += 1;
    if (picked.slice(0, 3).some((chunk) => centerHits(span(chunk), chunk.sourceId, question.referenceChunks, "cover"))) score.cover3 += 1;
  }
  return score;
}

console.log("\n=== 验证（判据：中心@3 尺寸中立，可与现状公平比）===\n");

const parentMain = runStack(mainQuestions, { useParent: true });
const parentHeld = runStack(heldoutQuestions, { useParent: true });
const budget = Math.round((parentMain.avgBudget + parentHeld.avgBudget) / 2);

const rows = [
  { name: "现状（自建栈 494 段）", main: runBaseline(mainQuestions), held: runBaseline(heldoutQuestions), note: "返回 3 段" },
  { name: "① 自适应分块（子块检索）", main: runStack(mainQuestions, { useParent: false }), held: runStack(heldoutQuestions, { useParent: false }), note: "返回 3 个子块" },
  { name: "② ① + 父块返回（3 个父块）", main: parentMain, held: parentHeld, note: `实际返回 ~${budget} 个子块` },
  { name: "③ 对照：直接多返回同等数量子块", main: runStack(mainQuestions, { useParent: false, topN: budget }), held: runStack(heldoutQuestions, { useParent: false, topN: budget }), note: `同样 ~${budget} 个子块，但无父子结构` },
];

const mainLabel = `主集(${mainQuestions.length})@3`;
const heldLabel = `留出集(${heldoutQuestions.length})@3`;
console.log("方案".padEnd(34) + `${mainLabel}   ${heldLabel}   主集@10   留出集@10   返回量`);
console.log("-".repeat(100));
for (const row of rows) {
  const p = (v, n) => `${String(v).padStart(2)}/${n}`;
  console.log(
    row.name.padEnd(30) +
      `${p(row.main.center3, mainQuestions.length)}        ${p(row.held.center3, heldoutQuestions.length)}         ` +
      `${p(row.main.center10, mainQuestions.length)}      ${p(row.held.center10, heldoutQuestions.length)}       ${row.note}`,
  );
}
console.log("\n读法：② 比 ① 强，但 ② 返回的内容也多得多。**判断结构本身有没有价值看 ② vs ③**——");
console.log("     两者返回量相同，③ 只是没有父子结构。② 不显著超过 ③，就说明收益来自「多返回」而不是「父子结构」。");

mkdirSync(OUT_DIR, { recursive: true });

// ---------- 落成可用的知识库产物（不是内存里的实验） ----------

const childJsonl = children.map((child) => JSON.stringify({
  recordType: "child",
  id: child.id,
  sourceId: child.sourceId,
  charRange: [child.start, child.end],
  contextHeader: child.header || null,
  indexedText: indexText(child),
})).join("\n");
writeFileSync(resolve(OUT_DIR, "children.jsonl"), `${childJsonl}\n`, "utf8");

const parentJsonl = parents.map((parent) => JSON.stringify({
  recordType: "parent",
  id: parent.parentId,
  sourceId: parent.sourceId,
  children: parent.children,
})).join("\n");
writeFileSync(resolve(OUT_DIR, "parents.jsonl"), `${parentJsonl}\n`, "utf8");

// 检索索引也一起落盘，这样别的脚本不用重建就能直接查
writeFileSync(
  resolve(OUT_DIR, "index.json"),
  `${JSON.stringify({ schema: "weknora-inspired-index/v1", tokenizer: TOKENIZER_MODE, docs: children.map((c) => ({ id: c.id, text: indexText(c) })) })}\n`,
  "utf8",
);

writeFileSync(
  resolve(OUT_DIR, "stack.json"),
  `${JSON.stringify({
    schema: "weknora-inspired-stack/v1",
    note: "按 Tencent/WeKnora docs/CHUNKING.md 的思路（MIT）重建的稀疏检索版：文档画像 → 自适应分块（heading/递归两层）+ 面包屑上下文头 + 父子块 + BM25。未复制其代码，也没有照搬它依赖 embedding 服务与 Neo4j 的部分。",
    parameters: { chunkSize: CHUNK_SIZE, chunkOverlap: CHUNK_OVERLAP, parentSize: PARENT_SIZE, headingTierMinHeadings: HEADING_TIER_MIN_HEADINGS, contextDelimiter: CONTEXT_DELIMITER },
    tokenizer: TOKENIZER_MODE,
    profiles,
    counts: { documents: documents.length, children: children.length, parents: parents.length, headingTier, recursiveTier: profiles.length - headingTier },
    results: rows.map((row) => ({ name: row.name, note: row.note, main: row.main, held: row.held })),
    files: { children: "knowledge/weknora/children.jsonl", parents: "knowledge/weknora/parents.jsonl", index: "knowledge/weknora/index.json" },
  }, null, 2)}\n`,
  "utf8",
);
console.log(`\n产物：knowledge/weknora/{children.jsonl, parents.jsonl, index.json, stack.json}`);
