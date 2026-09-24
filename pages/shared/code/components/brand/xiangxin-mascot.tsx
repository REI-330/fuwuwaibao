import type { CSSProperties } from "react";

export const mascotStates = {
  listening: ["listening", "倾听"], thinking: ["thinking", "思考"],
  guiding: ["guiding", "指路"], coaching: ["coaching", "陪练"],
  echo: ["echo", "成长回响"], celebrating: ["celebrating", "庆祝"],
} as const;
export type MascotState = keyof typeof mascotStates;

export function XiangxinMascot({ state = "listening", size = 110, animated = true, intensity = "soft", className = "" }: {
  state?: MascotState; size?: number; animated?: boolean; intensity?: "soft" | "full"; className?: string;
}) {
  const [file, label] = mascotStates[state];
  return <figure className={`xn-mascot ${animated ? `xn-mascot-${intensity}` : ""} ${className}`} style={{ "--mascot-size": `${size}px` } as CSSProperties} data-state={state}>
    {/* Whole transparent artwork stays proportional; no separate limb or dial transforms. */}
    {/* eslint-disable-next-line @next/next/no-img-element */}
    <img src={`/assets/xiangxin/${file}.png`} alt={`新向正在${label}`} width={1254} height={1254} />
    <figcaption>{label}</figcaption>
  </figure>;
}
