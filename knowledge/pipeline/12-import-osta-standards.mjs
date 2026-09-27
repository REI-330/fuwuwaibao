#!/usr/bin/env node
/**
 * 12 技能标准入库（国家职业技能标准 → 中文语料）
 *
 * 为什么需要这一步：现役语料的「中文叙述密度」只有 22%（中文词项仅占有效词项的 43.4%），
 * 因为技术文档的行大多是代码与 API。国家职业技能标准的正文是**纯中文叙述**且主题
 * 正是「某职业需要什么能力」，中文词项占比 95.5%，是补强分词与分块效果的最佳语料。
 *
 * ## 输入
 *   knowledge/import/raw/osta-standards/tables.jsonl   43,642 行（含 code=职业编码）
 *   knowledge/import/build/dadian.jsonl                职业分类大典（用于按方向筛职业）
 *
 * ## 输出（与步骤 02 同构，因此步骤 03 可直接消费）
 *   knowledge/raw/<sourceId>.html         合成 HTML（sha256 对它算）
 *   knowledge/raw/<sourceId>.blocks.json  用管线自己的 htmlToBlocks 切块
 *   knowledge/raw/<sourceId>.txt          由 blocks 拼出的纯文本
 * 并把该来源并入 knowledge/sources/sources.json（保留既有来源的 sha256，不重跑 01）
 *
 * ## 抽取文本怎么坏的，以及怎么修
 * PDF 表格里「序号列」与「正文列」并排，抽取器按**物理行**读，把序号插进了正文中间，
 * 且每个条目在单元格里折成两行、序号落在该条目**第一行的末尾**。例如：
 *
 *   技能要求原文（PDF 视觉）：  1.1.1 能使用清洁工具清理一般生产区
 *                              1.1.2 能按要求清洗及定置清洁工具
 *   抽取结果：                  能使用清洁工具清【1.1.1】理一般生产区能按要求清洗及定【1.1.2】置清洁工具
 *   按序号切分得到的 pieces：   ["能使用清洁工具清", "理一般生产区能按要求清洗及定", "置清洁工具"]
 *
 * 修复规则（**已按「条目数必须等于序号数」逐行校验**）：
 *   · 技能要求：条目的下一个总是以「能」开头 —— pieces[k] 里第一个「能」之前的部分属于上一条
 *   · 相关知识：条目的下一个以「…知识/方法/规程/要求/标准/…」等收尾词结束 ——
 *     取 pieces[k] 中**最短的、以收尾词结束**的前缀作为上一条的结尾
 * 一条都切不出来时**不猜**：整段保留（宁可边界粗，也不编造切点），并计入日志。
 *
 * 跑法（仓库根目录）：
 *   node knowledge/pipeline/12-import-osta-standards.mjs
 */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

import { nowIso, writeJson, readJson, recordStep, logLine, fail } from "./lib/log.mjs";
import { htmlToBlocks, blocksToDocument } from "./lib/html.mjs";
import { RAW_BLOCKS, RAW_HTML, RAW_TEXT, SOURCES_FILE, relPath } from "./lib/paths.mjs";

const STEP = "12";
const TITLE = "技能标准入库";
const COMMAND = "node knowledge/pipeline/12-import-osta-standards.mjs";

const ROOT = resolve(new URL(".", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"), "..", "..");
const SOURCE_ID = "S26";

/** 按方向筛职业：命中这些关键词的职业才算「IT/电子/自动化」相关。 */
const VOCATION_KEYWORDS = [
  "软件", "计算机", "嵌入式", "人工智能", "智能", "数据", "网络", "测试", "视觉", "程序",
  "信息", "电子", "自动化", "算法", "通信", "芯片", "集成电路", "物联网", "云",
];

const NUMBER_RE = /\d+(?:\.\d+)+/g;
const KNOWLEDGE_ENDINGS = [
  "注意事项", "知识", "方法", "要求", "规程", "标准", "原理", "流程", "要点", "规范",
  "参数", "步骤", "内容", "方式", "措施", "规定", "条件", "说明", "规则", "指标",
  "技能", "能力", "作用", "概念", "特性", "结构", "组成", "工艺", "技巧", "用途",
];

function piecesOf(text) {
  return String(text ?? "")
    .split(NUMBER_RE)
    .map((piece) => piece.trim())
    .filter((piece) => piece.length > 0);
}

/** 下一条技能要求总以「能」开头；返回上一条的结尾位置（找不到返回 -1）。 */
function cutForSkill(piece) {
  const index = piece.indexOf("能");
  return index > 0 ? index : -1;
}

/** 下一条相关知识以收尾词结束；取最短的、以收尾词结束的前缀。 */
function cutForKnowledge(piece) {
  for (let i = 2; i <= piece.length; i += 1) {
    const prefix = piece.slice(0, i);
    if (KNOWLEDGE_ENDINGS.some((ending) => prefix.endsWith(ending))) return i;
  }
  return -1;
}

function recombine(raw, cutFn) {
  const pieces = piecesOf(raw);
  if (pieces.length === 0) return [];
  if (pieces.length === 1) return [pieces[0]];
  const items = [];
  let current = pieces[0];
  for (let k = 1; k < pieces.length; k += 1) {
    const piece = pieces[k];
    const cut = cutFn(piece);
    if (cut > 0) {
      items.push(current + piece.slice(0, cut));
      current = piece.slice(cut);
    } else {
      current += piece;
    }
  }
  items.push(current);
  return items.map((item) => item.trim()).filter(Boolean);
}

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));

const cleanLabel = (value) => String(value ?? "").replace(NUMBER_RE, "").trim();

// ---------------------------------------------------------------- 读输入
const tablesPath = resolve(ROOT, "knowledge/import/raw/osta-standards/tables.jsonl");
const dadianPath = resolve(ROOT, "knowledge/import/build/dadian.jsonl");
if (!existsSync(tablesPath)) fail(`读不到技能标准原始表：${tablesPath}`);
if (!existsSync(dadianPath)) fail(`读不到职业分类大典：${dadianPath}`);

const readJsonl = (path) =>
  readFileSync(path, "utf8").split("\n").filter((line) => line.trim()).map((line) => JSON.parse(line));

const tables = readJsonl(tablesPath);
const occupations = readJsonl(dadianPath).filter((row) => row.recordType === "node");
const vocationName = new Map(
  occupations
    .filter((row) => row.standardRef && VOCATION_KEYWORDS.some((kw) => (row.label ?? "").includes(kw)))
    .map((row) => [row.standardRef, row.label]),
);

const rows = tables.filter((row) => vocationName.has(row.code));
if (rows.length === 0) fail("筛出 0 行：职业关键词与 tables.jsonl 的 code 没匹配上");

// ---------------------------------------------------------------- 重组
let stat = { rows: rows.length, skillItems: 0, knowledgeItems: 0, skillFallback: 0, knowledgeFallback: 0 };
const grouped = new Map(); // code -> function -> workItem -> {skills, knowledge, level}
for (const row of rows) {
  const code = row.code;
  const fn = cleanLabel(row.function) || "未分类";
  const work = cleanLabel(row.workItem) || "未命名工作内容";
  if (!grouped.has(code)) grouped.set(code, new Map());
  const byFn = grouped.get(code);
  if (!byFn.has(fn)) byFn.set(fn, new Map());
  const byWork = byFn.get(fn);
  if (!byWork.has(work)) byWork.set(work, { level: row.level, skills: [], knowledge: [] });
  const bucket = byWork.get(work);

  const skills = recombine(row.skill, cutForSkill);
  const knowledge = recombine(row.knowledge, cutForKnowledge);
  const expected = (String(row.skill ?? "").match(NUMBER_RE) ?? []).length;
  if (expected >= 2 && skills.length !== expected) stat.skillFallback += 1;
  const expectedK = (String(row.knowledge ?? "").match(NUMBER_RE) ?? []).length;
  if (expectedK >= 2 && knowledge.length !== expectedK) stat.knowledgeFallback += 1;

  bucket.skills.push(...skills);
  bucket.knowledge.push(...knowledge);
  stat.skillItems += skills.length;
  stat.knowledgeItems += knowledge.length;
}

// ---------------------------------------------------------------- 合成 HTML
const lines = [
  "<html><head><meta charset=\"utf-8\"><title>国家职业技能标准（IT / 电子 / 自动化类职业）</title></head><body>",
  "<h1>国家职业技能标准：IT / 电子 / 自动化类职业</h1>",
  "<p>本页由国家职业技能标准的「工作要求」表重组而成，来源为 osta.org.cn 公开标准的抽取表。条目原文为纯中文叙述。</p>",
];
for (const [code, byFn] of grouped) {
  lines.push(`<h2>${escapeHtml(vocationName.get(code))}</h2>`);
  lines.push(`<p>职业编码：${code}。</p>`);
  for (const [fn, byWork] of byFn) {
    lines.push(`<h3>职业功能：${escapeHtml(fn)}</h3>`);
    for (const [work, bucket] of byWork) {
      lines.push(`<h4>工作内容：${escapeHtml(work)}（等级 ${bucket.level}）</h4>`);
      for (const item of bucket.skills) lines.push(`<p>技能要求：${escapeHtml(item)}</p>`);
      for (const item of bucket.knowledge) lines.push(`<p>相关知识：${escapeHtml(item)}</p>`);
    }
  }
}
lines.push("</body></html>");
const html = lines.join("\n");

// ---------------------------------------------------------------- 写产物（与 02 同构）
const { blocks } = htmlToBlocks(html);
const { text: docText, ranges } = blocksToDocument(blocks);
const htmlBytes = Buffer.from(html, "utf8");
const sha256 = createHash("sha256").update(htmlBytes).digest("hex");

writeFileSync(RAW_HTML(SOURCE_ID), htmlBytes);
writeFileSync(RAW_TEXT(SOURCE_ID), docText);
writeJson(RAW_BLOCKS(SOURCE_ID), {
  schema: "career-graph-blocks/v1",
  sourceId: SOURCE_ID,
  url: "https://www.osta.org.cn/",
  finalUrl: "https://www.osta.org.cn/",
  fetchedAt: nowIso(),
  httpStatus: 200,
  contentType: "text/html; charset=utf-8",
  charset: "utf-8",
  blockCount: blocks.length,
  extractedBy: `${STEP}-import-osta-standards（合成，非网络抓取）`,
  blocks,
  ranges,
  charRange: [0, docText.length],
});

// ---------------------------------------------------------------- 并入来源登记表
const registry = readJson(SOURCES_FILE);
if (!Array.isArray(registry.sources)) fail(`来源登记表缺 sources[]：${relPath(SOURCES_FILE)}`);
registry.sources = registry.sources.filter((source) => source.sourceId !== SOURCE_ID);
registry.sources.push({
  sourceId: SOURCE_ID,
  title: "国家职业技能标准（IT / 电子 / 自动化类职业，工作要求表）",
  publisher: "人力资源和社会保障部 职业技能鉴定中心（osta.org.cn）",
  url: "https://www.osta.org.cn/",
  publishedAt: null,
  collectedAt: nowIso(),
  license: "public-policy",
  scopeZh: "支持：职业功能、工作内容、技能要求与相关知识（纯中文叙述），用于岗位能力项的权威依据。不支持：具体企业的岗位职责与薪资结论。",
  sha256,
  chunkCount: 0,
  direction: "X-policy",
  tags: ["国家职业技能标准", "职业功能", "技能要求", "相关知识"],
  status: "chunked",
  sourceKind: "synthesized-from-tables",
  note: "由步骤 12 从 import/raw/osta-standards/tables.jsonl 重组生成；序号列与正文列在 PDF 抽取时交错，重组规则见脚本头部注释。",
});
registry.sources.sort((a, b) => a.sourceId.localeCompare(b.sourceId));
writeJson(SOURCES_FILE, registry);

recordStep({
  step: STEP,
  title: TITLE,
  command: COMMAND,
  inputs: [relPath(tablesPath), relPath(dadianPath)],
  outputs: [relPath(RAW_HTML(SOURCE_ID)), relPath(RAW_BLOCKS(SOURCE_ID)), relPath(RAW_TEXT(SOURCE_ID)), relPath(SOURCES_FILE)],
  counts: {
    tableRows: tables.length,
    vocationsMatched: vocationName.size,
    rowsSelected: rows.length,
    vocationsCovered: grouped.size,
    skillItems: stat.skillItems,
    knowledgeItems: stat.knowledgeItems,
    blocks: blocks.length,
  },
  notes: [
    `职业筛选：命中关键词的 ${vocationName.size} 个职业 → tables 命中 ${rows.length} 行，覆盖 ${grouped.size} 个职业`,
    `重组：技能要求 ${stat.skillItems} 条 / 相关知识 ${stat.knowledgeItems} 条`,
    `切点回退（条目数与序号数不符，整段保留不猜）：技能 ${stat.skillFallback} 行 / 知识 ${stat.knowledgeFallback} 行`,
    `产出 sha256 ${sha256.slice(0, 12)}，正文 ${docText.length} 字符`,
  ],
});
logLine(`[kb] 12 技能标准入库：${grouped.size} 个职业 / 技能要求 ${stat.skillItems} 条 / 相关知识 ${stat.knowledgeItems} 条 → ${SOURCE_ID}`);
logLine(`[kb]   切点回退：技能 ${stat.skillFallback} 行、知识 ${stat.knowledgeFallback} 行（整段保留，未编造切点）`);
logLine(`[kb]   正文 ${docText.length} 字符，blocks ${blocks.length} 个`);
