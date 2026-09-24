import type { OccupationDetail, OccupationSummary, SkillSummary } from "../../types/contracts/catalog";

export type GraphNode = { id: string; label: string; kind: "occupation" | "skill" | "knowledge" | "tool"; x: number; y: number; description: string; ref?: string };
export type GraphEdge = { from: string; to: string; relation: string };
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
