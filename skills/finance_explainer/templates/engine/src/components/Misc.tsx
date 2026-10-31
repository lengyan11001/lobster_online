import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import type { Beat, Overlay, Timeline } from "../lib/types";
import type { Theme } from "../lib/theme";
import { Asset } from "./Overlays";

const stroke = (c = "#000", w = 3) => {
  const s: string[] = []; for (let a = 0; a < 12; a++) { const r = (a / 12) * Math.PI * 2; s.push(`${(Math.cos(r) * w).toFixed(1)}px ${(Math.sin(r) * w).toFixed(1)}px 0 ${c}`); }
  return s.join(",");
};

// 字幕：一屏 6–14 字，白字黑描边，底部居中
export const Subtitles: React.FC<{ tl: Timeline; theme: Theme }> = ({ tl, theme }) => {
  const f = useCurrentFrame(); const { fps } = useVideoConfig(); const t = f / fps;
  const s = tl.subs.find((x) => t >= x.start && t < x.end);
  if (!s) return null;
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: 58, textAlign: "center", fontFamily: theme.font, fontWeight: 800, fontSize: 54,
                  color: "#FFFFFF", textShadow: `${stroke("#111", 3)}, 0 4px 12px rgba(0,0,0,.5)`, letterSpacing: 1 }}>{s.text}</div>
  );
};

// 章节标题卡：空镜 + 大字距中文 + 细英文
export const Chapter: React.FC<{ beat: Beat; tl: Timeline; theme: Theme }> = ({ beat, tl, theme }) => {
  const f = useCurrentFrame(); const { fps } = useVideoConfig();
  const lf = f - Math.round(beat.start * fps); const dur = Math.round((beat.end - beat.start) * fps);
  const p = spring({ frame: lf, fps, config: { damping: 20, stiffness: 120 } });
  const out = interpolate(lf, [dur - 8, dur], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const ls = interpolate(lf, [0, dur], [0.5, 0.62]);
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <div style={{ position: "absolute", inset: 0, opacity: 0.55 }}><Asset id={beat.bg} tl={tl} kb /></div>
      <AbsoluteFill style={{ background: "radial-gradient(circle at 50% 50%, transparent 20%, rgba(0,0,0,.7))" }} />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", opacity: out }}>
        <div style={{ fontFamily: theme.display, fontWeight: 900, fontSize: 130, color: "#fff", letterSpacing: `${ls}em`, opacity: p,
                      transform: `translateY(${(1 - p) * 30}px)`, textShadow: "0 8px 40px rgba(0,0,0,.6)" }}>{beat.title}</div>
        <div style={{ fontFamily: theme.mono, fontSize: 30, color: "rgba(255,255,255,.75)", letterSpacing: "0.5em", marginTop: 26,
                      opacity: interpolate(lf, [8, 20], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) }}>{beat.subtitle}</div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// 问题云：本期要回答的问题浮在主播四周
export const Questions: React.FC<{ o: Overlay; theme: Theme }> = ({ o, theme }) => {
  const f = useCurrentFrame(); const { fps } = useVideoConfig();
  const lf = f - Math.round(o.t * fps); if (lf < 0 || (o.t2 != null && f > o.t2 * fps)) return null;
  const spots = [[90, 170], [1290, 210], [120, 640], [1260, 660], [700, 120]];
  return (
    <AbsoluteFill>
      {o.items.map((q: string, i: number) => {
        const p = spring({ frame: lf - i * 6, fps, config: { damping: 13, stiffness: 180 } });
        const [x, y] = spots[i % spots.length];
        return <div key={i} style={{ position: "absolute", left: x, top: y + Math.sin((f + i * 20) / 18) * 6, fontFamily: theme.font, fontWeight: 800, fontSize: 46,
                                     color: "#fff", background: "rgba(10,14,20,.72)", border: `2px solid ${theme.accent}`, borderRadius: 40, padding: "12px 30px",
                                     transform: `scale(${p})`, opacity: p }}>{q}</div>;
      })}
    </AbsoluteFill>
  );
};

// 水印 / 角标（演示片声明）
export const Badge: React.FC<{ text?: string; theme: Theme }> = ({ text, theme }) => !text ? null : (
  <div style={{ position: "absolute", right: 36, top: 30, fontFamily: theme.font, fontSize: 22, color: "rgba(255,255,255,.8)", background: "rgba(0,0,0,.35)",
                padding: "6px 14px", borderRadius: 8 }}>{text}</div>
);
