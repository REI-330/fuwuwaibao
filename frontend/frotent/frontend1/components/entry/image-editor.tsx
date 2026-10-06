"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Rect = { x: number; y: number; width: number; height: number };

export function ImageEditor({ file, onConfirm, onCancel }: {
  file: File;
  onConfirm: (blob: Blob) => void;
  onCancel: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const startRef = useRef<{ x: number; y: number } | null>(null);
  const url = useMemo(() => URL.createObjectURL(file), [file]);
  const [rects, setRects] = useState<Rect[]>([]);
  const [draft, setDraft] = useState<Rect | null>(null);

  useEffect(() => () => URL.revokeObjectURL(url), [url]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const image = imageRef.current;
    if (!canvas || !image || !image.naturalWidth) return;
    const scale = Math.min(1, 760 / image.naturalWidth, 520 / image.naturalHeight);
    canvas.width = Math.round(image.naturalWidth * scale);
    canvas.height = Math.round(image.naturalHeight * scale);
    const context = canvas.getContext("2d");
    if (!context) return;
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    [...rects, ...(draft ? [draft] : [])].forEach((rect) => {
      context.fillStyle = "#111827";
      context.fillRect(rect.x, rect.y, rect.width, rect.height);
    });
  }, [draft, rects]);

  useEffect(() => { draw(); }, [url, draw]);

  function point(event: React.PointerEvent<HTMLCanvasElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  }
  function pointerDown(event: React.PointerEvent<HTMLCanvasElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    startRef.current = point(event);
  }
  function pointerMove(event: React.PointerEvent<HTMLCanvasElement>) {
    const start = startRef.current;
    if (!start) return;
    const current = point(event);
    setDraft({ x: Math.min(start.x, current.x), y: Math.min(start.y, current.y), width: Math.abs(current.x - start.x), height: Math.abs(current.y - start.y) });
  }
  function pointerUp() {
    if (draft && draft.width > 4 && draft.height > 4) setRects((items) => [...items, draft]);
    setDraft(null);
    startRef.current = null;
  }
  function confirm() {
    const canvas = canvasRef.current;
    if (!canvas) return onCancel();
    canvas.toBlob((blob) => { if (blob) onConfirm(blob); }, "image/png");
  }

  return (
    <div role="dialog" aria-modal="true" aria-label="编辑图片内容" style={{ position: "fixed", inset: 0, zIndex: 50, display: "grid", placeItems: "center", padding: 20, background: "rgba(15,23,42,.58)" }}>
      <div style={{ width: "min(840px, 100%)", maxHeight: "90vh", overflow: "auto", padding: 24, borderRadius: 12, background: "#fff" }}>
        <div className="xn-step-heading" style={{ marginTop: 0 }}><span>图片简历</span><h1>编辑需要隐藏的内容</h1><p>在图片上拖动框选敏感信息，框选区域会被遮盖。</p></div>
        {/* Object URLs are drawn into a canvas and never rendered as page content. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img ref={imageRef} src={url} alt="待编辑的简历" onLoad={draw} style={{ display: "none" }} />
        <canvas ref={canvasRef} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} style={{ display: "block", maxWidth: "100%", height: "auto", margin: "0 auto", border: "1px solid #dbe4ef", cursor: "crosshair" }} />
        <div className="xn-step-actions"><button type="button" className="xn-entry-secondary" onClick={() => setRects((items) => items.slice(0, -1))}>撤销框选</button><button type="button" className="xn-entry-secondary" onClick={onCancel}>取消</button><button type="button" className="xn-entry-primary" onClick={confirm}>使用编辑后的图片</button></div>
      </div>
    </div>
  );
}
