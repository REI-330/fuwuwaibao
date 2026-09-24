#!/usr/bin/env node
/**
 * 留出评测集跑分：用「答案不在图的引用集合里」的题目，衡量检索本身的好坏。
 *
 *   node knowledge/pipeline/probe-heldout.mjs
 *
 * **为什么需要它。** 主评测集的 134 段参考答案 100% 落在「图的引用集合」里
 * （答案是人从构图时引用的原文里挑的）。于是任何「优先返回图引用过的段落」的做法
 * 都会拿到虚高的分数 —— 实测能把命中率从 4/18 推到 12/18。那个分数测的是
 * 「系统与这份评测集口径一致」，不是检索能力。
 *
 * 这个探针做两件事：
 *   ① **强制校验**：heldout-questions.json 里每条参考答案都必须是非引用段落，
 *      只要有一条落在引用集合里，直接报错退出 —— 题库被污染了就不能出分。
 *   ② **同一批题上跑两种策略**：正常的查询扩展，以及那个「对引用集合加权」的泄漏策略。
 *      如果泄漏策略在留出集上不再领先，就证明它在主评测集上的优势确实来自同源。
 *
 * 借鉴来源：Tencent/WeKnora 的 `dataset/qa_dataset.py`（采样 → 生成 → 查看三段式）。
 * 差别在于它用 GPT 生成答案、数据来自 MS MARCO；我们**不外接 LLM**，
 * 题目是人读原文手写的，所以只有「校验 + 跑分」两步，没有「生成」那一步。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { bm25Search, buildBm25, TOKENIZER_MODE } from "./lib/graph.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = resolve(HERE, "..", "..");
const KNOWLEDGE = resolve(WORKSPACE_ROOT, "knowledge");

const chunks = readFileSync(resolve(KNOWLEDGE, "chunks", "chunks.jsonl"), "utf8")
  .split("\n")
  .filter((line) => line.trim().length > 0)
  .map((line) => JSON.parse(line));
const byId = new Map(chunks.map((chunk) => [chunk.chunkId, chunk]));
const graph = JSON.parse(readFileSync(resolve(KNOWLEDGE, "exports", "career-graph.json"), "utf8"));

// ---------- 引用集合（主评测集泄漏的根源） ----------

const cited = new Set();
for (const node of graph.nodes) for (const ref of node.sourceRefs ?? []) cited.add(ref);
for (const edge of graph.edges) for (const ref of edge.sourceRefs ?? []) cited.add(ref);

// ---------- 校验题库 ----------

const heldout = JSON.parse(readFileSync(resolve(KNOWLEDGE, "evaluations", "heldout-questions.json"), "utf8"));
const questions = heldout.questions;

const violations = [];
for (const question of questions) {
  for (const ref of question.referenceChunks) {
    if (!byId.has(ref)) violations.push(`${question.questionId}: 参考答案 ${ref} 不存在于语料`);
    else if (cited.has(ref)) violations.push(`${question.questionId}: 参考答案 ${ref} 被图谱引用过 —— 题库被污染`);
  }
}
if (violations.length > 0) {
  console.error("留出集校验失败，拒绝出分：");
  for (const line of violations) console.error(`  · ${line}`);
  process.exit(1);
}
console.log(`留出集校验通过：${questions.length} 题，${questions.reduce((sum, q) => sum + q.referenceChunks.length, 0)} 段参考答案，全部不在引用集合里。`);
console.log(`对照：全语料 ${chunks.length} 段，被图引用 ${cited.size} 段（题目只从剩下的 ${chunks.length - cited.size} 段里出）。`);
console.log(`分词器：${TOKENIZER_MODE}\n`);

// ---------- 检索组件（与线上一致：图邻域 + 节点描述并入查询） ----------

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
const index = buildBm25(chunks.map((chunk) => ({
  id: chunk.chunkId,
  text: [chunk.heading, chunk.sectionPath, (chunk.tags ?? []).join(" "), chunk.text].filter(Boolean).join(" "),
  meta: {},
})));

function hoodOf(question) {
  const hood = new Set(bm25Search(nodeIndex, question, 3).map((hit) => hit.id));
  for (const neighbor of [...hood]) for (const next of adjacency.get(neighbor) ?? []) hood.add(next);
  return hood;
}
function expandedQuery(question, hood) {
  const words = [...hood].map((id) => nodeById.get(id)).filter(Boolean)
    .flatMap((node) => [node.label, ...(node.aliases ?? []), node.description ?? ""]).filter(Boolean);
  return `${question} ${words.join(" ")}`;
}
function citedRefsOf(hood) {
  const refs = new Set();
  for (const id of hood) for (const ref of nodeById.get(id)?.sourceRefs ?? []) refs.add(ref);
  for (const edge of graph.edges) {
    if (!hood.has(edge.from) && !hood.has(edge.to)) continue;
    for (const ref of edge.sourceRefs ?? []) refs.add(ref);
  }
  return refs;
}

/** 尺寸中立判据：检索出的段落中点是否落在参考答案段内。 */
const centerHit = (chunk, refIds) => refIds.some((refId) => {
  const ref = byId.get(refId);
  if (!ref || ref.sourceId !== chunk.sourceId) return false;
  const middle = Math.floor((chunk.charRange[0] + chunk.charRange[1]) / 2);
  return middle >= ref.charRange[0] && middle < ref.charRange[1];
});

// ---------- 两种策略 ----------

const strategies = [
  {
    name: "正常检索（图扩展 + 描述）",
    run: (question) => {
      const hood = hoodOf(question.question);
      return bm25Search(index, expandedQuery(question.question, hood), chunks.length).map((hit) => byId.get(hit.id));
    },
  },
  {
    name: "泄漏策略（对引用集合 ×1.3）",
    run: (question) => {
      const hood = hoodOf(question.question);
      const refs = citedRefsOf(hood);
      return bm25Search(index, expandedQuery(question.question, hood), chunks.length)
        .map((hit) => ({ ...hit, score: refs.has(hit.id) ? hit.score * 1.3 : hit.score }))
        .sort((a, b) => b.score - a.score || a.id.localeCompare(b.id))
        .map((hit) => byId.get(hit.id));
    },
  },
];

console.log("策略".padEnd(30) + "精确@3   中心@3   中心@10");
console.log("-".repeat(58));
for (const strategy of strategies) {
  let exact3 = 0;
  let center3 = 0;
  let center10 = 0;
  for (const question of questions) {
    const ranked = strategy.run(question).filter(Boolean);
    const refs = new Set(question.referenceChunks);
    if (ranked.slice(0, 3).some((chunk) => refs.has(chunk.chunkId))) exact3 += 1;
    if (ranked.slice(0, 3).some((chunk) => centerHit(chunk, question.referenceChunks))) center3 += 1;
    if (ranked.slice(0, 10).some((chunk) => centerHit(chunk, question.referenceChunks))) center10 += 1;
  }
  const pad = (v) => `${String(v).padStart(2)}/${questions.length}`;
  console.log(strategy.name.padEnd(26) + `${pad(exact3)}    ${pad(center3)}    ${pad(center10)}`);
}

console.log("\n注：留出集只有 " + questions.length + " 题，样本很小，只能当方向性信号；");
console.log("   出题人是读原文手写的，不是从图里反推的 —— 这是它与主评测集最大的区别。");
