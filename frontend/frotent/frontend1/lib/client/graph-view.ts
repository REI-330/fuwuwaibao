/**
 * 职业图谱视图模型 —— 纯函数模块
 *
 * 这里只做四件事，全部可单测、可复算、无副作用：
 *   1. edgeKey / buildAdjacency：把 CareerGraph 变成可遍历的邻接表（显式排除 `chunk:` 引用）；
 *   2. hopDistances：以焦点为源的 BFS 跳数；
 *   3. graphViewport / cameraFor：确定性包围盒与"镜头飞行"参数；
 *   4. buildGraphView：输出 nodeStates / edgeStates / hop / camera，供前端动画与报告评测共用。
 *
 * 不变量（改动前先读这段）：
 *   - 不读时钟、不用随机数，同一份输入永远得到同一份输出，因此可以拿快照做回归测试；
 *   - 不 import React、不触碰 DOM，Node 侧（评测脚本、MCP server）可直接复用同一份算法；
 *   - `evidenced_by` 的 `chunk:` 目标不是图节点、没有 x/y，必须排除，
 *     否则邻接表里会长出幽灵节点并污染 BFS 跳数（见 KNOWLEDGE_BASE_DESIGN.md §3.2）。
 */

import { RELATION_LABELS, type CareerGraph, type GraphEdge, type GraphNode } from "./career-graph";

/* ------------------------------------------------------------------ *
 * 常量
 * ------------------------------------------------------------------ */

/** 节点是 210×58 的圆角胶囊（见 knowledge-graph.tsx），这里存半宽/半高用于边端点裁剪。 */
export const NODE_HALF_WIDTH = 105;
export const NODE_HALF_HEIGHT = 29;
/** 边端点额外让出的间隙，避免箭头贴住节点边框。 */
export const NODE_GAP = 8;

export const DEFAULT_MAX_HOP = 2;
/** 先修链逐跳描边的节奏，第 n 跳的边延迟 n × 这个值。 */
export const PREREQUISITE_STAGGER_MS = 160;

export const CHUNK_PREFIX = "chunk:";

/** 画布兜底尺寸，和改造前的 knowledge-graph.tsx 保持一致。 */
export const DEFAULT_CANVAS = { width: 1050, height: 820, padding: 24 } as const;

/** 前端现有 relation 文案。图谱导出（career-graph.json）用英文 `type`，
 *  已由 career-graph.ts 的 adaptCareerGraph 映射；这里直接引用同一张表，避免两处漂移。 */
export const RELATION = {
  requires: RELATION_LABELS.requires,
  prerequisite: RELATION_LABELS.prerequisite,
  uses: RELATION_LABELS.uses,
  learningUnit: RELATION_LABELS.learning_unit
} as const;

const PREREQUISITE_ALIASES = new Set(["前置技能", "前置", "prerequisite"]);

/**
 * 前端 `GraphNode.kind` 目前只有四种字面量，图谱导出里还有 task / trend / credential / domain。
 * 未拓宽联合类型前那四类节点到不了前端，这里仍然给出中文名，供 MCP 与报告复用同一份词表。
 */
const NODE_KIND_LABELS: Record<string, string> = {
  occupation: "职业",
  skill: "技能",
  knowledge: "知识 / 学习单元",
  tool: "工具",
  task: "训练任务",
  trend: "趋势",
  credential: "认证",
  domain: "能力域"
};

/** 图例只列前端真能画出来的四类，避免出现"图例有、图上没有"的尴尬。 */
export const NODE_KIND_LEGEND = ["occupation", "skill", "knowledge", "tool"] as const;

export function nodeKindLabel(kind: string): string {
  return NODE_KIND_LABELS[kind] ?? kind;
}

export function isPrerequisiteRelation(relation: string): boolean {
  const trimmed = relation.trim();
  return PREREQUISITE_ALIASES.has(trimmed) || PREREQUISITE_ALIASES.has(trimmed.toLowerCase());
}

/** `chunk:S03#2.1` 这类引用不是节点，图上没有位置。 */
export function isChunkReference(id: string): boolean {
  return id.startsWith(CHUNK_PREFIX);
}

/* ------------------------------------------------------------------ *
 * 纯工具
 * ------------------------------------------------------------------ */

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** 统一四舍五入，让快照与截图稳定（浮点尾差会让 viewBox 每帧都在变）。 */
function round(value: number, digits = 2): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

/* ------------------------------------------------------------------ *
 * 1. 边标识与邻接表
 * ------------------------------------------------------------------ */

/** 同一对节点之间可能有多条不同类型的边，所以 key 必须带上 relation。 */
export function edgeKey(edge: GraphEdge): string {
  return `${edge.from}|${edge.to}|${edge.relation}`;
}

export type GraphLink = {
  /** 另一端节点 id。 */
  id: string;
  relation: string;
  /** 从当前节点看出去的方向，`out` 表示当前节点是 from。 */
  direction: "out" | "in";
  edge: GraphEdge;
};

export type GraphAdjacency = Map<string, GraphLink[]>;

/**
 * 邻接表。只保留两端都是真实节点的边：
 * `chunk:` 引用、以及任何指向不存在节点的悬空边都会被丢掉。
 */
export function buildAdjacency(graph: CareerGraph): GraphAdjacency {
  const nodeIds = new Set(graph.nodes.map(node => node.id));
  const adjacency: GraphAdjacency = new Map();
  for (const node of graph.nodes) adjacency.set(node.id, []);
  for (const edge of graph.edges) {
    if (!nodeIds.has(edge.from) || !nodeIds.has(edge.to)) continue;
    adjacency.get(edge.from)?.push({ id: edge.to, relation: edge.relation, direction: "out", edge });
    adjacency.get(edge.to)?.push({ id: edge.from, relation: edge.relation, direction: "in", edge });
  }
  return adjacency;
}

export type SkippedEdge = { key: string; reason: "chunk-reference" | "missing-endpoint" };

/** 边能不能画在图上的唯一判据，改造前后的组件都依赖它。 */
export function isRenderableEdge(edge: GraphEdge, nodeIds: ReadonlySet<string>): boolean {
  return nodeIds.has(edge.from) && nodeIds.has(edge.to) && !isChunkReference(edge.from) && !isChunkReference(edge.to);
}

/* ------------------------------------------------------------------ *
 * 2. BFS 跳数
 * ------------------------------------------------------------------ */

/**
 * 以 `focusId` 为源做无向 BFS（邻域高亮关心"能否到达"，不关心方向）。
 * 只返回可达节点；焦点本身是 0 跳。`maxHop` 默认无穷大，
 * 这样调用方既能取"2 跳以内"用于描边，也能区分"更远但连通"与"完全不可达"。
 */
export function hopDistances(graph: CareerGraph, focusId: string, maxHop: number = Number.POSITIVE_INFINITY): Map<string, number> {
  const hop = new Map<string, number>();
  const adjacency = buildAdjacency(graph);
  if (!adjacency.has(focusId)) return hop;

  hop.set(focusId, 0);
  let frontier = [focusId];
  for (let depth = 1; depth <= maxHop && frontier.length > 0; depth += 1) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const link of adjacency.get(id) ?? []) {
        if (hop.has(link.id)) continue;
        hop.set(link.id, depth);
        next.push(link.id);
      }
    }
    frontier = next;
  }
  return hop;
}

/* ------------------------------------------------------------------ *
 * 3. 包围盒与镜头
 * ------------------------------------------------------------------ */

export type GraphBounds = {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  width: number;
  height: number;
};

export type ViewportOptions = {
  halfWidth?: number;
  halfHeight?: number;
  padding?: number;
  minWidth?: number;
  minHeight?: number;
};

/** 节点坐标是中心点，包围盒要按胶囊外框算，否则贴边的节点会被裁掉一半。 */
export function graphViewport(points: readonly { x: number; y: number }[], options: ViewportOptions = {}): GraphBounds {
  const halfWidth = options.halfWidth ?? NODE_HALF_WIDTH;
  const halfHeight = options.halfHeight ?? NODE_HALF_HEIGHT;
  const padding = options.padding ?? DEFAULT_CANVAS.padding;
  const minWidth = options.minWidth ?? DEFAULT_CANVAS.width;
  const minHeight = options.minHeight ?? DEFAULT_CANVAS.height;

  if (points.length === 0) {
    return { minX: 0, minY: 0, maxX: minWidth, maxY: minHeight, width: minWidth, height: minHeight };
  }

  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const point of points) {
    minX = Math.min(minX, point.x - halfWidth - padding);
    minY = Math.min(minY, point.y - halfHeight - padding);
    maxX = Math.max(maxX, point.x + halfWidth + padding);
    maxY = Math.max(maxY, point.y + halfHeight + padding);
  }

  const roundedMinX = Math.round(Math.min(minX, 0));
  const roundedMinY = Math.round(Math.min(minY, 0));
  const roundedMaxX = Math.round(Math.max(maxX, roundedMinX + minWidth));
  const roundedMaxY = Math.round(Math.max(maxY, roundedMinY + minHeight));

  return {
    minX: roundedMinX,
    minY: roundedMinY,
    maxX: roundedMaxX,
    maxY: roundedMaxY,
    width: roundedMaxX - roundedMinX,
    height: roundedMaxY - roundedMinY
  };
}

export type CameraOptions = {
  viewportWidth?: number;
  viewportHeight?: number;
  minScale?: number;
  maxScale?: number;
  padding?: number;
};

/**
 * 镜头参数。返回的是**内层 g 的 transform**（`translate(tx,ty) scale(scale)`），
 * 不是 viewBox —— 外层 viewBox 保持不动，靠 transform 过渡做镜头飞行，
 * 这样浏览器只需要插值一个 transform，比逐帧改 viewBox 平滑得多。
 */
export type GraphCamera = {
  focused: boolean;
  scale: number;
  tx: number;
  ty: number;
  viewportWidth: number;
  viewportHeight: number;
  viewBox: GraphBounds;
};

export function cameraFor(
  bounds: GraphBounds,
  focus: GraphNode | null,
  neighborhood: readonly { x: number; y: number }[] = [],
  options: CameraOptions = {}
): GraphCamera {
  const viewportWidth = options.viewportWidth ?? bounds.width;
  const viewportHeight = options.viewportHeight ?? bounds.height;
  const minScale = options.minScale ?? 0.65;
  const maxScale = options.maxScale ?? 1.15;
  const padding = options.padding ?? DEFAULT_CANVAS.padding;

  if (!focus) {
    return { focused: false, scale: 1, tx: 0, ty: 0, viewportWidth, viewportHeight, viewBox: bounds };
  }

  const area = graphViewport(neighborhood.length > 0 ? [...neighborhood, focus] : [focus], {
    padding,
    minWidth: 0,
    minHeight: 0
  });
  const fitScale = Math.min(viewportWidth / Math.max(area.width, 1), viewportHeight / Math.max(area.height, 1));
  const scale = round(clamp(fitScale, minScale, maxScale), 3);

  return {
    focused: true,
    scale,
    tx: round(viewportWidth / 2 - focus.x * scale, 2),
    ty: round(viewportHeight / 2 - focus.y * scale, 2),
    viewportWidth,
    viewportHeight,
    viewBox: bounds
  };
}

/* ------------------------------------------------------------------ *
 * 4. 边的几何
 * ------------------------------------------------------------------ */

export type Point = { x: number; y: number };

/** 把射线裁到胶囊外框上，得到边的实际起止点（箭头落在边框外侧，不压住文字）。 */
export function edgeEndpoints(from: Point, to: Point, halfWidth = NODE_HALF_WIDTH, halfHeight = NODE_HALF_HEIGHT, gap = NODE_GAP): { start: Point; end: Point } {
  const trim = (origin: Point, target: Point): Point => {
    const dx = target.x - origin.x;
    const dy = target.y - origin.y;
    if (dx === 0 && dy === 0) return { x: origin.x, y: origin.y };
    const tx = dx === 0 ? Number.POSITIVE_INFINITY : (halfWidth + gap) / Math.abs(dx);
    const ty = dy === 0 ? Number.POSITIVE_INFINITY : (halfHeight + gap) / Math.abs(dy);
    const t = clamp(Math.min(tx, ty), 0, 1);
    return { x: origin.x + dx * t, y: origin.y + dy * t };
  };
  return { start: trim(from, to), end: trim(to, from) };
}

export function edgePath(from: Point, to: Point): string {
  const { start, end } = edgeEndpoints(from, to);
  return `M${round(start.x)},${round(start.y)} L${round(end.x)},${round(end.y)}`;
}

export function edgeLength(from: Point, to: Point): number {
  return round(Math.hypot(to.x - from.x, to.y - from.y), 2);
}

/* ------------------------------------------------------------------ *
 * 5. 视图模型
 * ------------------------------------------------------------------ */

export type NodeVisualState = "focal" | "hop1" | "hop2" | "context" | "dimmed";
export type EdgeVisualState = "prerequisite-active" | "incident" | "neighborhood" | "context" | "dimmed";

export type NodeViewState = {
  id: string;
  kind: GraphNode["kind"];
  kindLabel: string;
  label: string;
  x: number;
  y: number;
  state: NodeVisualState;
  /** 到焦点的跳数；无焦点或不可达时为 null。 */
  hop: number | null;
  /** 图上连边条数（只算可渲染的边）。 */
  degree: number;
};

export type EdgeViewState = {
  key: string;
  from: string;
  to: string;
  relation: string;
  state: EdgeVisualState;
  /** 两端中较小的跳数，用于逐跳描边的排序与延迟。 */
  hop: number;
  delayMs: number;
  drawOn: boolean;
  isPrerequisite: boolean;
  path: string;
  length: number;
  /** 悬停/读屏用的一句话，和改造前的 `<title>` 文案一致。 */
  title: string;
};

export type GraphViewOptions = {
  maxHop?: number;
  /** 关闭后 `delayMs` 全为 0、`drawOn` 全为 false，供 prefers-reduced-motion 降级。 */
  animate?: boolean;
  camera?: CameraOptions;
};

export type GraphView = {
  focusId: string | null;
  maxHop: number;
  animate: boolean;
  nodes: NodeViewState[];
  edges: EdgeViewState[];
  /** 可达节点的跳数（可序列化，便于写进评测快照）。 */
  hop: Record<string, number>;
  camera: GraphCamera;
  bounds: GraphBounds;
  skippedEdges: SkippedEdge[];
  counts: {
    nodes: number;
    focal: number;
    hop1: number;
    hop2: number;
    context: number;
    dimmed: number;
    edges: number;
    edgesSkipped: number;
    prerequisiteChain: number;
  };
};

/**
 * 把图 + 焦点编译成"该长什么样"的完整描述。
 * 组件只负责把 state 映射成 className，判定逻辑全在这里，因此可以脱离浏览器测试。
 */
export function buildGraphView(graph: CareerGraph, focusId: string | null, options: GraphViewOptions = {}): GraphView {
  const maxHop = options.maxHop ?? DEFAULT_MAX_HOP;
  const animate = options.animate ?? true;

  const nodeById = new Map<string, GraphNode>();
  for (const node of graph.nodes) nodeById.set(node.id, node);
  const nodeIds = new Set<string>(nodeById.keys());
  const focus = focusId ? nodeById.get(focusId) ?? null : null;
  const effectiveFocusId = focus ? focus.id : null;

  const hop = focus ? hopDistances(graph, focus.id) : new Map<string, number>();

  const renderableEdges: GraphEdge[] = [];
  const skippedEdges: SkippedEdge[] = [];
  const degree = new Map<string, number>();
  for (const edge of graph.edges) {
    if (isRenderableEdge(edge, nodeIds)) {
      renderableEdges.push(edge);
      degree.set(edge.from, (degree.get(edge.from) ?? 0) + 1);
      degree.set(edge.to, (degree.get(edge.to) ?? 0) + 1);
    } else {
      skippedEdges.push({ key: edgeKey(edge), reason: isChunkReference(edge.from) || isChunkReference(edge.to) ? "chunk-reference" : "missing-endpoint" });
    }
  }

  /**
   * 无焦点时一律中性；有焦点时按跳数分层。
   * 视觉上只区分"焦点 / 一跳 / 两跳（含更远的同侧外圈）"三档，
   * 超出 maxHop 但仍连通 → context；完全不可达 → dimmed。
   */
  const stateOf = (id: string): NodeVisualState => {
    if (!focus) return "context";
    const distance = hop.get(id);
    if (distance === undefined) return "dimmed";
    if (distance === 0) return "focal";
    if (distance === 1) return "hop1";
    if (distance <= maxHop) return "hop2";
    return "context";
  };

  const nodes: NodeViewState[] = graph.nodes.map(node => {
    const distance = focus ? hop.get(node.id) : undefined;
    const state = stateOf(node.id);
    return {
      id: node.id,
      kind: node.kind,
      kindLabel: nodeKindLabel(node.kind),
      label: node.label,
      x: node.x,
      y: node.y,
      state,
      hop: distance ?? null,
      degree: degree.get(node.id) ?? 0
    };
  });

  const edges: EdgeViewState[] = [];
  for (const edge of renderableEdges) {
    const from = nodeById.get(edge.from);
    const to = nodeById.get(edge.to);
    if (!from || !to) continue;

    const fromHop = focus ? hop.get(edge.from) : undefined;
    const toHop = focus ? hop.get(edge.to) : undefined;
    const isPrerequisite = isPrerequisiteRelation(edge.relation);
    const chainHop = Math.min(fromHop ?? Number.POSITIVE_INFINITY, toHop ?? Number.POSITIVE_INFINITY);
    const withinRadius = fromHop !== undefined && toHop !== undefined && fromHop <= maxHop && toHop <= maxHop;

    let state: EdgeVisualState;
    if (!focus) {
      state = "context";
    } else if (isPrerequisite && withinRadius) {
      // 两端都在描边半径内 → 这条边属于要逐跳画出来的先修链。
      state = "prerequisite-active";
    } else if (edge.from === effectiveFocusId || edge.to === effectiveFocusId) {
      state = "incident";
    } else if (withinRadius) {
      state = "neighborhood";
    } else {
      state = "dimmed";
    }

    const delayMs = state === "prerequisite-active" && animate && Number.isFinite(chainHop) ? chainHop * PREREQUISITE_STAGGER_MS : 0;

    edges.push({
      key: edgeKey(edge),
      from: edge.from,
      to: edge.to,
      relation: edge.relation,
      state,
      hop: Number.isFinite(chainHop) ? chainHop : -1,
      delayMs,
      drawOn: state === "prerequisite-active" && animate,
      isPrerequisite,
      path: edgePath(from, to),
      length: edgeLength(from, to),
      title: `${from.label} → ${edge.relation} → ${to.label}`
    });
  }

  const bounds = graphViewport(graph.nodes);
  const neighborhood = focus ? graph.nodes.filter(node => (hop.get(node.id) ?? Number.POSITIVE_INFINITY) <= maxHop) : [];
  const camera = cameraFor(bounds, focus, neighborhood, {
    viewportWidth: options.camera?.viewportWidth ?? bounds.width,
    viewportHeight: options.camera?.viewportHeight ?? bounds.height,
    minScale: options.camera?.minScale,
    maxScale: options.camera?.maxScale,
    padding: options.camera?.padding
  });

  const hopRecord: Record<string, number> = {};
  for (const node of graph.nodes) {
    const distance = hop.get(node.id);
    if (distance !== undefined) hopRecord[node.id] = distance;
  }

  const countByState = (state: NodeVisualState) => nodes.filter(node => node.state === state).length;

  return {
    focusId: effectiveFocusId,
    maxHop,
    animate,
    nodes,
    edges,
    hop: hopRecord,
    camera,
    bounds,
    skippedEdges,
    counts: {
      nodes: nodes.length,
      focal: countByState("focal"),
      hop1: countByState("hop1"),
      hop2: countByState("hop2"),
      context: countByState("context"),
      dimmed: countByState("dimmed"),
      edges: edges.length,
      edgesSkipped: skippedEdges.length,
      prerequisiteChain: edges.filter(edge => edge.state === "prerequisite-active").length
    }
  };
}

/** 一行中文摘要，用于 aria-live 播报与评测快照，避免两处各写一套文案。 */
export function describeGraphView(view: GraphView): string {
  if (!view.focusId) {
    return `未选中节点：${view.counts.nodes} 个节点、${view.counts.edges} 条关系。`;
  }
  const focusLabel = view.nodes.find(node => node.id === view.focusId)?.label ?? view.focusId;
  const parts = [`已选中「${focusLabel}」`, `直接相关 ${view.counts.hop1} 个`, `两跳内 ${view.counts.hop1 + view.counts.hop2} 个`];
  if (view.counts.prerequisiteChain > 0) parts.push(`先修链 ${view.counts.prerequisiteChain} 段`);
  if (view.counts.dimmed > 0) parts.push(`无关节点 ${view.counts.dimmed} 个已降噪`);
  return `${parts.join("，")}。`;
}
