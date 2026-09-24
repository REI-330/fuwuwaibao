#!/usr/bin/env node
/**
 * 01 来源登记
 *
 * 设计文档 §6 第 1 步：来源登记 → sources.json（sourceId、sha256、scopeZh）。
 * 设计文档 §2.1 规定每条记录的字段：
 *   sourceId, title, publisher, url, publishedAt, collectedAt, license, scopeZh, sha256, chunkCount
 *
 * 输入：knowledge/pipeline/sources.candidates.json（人工挑选、且经过可达性探测的候选）
 * 输出：knowledge/sources/sources.json（登记表）
 * 日志：knowledge/evidence/pipeline-log.json（本步一条记录）
 *
 * 刻意不做的事：
 *   - 不猜 publishedAt。候选里没有可核验日期就留 null，前端会显示“缺少时间信息”。
 *   - 不在这里抓网页。sha256 必须来自真实字节，因此由步骤 02 回填，本步写 null + status。
 *   - 不编造 license。候选里写的是发布方/仓库的许可口径，口径不明者标 community-mirror / vendor-docs。
 *
 * 跑法（仓库根目录）：
 *   node knowledge/pipeline/01-register-sources.mjs
 */
import { nowIso, writeJson, recordStep, logLine, fail } from "./lib/log.mjs";
import { readFileSync } from "node:fs";
import {
  SOURCE_CANDIDATES_FILE,
  SOURCES_FILE,
  relPath,
} from "./lib/paths.mjs";

const STEP = "01";
const TITLE = "来源登记";
const COMMAND = "node knowledge/pipeline/01-register-sources.mjs";

/** 契约里 sources[] 的 10 个字段，顺序照抄设计文档 §2.1。 */
const CONTRACT_FIELDS = [
  "sourceId",
  "title",
  "publisher",
  "url",
  "publishedAt",
  "collectedAt",
  "license",
  "scopeZh",
  "sha256",
  "chunkCount",
];

/** 只在本仓库中间产物里存在的字段，导出（第 11 步）时必须剥掉。 */
const INTERMEDIATE_FIELDS = ["direction", "tags", "status", "byteSize", "charset", "fetchedAt", "notes"];

const SOURCE_ID_RE = /^S\d{2,}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** 去重用的 URL 归一：忽略协议、www.、末尾斜杠与大小写的差异。 */
function normalizeUrl(raw) {
  try {
    const parsed = new URL(raw);
    const host = parsed.host.toLowerCase().replace(/^www\./, "");
    const path = parsed.pathname.replace(/\/+$/, "");
    return `${host}${path}${parsed.search}`.toLowerCase();
  } catch {
    return String(raw).trim().toLowerCase();
  }
}

function requireText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

const startedAt = nowIso();

let candidatesFile;
try {
  candidatesFile = JSON.parse(readFileSync(SOURCE_CANDIDATES_FILE, "utf8"));
} catch (error) {
  fail(`读不到候选来源表 ${relPath(SOURCE_CANDIDATES_FILE)}：${error.message}`);
  process.exit(1);
}

const candidates = Array.isArray(candidatesFile.candidates) ? candidatesFile.candidates : [];
if (candidates.length === 0) {
  fail(`候选来源表为空：${relPath(SOURCE_CANDIDATES_FILE)}`);
  process.exit(1);
}

const collectedAt = startedAt.slice(0, 10);
const seenIds = new Map();
const seenUrls = new Map();
const sources = [];
const rejected = [];
const duplicates = [];

for (const candidate of candidates) {
  const sourceId = String(candidate.sourceId ?? "").trim();
  const url = String(candidate.url ?? "").trim();
  const problems = [];

  if (!SOURCE_ID_RE.test(sourceId)) problems.push("sourceId 必须形如 S01");
  if (!/^https?:\/\//i.test(url)) problems.push("url 必须是 http(s) 绝对地址");
  if (!requireText(candidate.title)) problems.push("缺 title");
  if (!requireText(candidate.publisher)) problems.push("缺 publisher");
  if (!requireText(candidate.license)) problems.push("缺 license");
  if (!requireText(candidate.scopeZh)) problems.push("缺 scopeZh（必须写清支持/不支持哪些结论）");
  if (candidate.publishedAt != null && !DATE_RE.test(String(candidate.publishedAt))) {
    problems.push("publishedAt 只能是 null 或 YYYY-MM-DD");
  }

  if (problems.length > 0) {
    rejected.push({ sourceId: sourceId || "(空)", url, reasons: problems });
    continue;
  }

  const urlKey = normalizeUrl(url);
  if (seenIds.has(sourceId)) {
    duplicates.push({ sourceId, url, duplicateOf: seenIds.get(sourceId), kind: "sourceId" });
    continue;
  }
  if (seenUrls.has(urlKey)) {
    duplicates.push({ sourceId, url, duplicateOf: seenUrls.get(urlKey), kind: "url" });
    continue;
  }
  seenIds.set(sourceId, sourceId);
  seenUrls.set(urlKey, sourceId);

  sources.push({
    sourceId,
    title: String(candidate.title).trim(),
    publisher: String(candidate.publisher).trim(),
    url,
    publishedAt: candidate.publishedAt == null ? null : String(candidate.publishedAt),
    collectedAt,
    license: String(candidate.license).trim(),
    scopeZh: String(candidate.scopeZh).trim(),
    sha256: null, // 由 02 抓取后回填
    chunkCount: 0, // 由 03 分块后回填
    direction: candidate.direction ?? null,
    tags: Array.isArray(candidate.tags) ? candidate.tags.map((tag) => String(tag)) : [],
    status: "registered",
    notes: [],
  });
}

sources.sort((a, b) => a.sourceId.localeCompare(b.sourceId));

const byDirection = {};
for (const source of sources) {
  const key = source.direction ?? "(未分类)";
  byDirection[key] = (byDirection[key] ?? 0) + 1;
}

const missingPublishedAt = sources.filter((source) => source.publishedAt === null).length;

const counts = {
  candidates: candidates.length,
  registered: sources.length,
  rejected: rejected.length,
  duplicates: duplicates.length,
  missingPublishedAt,
};

const outFile = SOURCES_FILE;
const payload = {
  schema: "career-graph-sources/v1",
  generatedAt: startedAt,
  note: "L0 证据层的来源登记表。sha256 / chunkCount 由步骤 02 / 03 回填。status 走向：registered → fetched | fetched-thin | duplicate-content | fetch-failed → chunked。",
  contractFields: CONTRACT_FIELDS,
  intermediateFields: INTERMEDIATE_FIELDS,
  directions: candidatesFile.directions ?? [],
  counts: { ...counts, byDirection },
  rejected,
  duplicates,
  sources,
};

writeJson(outFile, payload);

const notes = [
  `候选 ${candidates.length} 条，登记 ${sources.length} 条，拒绝 ${rejected.length} 条，按 sourceId/url 去重 ${duplicates.length} 条。`,
  `publishedAt 缺失 ${missingPublishedAt}/${sources.length} 条：公开文档站多无明确定版日期，按设计 §2.1 留 null、不猜。`,
  `sha256 全部为 null：字节哈希必须来自真实抓取，由步骤 02 回填。`,
  `非契约字段 ${INTERMEDIATE_FIELDS.join(" / ")} 仅存在于中间产物，导出时由步骤 11 剥除。`,
];

recordStep({
  step: STEP,
  title: TITLE,
  command: COMMAND,
  inputs: [SOURCE_CANDIDATES_FILE],
  outputs: [outFile],
  counts,
  notes,
  startedAt,
});

logLine(`${TITLE}：登记 ${sources.length} 条来源（候选 ${candidates.length}，拒绝 ${rejected.length}），写入 ${relPath(outFile)}`);
for (const [direction, count] of Object.entries(byDirection)) logLine(`  - ${direction}: ${count} 条`);
