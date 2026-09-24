#!/usr/bin/env node
/**
 * 把《国家职业技能标准》的表格转成技能节点与技能边
 *   → `knowledge/import/build/dadian-skills.jsonl`
 *
 *   node knowledge/pipeline/import/import-dadian-skills.mjs
 *
 * 输入是 `fetch-osta-standards.py` 抽出来的两份产物：
 *   raw/osta-standards/tables.jsonl     工作要求表格逐行（职业功能 / 工作内容 / 技能要求 / 相关知识要求 + 所属等级）
 *   raw/osta-standards/manifest.json    702 份标准的元信息与「基础知识」小节名
 *
 * 这一层做的是**归一化合并**，不是抽取 —— 抽取已经在 Python 侧完成（PDF 表格是那边才解析得干净）。
 *
 * 三个必须说清楚的判断：
 *
 * 1. **技能节点取「工作内容」而不是「技能要求」的每一条。**
 *    「技能要求」是句级描述（「能检查、识别并确认作业环境和工作场所」），702 份标准下会有五万多条，
 *    且几乎每条都绑死在具体工种上（「能检查高空作业机械油位」）。把它们做成节点，
 *    得到的是 5 万个只出现一次的节点，不是技能表。「工作内容」是它上一层的能力条目
 *    （「作业环境识别和安全防护」「设备运行检查」），实测跨职业重复率 56%，合并之后才有共享层。
 *    逐条「技能要求」不丢：存进每个技能节点的 `sampleRequirements`，可回溯、可展示。
 *
 * 2. **等级取该职业首次要求这项能力的等级（多条里最小的那个）。**
 *    「五级/初级工」= 1 …「一级/高级技师」= 5。同一项能力在四级、三级都出现时取 4，
 *    含义是「从这个等级起就得会」。这是原文里的既成事实，不做加权、不编重要度。
 *
 * 3. **不给技能编 domain。** 标准里的「基础知识」小节（机械基础知识、电工与电子基础知识…）
 *    是独立的一层，直接挂成 `domain` 节点；但「哪项工作内容属于哪个知识域」标准里没写，
 *    自己连就是编，所以不连。
 *
 * 确定性：同一份输入跑两次，输出逐字节相同（排序固定、字段顺序固定、不用时间戳）。
 */
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { sortRecords, toJsonl } from "./lib/external.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = resolve(HERE, "..", "..", "..");
const RAW_DIR = resolve(WORKSPACE_ROOT, "knowledge", "import", "raw", "osta-standards");
const BUILD_DIR = resolve(WORKSPACE_ROOT, "knowledge", "import", "build");

const RECORD_DIR = resolve(RAW_DIR, "records");
const TABLE_DIR = resolve(RAW_DIR, "tables");
for (const dir of [RECORD_DIR, TABLE_DIR]) {
  if (!existsSync(dir)) {
    throw new Error(`缺少 ${dir}：请先跑 python knowledge/pipeline/import/fetch-osta-standards.py`);
  }
}

const SOURCE_ID = "cn-osta-standards";
const SOURCE_NAME = "《国家职业技能标准》（人力资源和社会保障部 · 技能人才评价工作网 国家职业标准查询系统）";
const SOURCE_PAGE = "https://www.osta.org.cn/skillStandard";
/** 每个技能节点最多带几条技能要求原文；单条最长多少字。带太多会让节点膨胀，带 0 条则失去可核对性。 */
const MAX_SAMPLES = 3;
const MAX_SAMPLE_CHARS = 240;

/**
 * 直接读「一标准一文件」的目录，不读 manifest.json —— manifest 是抓取脚本**跑完时**才重建的，
 * 而抓取要跑一个多小时。按目录读，解析与合并随时都能跑，抓取中途也能出中间结果。
 * 缺哪份标准的表格文件就按「这份标准没有工作要求表格」处理，不报错（抓取中断时会出现这种情况）。
 */
const standards = readdirSync(RECORD_DIR)
  .filter((name) => name.endsWith(".json"))
  .map((name) => JSON.parse(readFileSync(resolve(RECORD_DIR, name), "utf8")))
  .sort((a, b) => Number(a.standardId) - Number(b.standardId));

const tables = [];
const missingTables = [];
for (const standard of standards) {
  const file = resolve(TABLE_DIR, `${standard.standardId}.jsonl`);
  if (!existsSync(file)) {
    missingTables.push(standard.standardId);
    continue;
  }
  for (const line of readFileSync(file, "utf8").split("\n")) {
    if (line.trim().length > 0) tables.push(JSON.parse(line));
  }
}

/**
 * 清理国家职业技能标准里的单元格文本。
 *
 * 为什么需要这一步：这些 PDF 的文本层把条目的编号（`1.1` / `2.1.1`）排在了**词中间** ——
 * 原文是「1. 1 监 控 / 运行」，按坐标取文本时编号会落到「监控」和「运行」之间，
 * 变成「监控1.1运行」。实测 21,642 行里 13,295 行（61%）有这个问题。
 * 编号是结构标记、不是内容，去掉它反而还原了原意（「监控1.1运行」→「监控运行」）。
 *
 * 顺带处理的：编号会带出多余标点（「元一次设备二次设备运、、行状态」），重复标点收成一个。
 *
 * **做不到的事写在明面上**：PDF 文本层里标点也会被位移，去掉编号后仍可能出现语序错乱。
 * 所以这一层只保证「编号不混进内容」，不保证每个字都回到原位 ——
 * 要逐字还原得回到 PDF 版面重新排版，那是另一件事。
 */
function cleanCn(text) {
  return String(text ?? "")
    .normalize("NFKC")
    .replace(/[\u2217\u203b\u25cf\u25a0]/g, "") // 有些标准用 ∗ 当条目前导符，它会把后面的编号一起带进来
    .replace(/\d+(?:\s*\.\s*\d+)+\.?\s*/g, " ") // 去掉 x.y / x.y.z 形式的条目编号（可能写作「1. 1. 1」）
    .replace(/\d+\.(?=\D)/g, "") // 再去掉单独的「1.」形式（如「1.生活照护」）
    .replace(/[\s\u3000]+/g, "")
    .replace(/([、，。；：])\1+/g, "$1")
    .replace(/^[、，。；：.]+|[、，。；：.]+$/g, "")
    .trim();
}

/**
 * 判断清理后的文本是不是一个能看的名字。
 *
 * 有一批 PDF 的字体编码是坏的：取出来只剩 Latin/ASCII 字符（`!`、`"`、`?@ABC)`、`%$`…）。
 * 国家职业技能标准的条目名都是中文，所以这里要求**至少含一个汉字且长度 ≥2** ——
 * 纯 ASCII 的一律当作解码失败丢掉。丢弃的条数与样例都记进 summary，不静默丢，
 * 真要是误伤了纯英文条目，在样例里一眼能看见。
 */
function isReadableLabel(text) {
  return /[\u4e00-\u9fff]/.test(text) && text.length >= 2;
}

// ---------- 1. 工作内容 → skill 节点；职业功能 → domain 节点 ----------

/** key → { labels: Map<原写法, 次数>, levels: Set, codes: Set, samples: string[], functions: Set } */
const skillAgg = new Map();
const functionAgg = new Map();
const standardCodes = new Set();
let unreadableSkillRows = 0;
let unreadableFunctionRows = 0;
/** 被丢掉的乱码样例（最多留 10 个），写进 summary 供人核对「丢的是不是真乱码」。 */
const unreadableSamples = new Set();
const noteUnreadable = (text) => {
  if (unreadableSamples.size < 10) unreadableSamples.add(text.slice(0, 20));
};

for (const row of tables) {
  const workItem = cleanCn(row.workItem);
  if (workItem && !isReadableLabel(workItem)) {
    unreadableSkillRows += 1;
    noteUnreadable(workItem);
  }
  if (!workItem || !isReadableLabel(workItem)) continue;
  standardCodes.add(row.code);
  if (!skillAgg.has(workItem)) skillAgg.set(workItem, { labels: new Map(), levels: new Set(), codes: new Set(), samples: [], functions: new Set() });
  const entry = skillAgg.get(workItem);
  entry.labels.set(workItem, (entry.labels.get(workItem) ?? 0) + 1);
  if (typeof row.level === "number") entry.levels.add(row.level);
  entry.codes.add(row.code);

  // 存整行的「技能要求」原文，**不拆条**：拆条要靠条目编号当分隔符，而编号在文本层里被位移了
  // （见 cleanCn 的注释），按编号切会切出错位的碎片。宁可给一段完整、可核对的原文，
  // 也不给一堆看起来整齐其实错位的碎片。
  const sample = cleanCn(row.skill).slice(0, MAX_SAMPLE_CHARS);
  if (sample && entry.samples.length < MAX_SAMPLES && !entry.samples.includes(sample)) entry.samples.push(sample);

  // 职业功能（作业准备 / 设备维护 / 质量检验…）是「工作内容」上一层的能力归类，
  // 也是标准自己的组织方式，用它当 domain 让「职业 → 技能 → 能力归类」三层可浏览。
  //
  // 但**别把它当成共享技能表**：实测跨职业复用只有个位数百分比（工作内容约 4%、职业功能约 8%）。
  // 行数看着热闹是另一回事 —— 要求表按五级/四级/三级分别列一遍，同一对 (职业功能, 工作内容)
  // 会被重复计入多次，所以「出现频次高」不等于「多个职业共用」。
  // 结论：这套标准的性质就是逐个职业描述工作活动，不是一份技能分类法。数字照实记，不粉饰。
  const functionName = cleanCn(row.function);
  if (functionName && !isReadableLabel(functionName)) {
    unreadableFunctionRows += 1;
    noteUnreadable(functionName);
  }
  if (!functionName || !isReadableLabel(functionName)) continue;
  entry.functions.add(functionName);
  if (!functionAgg.has(functionName)) functionAgg.set(functionName, { labels: new Map(), codes: new Set() });
  const functionEntry = functionAgg.get(functionName);
  functionEntry.labels.set(functionName, (functionEntry.labels.get(functionName) ?? 0) + 1);
  functionEntry.codes.add(row.code);
}

// ---------- 2. 基础知识小节 → domain 节点 ----------

const domainAgg = new Map();
for (const standard of standards) {
  for (const raw of standard.knowledgeDomains ?? []) {
    const name = cleanCn(raw);
    if (!name || !isReadableLabel(name)) continue;
    if (!domainAgg.has(name)) domainAgg.set(name, { labels: new Map(), codes: new Set() });
    const entry = domainAgg.get(name);
    entry.labels.set(name, (entry.labels.get(name) ?? 0) + 1);
    entry.codes.add(standard.code);
  }
}

/** 取出现次数最多的原写法当 label；并列时按 Unicode 序，保证确定性。 */
function pickLabel(labels) {
  return [...labels.entries()].sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0]))[0][0];
}

const nodes = [];
const skillIdByKey = new Map();
for (const [key, entry] of skillAgg) {
  const id = `skill:cn-${key}`;
  skillIdByKey.set(key, id);
  nodes.push({
    recordType: "node",
    id,
    kind: "skill",
    label: pickLabel(entry.labels),
    description: `国家职业技能标准里的「工作内容」条目，出现在 ${entry.codes.size} 个职业的标准中。`,
    standardRef: `国家职业技能标准 工作内容「${pickLabel(entry.labels)}」`,
    standardScheme: "cn-osta-skill-standard",
    standardVersion: "osta-standards",
    levels: [...entry.levels].sort((a, b) => a - b),
    standardCount: entry.codes.size,
    sampleRequirements: entry.samples,
    sourceId: SOURCE_ID,
    layer: "external",
  });
}

/** 两类 domain 用不同前缀，避免「职业功能」和「基础知识」小节名撞车时合成一个节点。 */
const knowledgeDomainIdByKey = new Map();
for (const [key, entry] of domainAgg) {
  const id = `domain:cn-know-${key}`;
  knowledgeDomainIdByKey.set(key, id);
  nodes.push({
    recordType: "node",
    id,
    kind: "domain",
    domainKind: "knowledge-area",
    label: pickLabel(entry.labels),
    description: `国家职业技能标准「基本要求 · 基础知识」下的小节，出现在 ${entry.codes.size} 个职业的标准中。`,
    standardRef: `国家职业技能标准 基础知识「${pickLabel(entry.labels)}」`,
    standardScheme: "cn-osta-skill-standard",
    standardVersion: "osta-standards",
    standardCount: entry.codes.size,
    sourceId: SOURCE_ID,
    layer: "external",
  });
}

const functionDomainIdByKey = new Map();
for (const [key, entry] of functionAgg) {
  const id = `domain:cn-func-${key}`;
  functionDomainIdByKey.set(key, id);
  nodes.push({
    recordType: "node",
    id,
    kind: "domain",
    domainKind: "occupational-function",
    label: pickLabel(entry.labels),
    description: `国家职业技能标准「工作要求」表里的「职业功能」列 —— 一组工作内容的归类，出现在 ${entry.codes.size} 个职业的标准中。`,
    standardRef: `国家职业技能标准 职业功能「${pickLabel(entry.labels)}」`,
    standardScheme: "cn-osta-skill-standard",
    standardVersion: "osta-standards",
    standardCount: entry.codes.size,
    sourceId: SOURCE_ID,
    layer: "external",
  });
}

// ---------- 3. 边：standard（职业）specifies skill / domain ----------

/** (职业编号, 技能key) → 出现过的最低等级。 */
/**
 * (职业编号, 条目名) → 该职业首次要求它的等级。
 *
 * 这里的过滤条件必须和上面建节点时**完全一致**（同样过 isReadableLabel）。
 * 差一个条件就会生成「指向不存在节点」的边 —— 实测漏了这一条，产出了 19 条 `to` 为 undefined 的边。
 * 所以除了同步过滤，下面组装边时还会对查不到目标的情况直接报错，不再让它悄悄写进文件。
 *
 * 分隔符用 NUL：条目名是中文，不可能含 NUL，用 `|` 反而可能被名字里的字符撞上。
 */
const SEP = "\u0000";
const skillEdgeLevel = new Map();
const domainPairs = new Set();
for (const row of tables) {
  const key = cleanCn(row.workItem);
  if (!key || !isReadableLabel(key) || !row.code) continue;
  const pair = `${row.code}${SEP}${key}`;
  const level = typeof row.level === "number" ? row.level : null;
  const previous = skillEdgeLevel.get(pair);
  if (previous === undefined) skillEdgeLevel.set(pair, level);
  else if (level !== null && (previous === null || level < previous)) skillEdgeLevel.set(pair, level);
}
for (const standard of standards) {
  for (const raw of standard.knowledgeDomains ?? []) {
    const key = cleanCn(raw);
    if (key && isReadableLabel(key)) domainPairs.add(`${standard.code}${SEP}${key}`);
  }
}

/** 查不到目标就报错。外部层宁可整批失败，也不写一条端点是 undefined 的边。 */
function mustResolve(map, key, what, pair) {
  const id = map.get(key);
  if (!id) throw new Error(`${what}查不到节点：${JSON.stringify(key)}（来自 ${pair.replace(SEP, " × ")}）`);
  return id;
}

const edges = [];
for (const [pair, level] of skillEdgeLevel) {
  const [code, key] = pair.split(SEP);
  edges.push({
    recordType: "edge",
    type: "specifies",
    from: `standard:cn-${code}`,
    to: mustResolve(skillIdByKey, key, "技能", pair),
    level: level ?? undefined,
    standardRef: `国家职业技能标准 ${code}`,
    sourceId: SOURCE_ID,
    layer: "external",
  });
}
for (const pair of domainPairs) {
  const [code, key] = pair.split(SEP);
  edges.push({
    recordType: "edge",
    type: "specifies",
    from: `standard:cn-${code}`,
    to: mustResolve(knowledgeDomainIdByKey, key, "基础知识域", pair),
    standardRef: `国家职业技能标准 ${code}`,
    sourceId: SOURCE_ID,
    layer: "external",
  });
}

/**
 * 技能 → 职业功能。这是标准里唯一一条「同一个能力归类被多个职业共用」的关系，
 * 所以用已有的 belongs_to（skill → domain），不新造边类型。
 * 同一条工作内容在不同职业里可能归到不同职业功能下，所以是 (技能, 职业功能) 对，不是技能属性。
 */
for (const [skillKey, entry] of skillAgg) {
  for (const functionKey of entry.functions) {
    edges.push({
      recordType: "edge",
      type: "belongs_to",
      from: mustResolve(skillIdByKey, skillKey, "技能", skillKey),
      to: mustResolve(functionDomainIdByKey, functionKey, "职业功能", `${skillKey} × ${functionKey}`),
      standardRef: `国家职业技能标准 职业功能「${pickLabel(functionAgg.get(functionKey).labels)}」`,
      sourceId: SOURCE_ID,
      layer: "external",
    });
  }
}

// ---------- 4. 落盘 ----------

const sortedNodes = sortRecords(nodes);
sortedNodes.forEach((node, index) => {
  node.externalIndex = index + 1;
});
const sortedEdges = sortRecords(edges);
sortedEdges.forEach((edge, index) => {
  edge.id = `cnstd-e-${String(index + 1).padStart(6, "0")}`;
});

const NODE_FIELDS = [
  "recordType", "id", "kind", "domainKind", "label", "description", "levels", "standardCount", "sampleRequirements",
  "standardRef", "standardScheme", "standardVersion", "sourceId", "layer", "externalIndex",
];
const EDGE_FIELDS = ["recordType", "id", "type", "from", "to", "level", "standardRef", "sourceId", "layer"];

mkdirSync(BUILD_DIR, { recursive: true });
writeFileSync(resolve(BUILD_DIR, "dadian-skills.jsonl"), toJsonl([...sortedNodes, ...sortedEdges], { node: NODE_FIELDS, edge: EDGE_FIELDS }), "utf8");

const byKind = {};
for (const node of sortedNodes) byKind[node.kind] = (byKind[node.kind] ?? 0) + 1;
const byType = {};
for (const edge of sortedEdges) byType[edge.type] = (byType[edge.type] ?? 0) + 1;
const skillNodes = sortedNodes.filter((node) => node.kind === "skill");
const skillsReused = skillNodes.filter((node) => node.standardCount > 1).length;
const functionReuse = sortedNodes.filter((node) => node.domainKind === "occupational-function" && node.standardCount > 1).length;

const summary = {
  schema: "career-graph-external-import/v1",
  library: "国家职业技能标准（工作内容 → 技能）",
  version: "osta-standards",
  sourceId: SOURCE_ID,
  sourceName: SOURCE_NAME,
  sourcePage: SOURCE_PAGE,
  rawDir: "knowledge/import/raw/osta-standards",
  output: "knowledge/import/build/dadian-skills.jsonl",
  note: "技能节点取自表格的「工作内容」列（跨职业按 cleanCn 清理后的名字合并），不是「技能要求」的每一条；每行的技能要求原文整段存在节点的 sampleRequirements 里（不拆条，原因见脚本里 cleanCn 的注释）。边上的 level = 该职业首次要求这项能力的职业技能等级（五级=1 … 一级=5）。职业功能单独成 domain 节点，用 belongs_to 连到技能上。",
  dataQuality: {
    pdfTextLayerIssue: "这些 PDF 的文本层把条目编号排在了词中间（「1. 1 监 控 / 运行」），按坐标取文本时会插进字符串里。cleanCn 负责去掉编号并收拢重复标点，实测清理后 0 个标签残留编号。",
    stillImperfect: "标点与个别字仍可能因文本层位移而语序错乱 —— 这一层只保证「编号不混进内容」，不保证逐字还原。要逐字还原得回到 PDF 版面重新排版。",
    droppedUnreadableRows: `${unreadableSkillRows} 行工作内容、${unreadableFunctionRows} 行职业功能的字体编码是坏的（取出来只剩 ASCII 标点），已丢弃并在 unreadableRows 里计数。`,
  },
  counts: {
    nodes: sortedNodes.length,
    edges: sortedEdges.length,
    byKind,
    byType,
    standards: standards.length,
    missingTableFiles: missingTables.length,
    unreadableRows: {
      skill: unreadableSkillRows,
      function: unreadableFunctionRows,
      samples: [...unreadableSamples],
      note: "字体编码坏掉的 PDF 取出来只剩 Latin/ASCII 字符，这些行被丢弃（要求条目名至少含一个汉字）。样例在此，供核对丢的确实是乱码而不是误伤。",
    },
    tableRows: tables.length,
    standardCodes: standardCodes.size,
    share: {
      skillsAcrossOccupations: skillsReused,
      skillsOnlyInOneOccupation: skillNodes.length - skillsReused,
      functionsAcrossOccupations: functionReuse,
      functionsOnlyInOneOccupation: sortedNodes.filter((node) => node.domainKind === "occupational-function").length - functionReuse,
      note: "跨职业复用率低不是 bug，是这份标准的性质：它逐个职业描述工作活动，不是一份技能分类法。两个数字都列出来，不拿好那个充数。注意「出现次数多」不等于「多个职业共用」——要求表按五级/四级/三级分别列一遍，同一对 (职业功能, 工作内容) 会被重复计入多次。",
    },
  },
  notImported: [
    "「技能要求」逐条不建节点（五万多条且绑死具体工种），只作为技能的 sampleRequirements 保留",
    "「相关知识要求」逐条同样不建节点",
    "「权重表」本次未使用",
  ],
};
writeFileSync(resolve(BUILD_DIR, "dadian-skills.summary.json"), `${JSON.stringify(summary, null, 2)}\n`, "utf8");

console.log("国家职业技能标准解析完成 → knowledge/import/build/dadian-skills.jsonl");
console.log(`  标准 ${standards.length} 份（缺表格文件 ${missingTables.length} 份）｜ 表格行 ${tables.length} ｜ 涉及职业编号 ${standardCodes.size} 个`);
console.log(`  节点 ${sortedNodes.length}：${Object.entries(byKind).map(([k, v]) => `${k} ${v}`).join("，")}`);
console.log(`  边   ${sortedEdges.length}（${Object.entries(byType).map(([k, v]) => `${k} ${v}`).join("，")}）`);
const functionCount = sortedNodes.filter((node) => node.domainKind === "occupational-function").length;
console.log(`  跨职业复用：工作内容 ${skillsReused}/${skillNodes.length}，职业功能 ${functionReuse}/${functionCount}`);
if (unreadableSkillRows + unreadableFunctionRows > 0) {
  console.log(`  丢弃乱码行：工作内容 ${unreadableSkillRows}，职业功能 ${unreadableFunctionRows}（字体编码坏掉，样例见 summary）`);
}
