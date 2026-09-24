#!/usr/bin/env node
/**
 * 语料与词表的共用读取层（步骤 04–11 都用它）
 *
 * 这里只有两件事：
 *   1) 把 chunks.jsonl 读成内存索引，并提供「引文是否真的落在原文里」的硬校验函数。
 *      设计文档的 L0 铁律是「不当无出处结论的输出方」，所以引文校验必须是
 *      逐字符的子串检查，而不是「大概像」的相似度判断。
 *   2) 读 taxonomy.json（kind / edge type 的唯一词表）与判定 prerequisite 环路。
 */
import { readFileSync } from "node:fs";

import { readJson, readJsonl } from "./log.mjs";
import { CHUNKS_FILE, TAXONOMY_FILE, relPath } from "./paths.mjs";

/** 归一化只做一件事：抹掉所有空白。跨行、跨空段落的引文因此也能匹配上。 */
export function normalizeForMatch(input) {
  return String(input ?? "").replace(/\s+/g, "");
}

export function loadTaxonomy() {
  return readJson(TAXONOMY_FILE);
}

/** chunkId → chunk 的索引；顺带校验 chunkId 唯一。 */
export function loadChunkIndex() {
  const rows = readJsonl(CHUNKS_FILE);
  const index = new Map();
  const duplicated = [];
  for (const row of rows) {
    if (index.has(row.chunkId)) duplicated.push(row.chunkId);
    index.set(row.chunkId, row);
  }
  if (duplicated.length > 0) {
    throw new Error(`${relPath(CHUNKS_FILE)} 里 chunkId 重复：${duplicated.slice(0, 5).join(", ")}`);
  }
  return { rows, index };
}

/**
 * 引文校验。返回 { ok, reason }。
 * quote 太短（< MIN_QUOTE_CHARS）也算不通过 —— 「的」「和」这种片段挂上去等于没出处。
 */
export const MIN_QUOTE_CHARS = 6;

export function checkQuote(chunk, quote) {
  if (!chunk) return { ok: false, reason: "chunkId 不存在" };
  const raw = String(quote ?? "").trim();
  if (raw.length < MIN_QUOTE_CHARS) {
    return { ok: false, reason: `引文过短（< ${MIN_QUOTE_CHARS} 字符）` };
  }
  if (!normalizeForMatch(chunk.text).includes(normalizeForMatch(raw))) {
    return { ok: false, reason: "引文不在该 chunk 原文中" };
  }
  return { ok: true };
}

/** 把 evidence[] 逐条校验，返回 { refs, problems }；refs 是去重后的 chunkId 列表。 */
export function checkEvidence(evidence, chunkIndex, owner) {
  const refs = [];
  const problems = [];
  const list = Array.isArray(evidence) ? evidence : [];
  if (list.length === 0) {
    problems.push(`${owner}：没有任何 evidence（每条结论都必须指向原文 chunk）`);
    return { refs, problems };
  }
  for (const item of list) {
    const chunkId = item?.chunkId;
    const chunk = chunkIndex.get(chunkId);
    const verdict = checkQuote(chunk, item?.quote);
    if (!verdict.ok) {
      const excerpt = String(item?.quote ?? "").replace(/\s+/g, " ").slice(0, 60);
      problems.push(`${owner}：chunk ${chunkId ?? "(缺 chunkId)"} 校验失败（${verdict.reason}）引文「${excerpt}」`);
      continue;
    }
    if (!refs.includes(chunkId)) refs.push(chunkId);
  }
  return { refs, problems };
}

/**
 * prerequisite 环路检测（设计文档 §3.3 硬约束 3：环即失败）。
 * 返回 [] 表示无环，否则返回环上的节点序列（首尾相同）。
 */
export function findPrerequisiteCycle(edges) {
  const adjacency = new Map();
  for (const edge of edges) {
    if (edge.type !== "prerequisite") continue;
    if (!adjacency.has(edge.from)) adjacency.set(edge.from, []);
    adjacency.get(edge.from).push(edge.to);
  }

  const WHITE = 0;
  const GRAY = 1;
  const BLACK = 2;
  const color = new Map();
  const stack = [];
  let cycle = null;

  const visit = (node) => {
    if (cycle) return;
    color.set(node, GRAY);
    stack.push(node);
    for (const next of adjacency.get(node) ?? []) {
      const state = color.get(next) ?? WHITE;
      if (state === GRAY) {
        const at = stack.indexOf(next);
        cycle = [...stack.slice(at), next];
        return;
      }
      if (state === WHITE) {
        visit(next);
        if (cycle) return;
      }
    }
    stack.pop();
    color.set(node, BLACK);
  };

  for (const node of adjacency.keys()) {
    if ((color.get(node) ?? WHITE) === WHITE) {
      visit(node);
      if (cycle) break;
    }
  }
  return cycle ?? [];
}

/** 读 UTF-8 文本产物（步骤 07 编译 Wiki 时要回读原始 html 之外的东西时用得上）。 */
export function readTextFile(file) {
  return readFileSync(file, "utf8");
}
