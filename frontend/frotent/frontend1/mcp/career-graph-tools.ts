/**
 * MCP 工具的真正逻辑，与 MCP 协议解耦（可被测试直接调用）。
 *
 * 两个原则：
 * 1. 复用前端纯函数 —— `adaptCareerGraph` / `buildAdjacency` / `hopDistances` /
 *    `buildGraphView` / `describeGraphView` / `isPrerequisiteRelation` / `RELATION` / `nodeKindLabel`
 *    全部来自 `lib/client/**`，MCP 侧不另写一套。
 * 2. MCP 的价值在「图推理」而不是「再抄一个检索」——所以检索结果一定带回
 *    图邻域、先修链与 chunk 级引用，而不是只甩一段文本。
 */
import { RELATION, nodeKindLabel, type GraphView } from "../lib/client/graph-view";
import { buildGraphView, describeGraphView, hopDistances, isPrerequisiteRelation } from "../lib/client/graph-view";
import {
  citationForChunk,
  citationsForNode,
  edgeRelationLabel,
  resolveNodeId,
  type CareerKnowledgeStore,
  type Citation,
  type EvaluationQuestion,
  type KnowledgeNode
} from "./career-graph-store";

/* ------------------------------------------------------------------ *
 * 公共返回块
 * ------------------------------------------------------------------ */

export type DataVersion = {
  kbVersion: string;
  graphVersion: string;
  generatedAt: string;
  status: string;
  exportPath: string;
  counts: { nodes: number; edges: number; chunks: number; sources: number; wikiPages: number; evaluationQuestions: number };
};

function dataVersion(store: CareerKnowledgeStore): DataVersion {
  return {
    kbVersion: store.raw.meta.kbVersion,
    graphVersion: store.raw.meta.graphVersion,
    generatedAt: store.raw.meta.generatedAt,
    status: store.raw.meta.status,
    exportPath: store.path,
    counts: {
      nodes: store.raw.nodes.length,
      edges: store.raw.edges.length,
      chunks: store.raw.chunks.length,
      sources: store.raw.sources.length,
      wikiPages: store.raw.wikiPages.length,
      evaluationQuestions: store.raw.evaluationQuestions.length
    }
  };
}

type Base = { ok: boolean; error?: string };

/* ------------------------------------------------------------------ *
 * 1. search_career_knowledge
 * ------------------------------------------------------------------ */

export type SearchInput = { query: string; limit?: number; kinds?: string[] };

export type GraphNeighbor = {
  direction: "out" | "in";
  relation: string;
  /** 原始英文类型，溯源用。 */
  type: string;
  id: string;
  label: string;
  kind: string;
  kindLabel: string;
  weight: number | null;
};

export type SearchMatch = {
  id: string;
  kind: string;
  kindLabel: string;
  label: string;
  description: string;
  aliases: string[];
  score: number;
  matchedOn: string[];
  /** 到「命中度最高那个节点」的跳数，用于判断命中项之间是不是同一片区域。 */
  hopFromTop: number | null;
  relationCount: number;
  neighbors: GraphNeighbor[];
  citations: Citation[];
  wiki: { path: string; review: string; reviewedBy: string | null } | null;
};

export type ChunkMatch = {
  chunkId: string;
  heading: string;
  text: string;
  tags: string[];
  score: number;
  matchedOn: string[];
  citations: Citation[];
  /** 这段原文挂在哪几个图谱节点上（`evidenced_by` 边 + `sourceRefs`）。 */
  entities: { id: string; label: string; kind: string; kindLabel: string }[];
};

export type SearchPayload = Base & {
  query: string;
  limit: number;
  kinds: string[];
  matches: SearchMatch[];
  chunkMatches: ChunkMatch[];
  relatedEvaluationQuestions: EvaluationQuestion[];
  totalScored: number;
  adapter: { translatedEdges: number; droppedNodes: { id: string; kind: string; reason: string }[] };
  dataVersion: DataVersion;
  summaryZh: string;
};

const CJK = /[\u3400-\u9fff]/;

/** 中文没有空格，所以除了按空白切词，再补一层 2 字滑窗，保证「量化」能命中「模型量化与部署」。 */
export function tokenize(query: string): string[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return [];
  const tokens = new Set<string>();
  for (const piece of normalized.split(/[\s,，、;；/|]+/)) {
    if (!piece) continue;
    tokens.add(piece);
    if (CJK.test(piece) && piece.length > 2) {
      for (let i = 0; i + 2 <= piece.length; i += 1) tokens.add(piece.slice(i, i + 2));
    }
  }
  const compact = normalized.replace(/\s+/g, "");
  if (compact) tokens.add(compact);
  return [...tokens].filter(token => token.length >= 2);
}

function scoreNode(node: KnowledgeNode, query: string, compact: string, tokens: string[]): { score: number; matchedOn: string[] } {
  const matchedOn: string[] = [];
  let score = 0;

  const label = node.label.toLowerCase();
  const labelCompact = label.replace(/\s+/g, "");
  if (label === query || (compact !== "" && labelCompact === compact)) {
    score += 8;
    matchedOn.push("label=查询词");
  } else if (label.includes(query) || (compact !== "" && labelCompact.includes(compact))) {
    score += 5;
    matchedOn.push("label 包含查询词");
  }

  for (const alias of node.aliases ?? []) {
    const value = alias.toLowerCase();
    const valueCompact = value.replace(/\s+/g, "");
    if (value === query || (compact !== "" && valueCompact === compact)) {
      score += 4;
      matchedOn.push(`alias=${alias}`);
    } else if (value.includes(query) || (compact !== "" && valueCompact.includes(compact))) {
      score += 2.5;
      matchedOn.push(`alias 含 ${alias}`);
    }
  }

  const id = node.id.toLowerCase();
  if (id.includes(query)) {
    score += 3;
    matchedOn.push("id");
  }
  const ref = id.slice(id.indexOf(":") + 1);
  if (ref !== "" && ref === query) {
    score += 3;
    matchedOn.push("ref");
  }

  const description = (node.description ?? "").toLowerCase();
  if (description.includes(query)) {
    score += 1.5;
    matchedOn.push("description");
  }

  let tokenHits = 0;
  for (const token of tokens) {
    if (label.includes(token) || description.includes(token) || (node.aliases ?? []).some(alias => alias.toLowerCase().includes(token))) {
      tokenHits += 1;
    }
  }
  if (tokenHits > 0) {
    score += tokenHits * 0.5;
    matchedOn.push(`分词命中×${tokenHits}`);
  }

  return { score, matchedOn };
}

function neighborsOf(store: CareerKnowledgeStore, nodeId: string, max = 12): GraphNeighbor[] {
  const links = [...(store.adjacency.get(nodeId) ?? [])].sort((a, b) => {
    if (a.direction !== b.direction) return a.direction === "out" ? -1 : 1;
    const left = edgeRelationLabel(a.edge);
    const right = edgeRelationLabel(b.edge);
    if (left !== right) return left.localeCompare(right, "zh-Hans-CN");
    return a.id.localeCompare(b.id);
  });

  return links.slice(0, max).map(link => {
    const other = store.nodeById.get(link.id);
    const raw = store.rawEdgeById.get(link.edge.id ?? "");
    return {
      direction: link.direction,
      relation: edgeRelationLabel(link.edge),
      type: link.edge.type ?? "",
      id: link.id,
      label: other?.label ?? link.id,
      kind: other?.kind ?? "unknown",
      kindLabel: nodeKindLabel(other?.kind ?? "unknown"),
      weight: typeof raw?.weight === "number" ? raw.weight : null
    };
  });
}

function scoreChunk(
  chunk: { heading: string; text: string; tags: string[] },
  query: string,
  compact: string,
  tokens: string[]
): { score: number; matchedOn: string[] } {
  const matchedOn: string[] = [];
  let score = 0;
  const heading = chunk.heading.toLowerCase();
  const text = chunk.text.toLowerCase();
  const tags = chunk.tags.map(tag => tag.toLowerCase());

  if (heading.includes(query) || (compact !== "" && heading.replace(/\s+/g, "").includes(compact))) {
    score += 3;
    matchedOn.push("heading");
  }
  if (tags.some(tag => tag === query || tag.includes(query))) {
    score += 3;
    matchedOn.push("tags");
  }
  if (text.includes(query)) {
    score += 2;
    matchedOn.push("text");
  }

  let tokenHits = 0;
  for (const token of tokens) {
    if (heading.includes(token) || text.includes(token) || tags.some(tag => tag.includes(token))) tokenHits += 1;
  }
  if (tokenHits > 0) {
    score += tokenHits * 0.4;
    matchedOn.push(`分词命中×${tokenHits}`);
  }
  return { score, matchedOn };
}

/** 某个 chunk 挂在哪几个节点上：`evidenced_by` 边优先，其次 `sourceRefs`。 */
function entitiesForChunk(store: CareerKnowledgeStore, chunkId: string): { id: string; label: string; kind: string; kindLabel: string }[] {
  const ids = new Set<string>();
  for (const edge of store.raw.edges) {
    if (edge.type === "evidenced_by" && edge.to === `chunk:${chunkId}` && store.nodeById.has(edge.from)) ids.add(edge.from);
  }
  for (const node of store.raw.nodes) {
    if ((node.sourceRefs ?? []).includes(chunkId)) ids.add(node.id);
  }
  return [...ids]
    .sort()
    .map(id => {
      const node = store.nodeById.get(id);
      return { id, label: node?.label ?? id, kind: node?.kind ?? "unknown", kindLabel: nodeKindLabel(node?.kind ?? "unknown") };
    });
}

export function searchCareerKnowledge(store: CareerKnowledgeStore, input: SearchInput): SearchPayload {
  const query = (input.query ?? "").trim();
  const limit = Math.min(Math.max(input.limit ?? 5, 1), 20);
  const kinds = (input.kinds ?? []).map(kind => kind.trim()).filter(Boolean);
  const empty: SearchMatch[] = [];

  if (!query) {
    return {
      ok: false,
      error: "query 不能为空。",
      query,
      limit,
      kinds,
      matches: empty,
      chunkMatches: [],
      relatedEvaluationQuestions: [],
      totalScored: 0,
      adapter: { translatedEdges: store.adaptReport.edges, droppedNodes: store.adaptReport.droppedNodes },
      dataVersion: dataVersion(store),
      summaryZh: "查询词为空，未检索。"
    };
  }

  const lowered = query.toLowerCase();
  const compact = lowered.replace(/\s+/g, "");
  const tokens = tokenize(query);

  const scoredNodes = store.raw.nodes
    .filter(node => kinds.length === 0 || kinds.includes(node.kind))
    .map(node => ({ node, ...scoreNode(node, lowered, compact, tokens) }))
    .filter(entry => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.node.id.localeCompare(b.node.id));

  const top = scoredNodes.slice(0, limit);
  const topId = top[0]?.node.id ?? null;
  const hopFromTop = topId ? hopDistances(store.graph, topId, 2) : new Map<string, number>();

  const matches: SearchMatch[] = top.map(entry => {
    const wiki = store.wikiByEntityId.get(entry.node.id) ?? null;
    const neighbors = neighborsOf(store, entry.node.id);
    return {
      id: entry.node.id,
      kind: entry.node.kind,
      kindLabel: nodeKindLabel(entry.node.kind),
      label: entry.node.label,
      description: entry.node.description ?? "",
      aliases: entry.node.aliases ?? [],
      score: entry.score,
      matchedOn: entry.matchedOn,
      hopFromTop: hopFromTop.get(entry.node.id) ?? null,
      relationCount: neighbors.length,
      neighbors,
      citations: citationsForNode(store, entry.node.id),
      wiki: wiki ? { path: wiki.path, review: wiki.review, reviewedBy: wiki.reviewedBy } : null
    };
  });

  const chunkMatches: ChunkMatch[] = store.raw.chunks
    .map(chunk => ({ chunk, ...scoreChunk(chunk, lowered, compact, tokens) }))
    .filter(entry => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.chunk.chunkId.localeCompare(b.chunk.chunkId))
    .slice(0, limit)
    .map(entry => {
      const citation = citationForChunk(store, entry.chunk.chunkId);
      return {
        chunkId: entry.chunk.chunkId,
        heading: entry.chunk.heading,
        text: entry.chunk.text,
        tags: entry.chunk.tags,
        score: entry.score,
        matchedOn: entry.matchedOn,
        citations: citation ? [citation] : [],
        entities: entitiesForChunk(store, entry.chunk.chunkId)
      };
    });

  const relatedEvaluationQuestions = store.questions
    .filter(question => {
      const text = `${question.question} ${question.category}`.toLowerCase();
      return text.includes(lowered) || tokens.some(token => text.includes(token));
    })
    .slice(0, 3);

  const nodeName = matches[0]?.label ?? "（无）";
  const summaryZh =
    matches.length === 0 && chunkMatches.length === 0
      ? `在 ${store.raw.meta.kbVersion} 里没有检索到与「${query}」相关的图谱节点或原文片段，不要凭印象回答。`
      : `在 ${store.raw.meta.kbVersion} 里为「${query}」命中 ${matches.length} 个图谱节点（最高分「${nodeName}」）、${chunkMatches.length} 段原文；每个节点都带图邻域与 chunk 级引用。`;

  return {
    ok: true,
    query,
    limit,
    kinds,
    matches,
    chunkMatches,
    relatedEvaluationQuestions,
    totalScored: scoredNodes.length,
    adapter: { translatedEdges: store.adaptReport.edges, droppedNodes: store.adaptReport.droppedNodes },
    dataVersion: dataVersion(store),
    summaryZh
  };
}

/* ------------------------------------------------------------------ *
 * 2. get_skill_gap
 * ------------------------------------------------------------------ */

export type SkillGapInput = { target: string; ownedSkills?: string[]; maxHop?: number };

export type GapStep = {
  step: number;
  id: string;
  label: string;
  kind: string;
  kindLabel: string;
  description: string;
  /** 目标（职业）直接要求的等级与权重，来自 `requires` 边。 */
  targetLevel: number | null;
  weight: number | null;
  /** 还缺哪些先修。 */
  blockedBy: { id: string; label: string }[];
  /** 它是被谁带进来的：目标直招，还是某技能的先修。 */
  neededFor: { id: string; label: string }[];
  citations: Citation[];
};

export type SkillGapPayload = Base & {
  targetResolution: { input: string; matchedBy: string; id: string | null };
  target: { id: string; label: string; kind: string; kindLabel: string; description: string } | null;
  requiredSkills: { id: string; label: string; targetLevel: number | null; weight: number | null; satisfied: boolean }[];
  /** 学习序：先修在前。 */
  learningOrder: GapStep[];
  satisfiedSkills: { id: string; label: string; labelIdentity: string }[];
  unmatchedOwnedSkills: string[];
  requiredTools: { id: string; label: string; relation: string }[];
  nextRoles: { id: string; label: string; relation: string; horizon: number | null; deltaSkills: { id: string; label: string }[] }[];
  citations: Citation[];
  dataVersion: DataVersion;
  summaryZh: string;
};

function emptyGapPayload(store: CareerKnowledgeStore, input: SkillGapInput, error: string): SkillGapPayload {
  return {
    ok: false,
    error,
    targetResolution: { input: input.target ?? "", matchedBy: "none", id: null },
    target: null,
    requiredSkills: [],
    learningOrder: [],
    satisfiedSkills: [],
    unmatchedOwnedSkills: (input.ownedSkills ?? []).slice(),
    requiredTools: [],
    nextRoles: [],
    citations: [],
    dataVersion: dataVersion(store),
    summaryZh: error
  };
}

export function getSkillGap(store: CareerKnowledgeStore, input: SkillGapInput): SkillGapPayload {
  const targetReference = (input.target ?? "").trim();
  if (!targetReference) return emptyGapPayload(store, input, "target 不能为空：请给出职业或技能节点 id（例如 occupation:AI004 / skill:SK215），也可以直接写中文名。");

  const resolution = resolveNodeId(store, targetReference);
  if (!resolution.id) {
    const hint = resolution.matchedBy === "ambiguous" ? "匹配到多个节点，请改用节点 id。" : "没有这个节点。";
    return emptyGapPayload(store, input, `无法解析 target「${targetReference}」：${hint}`);
  }

  const targetNode = store.nodeById.get(resolution.id);
  if (!targetNode) return emptyGapPayload(store, input, `节点 ${resolution.id} 在图谱里不存在。`);

  /* ---- 目标直接要求的能力项 ---- */
  const requiredSkills: SkillGapPayload["requiredSkills"] = [];
  const requiredTools: SkillGapPayload["requiredTools"] = [];
  const nextRoles: SkillGapPayload["nextRoles"] = [];

  for (const link of store.adjacency.get(targetNode.id) ?? []) {
    if (link.direction !== "out") continue;
    const other = store.nodeById.get(link.id);
    if (!other) continue;
    const relation = edgeRelationLabel(link.edge);
    const raw = store.rawEdgeById.get(link.edge.id ?? "");
    if (relation === RELATION.requires && other.kind === "skill") {
      requiredSkills.push({
        id: other.id,
        label: other.label,
        targetLevel: typeof raw?.targetLevel === "number" ? raw.targetLevel : null,
        weight: typeof raw?.weight === "number" ? raw.weight : null,
        satisfied: false
      });
    } else if (relation === RELATION.uses) {
      requiredTools.push({ id: other.id, label: other.label, relation });
    } else if (link.edge.type === "transitions_to" && other.kind === "occupation") {
      nextRoles.push({
        id: other.id,
        label: other.label,
        relation,
        horizon: typeof raw?.horizon === "number" ? raw.horizon : null,
        deltaSkills: (raw?.deltaSkills ?? []).map(skillId => ({ id: skillId, label: store.nodeById.get(skillId)?.label ?? skillId }))
      });
    }
  }

  if (targetNode.kind === "skill" && requiredSkills.length === 0) {
    requiredSkills.push({ id: targetNode.id, label: targetNode.label, targetLevel: null, weight: null, satisfied: false });
  }

  /* ---- 已有技能 ---- */
  const satisfiedSkills: SkillGapPayload["satisfiedSkills"] = [];
  const unmatchedOwnedSkills: string[] = [];
  const ownedIds = new Set<string>();
  for (const owned of input.ownedSkills ?? []) {
    const ownedResolution = resolveNodeId(store, owned);
    if (!ownedResolution.id) {
      unmatchedOwnedSkills.push(owned);
      continue;
    }
    ownedIds.add(ownedResolution.id);
    const ownedNode = store.nodeById.get(ownedResolution.id);
    satisfiedSkills.push({
      id: ownedResolution.id,
      label: ownedNode?.label ?? ownedResolution.id,
      labelIdentity: ownedResolution.matchedBy
    });
  }

  const missingDirect = requiredSkills.filter(skill => !ownedIds.has(skill.id));
  for (const skill of requiredSkills) skill.satisfied = ownedIds.has(skill.id);

  /* ---- 先修闭包：prerequisite 边方向是 from=先修 → to=依赖方 ---- */
  const prerequisitesOf = new Map<string, string[]>();
  for (const edge of store.graph.edges) {
    if (!isPrerequisiteRelation(edge.relation)) continue;
    if (edge.from.startsWith("chunk:") || edge.to.startsWith("chunk:")) continue;
    if (!store.nodeById.has(edge.from) || !store.nodeById.has(edge.to)) continue;
    prerequisitesOf.set(edge.to, [...(prerequisitesOf.get(edge.to) ?? []), edge.from]);
  }

  /** 待学集合 = 目标直缺的技能 + 它们（递归）还没具备的先修。 */
  const neededFor = new Map<string, Set<string>>();
  const unmet = new Set<string>(missingDirect.map(skill => skill.id));
  const queue = [...unmet];
  const blockedBy = new Map<string, Set<string>>();

  for (const skill of missingDirect) {
    for (const parent of prerequisitesOf.get(skill.id) ?? []) {
      if (ownedIds.has(parent)) continue;
      blockedBy.set(skill.id, new Set([...(blockedBy.get(skill.id) ?? []), parent]));
      if (!unmet.has(parent)) {
        unmet.add(parent);
        queue.push(parent);
      }
    }
  }
  while (queue.length > 0) {
    const current = queue.shift() as string;
    for (const parent of prerequisitesOf.get(current) ?? []) {
      if (ownedIds.has(parent)) continue;
      blockedBy.set(current, new Set([...(blockedBy.get(current) ?? []), parent]));
      if (!unmet.has(parent)) {
        unmet.add(parent);
        queue.push(parent);
      }
    }
  }

  for (const skill of missingDirect) neededFor.set(skill.id, new Set([targetNode.id]));
  for (const [child, parents] of blockedBy) {
    for (const parent of parents) neededFor.set(parent, new Set([...(neededFor.get(parent) ?? []), child]));
  }

  /** 学习序：从没有未满足先修的那些开始，逐层往后排。 */
  const depthOf = new Map<string, number>();
  const depth = (id: string, stack: Set<string>): number => {
    const cached = depthOf.get(id);
    if (cached !== undefined) return cached;
    if (stack.has(id)) return 0; // 环保护：真出现环也不死循环
    stack.add(id);
    let value = 0;
    for (const parent of blockedBy.get(id) ?? []) value = Math.max(value, depth(parent, stack) + 1);
    stack.delete(id);
    depthOf.set(id, value);
    return value;
  };

  const ordered = [...unmet].sort((a, b) => depth(a, new Set()) - depth(b, new Set()) || a.localeCompare(b));

  const learningOrder: GapStep[] = ordered.map((id, index) => {
    const node = store.nodeById.get(id);
    const rawRequirement = requiredSkills.find(skill => skill.id === id);
    return {
      step: index + 1,
      id,
      label: node?.label ?? id,
      kind: node?.kind ?? "unknown",
      kindLabel: nodeKindLabel(node?.kind ?? "unknown"),
      description: node?.description ?? "",
      targetLevel: rawRequirement?.targetLevel ?? null,
      weight: rawRequirement?.weight ?? null,
      blockedBy: [...(blockedBy.get(id) ?? [])].map(parent => ({
        id: parent,
        label: store.nodeById.get(parent)?.label ?? parent
      })),
      neededFor: [...(neededFor.get(id) ?? [])].map(child => ({
        id: child,
        label: store.nodeById.get(child)?.label ?? child
      })),
      citations: citationsForNode(store, id)
    };
  });

  const targetCitations = citationsForNode(store, targetNode.id);
  const gapCount = learningOrder.length;
  const satisfiedCount = requiredSkills.filter(skill => skill.satisfied).length;

  const summaryZh =
    gapCount === 0
      ? `「${targetNode.label}」要求的 ${requiredSkills.length} 项技能你已经全部具备，没有技能缺口。`
      : `${nodeKindLabel(targetNode.kind)}「${targetNode.label}」要求 ${requiredSkills.length} 项技能，已具备 ${satisfiedCount} 项，待学 ${gapCount} 项。推荐顺序：${learningOrder
          .map(item =>
            item.blockedBy.length === 0
              ? `${item.step}) ${item.label}`
              : `${item.step}) ${item.label}（先补 ${item.blockedBy.map(block => block.label).join("、")}）`
          )
          .join("；")}。`;

  return {
    ok: true,
    targetResolution: { input: targetReference, matchedBy: resolution.matchedBy, id: resolution.id },
    target: {
      id: targetNode.id,
      label: targetNode.label,
      kind: targetNode.kind,
      kindLabel: nodeKindLabel(targetNode.kind),
      description: targetNode.description ?? ""
    },
    requiredSkills,
    learningOrder,
    satisfiedSkills,
    unmatchedOwnedSkills,
    requiredTools,
    nextRoles,
    citations: targetCitations,
    dataVersion: dataVersion(store),
    summaryZh
  };
}

/* ------------------------------------------------------------------ *
 * 3. get_career_graph_view
 * ------------------------------------------------------------------ */

export type GraphViewInput = { focusId?: string; maxHop?: number; animate?: boolean };

export type GraphViewPayload = Base & {
  view: GraphView | null;
  /** 与前端 aria-live 播报同一句文案（`describeGraphView`）。 */
  summaryZh: string;
  dataVersion: DataVersion;
};

export function getCareerGraphView(store: CareerKnowledgeStore, input: GraphViewInput): GraphViewPayload {
  const reference = (input.focusId ?? "").trim();
  let focusId: string | null = null;

  if (reference !== "") {
    const resolution = resolveNodeId(store, reference);
    if (!resolution.id) {
      return {
        ok: false,
        error: `无法解析 focusId「${reference}」，可用节点：${store.raw.nodes.map(node => node.id).join("、")}`,
        view: null,
        summaryZh: `焦点节点「${reference}」不存在。`,
        dataVersion: dataVersion(store)
      };
    }
    focusId = resolution.id;
  }

  const maxHop = Math.min(Math.max(input.maxHop ?? 2, 1), 4);
  const view = buildGraphView(store.graph, focusId, { maxHop, animate: input.animate ?? false });

  return {
    ok: true,
    view,
    summaryZh: describeGraphView(view),
    dataVersion: dataVersion(store)
  };
}

/* ------------------------------------------------------------------ *
 * 4. 小工具：把「节点 id 清单」拼成报告用的一句话
 * ------------------------------------------------------------------ */

export function describeNodeKinds(store: CareerKnowledgeStore): string {
  const counts = new Map<string, number>();
  for (const node of store.raw.nodes) counts.set(node.kind, (counts.get(node.kind) ?? 0) + 1);
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([kind, count]) => `${nodeKindLabel(kind)}×${count}`)
    .join("、");
}
