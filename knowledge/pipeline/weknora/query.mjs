#!/usr/bin/env node
/**
 * 查这套 WeKnora 思路搭出来的知识库，确认它真的能用（不只是一个跑分脚本）。
 *
 *   node knowledge/pipeline/weknora/query.mjs "SPI 主机驱动怎么初始化总线"
 *   node knowledge/pipeline/weknora/query.mjs "模型量化" --parent
 *
 * `--parent` 走父子块路径：子块匹配 → 返回父块上下文（WeKnora 的 two-level retrieval）。
 * 不加则只返回命中的子块，便于对比。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { bm25Search, buildBm25 } from "../lib/graph.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = resolve(HERE, "..", "..", "..");
const OUT_DIR = resolve(WORKSPACE_ROOT, "knowledge", "weknora");
const RAW_DIR = resolve(WORKSPACE_ROOT, "knowledge", "raw");

const args = process.argv.slice(2);
const useParent = args.includes("--parent");
const limitFlag = args.indexOf("--limit");
const limit = limitFlag >= 0 ? Number(args[limitFlag + 1]) : 3;
const query = args.filter((arg, index) => !arg.startsWith("--") && !(limitFlag >= 0 && index === limitFlag + 1)).join(" ").trim();

if (!query) {
  console.error('用法：node knowledge/pipeline/weknora/query.mjs "问题" [--parent] [--limit N]');
  process.exit(1);
}

const indexFile = JSON.parse(readFileSync(resolve(OUT_DIR, "index.json"), "utf8"));
const children = readFileSync(resolve(OUT_DIR, "children.jsonl"), "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
const parents = readFileSync(resolve(OUT_DIR, "parents.jsonl"), "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
const childById = new Map(children.map((child) => [child.id, child]));

const texts = new Map();
const loadText = (sourceId) => {
  if (!texts.has(sourceId)) texts.set(sourceId, readFileSync(resolve(RAW_DIR, `${sourceId}.txt`), "utf8"));
  return texts.get(sourceId);
};

const index = buildBm25(indexFile.docs.map((doc) => ({ id: doc.id, text: doc.text, meta: {} })));
const ranked = bm25Search(index, query, children.length);

console.log(`查询：「${query}」｜ 索引 ${indexFile.docs.length} 个子块 ｜ 分词器 ${indexFile.tokenizer}`);
console.log(`模式：${useParent ? "父子块（子块匹配 → 返回父块上下文）" : "只返回子块"}\n`);

if (!useParent) {
  for (const hit of ranked.slice(0, limit)) {
    const child = childById.get(hit.id);
    const text = loadText(child.sourceId).slice(child.charRange[0], child.charRange[1]);
    console.log(`【${hit.score.toFixed(2)}】${hit.id}  ${child.sourceId} [${child.charRange[0]},${child.charRange[1]}]`);
    if (child.contextHeader) console.log(`   面包屑：${child.contextHeader}`);
    console.log(`   ${text.replace(/\s+/g, " ").slice(0, 200)}`);
    console.log();
  }
} else {
  const scoreOf = new Map(ranked.map((hit) => [hit.id, hit.score]));
  const scored = parents
    .map((parent) => ({ parent, score: Math.max(...parent.children.map((id) => scoreOf.get(id) ?? 0)) }))
    .filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score || a.parent.id.localeCompare(b.parent.id));

  for (const { parent, score } of scored.slice(0, limit)) {
    const spans = parent.children.map((id) => childById.get(id)).filter(Boolean);
    const text = spans.map((span) => loadText(span.sourceId).slice(span.charRange[0], span.charRange[1])).join("\n");
    console.log(`【${score.toFixed(2)}】${parent.id}  ${parent.sourceId}  含 ${spans.length} 个子块`);
    console.log(`   ${text.replace(/\s+/g, " ").slice(0, 300)}`);
    console.log();
  }
}
