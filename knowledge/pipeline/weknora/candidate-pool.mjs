#!/usr/bin/env node
/**
 * M2-4 的第一步：**候选池互补性**测量（自建 BM25 vs WeKnora 思路分块栈）。
 *
 *   node knowledge/pipeline/weknora/candidate-pool.mjs --questions knowledge/evaluations/questions-dev.json \
 *     --bm25 knowledge/eval/runs/af-dev-bm25-full.json
 *
 * ## 为什么先只测候选池
 *
 * M2-4 的完整定义是「两路各出候选 → 合并 → **统一重排**」。重排那一步每题一次 LLM 调用
 * （实测 17–150 s/题），属于最后一公里；而它有没有意义**完全取决于候选池里有没有**
 * 自建路拿不到、但含有参考答案的段。所以第一步只回答：
 *
 *   > 「把 WeKnora 那一路的候选并进来，参考答案进入候选池的比例涨不涨？」
 *
 * 不涨 → 重排（以及整个 M2-4）就没有可吃的空间，不必花那几十分钟。
 * 涨   → 值得再把合并结果交给重排。
 *
 * ## 两套分块怎么对齐（id 体系不变）
 *
 * WeKnora 栈是**另一套分块**（512 字自适应 + 面包屑），它的 `child.id` 不在现役语料里。
 * 这里按**字符区间重叠**把它映回现役的 1,757 段：child 与某 chunk 同来源且 `charRange`
 * 有交集 → 该 child 的名次记到那个 chunk 上（一个 child 可覆盖多段，取最优名次）。
 * 这保住了「参考答案用现役 chunkId 标注」这个口径。
 *
 * 产物：`knowledge/eval/runs/weknora-candidate-pool-<tag>.json`。**不调 LLM、不写图谱。**
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { bm25Search, buildBm25 } from "../lib/graph.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..", "..", "..");
const K = resolve(ROOT, "knowledge");

const arg = (name, dflt) => {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 ? process.argv[i + 1] : dflt;
};

const QFILE = arg("questions", resolve(K, "evaluations", "questions-dev.json"));
const BMFILE = arg("bm25", resolve(K, "eval", "runs", "af-dev-bm25-full.json"));
const TAG = arg("tag", "dev");
const KS = [3, 10, 30];

const readJsonl = (p) => readFileSync(p, "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));
const chunks = readJsonl(resolve(K, "chunks", "chunks.jsonl"));
const children = readJsonl(resolve(K, "weknora", "children.jsonl"));
const indexFile = JSON.parse(readFileSync(resolve(K, "weknora", "index.json"), "utf8"));

const qdoc = JSON.parse(readFileSync(QFILE, "utf8"));
const answerable = qdoc.questions.filter((q) => q.answerable !== false);
const bm = JSON.parse(readFileSync(BMFILE, "utf8"));
const bmRank = new Map(bm.perQuestion.map((x) => [x.questionId, x.ranking.map((r) => r.chunkId)]));

// ---------- child → 现役 chunk 的区间映射 ----------

const chunksBySource = new Map();   // sourceId -> [{chunkId, a, b}]
for (const c of chunks) {
  const src = c.sourceId ?? c.source ?? String(c.chunkId).split("#")[0];
  if (!chunksBySource.has(src)) chunksBySource.set(src, []);
  const [a, b] = c.charRange ?? [0, 0];
  chunksBySource.get(src).push({ chunkId: c.chunkId, a, b });
}
const overlaps = (x, y) => x.a < y.b && y.a < x.b;

const childIndex = new Map(children.map((c) => [c.id, c]));

const childRankToChunks = (rankedChildren) => {
  /** 返回 chunkId 的名次表：同一 child 覆盖的每段都拿到该 child 的名次（取最优）。 */
  const best = new Map();
  rankedChildren.forEach((hit, rank) => {
    const child = childIndex.get(hit.id);
    if (!child) return;
    const [a, b] = child.charRange ?? [0, 0];
    for (const ch of chunksBySource.get(child.sourceId) ?? []) {
      if (overlaps({ a, b }, ch)) {
        const prev = best.get(ch.chunkId);
        if (prev === undefined || rank < prev) best.set(ch.chunkId, rank);
      }
    }
  });
  return [...best.entries()].sort((p, q) => p[1] - q[1]).map(([chunkId]) => chunkId);
};

// ---------- 跑 ----------

const index = buildBm25(indexFile.docs.map((doc) => ({ id: doc.id, text: doc.text })));
console.log(`WeKnora 栈：${indexFile.docs.length} 个子块 / ${children.length} 条 child；现役语料：${chunks.length} 段`);
console.log(`题集：${QFILE.replace(ROOT + "/", "").replace(/\\/g, "/")}（可答 ${answerable.length} 题）\n`);

const rows = [];
const hitAt = (candidates, refs, k) => (candidates.slice(0, k).some((c) => refs.has(c)) ? 1 : 0);
const totals = { self: {}, weknora: {}, union: {} };
for (const k of KS) { totals.self[k] = 0; totals.weknora[k] = 0; totals.union[k] = 0; }

for (const q of answerable) {
  const refs = new Set(q.referenceChunks ?? []);
  const self = bmRank.get(q.questionId) ?? [];
  const ranked = bm25Search(index, q.question, indexFile.docs.length);
  const weknora = childRankToChunks(ranked);
  // union：两路交替（round-robin），保留各自顺序，不做分数融合（那是重排的事）
  const union = [];
  for (let i = 0; i < Math.max(self.length, weknora.length); i += 1) {
    if (i < self.length) union.push(self[i]);
    if (i < weknora.length) union.push(weknora[i]);
  }
  const row = { questionId: q.questionId, layer: q.layer, refs: [...refs] };
  for (const k of KS) {
    totals.self[k] += hitAt(self, refs, k);
    totals.weknora[k] += hitAt(weknora, refs, k);
    totals.union[k] += hitAt(union, refs, k);
  }
  row.selfTop10 = self.slice(0, 10); row.weknoraTop10 = weknora.slice(0, 10);
  rows.push(row);
}

const n = answerable.length;
console.log("候选池召回（参考答案进入候选池的比例）");
console.log("  K    自建 BM25    WeKnora 栈    合并");
for (const k of KS) {
  const f = (x) => `${String(x).padStart(3)}/${n} (${(x / n * 100).toFixed(1)}%)`;
  console.log(`  ${String(k).padEnd(4)} ${f(totals.self[k]).padEnd(13)} ${f(totals.weknora[k]).padEnd(13)} ${f(totals.union[k])}`);
}

// 只看「自建路漏掉、WeKnora 路捞到」的题 —— 那才是 M2-4 的增量
const rescued = answerable.filter((q) => {
  const refs = new Set(q.referenceChunks ?? []);
  const self = bmRank.get(q.questionId) ?? [];
  const weknora = childRankToChunks(bm25Search(index, q.question, indexFile.docs.length));
  return !self.slice(0, 30).some((c) => refs.has(c)) && weknora.slice(0, 30).some((c) => refs.has(c));
});
console.log(`\n自建路 Top-30 漏掉、WeKnora 路 Top-30 捞到的题：${rescued.length} 道`
  + (rescued.length ? `（${rescued.map((q) => q.questionId).join("、")}）` : ""));

const out = resolve(K, "eval", "runs", `weknora-candidate-pool-${TAG}.json`);
writeFileSync(out, JSON.stringify({
  schema: "career-graph-weknora-candidate-pool/v1",
  generatedAt: new Date().toISOString(),
  questions: QFILE.replace(ROOT, "").replace(/\\/g, "/"),
  bm25: BMFILE.replace(ROOT, "").replace(/\\/g, "/"),
  n, ks: KS,
  recall: Object.fromEntries(KS.map((k) => [k, { self: totals.self[k], weknora: totals.weknora[k], union: totals.union[k] }])),
  rescuedByWeknora: rescued.map((q) => q.questionId),
  perQuestion: rows,
}, null, 2), "utf8");
console.log(`\n产物：${out.replace(ROOT, "").replace(/\\/g, "/")}`);
