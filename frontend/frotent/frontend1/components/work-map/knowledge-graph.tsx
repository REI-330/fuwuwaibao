"use client";
import { useEffect, useMemo, useState } from "react";
import type { CareerGraph, GraphNode } from "../../lib/client/career-graph";
import { NODE_KIND_LEGEND, buildGraphView, describeGraphView, nodeKindLabel } from "../../lib/client/graph-view";

const labels = { occupation: "职业", skill: "技能", knowledge: "知识 / 学习单元", tool: "工具" } as const;

/** 统一四舍五入，避免浮点尾差让 transform 每帧都在变。 */
function round(value: number, digits = 2): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

/**
 * 职业知识图谱。
 *
 * 改造要点（对外 props 仍是 graph / selected / onSelect，调用方无需改动）：
 *   1. 判定逻辑全部搬到 lib/client/graph-view.ts 的纯函数里，组件只把 `state` 映射成 className；
 *   2. 镜头飞行不再改 viewBox，而是过渡内层 `<g>` 的 transform；
 *   3. 先修链用 pathLength 归一的描边路径 + CSS keyframes 逐跳画出，延迟由 delayMs 决定。
 */
export function KnowledgeGraph({ graph, selected, onSelect }: { graph: CareerGraph; selected: string; onSelect: (node: GraphNode) => void }) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [reducedMotion, setReducedMotion] = useState(false);

  // 系统开启"减少动态效果"时整段动画降级：delayMs 归零、不再描边，镜头直接切过去。
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReducedMotion(query.matches);
    sync();
    query.addEventListener?.("change", sync);
    return () => query.removeEventListener?.("change", sync);
  }, []);

  // 先停在"总览镜头"，下一帧再切到焦点镜头；这样即使父组件因换焦点而整块重挂载，
  // 镜头飞行也能播出来（否则新挂载的元素一上来就是终点机位，没有过渡可言）。
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(true), 30);
    return () => window.clearTimeout(timer);
  }, []);

  const view = useMemo(
    () => buildGraphView(graph, selected || null, { animate: !reducedMotion }),
    [graph, selected, reducedMotion]
  );

  const nodeStateById = useMemo(() => {
    const map = new Map<string, string>();
    for (const node of view.nodes) map.set(node.id, node.state);
    return map;
  }, [view]);

  const summary = useMemo(() => describeGraphView(view), [view]);

  // 把"镜头 transform"和用户的缩放/平移合成成内层 g 的一个 transform。
  // 用户缩放围绕画布中心，因此先平移回中心再缩放，避免放大时画面往左上角跑。
  const { camera, bounds } = view;
  const center = { x: bounds.width / 2, y: bounds.height / 2 };
  // settled 之前用"总览镜头"（无缩放、无平移），settled 之后再切到焦点镜头。
  const cameraScale = settled ? camera.scale : 1;
  const cameraTx = settled ? camera.tx : 0;
  const cameraTy = settled ? camera.ty : 0;
  const sceneScale = round(cameraScale * zoom, 4);
  const sceneX = round((cameraTx - center.x) * zoom + center.x + pan.x, 2);
  const sceneY = round((cameraTy - center.y) * zoom + center.y + pan.y, 2);
  const sceneTransform = `translate(${sceneX} ${sceneY}) scale(${sceneScale})`;
  const focusKey = view.focusId ?? "none";

  return <section className="xn-card xn-graph-canvas" aria-label="职业知识图谱">
    <div className="xn-graph-tools"><span>点击节点查看关系与详情</span><div><button aria-label="缩小图谱" onClick={() => setZoom(value => Math.max(.65, value - .15))}>−</button><output>{Math.round(zoom * 100)}%</output><button aria-label="放大图谱" onClick={() => setZoom(value => Math.min(2, value + .15))}>＋</button><button onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>复位</button></div></div>
    <div className="xn-graph-viewport">
      <svg viewBox={`${bounds.minX} ${bounds.minY} ${bounds.width} ${bounds.height}`} role="group" aria-label="可交互职业关系图">
        <defs>
          <marker id="graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#9eb4cc" /></marker>
          <marker id="graph-arrow-prereq" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#2563eb" /></marker>
        </defs>
        <g className={view.animate ? "xn-graph-scene" : "xn-graph-scene xn-graph-scene-static"} transform={sceneTransform}>
          <g className="xn-graph-edges">
            {view.edges.map(edge => <g key={edge.key} className={`xn-graph-edge ${edge.state}${edge.isPrerequisite ? " prerequisite" : ""}`}>
              <path className="xn-graph-edge-line" d={edge.path} strokeDasharray={edge.isPrerequisite ? "5 5" : undefined} markerEnd={edge.drawOn ? undefined : "url(#graph-arrow)"} />
              {edge.drawOn && <path key={`${edge.key}#${focusKey}`} className="xn-graph-edge-trace" d={edge.path} pathLength={1} style={{ animationDelay: `${edge.delayMs}ms` }} markerEnd="url(#graph-arrow-prereq)" />}
              <title>{edge.title}</title>
            </g>)}
          </g>
          <g className="xn-graph-nodes">
            {graph.nodes.map(node => {
              const state = nodeStateById.get(node.id) ?? "context";
              return <g key={node.id} role="button" tabIndex={0} aria-label={`${nodeKindLabel(node.kind)}：${node.label}`} aria-pressed={selected === node.id} className={`xn-graph-node ${node.kind} ${state}${selected === node.id ? " selected" : ""}`} transform={`translate(${node.x},${node.y})`} onClick={() => onSelect(node)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(node); } }}>
                <title>{node.label}：{node.description}</title><rect x="-105" y="-29" width="210" height="58" rx="18" /><circle cx="-84" r="5" /><text x="-69" y="5">{node.label.length > 13 ? `${node.label.slice(0, 12)}…` : node.label}</text>
              </g>;
            })}
          </g>
        </g>
      </svg>
    </div>
    <p className="xn-graph-summary" role="status" aria-live="polite">{summary}</p>
    <footer className="xn-graph-legend">{NODE_KIND_LEGEND.map(kind => <span className={kind} key={kind}><i />{labels[kind]}</span>)}<span className="prerequisite"><i />虚线：前置关系</span><span className="trace"><i />实线：先修链（逐跳描边）</span></footer>
    <div className="xn-graph-pan" aria-label="移动图谱">{[["←", -100, 0, "向左查看"], ["↑", 0, -100, "向上查看"], ["↓", 0, 100, "向下查看"], ["→", 100, 0, "向右查看"]].map(([label, x, y, aria]) => <button key={label} aria-label={String(aria)} onClick={() => setPan(value => ({ x: value.x + Number(x), y: value.y + Number(y) }))}>{label}</button>)}</div>
  </section>;
}
