#!/usr/bin/env node
/**
 * 用仓库既有的 BM25 + 分词器（lib/graph.mjs）跑一档检索，产出与
 * knowledge/eval/runs/*.json 同构的结果文件，供 deepeval 评测消费。
 *
 * 为什么单独写：旧的 results.json 是 5 档消融产物，绑定的是已被删除的旧题集
 * （Q01–Q24）。新题集（questions-v2.json）需要重跑基线，而 BM25 的实现与分词器
 * 必须沿用 lib/graph.mjs —— 自己重写一遍会引入口径漂移，两档就不可比了。
 *
 *   node knowledge/pipeline/bm25-run.mjs \
 *     --questions knowledge/evaluations/questions-v2.json \
 *     --out knowledge/eval/runs/bm25-topk.json --topk 3
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

const questionsPath = arg("questions", resolve(K, "evaluations", "questions-v2.json"));
const outPath = arg("out", resolve(K, "eval", "runs", "bm25-topk.json"));
const topk = Number(arg("topk", "3"));

const chunks = readFileSync(resolve(K, "chunks", "chunks.jsonl"), "utf8")
  .split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));
const byId = new Map(chunks.map((c) => [c.chunkId, c]));

const qdoc = JSON.parse(readFileSync(questionsPath, "utf8"));
const questions = (Array.isArray(qdoc) ? qdoc : qdoc.questions).filter((q) => q.answerable !== false);

// 与 probe-retrieval.mjs 一致的文档文本构成：标题 + 章节路径 + 标签 + 正文
const docText = (c) => [c.heading, c.sectionPath, (c.tags ?? []).join(" "), c.text]
  .filter(Boolean).join(" ");

const index = buildBm25(chunks.map((c) => ({ id: c.chunkId, text: docText(c) })));

const perQuestion = [];
let hits = 0;
for (const q of questions) {
  const ranked = bm25Search(index, q.question, topk);
  const top = ranked.map((h) => ({
    chunkId: h.id,
    score: Number(h.score.toFixed(6)),
    text: (byId.get(h.id)?.text ?? "").slice(0, 2000),
  }));
  const refs = new Set(q.referenceChunks ?? []);
  const hitIds = top.map((t) => t.chunkId).filter((id) => refs.has(id));
  if (hitIds.length) hits += 1;
  perQuestion.push({
    questionId: q.questionId,
    set: q.category ?? "main",
    question: q.question,
    referenceChunks: [...refs].sort(),
    topk: top,
    hit: hitIds.length > 0,
    hits: hitIds,
  });
}

const n = perQuestion.length;
const prec = perQuestion.reduce((s, r) => s + r.hits.length / topk, 0) / (n || 1);
const result = {
  schema: "career-graph-bm25-retrieval/v1",
  generatedAt: new Date().toISOString(),
  retriever: {
    type: "bm25", impl: "knowledge/pipeline/lib/graph.mjs",
    tokenizer: TOKENIZER_MODE, topk, corpus: chunks.length,
  },
  metrics: {
    main: {
      hitRate: `${hits}/${n}`,
      hitRateValue: Number((hits / (n || 1)).toFixed(4)),
      meanPrecisionAtK: Number(prec.toFixed(4)),
    },
  },
  perQuestion,
};

mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, JSON.stringify(result, null, 2), "utf8");
console.log(`BM25（${TOKENIZER_MODE} 分词）: 命中 ${hits}/${n}，平均精确率@${topk} ${prec.toFixed(4)}`);
console.log(`产物：${outPath.replace(ROOT + "/", "").replace(ROOT + "\\", "")}`);
