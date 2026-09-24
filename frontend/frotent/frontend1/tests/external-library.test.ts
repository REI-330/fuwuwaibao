/**
 * 机器验证：外部职业库（O*NET + 中国职业分类大典 + 国家职业技能标准）这一层接得对不对。
 *
 * 跑法（在 frontend/frotent/frontend1 下）：
 *   npx tsx --test tests/external-library.test.ts
 *
 * 这些断言把「导入了」「导对了」两件事分开：前者看数字，后者看契约。
 * 数字只在**稳定**的地方写死（O*NET 1,016 条职业、大典 1,676 条职业、桥边 9 条），
 * 会随抓取进度增长的层（国家职业技能标准）只断言结构与自洽 —— 否则每跑一批就要改一次测试，
 * 测试就从「守契约」退化成「追数字」。
 */
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { GRAPH_NODE_KINDS, RELATION_LABELS } from "../lib/client/career-graph";

const here = dirname(fileURLToPath(import.meta.url));
const knowledge = resolve(here, "../../../../knowledge");

const readJson = (path: string) => JSON.parse(readFileSync(path, "utf8"));
const readJsonl = (path: string): Record<string, unknown>[] =>
  readFileSync(path, "utf8").split("\n").filter(line => line.trim().length > 0).map(line => JSON.parse(line));

const taxonomy = readJson(resolve(knowledge, "pipeline/taxonomy.json"));
const library = readJson(resolve(knowledge, "exports/external-career-library.json"));
const builtin = readJson(resolve(knowledge, "exports/career-graph.json"));
const onetRows = readJsonl(resolve(knowledge, "import/build/onet.jsonl"));
const dadianRows = readJsonl(resolve(knowledge, "import/build/dadian.jsonl"));
const skillRows = readJsonl(resolve(knowledge, "import/build/dadian-skills.jsonl"));
const bridgeRows = readJsonl(resolve(knowledge, "import/build/bridge.jsonl"));

const onetNodes = onetRows.filter(row => row.recordType === "node");
const onetEdges = onetRows.filter(row => row.recordType === "edge");
const dadianNodes = dadianRows.filter(row => row.recordType === "node");
const skillNodes = skillRows.filter(row => row.recordType === "node");
const skillEdges = skillRows.filter(row => row.recordType === "edge");
const allNodes = [...onetNodes, ...dadianNodes, ...skillNodes];
const allEdges = [...onetEdges, ...skillEdges];

test("① 词表一致：taxonomy ↔ 前端联合类型与关系文案", () => {
  const taxonomyKinds: string[] = taxonomy.nodeKinds.map((spec: { id: string }) => spec.id);
  const taxonomyTypes: string[] = taxonomy.edgeTypes.map((spec: { id: string }) => spec.id);

  // 节点类型：顺序与集合都必须一致 —— 顺序决定图谱里色带的排列
  assert.deepEqual([...GRAPH_NODE_KINDS], taxonomyKinds, "前端 GRAPH_NODE_KINDS 与 taxonomy.nodeKinds 不一致");
  assert.equal(new Set(taxonomyKinds).size, taxonomyKinds.length, "taxonomy.nodeKinds 有重复 id");

  // 边类型：taxonomy 里有的，前端必须有文案，否则图上会出现英文原样（如 aligned_with）
  for (const type of taxonomyTypes) {
    assert.ok(Object.prototype.hasOwnProperty.call(RELATION_LABELS, type), `前端 RELATION_LABELS 缺少 ${type}`);
    assert.ok((RELATION_LABELS as Record<string, string>)[type].trim().length > 0, `${type} 的文案为空`);
  }
  assert.equal(RELATION_LABELS["specifies"], "标准规定技能");
  assert.equal(RELATION_LABELS["aligned_with"], "对齐标准");

  // 外部层真正用到的类型，必须都在词表里
  for (const type of ["specifies", "uses", "aligned_with", "belongs_to"]) {
    assert.ok(taxonomyTypes.includes(type), `外部层用了 ${type}，但 taxonomy.edgeTypes 里没有`);
  }
  // belongs_to 的两端也必须与外部层的用法相容（skill → domain）
  const belongsTo = taxonomy.edgeTypes.find((spec: { id: string }) => spec.id === "belongs_to");
  assert.deepEqual(belongsTo.from, ["skill"]);
  assert.deepEqual(belongsTo.to, ["domain"]);
});

test("② 合并后没有重号，每条边的两端都真实存在", () => {
  // 两个固定的库：数量是硬事实，不许漂
  assert.equal(onetNodes.length, 1254, "O*NET 应有 1254 个节点（1016 职业 + 35 技能 + 33 知识域 + 170 工具）");
  assert.equal(dadianNodes.length, 1676, "职业分类大典应有 1676 个职业节点");
  // 职业技能标准这一层随抓取进度增长，只要求非空且自洽
  assert.ok(skillNodes.length > 1000, `国家职业技能标准层只有 ${skillNodes.length} 个节点，太少`);

  const ids = allNodes.map(node => node.id as string);
  assert.equal(new Set(ids).size, ids.length, "节点 id 有重复");

  // 「标准编号还没进大典分类树」这一类边是已知的数据现状，build-library 会把它们挑出来单独记账；
  // 这里按同一口径排除，并反过来核对「被排除的」与「记账的」是同一批 —— 两边都算一遍才算真的查过。
  const dadianIds = new Set(dadianNodes.map(node => node.id as string));
  const unmatched = allEdges.filter(edge =>
    String(edge.from).startsWith("standard:cn-") && edge.type === "specifies" && !dadianIds.has(edge.from as string));
  assert.equal(unmatched.length, library.validation.unmatchedStandards.edges, "被挡下的边数与交付物里记的对不上");

  const nodeIds = new Set(ids);
  const resolvable = allEdges.filter(edge => !unmatched.includes(edge));
  for (const edge of resolvable) {
    assert.ok(nodeIds.has(edge.from as string), `${edge.id} 的 from 不存在：${edge.from}`);
    assert.ok(nodeIds.has(edge.to as string), `${edge.id} 的 to 不存在：${edge.to}`);
  }

  // kind 必须落在前端联合类型里，否则前端拿到这些数据会整批丢掉
  const usedKinds = new Set(allNodes.map(node => node.kind as string));
  for (const kind of usedKinds) assert.ok(GRAPH_NODE_KINDS.includes(kind as never), `未知 kind：${kind}`);
  assert.deepEqual([...usedKinds].sort(), ["domain", "skill", "standard", "tool"]);
});

test("③ 大典 1,676 个职业：编号四级、名称已剥掉 S/L 标注、分类路径完整", () => {
  assert.equal(dadianNodes.length, 1676);
  const occupiedCodes = new Set<string>();
  for (const node of dadianNodes) {
    assert.equal(node.kind, "standard");
    assert.match(node.id as string, /^standard:cn-\d-\d{2}-\d{2}-\d{2}$/, `${node.id} 的编号不是四级`);
    assert.match(node.label as string, /\S/);

    // 官方标注符号必须已经从 label 里剥离，剥下来的存进 dadianMarkers
    assert.equal(/[SL](\/[SL])*$/.test((node.label as string).trim()), false, `${node.id} 的 label 仍带标注：${node.label}`);
    if (node.dadianMarkers !== undefined) {
      assert.ok(Array.isArray(node.dadianMarkers) && (node.dadianMarkers as string[]).length > 0, `${node.id} 的 dadianMarkers 形状不对`);
      for (const marker of node.dadianMarkers as string[]) assert.match(marker, /^[SL]$/);
    }

    const path = node.categoryPath as Record<string, string>;
    for (const level of ["large", "middle", "small"]) {
      assert.ok(path[level] && path[level].trim().length > 0, `${node.id} 缺 ${level} 分类名`);
    }
    assert.equal(node.standardScheme, "cn-dadian");
    assert.equal(node.layer, "external");
    occupiedCodes.add(node.standardRef as string);
  }
  assert.equal(occupiedCodes.size, 1676, "职业编号有重复");
});

test("④ O*NET 1,016 条职业记录：编号、描述、归一后的 importance 都在范围内", () => {
  const standards = onetNodes.filter(node => node.kind === "standard");
  assert.equal(standards.length, 1016);
  for (const node of standards) {
    assert.match(node.standardRef as string, /^O\*NET-SOC \d{2}-\d{4}\.\d{2}$/, `${node.id} 的 SOC 编号形状不对`);
    assert.ok((node.description as string).trim().length > 20, `${node.id} 缺职责描述`);
    assert.equal(node.layer, "external");
  }

  // importance = IM / 5，必须落在 0–1；level 是 LV 原值，0–7；每条边至少要有其中一个
  let withImportance = 0;
  let withLevel = 0;
  for (const edge of onetEdges.filter(row => row.type === "specifies")) {
    if (edge.importance !== undefined) {
      withImportance += 1;
      const value = edge.importance as number;
      assert.ok(value >= 0 && value <= 1, `${edge.id} 的 importance 越界：${value}`);
    }
    if (edge.level !== undefined) {
      withLevel += 1;
      const value = edge.level as number;
      assert.ok(value >= 0 && value <= 7, `${edge.id} 的 level 越界：${value}`);
    }
    assert.ok(edge.importance !== undefined || edge.level !== undefined, `${edge.id} 两个字段都没有，这条边没有意义`);
  }
  assert.ok(withImportance > 50000, `带 importance 的 specifies 边只有 ${withImportance} 条`);
  assert.ok(withLevel > 0, "带 level 的 specifies 边为 0 条");

  // 工具边只连 Hot Technology
  const toolIds = new Set(onetNodes.filter(node => node.kind === "tool").map(node => node.id as string));
  assert.equal(toolIds.size, 170, "Hot Technology 应为 170 个");
  for (const edge of onetEdges.filter(row => row.type === "uses")) {
    assert.ok(toolIds.has(edge.to as string), `${edge.id} 连到了非 Hot Technology 工具：${edge.to}`);
  }
});

test("⑤ 国家职业技能标准层：技能来自「工作内容」、职业功能来自「职业功能」列，等级合法", () => {
  const skills = skillNodes.filter(node => node.kind === "skill");
  const functions = skillNodes.filter(node => node.domainKind === "occupational-function");
  const areas = skillNodes.filter(node => node.domainKind === "knowledge-area");
  assert.ok(skills.length > 1000, `技能节点只有 ${skills.length} 个`);
  assert.ok(functions.length > 100, `职业功能节点只有 ${functions.length} 个`);
  assert.ok(areas.length > 10, `基础知识域节点只有 ${areas.length} 个`);

  let withSamples = 0;
  for (const node of skills) {
    assert.ok((node.label as string).trim().length > 0);
    // 清理规则要求条目名至少含一个汉字：字体编码坏掉的 PDF 取出来只剩 ASCII，那些行必须已被丢掉
    assert.match(node.label as string, /[\u4e00-\u9fff]/, `${node.id} 的 label 不含汉字，说明乱码没被过滤：${node.label}`);
    assert.equal(/\d+\.\d+/.test(node.label as string), false, `${node.id} 的 label 仍残留条目编号：${node.label}`);
    assert.ok(Array.isArray(node.levels), `${node.id} 的 levels 不是数组`);
    for (const level of node.levels as number[]) assert.ok(level >= 1 && level <= 5, `${node.id} 的等级越界：${level}`);
    assert.ok(Array.isArray(node.sampleRequirements), `${node.id} 缺 sampleRequirements`);
    if ((node.sampleRequirements as string[]).length > 0) withSamples += 1;
  }
  // 有极少数行的「技能要求」列在原文里就是空的（标准本身没写），不该为此硬造一段原文；
  // 但比例必须很小，超过 5% 就说明解析出问题了。
  const sampleRate = withSamples / skills.length;
  assert.ok(sampleRate > 0.95, `只有 ${(sampleRate * 100).toFixed(1)}% 的技能留到了技能要求原文，太低了`);
  // 两类 domain 必须分得开，不能混成一堆
  for (const node of [...functions, ...areas]) {
    assert.ok(["occupational-function", "knowledge-area"].includes(node.domainKind as string), `domainKind 非法：${node.domainKind}`);
  }

  // specifies 的 level 落在 1–5；belongs_to 只连 技能 → 职业功能
  const functionIds = new Set(functions.map(node => node.id as string));
  const skillIds = new Set(skills.map(node => node.id as string));
  let specifies = 0;
  let belongsTo = 0;
  for (const edge of skillEdges) {
    if (edge.type === "specifies") {
      specifies += 1;
      if (edge.level !== undefined) assert.ok((edge.level as number) >= 1 && (edge.level as number) <= 5, `${edge.id} 的 level 越界：${edge.level}`);
    } else if (edge.type === "belongs_to") {
      belongsTo += 1;
      assert.ok(skillIds.has(edge.from as string), `${edge.id} 的 from 不是技能：${edge.from}`);
      assert.ok(functionIds.has(edge.to as string), `${edge.id} 的 to 不是职业功能：${edge.to}`);
    } else {
      assert.fail(`国家职业技能标准层出现了意料之外的边类型：${edge.type}`);
    }
  }
  assert.ok(specifies > 1000, `specifies 边只有 ${specifies} 条`);
  assert.ok(belongsTo > 1000, `belongs_to 边只有 ${belongsTo} 条`);
});

test("⑥ 桥边只连真实存在的两端，alignment 只用三个允许值", () => {
  assert.equal(bridgeRows.length, 9, "桥边应有 9 条");

  const builtinOccupations = new Set(
    (builtin.nodes as { id: string; kind: string }[]).filter(node => node.kind === "occupation").map(node => node.id),
  );
  const externalIds = new Set(allNodes.map(node => node.id as string));

  for (const edge of bridgeRows) {
    assert.equal(edge.type, "aligned_with");
    assert.ok(builtinOccupations.has(edge.from as string), `桥边的 from 不是自建职业：${edge.from}`);
    assert.ok(externalIds.has(edge.to as string), `桥边的 to 不是外部节点：${edge.to}`);
    assert.ok(["full", "partial", "related"].includes(edge.alignment as string), `alignment 非法：${edge.alignment}`);
    assert.ok((edge.note as string).trim().length > 10, `${edge.id} 缺少人工写下的理由`);
  }

  // 四个自建职业都要能被落到标准条目上，不能有孤儿
  for (const occupationId of builtinOccupations) {
    assert.ok(bridgeRows.some(edge => edge.from === occupationId), `${occupationId} 没有任何 aligned_with 边`);
  }
});

test("⑦ 交付物里的数字必须与 build/*.jsonl 逐项对得上", () => {
  assert.equal(library.counts.nodes, allNodes.length);
  // 合并后的边 = 全部外部边 − 被挡下的「标准编号未进大典树」那一批
  assert.equal(library.counts.edges, allEdges.length - library.counts.unmatchedStandardEdges);
  assert.equal(library.counts.bridge, bridgeRows.length);
  assert.equal(library.counts.byKind.standard, 1016 + 1676);
  assert.equal(library.counts.byKind.skill, onetNodes.filter(n => n.kind === "skill").length + skillNodes.filter(n => n.kind === "skill").length);
  assert.equal(library.counts.byKind.domain, onetNodes.filter(n => n.kind === "domain").length + skillNodes.filter(n => n.kind === "domain").length);
  assert.equal(library.counts.byKind.tool, 170);
  assert.deepEqual(library.counts.bridgeByAlignment, { partial: 5, full: 2, related: 2 });
  assert.equal(library.validation.passed, true);

  // 索引现在是「计数 + 最多 50 条样例」：计数必须与 byKind 对得上，
  // 样例必须真的抽样过（有上限、非空）—— 之前按全量列，交付物涨到 4.2 MB，等于把 JSONL 又抄了一份。
  for (const [kind, count] of Object.entries(library.counts.byKind as Record<string, number>)) {
    const entry = (library.index as Record<string, { count: number; sampleSize: number; sample: unknown[] }>)[kind];
    assert.ok(entry, `index 里没有 ${kind}`);
    assert.equal(entry.count, count, `index.${kind}.count 与 byKind 不一致`);
    assert.ok(entry.sample.length > 0, `index.${kind} 一条样例都没有`);
    assert.ok(entry.sample.length <= 50, `index.${kind} 的样例超过 50 条`);
    assert.equal(entry.sampleSize, entry.sample.length);
  }

  // 「标准编号未进大典树」这一类必须显式记账，不能是静默丢弃
  assert.equal(typeof library.validation.unmatchedStandards, "object");
  assert.equal(library.validation.unmatchedStandards.edges, library.counts.unmatchedStandardEdges);

  // 交付物必须是小文件：全量数据留在 JSONL，别把几十 MB 塞进 exports
  const exportSize = readFileSync(resolve(knowledge, "exports/external-career-library.json")).length;
  assert.ok(exportSize < 1024 * 1024, `交付物 ${exportSize} 字节，太大了，说明把全量塞进来了`);

  // 自建层没有被这次接入动过
  assert.equal((builtin.nodes as unknown[]).length, 63, "自建层节点数变了 —— 外部库不该碰它");
  assert.equal(existsSync(resolve(knowledge, "import/build/bridge.jsonl")), true);
});
