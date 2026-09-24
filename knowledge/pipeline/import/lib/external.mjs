#!/usr/bin/env node
/**
 * 外部职业库导入层的共用工具。
 *
 * 与 `knowledge/pipeline/lib/graph.mjs` 的分工必须说清楚，否则两套东西迟早混淆：
 *
 *   lib/graph.mjs  —— 自建层（步骤 04–11）的数据层，产物是 `extract/accepted.jsonl`，
 *                     每个节点/边都有 `sourceRefs` 指回我们自己的 chunk（`chunk:` 引用）。
 *                     它们经过人工裁定（06），`annotatedBy` 有 human / llm-reviewed / llm-draft 之分。
 *   lib/external.mjs（本文件）—— 外部层（O*NET / ESCO）的数据层。这一层是**导入**而不是抽取：
 *                     没有 chunk 可指，出处就是标准条目本身（`standardRef`），因此也不写
 *                     `annotatedBy`（没有「谁标注的」这回事，整条都是机器按官方数据换算的）。
 *
 * 两层共用同一份 `taxonomy.json` 的词表（kind 与 edge type 字面量），但不共用产物文件，
 * 也不共用完整性检查 —— 外部层不参与 Wiki 内链、不参与 24 题评测、不画在 1050×820 那张画布上。
 * 这样做的原因见 `knowledge/import/README.md`：千级职业铺不进一张单画布，硬合并只会让
 * 「有原文引文」这条对所有自建节点都成立的性质，被一万个没有引文的外部节点稀释掉。
 */
import { writeFileSync } from "node:fs";

/** 与 `knowledge/pipeline/lib/paths.mjs` 同源，但本层不复用它的常量，避免两层互相牵动。 */
export const ID_PREFIX = { onet: "onet", esco: "esco" };

/**
 * id 片段安全化。节点 id 要能到处当 key、当文件名、进 URL，所以只留 `[a-z0-9.-]`。
 * 全角、空格、斜杠、括号一律折成 `-`，连续 `-` 合并，首尾 `-` 去掉。
 * 不做音译：非拉丁字符（中文、希腊字母）会被整段丢弃，因此调用方在丢弃后必须回落到编号，
 * 否则两个不同条目会撞成同一个 id —— `assertUnique` 就是为此兜底的。
 */
export function slug(text, { maxLength = 64 } = {}) {
  const out = String(text ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9.\-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "");
  return out.length > maxLength ? out.slice(0, maxLength).replace(/-+$/g, "") : out;
}

/**
 * 名字里带 `#` / `+` 的技术名要先展开再 slug，否则 `C` / `C#` / `C++` 会一起折成 `c`，
 * 三个不同的东西合成一个节点 —— 这是实测踩到的坑，不是假想。
 */
export function slugName(text, options) {
  return slug(
    String(text ?? "")
      .replace(/#/g, " sharp ")
      .replace(/\+/g, " plus "),
    options,
  );
}

/** O*NET SOC 编号（`15-2051.00`）直接可用作 id 片段：本来就只含数字、`-`、`.`。 */
export function onetStandardId(soc) {
  return `standard:onet-${String(soc).trim().toLowerCase()}`;
}

/** ESCO 的 uri 形如 `http://data.europa.eu/esco/occupation/<uuid>`，取最后一段当 id 片段。 */
export function escoStandardId(uri) {
  const tail = String(uri).split("/").filter(Boolean).pop() ?? "";
  return `standard:esco-${slug(tail)}`;
}

/** 制表符分隔的官方文本库：首行表头，字段名原样保留（含空格）。 */
export function parseTsv(text) {
  const lines = String(text).replace(/^\uFEFF/, "").split(/\r?\n/).filter((line) => line.length > 0);
  if (lines.length === 0) return { header: [], rows: [] };
  const header = lines[0].split("\t");
  const rows = [];
  for (let i = 1; i < lines.length; i += 1) {
    const cells = lines[i].split("\t");
    const row = {};
    for (let c = 0; c < header.length; c += 1) row[header[c]] = cells[c] ?? "";
    rows.push(row);
  }
  return { header, rows };
}

/**
 * 同一份官方数据里出现两条同 id 记录时必须立刻炸掉，不能默默取后一条。
 * 外部数据的 id 撞车意味着我们在把两个职业当成一个，这是没法在事后发现的错误。
 */
export function assertUnique(records, what) {
  const seen = new Map();
  for (const record of records) {
    const previous = seen.get(record.id);
    if (previous !== undefined) {
      throw new Error(`${what}：id 撞车 ${record.id}（第 ${previous + 1} 条与第 ${seen.size + 1} 条）`);
    }
    seen.set(record.id, seen.size);
  }
  return records;
}

/**
 * 写 JSONL。字段顺序固定：先 recordType 与 id，再按各类型自己的声明顺序落其余字段，
 * 这样两次运行 `diff` 为空 —— 外部层同样要求可复算。
 *
 * @param {Record<string, unknown>[]} records
 * @param {{ node?: string[], edge?: string[] }} fieldOrder 按 recordType 分别声明，节点与边不互相带字段
 */
export function toJsonl(records, fieldOrder = {}) {
  const lines = records.map((record) => {
    const declared = (record.recordType === "edge" ? fieldOrder.edge : fieldOrder.node) ?? [];
    const keys = ["recordType", "id", ...declared.filter((key) => key !== "recordType" && key !== "id")];
    const out = {};
    for (const key of keys) if (record[key] !== undefined) out[key] = record[key];
    for (const key of Object.keys(record)) if (out[key] === undefined) out[key] = record[key];
    return JSON.stringify(out);
  });
  return `${lines.join("\n")}\n`;
}

export function writeText(file, text) {
  writeFileSync(file, text, "utf8");
}

/** 排序：节点按 id，边按 type|from|to —— 与自建层的 sortNodes / sortEdges 同一口径。 */
export function sortRecords(records) {
  return [...records].sort((a, b) => {
    if (a.recordType !== b.recordType) return a.recordType === "node" ? -1 : 1;
    if (a.recordType === "node") return a.id.localeCompare(b.id);
    const ka = `${a.type}|${a.from}|${a.to}`;
    const kb = `${b.type}|${b.from}|${b.to}`;
    return ka.localeCompare(kb);
  });
}

/** 0–1 归一。O*NET 的 IM 是 0–5，除 5 得到与 `requires.importance` 同口径的数。 */
export function round(value, digits = 3) {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}
