import type { OccupationDetail, OccupationSummary, SkillSummary } from "../../types/contracts/catalog";

/**
 * 图谱节点类型。前四类是前端本来就画的；task / trend / credential / domain 来自 v1 的 L1 图谱导出，
 * project / standard 是 v2 新增（`knowledge/pipeline/taxonomy.json` 的 nodeKinds 是唯一词表）。
 * 不改这里的联合类型它们就会被静默丢掉（见该文件 meta.adapterNotes[1]）。
 *
 * 顺序与 taxonomy.nodeKinds 保持一致，方便两边一眼对得上。
 */
export type GraphNodeKind =
  | "occupation"
  | "skill"
  | "knowledge"
  | "task"
  | "project"
  | "tool"
  | "trend"
  | "credential"
  | "standard"
  | "domain";

export const GRAPH_NODE_KINDS: readonly GraphNodeKind[] = ["occupation", "skill", "knowledge", "task", "project", "tool", "trend", "credential", "standard", "domain"];

export function isGraphNodeKind(value: string): value is GraphNodeKind {
  return (GRAPH_NODE_KINDS as readonly string[]).includes(value);
}

export type GraphNode = { id: string; label: string; kind: GraphNodeKind; x: number; y: number; description: string; ref?: string };
/** `relation` 是前端文案；`type` / `id` 保留图谱导出里的原始英文标识，供 MCP 与溯源使用。 */
export type GraphEdge = { from: string; to: string; relation: string; type?: string; id?: string };
export type CareerGraph = { nodes: GraphNode[]; edges: GraphEdge[] };
export const SKILLS_PER_PAGE = 8;

export function buildCareerGraph(occupation: OccupationDetail, page = 0): CareerGraph {
  const center = `occupation:${occupation.occupationId}`;
  const nodes: GraphNode[] = [{ id: center, ref: occupation.occupationId, kind: "occupation", label: occupation.targetJob, x: 510, y: 300, description: occupation.descriptionZh }];
  const edges: GraphEdge[] = [];
  const skills = occupation.skills.slice(page * SKILLS_PER_PAGE, (page + 1) * SKILLS_PER_PAGE);
  skills.forEach((skill, index) => {
    const id = `skill:${skill.skillId}`;
    nodes.push({ id, ref: skill.skillId, kind: "skill", label: skill.nameZh, x: 155, y: 80 + index * 78, description: `目标 ${skill.targetLevel} 级 · ${skill.stageLabel} · ${skill.categoryZh}` });
    edges.push({ from: center, to: id, relation: "需要技能" });
  });
  skills.forEach(skill => skill.prerequisites.forEach(prerequisite => {
    if (skills.some(item => item.skillId === prerequisite)) edges.push({ from: `skill:${prerequisite}`, to: `skill:${skill.skillId}`, relation: "前置技能" });
  }));
  occupation.tools.forEach((tool, index) => {
    const id = `tool:${tool}`;
    nodes.push({ id, kind: "tool", label: tool, x: 855 + Math.floor(index / 8) * 230, y: 80 + index % 8 * 78, description: `职业资料列出的工具：${tool}。工具与技能是不同节点，不表示你已经掌握。` });
    edges.push({ from: center, to: id, relation: "使用工具" });
  });
  occupation.stages.forEach((stage, index) => {
    const id = `knowledge:${occupation.occupationId}:${stage.stage}`;
    nodes.push({ id, kind: "knowledge", label: stage.unit, x: 300 + index * 260, y: 720, description: `${stage.period} · ${stage.goal}。交付成果：${stage.deliverable}` });
    edges.push({ from: center, to: id, relation: "学习单元" });
  });
  return { nodes, edges };
}

export function buildSkillGraph(skill: SkillSummary, available: SkillSummary[]): CareerGraph {
  const nodes: GraphNode[] = [{ id: `skill:${skill.skillId}`, ref: skill.skillId, kind: "skill", label: skill.nameZh, x: 500, y: 350, description: skill.descriptionZh }];
  const edges: GraphEdge[] = [];
  skill.prerequisites.forEach((id, index) => {
    const item = available.find(entry => entry.skillId === id);
    nodes.push({ id: `skill:${id}`, ref: id, kind: "skill", label: item?.nameZh ?? id, x: 170, y: 100 + index * 85, description: item?.descriptionZh ?? "点击节点可查询这项前置技能。" });
    edges.push({ from: `skill:${id}`, to: `skill:${skill.skillId}`, relation: "前置技能" });
  });
  return { nodes, edges };
}

export function buildOccupationOverview(occupations: OccupationSummary[]): CareerGraph {
  // The overview has no invented career-to-career relationships.
  return { nodes: occupations.map((item, index) => ({ id: `occupation:${item.occupationId}`, ref: item.occupationId, kind: "occupation", label: item.shortName, x: 150 + index % 4 * 245, y: 75 + Math.floor(index / 4) * 95, description: item.descriptionZh })), edges: [] };
}

/* ------------------------------------------------------------------ *
 * 适配层：L1 图谱导出（career-graph.json）→ CareerGraph
 *
 * 为什么必须存在：导出的边用英文 `type`（requires / prerequisite / …），
 * 而 `graph-view.ts` 全程读 `edge.relation`。少了这一层，
 * 226 条边的 relation 全是 undefined —— 前置关系判不出、虚线画不出、
 * 悬停文案会变成「A → undefined → B」。契约写在 meta.adapterNotes 里，
 * 这里把它落成代码，让网页与 MCP 消费同一份数据（铁律②）。
 * ------------------------------------------------------------------ */

/** 图谱导出的英文 `type` → 前端 relation 文案。未知类型原样返回，绝不返回 undefined。 */
export const RELATION_LABELS: Readonly<Record<string, string>> = {
  requires: "需要技能",
  prerequisite: "前置技能",
  uses: "使用工具",
  learning_unit: "学习单元",
  learningUnit: "学习单元",
  belongs_to: "属于",
  trains: "训练",
  transitions_to: "转型路径",
  emerging_in: "涌现于",
  specifies: "标准规定技能",
  certifies: "认证覆盖",
  aligned_with: "对齐标准",
  practiced_in: "实践中练",
  related_to: "相关技能",
  evidenced_by: "证据来源"
};

export function relationFromType(type: string): string {
  const key = (type ?? "").trim();
  if (!key) return "关联";
  return RELATION_LABELS[key] ?? RELATION_LABELS[key.toLowerCase()] ?? key;
}

/** 导出文件里我们用到的字段；其余字段（weight / sourceRefs / …）不参与画图，按需扩展。 */
export type RawGraphNode = { id: string; kind: string; label: string; description?: string; x?: number; y?: number };
export type RawGraphEdge = { id?: string; type: string; from: string; to: string };
export type RawCareerGraph = { nodes?: RawGraphNode[]; edges?: RawGraphEdge[] };

export type AdaptReport = {
  /** 端点不在图内的边（例如 `evidenced_by` 指向 `chunk:`）——交给 graph-view 记为 skippedEdges。 */
  edges: number;
  /** 被丢弃的节点：缺坐标（画不出来）或类型不在 GraphNodeKind 白名单里。 */
  droppedNodes: { id: string; kind: string; reason: "unknown-kind" | "missing-coordinates" }[];
};

/**
 * 纯函数适配，不读时钟、不碰 DOM：网页与 Node 侧（MCP server / 评测脚本）共用同一份实现，
 * 这样两端不会各自翻译一遍英文 type。
 */
export function adaptCareerGraph(raw: RawCareerGraph): { graph: CareerGraph; report: AdaptReport } {
  const nodes: GraphNode[] = [];
  const droppedNodes: AdaptReport["droppedNodes"] = [];

  for (const node of raw.nodes ?? []) {
    if (!isGraphNodeKind(node.kind)) {
      droppedNodes.push({ id: node.id, kind: node.kind, reason: "unknown-kind" });
      continue;
    }
    if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) {
      droppedNodes.push({ id: node.id, kind: node.kind, reason: "missing-coordinates" });
      continue;
    }
    const separator = node.id.indexOf(":");
    nodes.push({
      id: node.id,
      kind: node.kind,
      label: node.label,
      x: node.x as number,
      y: node.y as number,
      description: node.description ?? "",
      // 沿用现有约定：`skill:SK101` 的 ref 是 `SK101`，供调用方回查目录。
      ref: separator >= 0 ? node.id.slice(separator + 1) : undefined
    });
  }

  const edges: GraphEdge[] = (raw.edges ?? []).map(edge => ({
    from: edge.from,
    to: edge.to,
    relation: relationFromType(edge.type),
    type: edge.type,
    id: edge.id
  }));

  return { graph: { nodes, edges }, report: { edges: edges.length, droppedNodes } };
}
