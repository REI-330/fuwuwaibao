#!/usr/bin/env node
/**
 * chunk 检索小工具（只读，不写产物）
 *
 * 用途：抽取实体/边时，先在这里找到「真的能引用的句子」，再把它抄进 seed.json。
 * 之所以要有这个工具：步骤 04 会做引文硬校验（子串级），凭印象写的引文会被直接打回，
 * 所以取证这一步必须是「先看原文、再抄原话」。
 *
 * 跑法（仓库根目录）：
 *   node knowledge/pipeline/search-chunks.mjs 队列 信号量
 *   node knowledge/pipeline/search-chunks.mjs --source S05 量化 --context 160
 *   node knowledge/pipeline/search-chunks.mjs --sections S16 --limit 40
 *   node knowledge/pipeline/search-chunks.mjs --show S05#s96
 */
import { loadChunkIndex } from "./lib/corpus.mjs";

const argv = process.argv.slice(2);
const keywords = [];
let sourceId = null;
let limit = 12;
let context = 120;
let sectionsOnly = false;
let showChunkId = null;

for (let i = 0; i < argv.length; i += 1) {
  const arg = argv[i];
  if (arg === "--source") sourceId = argv[++i];
  else if (arg === "--limit") limit = Number(argv[++i]) || 12;
  else if (arg === "--context") context = Number(argv[++i]) || 120;
  else if (arg === "--sections") {
    sectionsOnly = true;
    if (/^S\d+$/.test(argv[i + 1] ?? "")) sourceId = argv[++i];
  }
  else if (arg === "--show") showChunkId = argv[++i];
  else keywords.push(arg);
}

const { rows } = loadChunkIndex();

if (showChunkId) {
  const row = rows.find((item) => item.chunkId === showChunkId);
  if (!row) {
    console.error(`找不到 chunk：${showChunkId}`);
    process.exit(1);
  }
  console.log(`chunkId: ${row.chunkId}`);
  console.log(`sourceId: ${row.sourceId}  sectionPath: ${row.sectionPath || "(无)"}`);
  console.log(`tags: ${(row.tags ?? []).join(", ") || "(无)"}`);
  console.log("---");
  console.log(row.text);
  process.exit(0);
}

const scope = sourceId ? rows.filter((row) => row.sourceId === sourceId) : rows;

if (sectionsOnly) {
  const seen = new Set();
  for (const row of scope) {
    const key = `${row.sourceId}|${row.sectionPath}`;
    if (seen.has(key) || !row.sectionPath) continue;
    seen.add(key);
    console.log(`${row.chunkId.padEnd(14)} ${row.sectionPath}`);
    if (seen.size >= limit) break;
  }
  process.exit(0);
}

if (keywords.length === 0) {
  console.error("至少给一个关键词，或用 --sections / --show。");
  process.exit(2);
}

let hits = 0;
for (const row of scope) {
  if (hits >= limit) break;
  for (const keyword of keywords) {
    const at = row.text.indexOf(keyword);
    if (at === -1) continue;
    const start = Math.max(0, at - Math.floor(context / 3));
    const excerpt = row.text.slice(start, start + context).replace(/\s+/g, " ");
    console.log(`${row.chunkId.padEnd(14)} ${row.sourceId}  ${(row.sectionPath || "-").slice(0, 46)}`);
    console.log(`   …${excerpt}…`);
    hits += 1;
    break;
  }
}
if (hits === 0) console.log(`没有命中：${keywords.join(" / ")}${sourceId ? `（限定 ${sourceId}）` : ""}`);
