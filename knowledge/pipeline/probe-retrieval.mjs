#!/usr/bin/env node
/**
 * 检索方案探针：把几类「提高命中率」的做法放在同一套 18 题上比。
 *
 *   node knowledge/pipeline/probe-retrieval.mjs
 *
 * 为什么单独一个脚本、不进流水线：这是**选型实验**，不是生产步骤。
 * 候选方案谁好谁坏要用数据说话，但实验代码不该混进交付链路 ——
 * 测出结论之后再决定把哪一个实现进 `lib/graph.mjs`。
 *
 * 三件要说清楚的事：
 *
 * 1. **判据不止一个。** 官方口径是「参考答案的 chunk 精确落进前 3 名」，这个口径偏严：
 *    参考答案动辄 7–9 段、跨 3–5 份文档，而只取 3 名。这里同时报四个：
 *      · 段落级@3 —— 官方口径，最严
 *      · 段落级@5 / @10 —— 放宽名次，用来判断「是不是口径卡太死」
 *      · 来源级@3 —— 前 3 名里有没有来自正确文档的段落
 *      · 章节级@3 —— 前 3 名所属章节里有没有参考答案所在章节（衡量「找对地方但切错了段」）
 *
 * 2. **对照必须同源。** 所有变体共用同一份 `lib/graph.mjs` 的 BM25 与分词器，
 *    只改被测的那一个变量，否则分不清是谁的贡献。
 *
 * 3. **不写文件、不动产物。** 纯读 + 打印，跑多少次都不影响流水线产物。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { TOKENIZER_MODE, bm25Search, buildBm25, tokenize } from "./lib/graph.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = resolve(HERE, "..", "..");
const KNOWLEDGE = resolve(WORKSPACE_ROOT, "knowledge");

const chunks = readFileSync(resolve(KNOWLEDGE, "chunks", "chunks.jsonl"), "utf8")
  .split("\n")
  .filter((line) => line.trim().length > 0)
  .map((line) => JSON.parse(line));
const questions = JSON.parse(readFileSync(resolve(KNOWLEDGE, "evaluations", "questions.json"), "utf8"));
const list = Array.isArray(questions) ? questions : questions.questions;
const answerable = list.filter((q) => q.answerable);

const byId = new Map(chunks.map((chunk) => [chunk.chunkId, chunk]));
const sourceOf = (chunkId) => String(chunkId).split("#")[0];
/** 章节键：没有 sectionPath 的就退回用 heading，再没有就用来源本身（不要伪造层级）。 */
const sectionOf = (chunkId) => {
  const chunk = byId.get(chunkId);
  if (!chunk) return null;
  return `${chunk.sourceId}|${chunk.sectionPath || chunk.heading || ""}`;
};

// ---------- 索引 ----------

const docText = (chunk) => [chunk.heading, chunk.sectionPath, (chunk.tags ?? []).join(" "), chunk.text].filter(Boolean).join(" ");
/** 字段加权的近似做法：把 heading 与 tags 重复若干份，等价于提高它们的权重（BM25F 的朴素版）。 */
const fieldWeightedText = (chunk, boost) =>
  [chunk.text, ...Array(boost).fill(chunk.heading ?? ""), ...Array(boost).fill((chunk.tags ?? []).join(" "))].filter(Boolean).join(" ");

const docs = chunks.map((chunk) => ({ id: chunk.chunkId, text: docText(chunk), meta: {} }));
const index = buildBm25(docs);

// ---------- 判据 ----------

function evaluate(ranked) {
  const metrics = { exact3: 0, exact5: 0, exact10: 0, source3: 0, section3: 0, bestRanks: [] };
  for (const question of answerable) {
    const ids = ranked(question).map((row) => row.id ?? row.chunkId);
    const refs = new Set(question.referenceChunks);
    const refSources = new Set(question.referenceChunks.map(sourceOf));
    const refSections = new Set(question.referenceChunks.map(sectionOf).filter(Boolean));
    if (ids.slice(0, 3).some((id) => refs.has(id))) metrics.exact3 += 1;
    if (ids.slice(0, 5).some((id) => refs.has(id))) metrics.exact5 += 1;
    if (ids.slice(0, 10).some((id) => refs.has(id))) metrics.exact10 += 1;
    if (ids.slice(0, 3).some((id) => refSources.has(sourceOf(id)))) metrics.source3 += 1;
    if (ids.slice(0, 3).some((id) => refSections.has(sectionOf(id)))) metrics.section3 += 1;
    const rank = ids.findIndex((id) => refs.has(id));
    metrics.bestRanks.push(rank < 0 ? ids.length : rank + 1);
  }
  return metrics;
}

/** 名次分布：看清「差一点」和「差一个数量级」是两种完全不同的病。 */
function rankSummary(ranks) {
  const sorted = [...ranks].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const buckets = { "第 1": 0, "第 2–5": 0, "第 6–20": 0, "第 21–100": 0, "100 名以外或未出现": 0 };
  for (const rank of ranks) {
    if (rank === 1) buckets["第 1"] += 1;
    else if (rank <= 5) buckets["第 2–5"] += 1;
    else if (rank <= 20) buckets["第 6–20"] += 1;
    else if (rank <= 100) buckets["第 21–100"] += 1;
    else buckets["100 名以外或未出现"] += 1;
  }
  return { median, buckets };
}

// ---------- 图邻域查询扩展（复刻流水线 B 档，用来做对照） ----------

const graphExport = JSON.parse(readFileSync(resolve(KNOWLEDGE, "exports", "career-graph.json"), "utf8"));
const nodeById = new Map(graphExport.nodes.map((node) => [node.id, node]));
const adjacency = new Map();
for (const edge of graphExport.edges) {
  if (String(edge.to).startsWith("chunk:")) continue;
  if (!adjacency.has(edge.from)) adjacency.set(edge.from, new Set());
  if (!adjacency.has(edge.to)) adjacency.set(edge.to, new Set());
  adjacency.get(edge.from).add(edge.to);
  adjacency.get(edge.to).add(edge.from);
}
const nodeIndex = buildBm25(graphExport.nodes.map((node) => ({
  id: node.id,
  text: [node.label, ...(node.aliases ?? []), node.description ?? ""].filter(Boolean).join(" "),
  meta: {},
})));

function graphExpandedQuery(question, hops = 1, withDescription = false) {
  let hood = new Set(bm25Search(nodeIndex, question, 3).map((hit) => hit.id));
  for (let depth = 0; depth < hops; depth += 1) {
    const next = new Set(hood);
    for (const id of hood) for (const neighbor of adjacency.get(id) ?? []) next.add(neighbor);
    hood = next;
  }
  const words = [...hood]
    .map((id) => nodeById.get(id))
    .filter(Boolean)
    .flatMap((node) => [node.label, ...(node.aliases ?? []), ...(withDescription && node.description ? [node.description] : [])])
    .filter(Boolean);
  return `${question} ${words.join(" ")}`;
}

// ---------- 候选方案 ----------

/**
 * 每个变体都接收 `limit`，评测时传 `chunks.length`（全量排名）——
 * 这样同一次计算既能算「前 3 名命中」，也能算「参考段的最佳名次」。
 * 只返回前 3 名的话，「排在第 40 名」和「根本没被召回」会看起来一模一样，
 * 而这两种病要开的药完全不同。
 */
const search = (query, limit) => bm25Search(index, query, limit);

function prfExpand(query, limit) {
  const first = search(query, 3);
  const original = new Set(tokenize(query));
  const counts = new Map();
  for (const hit of first) {
    for (const term of new Set(tokenize(byId.get(hit.id)?.text ?? ""))) {
      if (original.has(term) || term.length < 2) continue;
      counts.set(term, (counts.get(term) ?? 0) + 1);
    }
  }
  const expansion = [...counts.entries()]
    .filter(([, count]) => count >= 2) // 至少在 2 段里出现，避免把单篇噪声带进来
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 5)
    .map(([term]) => term);
  return `${query} ${expansion.join(" ")}`;
}

const weightedIndex = () =>
  buildBm25(chunks.map((chunk) => ({ id: chunk.chunkId, text: fieldWeightedText(chunk, 3), meta: {} })));

// ---------- 重排所需的两路信号 ----------

/** 邻域节点引用过的段落：节点与边的 `sourceRefs`。 */
const chunkRefsOfHood = (hood) => {
  const refs = new Set();
  for (const id of hood) {
    const node = nodeById.get(id);
    for (const ref of node?.sourceRefs ?? []) refs.add(ref);
  }
  for (const edge of graphExport.edges) {
    if (!hood.has(edge.from) && !hood.has(edge.to)) continue;
    for (const ref of edge.sourceRefs ?? []) refs.add(ref);
  }
  return refs;
};

/** 已人工审核的 wiki 页引用过的段落。 */
const wikiIndex = JSON.parse(readFileSync(resolve(KNOWLEDGE, "wiki", "index.json"), "utf8"));
const wikiPages = Array.isArray(wikiIndex) ? wikiIndex : wikiIndex.pages ?? wikiIndex.entries ?? [];
const reviewedChunks = new Set();
for (const page of wikiPages) {
  if (page.review !== "reviewed") continue;
  for (const ref of page.sources ?? []) reviewedChunks.add(ref);
}

/**
 * V7 的查询 + 按比例重排。
 *
 * 为什么要按比例：流水线里的 `GRAPH_BONUS = 0.5` 是在 BM25 分数（200–300 量级）上加的绝对值，
 * 占比 0.2%，永远改不了排序 —— 「wiki 加权无增益」这个结论其实是这么来的，不是 wiki 没用。
 * 重排换成乘一个倍数，才是在测同一件事。
 */
function rerank(question, graphFactor, wikiFactor, limit) {
  const hood = new Set(bm25Search(nodeIndex, question, 3).map((hit) => hit.id));
  for (const neighbor of [...hood]) for (const next of adjacency.get(neighbor) ?? []) hood.add(next);
  const query = graphExpandedQuery(question, 1, true);
  const hoodRefs = chunkRefsOfHood(hood);

  return bm25Search(index, query, chunks.length)
    .map((hit) => {
      let score = hit.score;
      if (hoodRefs.has(hit.id)) score *= graphFactor;
      if (wikiFactor !== null && reviewedChunks.has(hit.id)) score *= wikiFactor;
      return { ...hit, score: Math.round(score * 1000) / 1000 };
    })
    .sort((a, b) => b.score - a.score || a.id.localeCompare(b.id))
    .slice(0, limit);
}

const variants = [
  {
    name: "V0 现状（纯 BM25）",
    note: "流水线第 11 步 A 档：只用问题文本检索 chunk。",
    run: (question, limit) => search(question.question, limit),
  },
  {
    name: "V1 图邻域查询扩展",
    note: "线上用的就是它：节点索引定位种子，把一跳邻居的标签与别名并进查询。",
    run: (question, limit) => search(graphExpandedQuery(question.question), limit),
  },
  {
    name: "V2 PRF 伪相关反馈",
    note: "先用原查询取前 3 段，把其中「至少在 2 段出现」的高频词并进查询再检索一次。零依赖的经典查询扩展。",
    run: (question, limit) => search(prfExpand(question.question), limit),
  },
  {
    name: "V3 图扩展 + PRF 叠加",
    note: "两个都能扩召回，叠加看会不会互相打架。",
    run: (question, limit) => search(prfExpand(graphExpandedQuery(question.question)), limit),
  },
  {
    name: "V4 字段加权（heading/tags ×3）",
    note: "把标题与标签重复 3 份再建索引，近似 BM25F 的字段加权。",
    run: (question, limit) => bm25Search(weightedIndex(), question.question, limit),
  },
  {
    name: "V5 字段加权 + 图扩展",
    note: "字段加权与图扩展叠加。",
    run: (question, limit) => bm25Search(weightedIndex(), graphExpandedQuery(question.question), limit),
  },
  {
    name: "V6 图扩展半径 2",
    note: "把邻域从一跳扩到两跳，看更宽的扩展是帮忙还是拖累。",
    run: (question, limit) => search(graphExpandedQuery(question.question, 2), limit),
  },
  {
    name: "V7 图扩展 + 节点描述",
    note: "扩展词除了标签与别名，再加上节点的 description。",
    run: (question, limit) => search(graphExpandedQuery(question.question, 1, true), limit),
  },
  {
    name: "V8 = V7 + 相对加成 ×1.3",
    note: "给「被邻域节点引用过」的段落乘 1.3（相对倍数）。流水线里那个绝对 +0.5 在 200+ 的 BM25 分上等于没有，这里换成比例。",
    run: (question, limit) => rerank(question.question, 1.3, null, limit),
  },
  {
    name: "V9 = V7 + 图加成 ×1.3 + wiki ×1.15",
    note: "再把「已人工审核的 wiki 页引用过」的段落乘 1.15。用来验证「wiki 加权到底有没有用」——之前的绝对 +0.5 根本进不了排序。",
    run: (question, limit) => rerank(question.question, 1.3, 1.15, limit),
  },
];

// ---------- 跑 ----------

console.log(`分词器：${TOKENIZER_MODE} ｜ 语料 ${chunks.length} 段 ｜ 可答题 ${answerable.length} 道\n`);
console.log("方案".padEnd(32) + "段落@3  段落@5  段落@10  来源@3  章节@3   参考段最佳名次(中位)");
console.log("-".repeat(96));
const results = [];
for (const variant of variants) {
  const metrics = evaluate((question) => variant.run(question, chunks.length));
  const { median } = rankSummary(metrics.bestRanks);
  results.push({ variant, metrics });
  const pct = (n) => `${String(n).padStart(2)}/${answerable.length}`;
  console.log(
    variant.name.padEnd(28) +
      `  ${pct(metrics.exact3)}    ${pct(metrics.exact5)}    ${pct(metrics.exact10)}     ${pct(metrics.source3)}   ${pct(metrics.section3)}        第 ${median} 名`,
  );
}
console.log();
for (const { variant } of results) console.log(`· ${variant.name}\n    ${variant.note}`);

/**
 * 同源检查：参考答案有多少落在「图的引用集合」里。
 *
 * 这一步必须有，否则这套评测会骗自己：参考答案是人从被引用的原文里挑的，
 * 而任何「优先返回图引用过的段落」的做法，本质上是在**答案集合内部排序**。
 * 2026-09-15 实测：134 段参考答案 100% 落在引用集合里，18 题全覆盖。
 * 所以凡是带图加成的方案（V8/V9）分数高，**不能直接当作检索能力的提升**，
 * 只能说「系统与这份评测集的共同口径更一致」。
 */
const leak = (() => {
  let covered = 0;
  let total = 0;
  let full = 0;
  for (const question of answerable) {
    const hood = new Set(bm25Search(nodeIndex, question.question, 3).map((hit) => hit.id));
    for (const neighbor of [...hood]) for (const next of adjacency.get(neighbor) ?? []) hood.add(next);
    const refs = chunkRefsOfHood(hood);
    const hit = question.referenceChunks.filter((chunk) => refs.has(chunk)).length;
    covered += hit;
    total += question.referenceChunks.length;
    if (hit === question.referenceChunks.length) full += 1;
  }
  return { covered, total, full };
})();

console.log(`\n同源检查：参考答案 ${leak.total} 段中 ${leak.covered} 段落在「图的引用集合」里（${((leak.covered / leak.total) * 100).toFixed(0)}%），${leak.full}/${answerable.length} 题全覆盖。`);
console.log("  → 参考段落在引用集合里 = 这批题目的答案本来就选自图的引用；带图加成的方案（V8/V9）高分含同源水分，");
console.log("    要判断真实检索能力，需要一个答案不来自构图语料的独立评测集。");
// ---------- 父子块检索实验（借鉴 Tencent/WeKnora 的 parent-child） ----------

/**
 * 为什么父块不能直接用 `sectionPath` 分组：实测 494 段只落在 327 个 section 上，
 * 平均每节 1.5 段 —— 按节合并等于没合并。
 * WeKnora 的父块默认 4096 字符（约为我们子块 846 字的 5 倍），所以这里也按**固定字符窗口**
 * 把同一份来源里位置相邻的若干段并成一个父块。
 */
function buildParents(targetChars) {
  const parents = [];
  let id = 0;
  for (const sourceId of [...new Set(chunks.map((chunk) => chunk.sourceId))].sort()) {
    const list = chunks.filter((chunk) => chunk.sourceId === sourceId).sort((a, b) => a.charRange[0] - b.charRange[0]);
    let current = [];
    for (const chunk of list) {
      current.push(chunk);
      const span = current[current.length - 1].charRange[1] - current[0].charRange[0];
      if (span >= targetChars) {
        parents.push({ parentId: `P${String(++id).padStart(4, "0")}`, sourceId, children: current.map((c) => c.chunkId) });
        current = [];
      }
    }
    if (current.length > 0) parents.push({ parentId: `P${String(++id).padStart(4, "0")}`, sourceId, children: current.map((c) => c.chunkId) });
  }
  return parents;
}

/**
 * 父子块实验。判据口径必须说清楚，否则会得出假结论：
 *   直接比「父块@3 vs 子块@3」是不公平的 —— 一个父块装着好几段，等于一次返回了更多内容。
 *   所以同时报「同等预算的对照」：3 个父块平均装多少子块，就拿子块@同样数量去比。
 *   只有父块结构**超过**这个同等预算的对照，才说明「先小后大」这个结构本身有价值。
 */
function parentChildExperiment(question, parents, targetChars) {
  const query = graphExpandedQuery(question.question, 1, true);
  const childScores = new Map(bm25Search(index, query, chunks.length).map((hit) => [hit.id, hit.score]));

  const scored = parents
    .map((parent) => {
      let best = 0;
      for (const child of parent.children) best = Math.max(best, childScores.get(child) ?? 0);
      return { parent, score: best, size: parent.children.length };
    })
    .filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score || a.parent.parentId.localeCompare(b.parent.parentId));

  const topParents = scored.slice(0, 3);
  const covered = new Set(topParents.flatMap((row) => row.parent.children));
  const budget = topParents.reduce((sum, row) => sum + row.size, 0);
  const topChildren = [...childScores.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  return { covered, budget, sameBudgetChildren: topChildren.slice(0, budget).map(([chunkId]) => chunkId), targetChars };
}

console.log("===== 父子块检索实验 =====");
console.log("对照口径：父块@3 的覆盖集 vs 同样数量的子块@N（预算对齐），以及原始的子块@3\n");
console.log("父块目标大小   父块@3   同等预算子块@N   子块@3   平均每父块子块数");
console.log("-".repeat(78));
for (const targetChars of [2000, 4096, 8000]) {
  const parents = buildParents(targetChars);
  let parentHit = 0;
  let budgetHit = 0;
  let childHit = 0;
  let sizeSum = 0;
  for (const question of answerable) {
    const refs = new Set(question.referenceChunks);
    const { covered, budget, sameBudgetChildren } = parentChildExperiment(question, parents, targetChars);
    if ([...covered].some((id) => refs.has(id))) parentHit += 1;
    if (sameBudgetChildren.some((id) => refs.has(id))) budgetHit += 1;
    const top3 = bm25Search(index, graphExpandedQuery(question.question, 1, true), 3).map((hit) => hit.id);
    if (top3.some((id) => refs.has(id))) childHit += 1;
    sizeSum += budget / 3;
  }
  const n = answerable.length;
  console.log(
    `${String(targetChars).padEnd(14)} ${String(parentHit).padStart(2)}/${n}      ${String(budgetHit).padStart(2)}/${n}            ${String(childHit).padStart(2)}/${n}      ${(sizeSum / n).toFixed(1)}`,
  );
  console.log(`   （该档共 ${parents.length} 个父块）`);
}

console.log("\n参考段最佳名次分布（V0 现状）：");
const { buckets } = rankSummary(results[0].metrics.bestRanks);
for (const [label, count] of Object.entries(buckets)) console.log(`    ${label}: ${count} 题`);
