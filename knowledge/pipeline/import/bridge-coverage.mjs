#!/usr/bin/env node
/**
 * 桥接覆盖率与「能不能机械地加桥」的证据（M2-6 的机器那一半）。
 *
 *   node knowledge/pipeline/import/bridge-coverage.mjs
 *
 * ## 这个脚本回答两个问题
 *
 * 1. **现在桥接覆盖多少？** —— 9 条 `aligned_with` 桥边把自建层的 4 个职业接到外部标准条目上；
 *    外部层自己有 21,987 节点 / 108,662 条边（O*NET + 大典 + 国标），但**没有一条接回自建层**。
 * 2. **能不能靠一条机械规则把覆盖提上去？** —— 本脚本把「按名字对齐」这条路走到头：
 *    把自建层 63 个节点的 label + aliases 全部归一化，去和大典 1,676 个职业、
 *    14,137 个技能、4,850 个职业功能、O*NET 237 个条目标签做精确匹配。
 *
 * **结论（实测，不是推测）**：大典侧 **0 命中**，O*NET 侧只有 **1 条**（`tool:selenium`
 * ↔ `Selenium`）。两套词表是不相交的 —— 自建层是「SPI 总线事务设计」这种细粒度工程技能，
 * 外部层是「(二)孵化操作」这种按职业写的工作内容条目。
 *
 * ## 所以 M2-6 卡在哪
 *
 * `knowledge/import/README.md` §8 写得很清楚：
 *
 *   > **不做跨库合并** …… 两库各自成节点，需要合并时靠 `aligned_with` 边显式声明，
 *   >   **且必须由人写下 `alignment`（full / partial / related）与理由**。
 *
 * 也就是说，提高桥接覆盖**只可能是人力活**：机器能做的只有「把候选范围从 1,676 缩小」，
 * 不能替人写下 `alignment`。这与 M2-1 的「不代签」是同一类门禁 —— 本脚本**不产出任何边**，
 * 只产出证据与上限。
 *
 * 退出码：0 = 只是报告；若有人给这个脚本加了「自动写边」的开关，下面的 assertion 会先炸。
 */
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..", "..", "..");
const K = resolve(ROOT, "knowledge");

const readJsonl = (p) =>
  readFileSync(p, "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));

/** 归一化：去空白与常见连接符、去中英文标点、转小写。与 pixel 上其它脚本同口径。 */
const norm = (s) => String(s ?? "")
  .replace(/[\s_\-·、,，/（）()【】\[\]"]+/g, "")
  .toLowerCase();

const builtin = JSON.parse(readFileSync(resolve(K, "exports", "career-graph.json"), "utf8"));
const external = JSON.parse(readFileSync(resolve(K, "exports", "external-career-library.json"), "utf8"));
const bridgeEdges = readJsonl(resolve(K, "import", "build", "bridge.jsonl"));

const dadian = readJsonl(resolve(K, "import", "build", "dadian.jsonl"));
const dadianSkills = readJsonl(resolve(K, "import", "build", "dadian-skills.jsonl"));
const onet = readJsonl(resolve(K, "import", "build", "onet.jsonl"));

// ---------- 1) 现在覆盖多少 ----------

const builtinOccupations = builtin.nodes.filter((n) => n.kind === "occupation");
const bridgedOccupations = new Set(bridgeEdges.map((e) => e.from));
const bridgedTargets = new Set(bridgeEdges.map((e) => e.to));
const targetLib = (uri) => String(uri).startsWith("standard:onet-") ? "onet-29.1" : "cn-dadian";

console.log("==".repeat(34));
console.log("1) 桥接现状");
console.log("==".repeat(34));
console.log(`  自建层职业            ${builtinOccupations.length}（${builtinOccupations.map((n) => n.id).join("、")}）`);
console.log(`  aligned_with 桥边      ${bridgeEdges.length}`);
console.log(`  被桥到的自建职业        ${bridgedOccupations.size}/${builtinOccupations.length}`);
console.log(`  被桥到的外部条目        ${bridgedTargets.size}（大典 ${[...bridgedTargets].filter((u) => targetLib(u) === "cn-dadian").length} / O*NET ${[...bridgedTargets].filter((u) => targetLib(u) === "onet-29.1").length}）`);
const byAlignment = {};
for (const e of bridgeEdges) byAlignment[e.alignment] = (byAlignment[e.alignment] ?? 0) + 1;
console.log(`  对齐度分布            ${Object.entries(byAlignment).map(([k, v]) => `${k} ${v}`).join(" / ")}`);
console.log(`  外部层规模            节点 ${external.counts?.nodes} / 边 ${external.counts?.edges}（${external.libraries.map((l) => `${l.id} ${l.nodes}`).join("、")}）`);
const dadianOcc = dadian.filter((r) => r.recordType === "node");
console.log(`  大典职业（分母 1,676）  ${dadianOcc.length}`);
console.log(`  → 覆盖率               ${bridgedOccupations.size}/${dadianOcc.length} = ${(bridgedOccupations.size / dadianOcc.length * 100).toFixed(3)}%`);

// ---------- 2) 机械对齐能做到多少（实测） ----------

console.log(`\n${"==".repeat(34)}`);
console.log("2) 「按名字机械对齐」实测（这就是 M2-6 想找的那条规则）");
console.log("==".repeat(34));

const ours = new Map();
for (const n of builtin.nodes) {
  if (!["skill", "knowledge", "tool", "domain"].includes(n.kind)) continue;
  for (const name of [n.label, ...(n.aliases ?? [])]) {
    const key = norm(name);
    if (key) ours.set(key, { id: n.id, kind: n.kind, name });
  }
}
console.log(`  自建层参与比对的词条（label + aliases 去重后）  ${ours.size}`);

const buckets = [
  ["大典 · 职业", dadianOcc],
  ["大典 · 职业功能/技能", dadianSkills.filter((r) => r.recordType === "node")],
  ["O*NET · 全部条目", onet.filter((r) => r.recordType === "node")],
];
for (const [label, rows] of buckets) {
  const theirs = new Set(rows.map((r) => norm(r.label)).filter(Boolean));
  const hit = [...ours.keys()].filter((k) => theirs.has(k));
  console.log(`  ${label.padEnd(22)} 词表 ${String(theirs.size).padStart(6)} → 命中 ${hit.length}`
    + (hit.length ? `：${hit.slice(0, 8).map((k) => `${ours.get(k).id}~${ours.get(k).name}`).join("、")}` : ""));
}

// ---------- 3) 上限：就算全靠人写，能到多少 ----------

console.log(`\n${"==".repeat(34)}`);
console.log("3) 人力上限（供排期用，不是承诺）");
console.log("==".repeat(34));

const standardsWithSkills = new Set(
  dadianSkills.filter((r) => r.recordType === "edge" && r.type === "specifies")
    .map((e) => String(e.from).replace("standard:cn-", "")),
);
const dadianCodes = new Set(dadianOcc.map((n) => n.standardRef));
const covered = [...standardsWithSkills].filter((c) => dadianCodes.has(c));
console.log(`  有国标「工作内容」数据的大典职业编号  ${standardsWithSkills.size} 个（标准 702 份 → 编号 ${standardsWithSkills.size} 个）`);
console.log(`  其中能在 1,676 个职业树里对上号的    ${covered.length} 个 = ${(covered.length / dadianOcc.length * 100).toFixed(1)}%`);
console.log(`  → 只要人把这 ${covered.length} 个职业各写 1 条 alignment，桥接覆盖就能从`
  + ` ${(bridgedOccupations.size / dadianOcc.length * 100).toFixed(3)}% 提到 ${(covered.length / dadianOcc.length * 100).toFixed(1)}%`);
console.log("     （这是**上限**：没人写就没有；机器替不了这一笔，见 import/README.md §8）");

// ---------- 4) 不许偷偷写边 ----------

const autoSwitch = process.argv.find((a) => a.startsWith("--auto"));
if (autoSwitch) {
  console.error("\n✗ 本脚本不提供自动写边。跨库合并在本仓库里必须由人写下 alignment 与理由"
    + "（knowledge/import/README.md §8）。要提交人工对齐，改 "
    + "knowledge/import/mappings/occupation-alignments.json 后重跑 build-library.mjs。");
  process.exit(1);
}

console.log("\n（本脚本只读、不写任何文件；没有 --auto 之类的开关。）");
