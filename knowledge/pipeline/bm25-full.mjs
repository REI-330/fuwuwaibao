#!/usr/bin/env node
/**
 * 导出 BM25 的**完整排序**（每题 Top-N），供融合实验使用。
 *
 * 为什么需要：bm25-run.mjs 只输出 Top-3，够算命中率，但做不了融合——
 * 融合（如 RRF）需要各路的完整候选与名次。检索本身仍复用 lib/graph.mjs，
 * 不另写实现，保证与基线同源。
 *
 *   node knowledge/pipeline/bm25-full.mjs --top 50
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { bm25Search, buildBm25, TOKENIZER_MODE } from "./lib/graph.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..", "..");
const K = resolve(ROOT, "knowledge");

function arg(name, dflt) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 ? process.argv[i + 1] : dflt;
}

const topN = Number(arg("top", "50"));
const qPath = arg("questions", resolve(K, "evaluations", "questions-v2.json"));
const out = arg("out", resolve(K, "eval", "runs", "bm25-full.json"));

const chunks = readFileSync(resolve(K, "chunks", "chunks.jsonl"), "utf8")
  .split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));
const docText = (c) => [c.heading, c.sectionPath, (c.tags ?? []).join(" "), c.text]
  .filter(Boolean).join(" ");
const index = buildBm25(chunks.map((c) => ({ id: c.chunkId, text: docText(c) })));

const qdoc = JSON.parse(readFileSync(qPath, "utf8"));
const questions = (Array.isArray(qdoc) ? qdoc : qdoc.questions).filter((q) => q.answerable !== false);

const perQuestion = questions.map((q) => ({
  questionId: q.questionId,
  question: q.question,
  referenceChunks: q.referenceChunks ?? [],
  ranking: bm25Search(index, q.question, topN).map((h, i) => ({
    rank: i + 1, chunkId: h.id, score: Number(h.score.toFixed(6)),
  })),
}));

mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, JSON.stringify({
  schema: "career-graph-bm25-full/v1",
  tokenizer: TOKENIZER_MODE, topN, corpus: chunks.length, perQuestion,
}, null, 2), "utf8");
console.log(`BM25 完整排序已导出（每题 Top-${topN}，分词 ${TOKENIZER_MODE}）`);
