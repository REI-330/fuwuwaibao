#!/usr/bin/env node
/**
 * 03 主题分块
 *
 * 设计文档 §6 第 3 步：主题分块 → chunks.jsonl（sectionPath 保留原文层级）。
 * 设计文档 §2.2 规定每条 chunk 的字段：
 *   chunkId, sourceId, heading, sectionPath, charRange, text, tags[], version
 * 以及两条铁律：
 *   - sectionPath 保留原文层级（如 "2 > 2.1"），是「原文位置」可追溯的最小保证。
 *   - 分块不重写、不摘要；tags[] 只放主题词，不放推断结论。
 *
 * 输入：knowledge/raw/<sourceId>.blocks.json + .txt（步骤 02 产出）
 *       knowledge/sources/sources.json（回填 chunkCount）
 * 输出：knowledge/chunks/chunks.jsonl
 *       knowledge/evidence/pipeline-log.json（本步日志）
 *
 * charRange 口径：指向 <sourceId>.txt 的 UTF-16 下标区间 [start, end)，
 * 由步骤 02 的 blocksToDocument() 统一计算。校验方式是硬校验 ——
 * 本步会真的用 text.slice(start, end) 和 chunk.text 比对，不一致直接记账，不静默通过。
 *
 * 跑法（仓库根目录）：
 *   node knowledge/pipeline/03-chunk-topics.mjs
 */
import { existsSync } from "node:fs";

import { nowIso, writeJsonl, writeJson, readJson, recordStep, logLine, fail } from "./lib/log.mjs";
import {
  CHUNKS_FILE,
  RAW_BLOCKS,
  RAW_TEXT,
  SOURCES_FILE,
  relPath,
} from "./lib/paths.mjs";
import { numericPrefix, sectionKey, splitByParagraph, collapseLine } from "./lib/text.mjs";
import { readFileSync } from "node:fs";

const STEP = "03";
const TITLE = "主题分块";
const COMMAND = "node knowledge/pipeline/03-chunk-topics.mjs";

/** 单个 chunk 的字符上限；超长小节按段落边界切开，不切断段落。 */
const MAX_CHUNK_CHARS = 1600;
/** 短于这个长度的片段（多为只有一行导航或一句过渡）不成块。 */
const MIN_CHUNK_CHARS = 40;

/**
 * 按标题层级切小节。
 * 标题块本身作为 section.heading，不重复计入正文；
 * sectionPath 是「祖先短标签」链，短标签优先取编号（"2.1"），无编号则取标题原文。
 */
function buildSections(blocks, ranges) {
  const sections = [];
  const stack = [];
  let current = null;

  blocks.forEach((block, index) => {
    const range = ranges[index] ?? [0, 0];

    if (block.kind === "heading" && block.level > 0) {
      while (stack.length > 0 && stack[stack.length - 1].level >= block.level) stack.pop();
      const short = numericPrefix(block.text) ?? collapseLine(block.text);
      stack.push({ level: block.level, short });
      current = {
        heading: block.text,
        level: block.level,
        sectionPath: stack.map((item) => item.short).join(" > "),
        items: [],
      };
      sections.push(current);
      return;
    }

    if (current === null) {
      current = { heading: "", level: 0, sectionPath: "", items: [] };
      sections.push(current);
    }
    current.items.push({ block, range, index });
  });

  return sections;
}

/** 主题词只来自标题链与来源自带 tags 的字面命中，不做任何推断。 */
function deriveTags(section, text, sourceTags) {
  const fromHeading = section.sectionPath
    .split(" > ")
    .flatMap((part) => part.split(/[\s,，、/|()（）[\]【】]+/))
    .map((token) => token.trim())
    .filter((token) => token.length >= 2 && token.length <= 16 && !/^\d+(\.\d+)*$/.test(token));

  const fromSource = (sourceTags ?? []).filter((tag) => tag && text.includes(tag));

  const merged = [];
  for (const tag of [...fromHeading, ...fromSource]) {
    if (!merged.includes(tag)) merged.push(tag);
  }
  return merged.slice(0, 6);
}

const startedAt = nowIso();

let registry;
try {
  registry = readJson(SOURCES_FILE);
} catch (error) {
  fail(`读不到来源登记表 ${relPath(SOURCES_FILE)}，请先跑步骤 01/02：${error.message}`);
  process.exit(1);
}

const sources = Array.isArray(registry.sources) ? registry.sources : [];
if (sources.length === 0) {
  fail(`来源登记表里没有 sources[]：${relPath(SOURCES_FILE)}`);
  process.exit(1);
}

const rows = [];
const bySource = {};
const skipped = [];
const flagBucket = { emptySection: 0, tooShort: 0, charRangeMismatch: 0, split: 0 };
const usedChunkIds = new Set();
let ordinal = 0;

for (const source of sources) {
  const blocksFile = RAW_BLOCKS(source.sourceId);
  if (!existsSync(blocksFile)) {
    skipped.push({ sourceId: source.sourceId, reason: source.status ?? "no-raw-blocks" });
    source.chunkCount = 0;
    continue;
  }

  const raw = readJson(blocksFile);
  const blocks = Array.isArray(raw.blocks) ? raw.blocks : [];
  const ranges = Array.isArray(raw.ranges) ? raw.ranges : [];
  const docText = existsSync(RAW_TEXT(source.sourceId)) ? readFileSync(RAW_TEXT(source.sourceId), "utf8") : "";

  const sections = buildSections(blocks, ranges);
  let sourceChunks = 0;
  let cursor = 0;

  for (const section of sections) {
    if (section.items.length === 0) {
      flagBucket.emptySection += 1;
      continue;
    }

    ordinal += 1;
    const sectionText = section.items.map((item) => item.block.text).join("\n\n");
    const sectionStart = section.items[0].range[0];
    const sectionEnd = section.items[section.items.length - 1].range[1];

    // 硬校验：blocks 连续时，文档切片必须与小节文本逐字符相等。
    if (docText && docText.slice(sectionStart, sectionEnd) !== sectionText) {
      flagBucket.charRangeMismatch += 1;
    }

    const pieces =
      sectionText.length <= MAX_CHUNK_CHARS ? [sectionText] : splitByParagraph(sectionText, MAX_CHUNK_CHARS);
    if (pieces.length > 1) flagBucket.split += 1;

    const base = `${source.sourceId}#${sectionKey(section.heading, ordinal)}`;

    pieces.forEach((piece, pieceIndex) => {
      const text = piece.trim();
      if (text.length < MIN_CHUNK_CHARS) {
        flagBucket.tooShort += 1;
        return;
      }

      // 在本文档纯文本里定位该片段的真实偏移：charRange 必须能回指原文。
      const found = docText.indexOf(text, cursor);
      const start = found >= 0 ? found : sectionStart;
      if (found >= 0) cursor = found + text.length;
      const end = start + text.length;

      let chunkId = pieceIndex === 0 ? base : `${base}~${pieceIndex + 1}`;
      let attempt = 1;
      while (usedChunkIds.has(chunkId)) {
        attempt += 1;
        chunkId = `${base}${pieceIndex === 0 ? "" : `~${pieceIndex + 1}`}-${attempt}`;
      }
      usedChunkIds.add(chunkId);

      rows.push({
        chunkId,
        sourceId: source.sourceId,
        heading: section.heading,
        sectionPath: section.sectionPath,
        charRange: [start, end],
        text,
        tags: deriveTags(section, text, source.tags),
        version: 1,
      });
      sourceChunks += 1;
    });
  }

  source.chunkCount = sourceChunks;
  if (sourceChunks > 0) source.status = "chunked";
  bySource[source.sourceId] = sourceChunks;
}

rows.sort((a, b) => a.sourceId.localeCompare(b.sourceId) || a.charRange[0] - b.charRange[0]);

writeJsonl(CHUNKS_FILE, rows);

const counts = {
  sources: sources.length,
  sourcesWithChunks: Object.values(bySource).filter((n) => n > 0).length,
  chunks: rows.length,
  splitSections: flagBucket.split,
  skippedSections: flagBucket.emptySection,
  droppedTooShort: flagBucket.tooShort,
  charRangeMismatch: flagBucket.charRangeMismatch,
  totalChars: rows.reduce((sum, row) => sum + row.text.length, 0),
  longestChunk: rows.reduce((max, row) => Math.max(max, row.text.length), 0),
};

registry.generatedAt = startedAt;
registry.counts = { ...registry.counts, ...counts };
registry.bySourceChunks = bySource;
writeJson(SOURCES_FILE, registry);

const notes = [
  `从 ${counts.sourcesWithChunks} 个来源切出 ${counts.chunks} 个 chunk，合计 ${counts.totalChars} 字符，最长 ${counts.longestChunk} 字符。`,
  `超长小节按段落边界切开 ${flagBucket.split} 处；丢弃过短片段 ${flagBucket.tooShort} 个（阈值 ${MIN_CHUNK_CHARS} 字符）。`,
  `charRange 硬校验（docText.slice(start,end) === chunk.text）：不一致 ${flagBucket.charRangeMismatch} 处。`,
  `空小节 ${flagBucket.emptySection} 处（只有标题没有正文），未成块。`,
  skipped.length > 0 ? `${skipped.length} 个来源没有 raw 产物，未参与分块：${skipped.map((s) => s.sourceId).join(", ")}。` : `全部来源都有 raw 产物。`,
];

recordStep({
  step: STEP,
  title: TITLE,
  command: COMMAND,
  inputs: [SOURCES_FILE],
  outputs: [CHUNKS_FILE, SOURCES_FILE],
  counts,
  notes,
  startedAt,
});

logLine(`${TITLE}：${counts.chunks} 个 chunk（${counts.sourcesWithChunks} 个来源），写入 ${relPath(CHUNKS_FILE)}`);
logLine(`  超长切分 ${counts.splitSections} 处，丢弃过短 ${counts.droppedTooShort} 个，charRange 不一致 ${counts.charRangeMismatch} 处`);
for (const [sourceId, count] of Object.entries(bySource)) logLine(`  - ${sourceId}: ${count} chunk`);
