"use client";

import { useEffect, useRef } from "react";

const labels = ["专业知识", "问题分析", "工具应用", "沟通表达", "人机协作"];
const series = [
  { values: [62, 75, 72, 55, 48], color: "#2563eb", fill: "rgba(37,99,235,.07)" },
  { values: [45, 58, 65, 47, 42], color: "#14b8a6", fill: "rgba(20,184,166,.06)" },
  { values: [78, 88, 83, 80, 76], color: "#f97316", fill: "rgba(249,115,22,.05)" },
];

export function EvidenceRadar() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const width = 310, height = 278, ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio; canvas.height = height * ratio; canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d"); if (!ctx) return; ctx.scale(ratio, ratio);
    const cx = width / 2, cy = height / 2 + 8, radius = 91;
    const point = (index: number, value: number) => { const angle = -Math.PI / 2 + index * Math.PI * 2 / labels.length; return [cx + Math.cos(angle) * radius * value, cy + Math.sin(angle) * radius * value] as const; };
    for (let level = 1; level <= 5; level++) { ctx.beginPath(); labels.forEach((_, index) => { const [x,y]=point(index,level/5); if(index===0)ctx.moveTo(x,y);else ctx.lineTo(x,y); }); ctx.closePath(); ctx.strokeStyle="#d9e3f1"; ctx.stroke(); }
    labels.forEach((label,index)=>{ const [x,y]=point(index,1.27); ctx.fillStyle="#31415c"; ctx.font='12px "Microsoft YaHei"'; ctx.textAlign=x<cx-8?"right":x>cx+8?"left":"center"; ctx.textBaseline=y<cy?"bottom":"top"; ctx.fillText(label,x,y); });
    series.forEach((item)=>{ ctx.beginPath(); item.values.forEach((value,index)=>{ const [x,y]=point(index,value/100); if(index===0)ctx.moveTo(x,y);else ctx.lineTo(x,y); }); ctx.closePath();ctx.fillStyle=item.fill;ctx.fill();ctx.strokeStyle=item.color;ctx.lineWidth=2;ctx.stroke(); });
  }, []);
  return <canvas ref={ref} className="xn-evidence-radar" aria-label="五维证据覆盖雷达图" />;
}
