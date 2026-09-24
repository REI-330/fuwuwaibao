#!/usr/bin/env node
/**
 * 导入《中华人民共和国职业分类大典》职业清单 → `knowledge/import/build/dadian.jsonl`
 *
 *   node knowledge/pipeline/import/import-dadian.mjs
 *
 * 输入是 `fetch-osta-dadian.mjs` 抓下来的原始 JSON（抓取与换算分开：抓一次，换算可重跑）：
 *   raw/osta-dadian/tree.json      四级分类树（大类 → 中类 → 小类）
 *   raw/osta-dadian/careers.json   450 个小类下的 1,676 个职业
 *
 * 产出：每个职业一个 `standard` 节点。**不造边** —— 这一版只把「职业分类」接进来，
 * 「这个职业要求什么技能」要等人社部那 702 份国家职业技能标准（PDF）解析完才有，
 * 那一步没做完之前硬编边就等于编数据。
 *
 * 三个刻意的处理：
 *   1. 名称末尾的 `S` / `L` 是官方标注符号（见 README），从 label 里剥掉存进 `dadianMarkers`，
 *      因为「嵌入式系统设计工程技术人员S」不是一个职业的名字，多一个 S 会污染检索。
 *   2. 分类层级不做成节点，只写成 `categoryPath` 字段。职业编号 `1-01-00-01` 本身就把
 *      大类/中类/小类全编码进去了，为复述编号再加 537 个节点和一种新边类型不划算。
 *   3. 工种（3,053 个）只记数量，不建节点。它们是同一职业下的具体分工，
 *      属于「别名」而不是「另一个职业」。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { assertUnique, sortRecords, toJsonl } from "./lib/external.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = resolve(HERE, "..", "..", "..");
const RAW_DIR = resolve(WORKSPACE_ROOT, "knowledge", "import", "raw", "osta-dadian");
const BUILD_DIR = resolve(WORKSPACE_ROOT, "knowledge", "import", "build");

const SOURCE_ID = "cn-dadian";
const SOURCE_NAME = "中华人民共和国职业分类大典（人力资源和社会保障部 · 技能人才评价工作网 职业分类大典系统）";
const SOURCE_PAGE = "https://www.osta.org.cn/career";

for (const name of ["tree.json", "careers.json"]) {
  if (!existsSync(resolve(RAW_DIR, name))) {
    throw new Error(`缺少 ${RAW_DIR}\\${name}：请先跑 node knowledge/pipeline/import/fetch-osta-dadian.mjs`);
  }
}

const treeFile = JSON.parse(readFileSync(resolve(RAW_DIR, "tree.json"), "utf8"));
const careersFile = JSON.parse(readFileSync(resolve(RAW_DIR, "careers.json"), "utf8"));
const VERSION_ID = careersFile.versionId;

/** 大典名称末尾的官方标注：S / L（也可能同时出现，如 `L/S`）。只记录，不解释。 */
const MARKER_RE = /([SL](?:\/[SL])*)\s*$/;

/** code → 各级名称。层级从 code 前缀推：`1-01-00-01` 的大类是 `1`、中类是 `1-01`、小类是 `1-01-00`。 */
const largeNameByCode = new Map();
const middleNameByCode = new Map();
const smallNameByCode = new Map();
const walkTree = (node, depth) => {
  if (depth === 0) largeNameByCode.set(node.careerCode, node.careerName);
  if (depth === 1) middleNameByCode.set(node.careerCode, node.careerName);
  if (depth === 2) smallNameByCode.set(node.careerCode, node.careerName);
  for (const child of node.children ?? []) walkTree(child, depth + 1);
};
for (const node of treeFile.body) walkTree(node, 0);

const nodes = [];
const markerTally = {};
for (const group of careersFile.bySmallCategory) {
  for (const career of group.careers) {
    const code = String(career.code).trim();
    if (code.split("-").length !== 4) continue;
    const rawName = String(career.name).trim();
    const markerMatch = rawName.match(MARKER_RE);
    const markers = markerMatch ? markerMatch[1].split("/") : [];
    const label = markerMatch ? rawName.slice(0, markerMatch.index).trim() : rawName;
    for (const marker of markers) markerTally[marker] = (markerTally[marker] ?? 0) + 1;

    const parts = code.split("-");
    nodes.push({
      recordType: "node",
      id: `standard:cn-${code}`,
      kind: "standard",
      label,
      // 大典没有给每个职业写职责描述（那是 702 份国家职业技能标准里的事），不编。
      categoryPath: {
        large: `${parts[0]} ${largeNameByCode.get(parts[0]) ?? ""}`.trim(),
        middle: `${parts.slice(0, 2).join("-")} ${middleNameByCode.get(parts.slice(0, 2).join("-")) ?? ""}`.trim(),
        small: `${parts.slice(0, 3).join("-")} ${smallNameByCode.get(group.smallCode) ?? group.smallName ?? ""}`.trim(),
      },
      dadianMarkers: markers.length > 0 ? markers : undefined,
      workNum: career.workNum || 0,
      standardRef: code,
      standardScheme: "cn-dadian",
      standardVersion: `osta-versionId-${VERSION_ID}`,
      sourceId: SOURCE_ID,
      layer: "external",
    });
  }
}

const unique = assertUnique(sortRecords(nodes), "大典职业节点");
unique.forEach((node, index) => {
  node.externalIndex = index + 1;
});

const NODE_FIELDS = [
  "recordType", "id", "kind", "label", "categoryPath", "dadianMarkers", "workNum",
  "standardRef", "standardScheme", "standardVersion", "sourceId", "layer", "externalIndex",
];

mkdirSync(BUILD_DIR, { recursive: true });
const jsonl = toJsonl(unique, { node: NODE_FIELDS });
writeFileSync(resolve(BUILD_DIR, "dadian.jsonl"), jsonl, "utf8");

const byLarge = {};
for (const node of unique) {
  const large = node.categoryPath.large;
  byLarge[large] = (byLarge[large] ?? 0) + 1;
}

const summary = {
  schema: "career-graph-external-import/v1",
  library: "中华人民共和国职业分类大典",
  version: `osta-versionId-${VERSION_ID}`,
  sourceId: SOURCE_ID,
  sourceName: SOURCE_NAME,
  sourcePage: SOURCE_PAGE,
  rawDir: "knowledge/import/raw/osta-dadian",
  output: "knowledge/import/build/dadian.jsonl",
  note: "职业在第四级（编号形如 1-01-00-01）。本版只导入职业节点，不造边：职业要求什么技能要等 702 份国家职业技能标准解析完。",
  counts: { nodes: unique.length, edges: 0, byLargeCategory: byLarge, works: unique.reduce((sum, node) => sum + node.workNum, 0) },
  markers: {
    tally: markerTally,
    note: "职业名称末尾的 `S` / `L` 是大典的官方标注符号，已从 label 剥离并记进 dadianMarkers；本层不据此做任何图上的判断。一个名字可以同时带两个（如 `L/S`），此时 S 与 L 各计一次，所以两个数字相加大于带标注的节点数（223）。实测：只标 L 115 个、只标 S 84 个、两个都标 24 个。",
  },
  notImported: ["工种（3,053 个，只记数量，是同一职业下的分工而非另一个职业）", "702 份国家职业技能标准（PDF，待解析）"],
};
writeFileSync(resolve(BUILD_DIR, "dadian.summary.json"), `${JSON.stringify(summary, null, 2)}\n`, "utf8");

console.log(`职业分类大典导入完成 → knowledge/import/build/dadian.jsonl`);
console.log(`  节点 ${unique.length}（standard），工种 ${summary.counts.works}，无新增边`);
for (const [name, count] of Object.entries(byLarge)) console.log(`    ${String(count).padStart(5)}  ${name}`);
console.log(`  标注符号：${Object.entries(markerTally).map(([k, v]) => `${k} ${v}`).join("，")}`);
