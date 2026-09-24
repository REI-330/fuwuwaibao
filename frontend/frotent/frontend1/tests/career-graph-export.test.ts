/**
 * 机器验证：L1 图谱导出 → 前端视图模型。
 *
 * 跑法（在 frontend/frotent/frontend1 下）：
 *   npx tsx --test tests/career-graph-export.test.ts
 *
 * 这些断言把此前只能"人工核对"的结论变成可复算的检查：
 *   ① 导出的英文 `type` 经过适配层后，226 条边都有 relation（此前是 undefined）；
 *   ② 8 类节点不会被 GraphNode.kind 联合类型丢掉；
 *   ③ `chunk:` 引用边被排除，不产生幽灵节点；
 *   ④ 悬停文案与先修链判定真的能拿到数据。
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { GRAPH_NODE_KINDS, adaptCareerGraph, relationFromType } from "../lib/client/career-graph";
import { buildGraphView, describeGraphView, isChunkReference, isPrerequisiteRelation } from "../lib/client/graph-view";

const here = dirname(fileURLToPath(import.meta.url));
const exportPath = resolve(here, "../../../../knowledge/exports/career-graph.json");
const raw = JSON.parse(readFileSync(exportPath, "utf8")) as Parameters<typeof adaptCareerGraph>[0];

const { graph, report } = adaptCareerGraph(raw);

test("① 导出的每条边都被翻译成非空 relation（不再是 undefined）", () => {
  assert.equal(graph.edges.length, 226, "真实导出应有 226 条边");
  for (const edge of graph.edges) {
    assert.equal(typeof edge.relation, "string", `${edge.id} 的 relation 不是字符串`);
    assert.notEqual(edge.relation, "undefined");
    assert.ok(edge.relation.trim().length > 0, `${edge.id} 的 relation 为空`);
    assert.equal(edge.type, raw.edges?.find(candidate => candidate.id === edge.id)?.type);
  }
  // 未知 type 原样返回，绝不死掉
  assert.equal(relationFromType("requires"), "需要技能");
  assert.equal(relationFromType("prerequisite"), "前置技能");
  assert.equal(relationFromType("brand_new_type"), "brand_new_type");
  assert.equal(relationFromType(""), "关联");

  // 契约本身没有 relation 字段 —— 这正是适配层必须存在的原因
  for (const edge of raw.edges ?? []) {
    assert.equal(Object.prototype.hasOwnProperty.call(edge, "relation"), false, `${edge.id} 不该带 relation`);
  }
});

test("② 8 类节点全部保留，没有被 GraphNode.kind 联合类型丢掉", () => {
  assert.equal(graph.nodes.length, 63, "真实导出应有 63 个节点");
  assert.equal(report.droppedNodes.length, 0, `不应丢节点：${JSON.stringify(report.droppedNodes)}`);
  const kinds = new Set(graph.nodes.map(node => node.kind));
  for (const kind of kinds) assert.ok(GRAPH_NODE_KINDS.includes(kind), `未知 kind：${kind}`);
  assert.deepEqual([...kinds].sort(), ["credential", "domain", "knowledge", "occupation", "skill", "task", "tool", "trend"]);
  // x / y 来自流水线，适配层不重算
  for (const node of graph.nodes) {
    assert.ok(Number.isFinite(node.x) && Number.isFinite(node.y), `${node.id} 缺坐标`);
  }
});

test("③ chunk: 引用边被排除，且记录成 skippedEdges 而不是幽灵节点", () => {
  const chunkEdges = graph.edges.filter(edge => isChunkReference(edge.from) || isChunkReference(edge.to));
  assert.equal(chunkEdges.length, 109, "真实导出有 109 条指向 chunk: 的 evidenced_by 边");
  for (const edge of chunkEdges) assert.equal(edge.type, "evidenced_by");

  const view = buildGraphView(graph, null);
  assert.equal(view.counts.edges, 117, "可渲染边 = 226 - 109");
  assert.equal(view.skippedEdges.length, 109);
  for (const skipped of view.skippedEdges) assert.equal(skipped.reason, "chunk-reference");
  assert.ok(view.skippedEdges.some(skipped => skipped.key.includes("chunk:S04#s73")), "AI001 的 chunk 引用边应出现在跳过清单里");
  assert.ok(view.skippedEdges.every(skipped => skipped.key.includes("chunk:")), "跳过的边必须都是 chunk: 引用");

  const nodeIds = new Set(graph.nodes.map(node => node.id));
  assert.equal(nodeIds.has("chunk:S04#s73"), false, "chunk: 不是图节点");
  assert.equal(view.nodes.length, graph.nodes.length, "BFS 不应长出幽灵节点");
});

test("④ 悬停文案与先修链判定真的拿到了关系（此前全是 undefined）", () => {
  for (const node of graph.nodes) {
    const view = buildGraphView(graph, node.id);
    for (const edge of view.edges) {
      assert.equal(edge.title.includes("undefined"), false, `${edge.key} 的悬停文案是 ${edge.title}`);
      assert.match(edge.title, /→ .+ →/, `${edge.key} 的悬停文案缺少关系`);
    }
  }

  const prerequisiteEdges = graph.edges.filter(edge => isPrerequisiteRelation(edge.relation));
  assert.equal(prerequisiteEdges.length, 24, "真实导出有 24 条 prerequisite 边");
  assert.equal(prerequisiteEdges[0].relation, "前置技能");

  // 先修链动画的触发条件是「两端都在 maxHop 半径内」，至少要有一个焦点能满足
  const bestChain = Math.max(...graph.nodes.map(node => buildGraphView(graph, node.id).counts.prerequisiteChain));
  assert.ok(bestChain >= 1, "没有任何焦点能触发先修链描边");
});

test("⑤ 摘要文案可用（aria-live 播报与报告共用）", () => {
  const overview = buildGraphView(graph, null);
  assert.match(describeGraphView(overview), /63 个节点、117 条关系/);
  const focused = buildGraphView(graph, "occupation:AI001");
  const summary = describeGraphView(focused);
  assert.match(summary, /已选中「/);
  assert.equal(summary.includes("undefined"), false);
});
