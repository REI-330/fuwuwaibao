"use client";
import { useState } from "react";
import type { CareerGraph, GraphNode } from "../../lib/client/career-graph";

const labels = { occupation: "职业", skill: "技能", knowledge: "知识 / 学习单元", tool: "工具" };
export function KnowledgeGraph({ graph, selected, onSelect }: { graph: CareerGraph; selected: string; onSelect: (node: GraphNode) => void }) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const width = Math.max(1050, ...graph.nodes.map(node => node.x + 140));
  const height = Math.max(820, ...graph.nodes.map(node => node.y + 80));
  return <section className="xn-card xn-graph-canvas" aria-label="职业知识图谱">
    <div className="xn-graph-tools"><span>点击节点查看关系与详情</span><div><button aria-label="缩小图谱" onClick={() => setZoom(value => Math.max(.65, value - .15))}>−</button><output>{Math.round(zoom * 100)}%</output><button aria-label="放大图谱" onClick={() => setZoom(value => Math.min(2, value + .15))}>＋</button><button onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>复位</button></div></div>
    <div className="xn-graph-viewport">
      <svg viewBox={`${(width - width / zoom) / 2 + pan.x} ${(height - height / zoom) / 2 + pan.y} ${width / zoom} ${height / zoom}`} role="group" aria-label="可交互职业关系图">
        <defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#9eb4cc" /></marker></defs>
        {graph.edges.map(edge => {
          const from = graph.nodes.find(node => node.id === edge.from), to = graph.nodes.find(node => node.id === edge.to);
          if (!from || !to) return null;
          const active = edge.from === selected || edge.to === selected;
          const dx = to.x - from.x, dy = to.y - from.y;
          const offset = 1 / Math.max(Math.abs(dx) / 111, Math.abs(dy) / 35, 1);
          const start = { x: from.x + dx * offset, y: from.y + dy * offset };
          const end = { x: to.x - dx * offset, y: to.y - dy * offset };
          return <g key={`${edge.from}-${edge.to}-${edge.relation}`} className={active ? "xn-graph-edge active" : "xn-graph-edge"}><path d={`M${start.x},${start.y} L${end.x},${end.y}`} strokeDasharray={edge.relation === "前置技能" ? "5 5" : undefined} markerEnd="url(#graph-arrow)" /><title>{from.label} → {edge.relation} → {to.label}</title></g>;
        })}
        {graph.nodes.map(node => <g key={node.id} role="button" tabIndex={0} aria-label={`${labels[node.kind]}：${node.label}`} aria-pressed={selected === node.id} className={`xn-graph-node ${node.kind} ${selected === node.id ? "selected" : ""}`} transform={`translate(${node.x},${node.y})`} onClick={() => onSelect(node)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(node); } }}>
          <title>{node.label}：{node.description}</title><rect x="-105" y="-29" width="210" height="58" rx="18" /><circle cx="-84" r="5" /><text x="-69" y="5">{node.label.length > 13 ? `${node.label.slice(0, 12)}…` : node.label}</text>
        </g>)}
      </svg>
    </div>
    <footer className="xn-graph-legend">{Object.entries(labels).map(([kind, label]) => <span className={kind} key={kind}><i />{label}</span>)}<span>虚线：前置关系</span></footer>
    <div className="xn-graph-pan" aria-label="移动图谱">{[["←", -100, 0, "向左查看"], ["↑", 0, -100, "向上查看"], ["↓", 0, 100, "向下查看"], ["→", 100, 0, "向右查看"]].map(([label, x, y, aria]) => <button key={label} aria-label={String(aria)} onClick={() => setPan(value => ({ x: value.x + Number(x), y: value.y + Number(y) }))}>{label}</button>)}</div>
  </section>;
}
