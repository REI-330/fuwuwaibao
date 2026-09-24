export function CompanionArt({ size = "medium", state = "倾听中" }: { size?: "small" | "medium" | "large"; state?: string }) {
  return (
    <div className={`xn-companion-art xn-companion-${size}`}>
      <div role="img" aria-label="新向指南针成长伙伴" className="xn-companion-image" />
      {state && <span className="xn-companion-state"><i />{state}</span>}
    </div>
  );
}
