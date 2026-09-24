#!/usr/bin/env node
/**
 * 分块参数 A/B：把来源原文按不同参数重新切一遍，看检索命中率怎么变。
 *
 *   node knowledge/pipeline/probe-chunking.mjs
 *
 * 对照的参数来自 Tencent/WeKnora 的 `docs/CHUNKING.md`：递归切分、**512 字符 + 15% 重叠**
 * 被称为「最强单旋钮基线」。我们现在的分块是**中位 846 字、零重叠、参数没有依据**，
 * 所以这里拿它当对照组，看我们的参数是不是吃亏了、或者是不是反而更好。
 *
 * **这一条实验有个必须先解决的麻烦：换分块就等于换掉评测集的标注。**
 * 参考答案是「旧 chunk 的 id」，重新切之后那些 id 就不存在了。处理办法是靠**字符位置**对齐：
 * 旧 chunk 都带 `charRange`，新切出来的段落也带自己的字符范围；
 * 只要新段落与某个旧参考段落有足够重叠，就认为这次检索覆盖了那条参考答案。
 * 这样新旧分块才可比。对齐阈值写在 OVERLAP_RATIO，调它会改变结论，所以写死在这里、不藏。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { bm25Search, buildBm25, tokenize } from "./lib/graph.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = resolve(HERE, "..", "..");
const KNOWLEDGE = resolve(WORKSPACE_ROOT, "knowledge");

const oldChunks = readFileSync(resolve(KNOWLEDGE, "chunks", "chunks.jsonl"), "utf8")
  .split("\n")
  .filter((line) => line.trim().length > 0)
  .map((line) => JSON.parse(line));
const questions = JSON.parse(readFileSync(resolve(KNOWLEDGE, "evaluations", "questions.json"), "utf8"));
const answerable = (Array.isArray(questions) ? questions : questions.questions).filter((q) => q.answerable);
const oldById = new Map(oldChunks.map((chunk) => [chunk.chunkId, chunk]));

const graphExport = JSON.parse(readFileSync(resolve(KNOWLEDGE, "exports", "career-graph.json"), "utf8"));
const nodeById = new Map(graphExport.nodes.map((node) => [node.id, node]));
const nodeIndex = buildBm25(graphExport.nodes.map((node) => ({
  id: node.id,
  text: [node.label, ...(node.aliases ?? []), node.description ?? ""].filter(Boolean).join(" "),
  meta: {},
})));
const adjacency = new Map();
for (const edge of graphExport.edges) {
  if (String(edge.to).startsWith("chunk:")) continue;
  if (!adjacency.has(edge.from)) adjacency.set(edge.from, new Set());
  if (!adjacency.has(edge.to)) adjacency.set(edge.to, new Set());
  adjacency.get(edge.from).add(edge.to);
  adjacency.get(edge.to).add(edge.from);
}
/** 与线上一致：图邻域 + 节点描述并入查询（见 5.3 的 V7）。 */
function expandedQuery(question) {
  const hood = new Set(bm25Search(nodeIndex, question, 3).map((hit) => hit.id));
  for (const neighbor of [...hood]) for (const next of adjacency.get(neighbor) ?? []) hood.add(next);
  const words = [...hood].map((id) => nodeById.get(id)).filter(Boolean)
    .flatMap((node) => [node.label, ...(node.aliases ?? []), node.description ?? ""]).filter(Boolean);
  return `${question} ${words.join(" ")}`;
}

// ---------- 分块器 ----------

const SEPARATORS = ["\n\n", "\n", "。", "！", "？", ";", "；"];

/** 在不超过 size 的前提下尽量合并段落；单段超长就按下一个分隔符继续切。 */
function splitRecursive(text, size, depth = 0) {
  if (text.length <= size || depth >= SEPARATORS.length) return [text];
  const separator = SEPARATORS[depth];
  const pieces = text.split(separator);
  if (pieces.length === 1) return splitRecursive(text, size, depth + 1);
  const out = [];
  let buffer = "";
  for (const [index, piece] of pieces.entries()) {
    const withSeparator = index < pieces.length - 1 ? piece + separator : piece;
    if (buffer.length + withSeparator.length <= size) {
      buffer += withSeparator;
      continue;
    }
    if (buffer) out.push(buffer);
    if (withSeparator.length > size) out.push(...splitRecursive(withSeparator, size, depth + 1));
    else buffer = withSeparator;
    if (withSeparator.length > size) buffer = "";
  }
  if (buffer) out.push(buffer);
  return out;
}

/** 带重叠的分块：每块尾部重叠 overlap 个字符接到下一块开头（与 WeKnora 的做法一致）。 */
function chunkText(text, { size, overlap }) {
  const pieces = splitRecursive(text, size);
  const out = [];
  let cursor = 0;
  for (const piece of pieces) {
    const start = text.indexOf(piece, cursor);
    const at = start >= 0 ? start : cursor;
    out.push({ start: at, end: at + piece.length, text: piece });
    cursor = at + piece.length;
  }
  if (overlap <= 0) return out;
  return out.map((chunk, index) => {
    if (index === 0) return chunk;
    const previousEnd = out[index - 1].end;
    const start = Math.max(out[index - 1].start, previousEnd - overlap);
    return { start, end: chunk.end, text: text.slice(start, chunk.end) };
  });
}

// ---------- 对齐判据 ----------

/** 新旧段落重叠达到旧参考段的这个比例，就算「覆盖了那条参考答案」。 */
const OVERLAP_RATIO = 0.5;

const overlaps = (aStart, aEnd, bStart, bEnd) => Math.max(0, Math.min(aEnd, bEnd) - Math.max(aStart, bStart));

/**
 * 两套对齐判据，必须同时报 —— 只报一套会得出对自己有利的结论。
 *
 *   cover 方向（召回视角）：新段落**覆盖了**参考段 ≥50%。对小段落有利：
 *     一个 512 字的段整段落进 846 字的参考段里，覆盖率就是 60%，算命中。
 *   inside 方向（精度视角）：新段落**有 ≥50% 落在**参考段里。对大段落不利、
 *     但对「整段都在参考答案里」这件事要求更严。
 *
 * 两套都报，结论只在两套同向时才敢下。
 */
function overlapsReference(chunk, referenceIds, mode) {
  for (const refId of referenceIds) {
    const ref = oldById.get(refId);
    if (!ref) continue;
    if (ref.sourceId !== chunk.sourceId) continue; // ← 命门：charRange 是按来源各自计数的
    const [refStart, refEnd] = ref.charRange;
    if (mode === "center") {
      // 尺寸中立：新段落的中点落在参考段内即算命中。
      // 对基线（分块不变）来说，「中点落在参考段内」等价于「就是那个参考段」——因为旧段落互不重叠。
      // 所以这是唯一一个可以在新旧分块之间公平比较的判据。
      const middle = Math.floor((chunk.start + chunk.end) / 2);
      if (middle >= refStart && middle < refEnd) return true;
      continue;
    }
    const hit = overlaps(chunk.start, chunk.end, refStart, refEnd);
    const denominator = mode === "cover" ? refEnd - refStart : chunk.end - chunk.start;
    if (denominator > 0 && hit / denominator >= OVERLAP_RATIO) return true;
  }
  return false;
}

// ---------- 逐档跑 ----------

const texts = new Map();
for (const sourceId of [...new Set(oldChunks.map((chunk) => chunk.sourceId))]) {
  const list = oldChunks.filter((chunk) => chunk.sourceId === sourceId).sort((a, b) => a.charRange[0] - b.charRange[0]);
  texts.set(sourceId, list.map((chunk) => chunk.text).join(""));
}

/**
 * 重建索引。标题与标签按「重叠最多的那个旧段落」继承 —— 重新切之后没有结构信息了，
 * 用旧段落的结构是这里唯一可行的近似，不这么做就等于把 heading 全丢掉，会低估新参数。
 */
function buildCase({ size, overlap, label, useOld }) {
  if (useOld) {
    return {
      label,
      chunks: oldChunks.map((chunk) => ({
        id: chunk.chunkId,
        sourceId: chunk.sourceId,
        start: chunk.charRange[0],
        end: chunk.charRange[1],
        text: [chunk.heading, chunk.sectionPath, (chunk.tags ?? []).join(" "), chunk.text].filter(Boolean).join(" "),
      })),
    };
  }
  const out = [];
  let id = 0;
  for (const [sourceId, text] of texts) {
    for (const chunk of chunkText(text, { size, overlap })) {
      const spanning = oldChunks
        .filter((old) => old.sourceId === sourceId && overlaps(chunk.start, chunk.end, old.charRange[0], old.charRange[1]) > 0)
        .sort((a, b) => overlaps(chunk.start, chunk.end, b.charRange[0], b.charRange[1]) - overlaps(chunk.start, chunk.end, a.charRange[0], a.charRange[1]));
      const anchor = spanning[0];
      out.push({
        id: `N${String(++id).padStart(5, "0")}`,
        sourceId,
        start: chunk.start,
        end: chunk.end,
        text: [anchor?.heading ?? "", anchor?.sectionPath ?? "", (anchor?.tags ?? []).join(" "), chunk.text].filter(Boolean).join(" "),
      });
    }
  }
  return { label, chunks: out };
}

const cases = [
  { label: "现状（旧分块）", useOld: true },
  { label: "512 / 无重叠", size: 512, overlap: 0 },
  { label: "512 / 15% 重叠（WeKnora 基线）", size: 512, overlap: 77 },
  { label: "846 / 15% 重叠", size: 846, overlap: 127 },
  { label: "1024 / 15% 重叠", size: 1024, overlap: 154 },
  { label: "256 / 15% 重叠", size: 256, overlap: 38 },
];

console.log("分块参数 A/B ｜ 对齐阈值：重叠 ≥50%\n");
console.log("「中心」= 新段落中点落在参考段内（尺寸中立，可与基线公平比）");
console.log("「覆盖」= 新段落装下了参考段的一半（对小段落有利）");
console.log("「落入」= 新段落有一半落在参考段里（对大段落有利）\n");
console.log("分块参数".padEnd(28) + "段落数 中位长  中心@3   覆盖@3  落入@3  中心@10");
console.log("-".repeat(80));
for (const testCase of cases) {
  const built = buildCase(testCase);
  const index = buildBm25(built.chunks.map((chunk) => ({ id: chunk.id, text: chunk.text, meta: {} })));
  const byId = new Map(built.chunks.map((chunk) => [chunk.id, chunk]));
  const lengths = built.chunks.map((chunk) => chunk.text.length).sort((a, b) => a - b);
  const score = { center: 0, cover: 0, inside: 0, center10: 0 };
  for (const question of answerable) {
    const ranked = bm25Search(index, expandedQuery(question.question), built.chunks.length);
    const refs = question.referenceChunks;
    const top3 = ranked.slice(0, 3).map((row) => byId.get(row.id)).filter(Boolean);
    const top10 = ranked.slice(0, 10).map((row) => byId.get(row.id)).filter(Boolean);
    if (top3.some((chunk) => overlapsReference(chunk, refs, "center"))) score.center += 1;
    if (top3.some((chunk) => overlapsReference(chunk, refs, "cover"))) score.cover += 1;
    if (top3.some((chunk) => overlapsReference(chunk, refs, "inside"))) score.inside += 1;
    if (top10.some((chunk) => overlapsReference(chunk, refs, "center"))) score.center10 += 1;
  }
  const n = answerable.length;
  const pad = (v) => `${String(v).padStart(2)}/${n}`;
  console.log(
    built.label.padEnd(24) +
      `${String(built.chunks.length).padStart(5)} ${String(lengths[Math.floor(lengths.length / 2)]).padStart(5)}  ` +
      `${pad(score.center)}   ${pad(score.cover)}  ${pad(score.inside)}  ${pad(score.center10)}`,
  );
}
console.log(`\n注：对齐阈值 OVERLAP_RATIO = ${OVERLAP_RATIO}。改这个值会改变结论 —— 它衡量的是「新段落装进了多少原文」，`);
console.log("   装得越少越容易被判为没覆盖，所以阈值越低对新参数越有利，这里不挑对自己有利的值。");
