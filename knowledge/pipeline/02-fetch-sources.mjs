#!/usr/bin/env node
/**
 * 02 抓取与去重
 *
 * 设计文档 §6 第 2 步：抓取与去重 → raw/（按 sha256 判重）。
 * 设计文档 §2.1：sha256 用于去重与版本判定，同一 URL 内容变化视为新版本。
 *
 * 输入：knowledge/sources/sources.json（步骤 01 产出）
 * 输出：
 *   knowledge/raw/<sourceId>.html        原始字节（sha256 就是对它算的）
 *   knowledge/raw/<sourceId>.blocks.json 分块前的有序 block 列表 + charRange + 抓取元数据
 *   knowledge/raw/<sourceId>.txt         由 blocks 拼出的纯文本（chunk 的 charRange 以它为准）
 *   knowledge/raw/manifest.json          逐条抓取清单
 *   knowledge/sources/sourceset.json     sha256 → sourceId 的内容去重表
 *   knowledge/evidence/pipeline-log.json 本步日志
 *
 * 三个真实世界的坑，本步显式处理：
 *   1. 部分文档站返回「200 + 252 字节的 meta refresh 跳转页」（如 ESP-IDF 的 adc.html）。
 *      直接当正文会得到空内容 → 这里跟一跳，并把跳转链记进 manifest。
 *   2. 有些站不带 charset（如 freertos.org 返回 `text/html` 无 charset）。中文站可能是 GBK。
 *      按 content-type → <meta charset> → utf-8 的顺序判定，GB2312/GBK 统一按 GB18030 解码。
 *   3. SPA 站会「200 但没有正文」。这类不静默通过，标 insufficient-text 并计入日志。
 *   4. 剪枝可能剪过头（文档站把正文包在 `wy-grid-for-nav` 这类布局容器里）。所以抽取跑两遍：
 *      先剪样板，正文过薄就用「只丢标签、不剪样板」重跑并取更长者；两遍都薄才判 insufficient-text。
 *
 * 跑法（仓库根目录）：
 *   node knowledge/pipeline/02-fetch-sources.mjs
 */
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";

import { sleep, nowIso, writeJson, readJson, recordStep, logLine, fail } from "./lib/log.mjs";
import { extractTitle, htmlToBlocks, blocksToDocument } from "./lib/html.mjs";
import {
  MANIFEST_FILE,
  RAWSET_FILE,
  RAW_BLOCKS,
  RAW_DIR,
  RAW_HTML,
  RAW_TEXT,
  SOURCES_FILE,
  relPath,
} from "./lib/paths.mjs";

const STEP = "02";
const TITLE = "抓取与去重";
const COMMAND = "node knowledge/pipeline/02-fetch-sources.mjs";

/** 可达性探测时用的同一 UA。中文技术站对空 UA / curl UA 普遍反爬。 */
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

const REQUEST_TIMEOUT_MS = 25000;
const POLITE_DELAY_MS = 250;
const MAX_META_REFRESH_HOPS = 2;

/** 正文过短就判「抓到了壳、没抓到内容」。 */
const MIN_BLOCKS = 4;
const MIN_TEXT_CHARS = 300;

/**
 * 无剪枝回退的采纳门槛（比上面更严）。
 * 无剪枝会把导航、页脚、侧边栏一起收进来，所以只有当它明显更丰富时才采纳，
 * 否则「只剩导航标签的门户首页」会被误判成正常来源（实测 opencv.org.cn 首页 37 block / 411 字符全是菜单）。
 */
const FALLBACK_MIN_BLOCKS = MIN_BLOCKS * 4;
const FALLBACK_MIN_TEXT_CHARS = MIN_TEXT_CHARS * 3;

const META_REFRESH_RE =
  /<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]*content\s*=\s*["'][^"']*?url\s*=\s*([^"'>;\s]+)/i;

function sha256(buffer) {
  return createHash("sha256").update(buffer).digest("hex");
}

function detectCharset(buffer, contentType) {
  const fromHeader = /charset\s*=\s*"?([\w-]+)"?/i.exec(contentType ?? "");
  if (fromHeader) return fromHeader[1].toLowerCase();
  const head = buffer.subarray(0, 4096).toString("latin1");
  const fromMeta = /<meta[^>]+charset\s*=\s*["']?([\w-]+)/i.exec(head);
  if (fromMeta) return fromMeta[1].toLowerCase();
  return "utf-8";
}

function decodeBuffer(buffer, charset) {
  const label = charset === "gb2312" || charset === "gbk" ? "gb18030" : charset;
  try {
    return new TextDecoder(label, { fatal: false }).decode(buffer);
  } catch {
    return new TextDecoder("utf-8", { fatal: false }).decode(buffer);
  }
}

async function fetchOnce(url) {
  const response = await fetch(url, {
    redirect: "follow",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    headers: {
      "user-agent": USER_AGENT,
      accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    },
  });
  const buffer = Buffer.from(await response.arrayBuffer());
  return {
    httpStatus: response.status,
    ok: response.ok,
    finalUrl: response.url || url,
    contentType: response.headers.get("content-type") ?? null,
    buffer,
  };
}

/** 抓一个来源，必要时跟随 meta refresh 一跳。返回「最终正文」的字节与元数据。 */
async function fetchDocument(url) {
  const hops = [];
  let current = url;
  let last = null;

  for (let hop = 0; hop <= MAX_META_REFRESH_HOPS; hop += 1) {
    const result = await fetchOnce(current);
    const charset = detectCharset(result.buffer, result.contentType);
    const html = decodeBuffer(result.buffer, charset);
    const metaRefresh = META_REFRESH_RE.exec(html);
    hops.push({
      url: current,
      finalUrl: result.finalUrl,
      httpStatus: result.httpStatus,
      bytes: result.buffer.length,
      charset,
    });
    last = { ...result, charset, html };

    if (!result.ok || !metaRefresh || hop === MAX_META_REFRESH_HOPS) break;

    let next;
    try {
      next = new URL(metaRefresh[1].trim().replace(/^\?url=/, ""), result.finalUrl).toString();
    } catch {
      break;
    }
    if (next === current) break;
    hops[hops.length - 1].metaRefreshTo = next;
    await sleep(POLITE_DELAY_MS);
    current = next;
  }

  return { hops, ...last };
}

const startedAt = nowIso();

let registry;
try {
  registry = readJson(SOURCES_FILE);
} catch (error) {
  fail(`读不到来源登记表 ${relPath(SOURCES_FILE)}，请先跑步骤 01：${error.message}`);
  process.exit(1);
}

const sources = Array.isArray(registry.sources) ? registry.sources : [];
if (sources.length === 0) {
  fail(`来源登记表里没有 sources[]：${relPath(SOURCES_FILE)}`);
  process.exit(1);
}

const documents = [];
const bySha256 = {};
const failures = [];
let index = 0;

mkdirSync(RAW_DIR, { recursive: true });

for (const source of sources) {
  index += 1;
  if (index > 1) await sleep(POLITE_DELAY_MS);
  process.stdout.write(`[kb] 02 抓取 ${index}/${sources.length} ${source.sourceId} ${source.url}\n`);

  let doc;
  try {
    doc = await fetchDocument(source.url);
  } catch (error) {
    source.status = "fetch-failed";
    source.sha256 = null;
    source.byteSize = 0;
    source.notes = [`${source.notes?.join("；") ?? ""}${source.notes?.length ? "；" : ""}抓取失败：${error.message}`];
    failures.push({ sourceId: source.sourceId, url: source.url, error: String(error.message ?? error) });
    documents.push({
      sourceId: source.sourceId,
      url: source.url,
      httpStatus: null,
      bytes: 0,
      sha256: null,
      blockCount: 0,
      textLength: 0,
      flags: ["fetch-failed"],
      hops: [],
      note: String(error.message ?? error),
    });
    continue;
  }

  const flags = [];
  if (!doc.ok) flags.push(`http-${doc.httpStatus}`);

  const digest = sha256(doc.buffer);

  // 两遍抽取：先按 class/id 剪样板；若正文过薄，再用「只丢标签、不剪样板」重跑一次。
  // 取 block 数与纯文本都更长的那一遍，避免「剪枝把整页正文剪掉」被误判成 insufficient-text。
  const prunedBlocks = doc.ok ? htmlToBlocks(doc.html).blocks : [];
  const prunedDoc = blocksToDocument(prunedBlocks);
  let blocks = prunedBlocks;
  let extractionPass = doc.ok ? "pruned" : "skipped";

  if (doc.ok && (prunedBlocks.length < MIN_BLOCKS || prunedDoc.text.length < MIN_TEXT_CHARS)) {
    const looseBlocks = htmlToBlocks(doc.html, { skipBoilerplate: false }).blocks;
    const looseDoc = blocksToDocument(looseBlocks);
    const richer =
      looseBlocks.length > prunedBlocks.length && looseDoc.text.length > prunedDoc.text.length;
    const substantial =
      looseBlocks.length >= FALLBACK_MIN_BLOCKS && looseDoc.text.length >= FALLBACK_MIN_TEXT_CHARS;
    if (richer && substantial) {
      blocks = looseBlocks;
      extractionPass = "unpruned-fallback";
    }
  }

  const plain = blocksToDocument(blocks);

  if (doc.ok && (blocks.length < MIN_BLOCKS || plain.text.length < MIN_TEXT_CHARS)) {
    flags.push("insufficient-text");
  }

  const existing = doc.ok ? bySha256[digest] : null;
  const duplicateOf = existing ? existing.primary : null;

  source.sha256 = doc.ok ? digest : null;
  source.byteSize = doc.ok ? doc.buffer.length : 0;
  source.charset = doc.charset;
  source.fetchedAt = startedAt;

  if (!doc.ok) {
    source.status = "fetch-failed";
  } else if (duplicateOf) {
    source.status = "duplicate-content";
    source.notes = [
      ...(source.notes ?? []),
      `正文 sha256 与 ${duplicateOf} 相同，复用其 raw 产物，不重复入库。`,
    ];
  } else if (flags.includes("insufficient-text")) {
    source.status = "fetched-thin";
  } else {
    source.status = "fetched";
  }

  if (duplicateOf) {
    existing.sourceIds.push(source.sourceId);
  } else if (doc.ok) {
    bySha256[digest] = { primary: source.sourceId, sourceIds: [source.sourceId] };
    writeFileSync(RAW_HTML(source.sourceId), doc.buffer);
    writeJson(RAW_BLOCKS(source.sourceId), {
      schema: "career-graph-raw-blocks/v1",
      sourceId: source.sourceId,
      url: source.url,
      finalUrl: doc.finalUrl,
      fetchedAt: startedAt,
      httpStatus: doc.httpStatus,
      contentType: doc.contentType,
      charset: doc.charset,
      byteSize: doc.buffer.length,
      sha256: digest,
      title: extractTitle(doc.html),
      hops: doc.hops,
      flags,
      // extractionPass 说明这份 blocks 是哪一遍抽出来的：
      //   pruned            = 按 class/id 剪掉样板后的结果（默认）
      //   unpruned-fallback = 剪枝后正文过薄，改用「只丢标签不剪样板」的结果，取更长者
      extractionPass,
      prunedBlockCount: prunedBlocks.length,
      prunedTextLength: prunedDoc.text.length,
      blockCount: blocks.length,
      textLength: plain.text.length,
      // ranges[i] 与 blocks[i] 一一对应，指向 <sourceId>.txt 里的 [start, end)
      ranges: plain.ranges,
      blocks,
    });
    writeFileSync(RAW_TEXT(source.sourceId), plain.text, "utf8");
  }

  documents.push({
    sourceId: source.sourceId,
    url: source.url,
    finalUrl: doc.finalUrl,
    httpStatus: doc.httpStatus,
    contentType: doc.contentType,
    charset: doc.charset,
    bytes: doc.buffer.length,
    sha256: digest,
    extractionPass,
    blockCount: blocks.length,
    textLength: plain.text.length,
    flags,
    duplicateOf,
    hops: doc.hops,
  });
}

const counts = {
  sources: sources.length,
  fetched: sources.filter((source) => source.status === "fetched").length,
  fetchedThin: sources.filter((source) => source.status === "fetched-thin").length,
  duplicateContent: sources.filter((source) => source.status === "duplicate-content").length,
  fetchFailed: failures.length,
  distinctSha256: Object.keys(bySha256).length,
  metaRefreshFollowed: documents.filter((doc) => (doc.hops ?? []).some((hop) => hop.metaRefreshTo)).length,
  unprunedFallback: documents.filter((doc) => doc.extractionPass === "unpruned-fallback").length,
  totalBytes: documents.reduce((sum, doc) => sum + (doc.bytes ?? 0), 0),
};

registry.generatedAt = startedAt;
registry.counts = { ...registry.counts, ...counts };
writeJson(SOURCES_FILE, registry);

writeJson(MANIFEST_FILE, {
  schema: "career-graph-raw-manifest/v1",
  generatedAt: startedAt,
  counts,
  failures,
  documents,
});

writeJson(RAWSET_FILE, {
  schema: "career-graph-sourceset/v1",
  generatedAt: startedAt,
  note: "内容去重表：同一 sha256 只保留一份 raw 正文，重复来源在 sources.json 里标 duplicate-content。",
  bySha256,
  duplicateSources: documents.filter((doc) => doc.duplicateOf).map((doc) => doc.sourceId),
});

const notes = [
  `抓取 ${sources.length} 条：正常 ${counts.fetched}，正文过薄 ${counts.fetchedThin}，内容重复 ${counts.duplicateContent}，失败 ${counts.fetchFailed}。`,
  `内容去重后不同正文 ${counts.distinctSha256} 份，合计 ${counts.totalBytes} 字节。`,
  `其中 ${counts.metaRefreshFollowed} 条命中「200 但只是 meta refresh 跳转页」，已跟随一跳取真实正文。`,
  `其中 ${counts.unprunedFallback} 条剪枝后正文过薄，已改用无剪枝抽取并取更长结果。`,
  `insufficient-text 判定阈值：blocks < ${MIN_BLOCKS} 或纯文本 < ${MIN_TEXT_CHARS} 字符。`,
  `无剪枝回退采纳门槛（更严）：blocks ≥ ${FALLBACK_MIN_BLOCKS} 且纯文本 ≥ ${FALLBACK_MIN_TEXT_CHARS} 字符，避免把只剩菜单的门户首页当成正常来源。`,
];

recordStep({
  step: STEP,
  title: TITLE,
  command: COMMAND,
  inputs: [SOURCES_FILE],
  outputs: [MANIFEST_FILE, RAWSET_FILE, SOURCES_FILE],
  counts,
  notes,
  startedAt,
});

logLine(`${TITLE}：正常 ${counts.fetched} / 过薄 ${counts.fetchedThin} / 重复 ${counts.duplicateContent} / 失败 ${counts.fetchFailed}（无剪枝回退 ${counts.unprunedFallback}）`);
logLine(`  不同正文 ${counts.distinctSha256} 份，共 ${counts.totalBytes} 字节；清单 ${relPath(MANIFEST_FILE)}`);
if (failures.length > 0) for (const item of failures) logLine(`  失败：${item.sourceId} ${item.error}`);
