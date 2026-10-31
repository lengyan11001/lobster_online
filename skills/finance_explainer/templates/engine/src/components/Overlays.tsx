import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import type { Overlay, Timeline } from "../lib/types";
import type { Theme } from "../lib/theme";

// 所有叠加卡片：出现时刻 o.t（秒，整片时间），消失时刻 o.t2（默认所在拍结束）
const useLocal = (o: Overlay) => {
  const f = useCurrentFrame(); const { fps } = useVideoConfig();
  const lf = f - Math.round(o.t * fps);
  const outF = o.t2 != null ? Math.round(o.t2 * fps) - f : 9999;
  const inP = spring({ frame: lf, fps, config: { damping: 14, stiffness: 190, mass: 0.6 } });
  const outP = interpolate(outF, [0, 6], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return { lf, fps, inP, outP, vis: lf >= 0 && outF > 0 };
};

const posStyle = (pos: string | undefined): React.CSSProperties => {
  switch (pos) {
    case "left": return { left: 90, top: 300, alignItems: "flex-start" };
    case "topleft": return { left: 70, top: 70, alignItems: "flex-start" };
    case "right": return { right: 90, top: 300, alignItems: "flex-end" };
    case "lower": return { left: 0, right: 0, top: 640, alignItems: "center" };
    case "center": return { left: 0, right: 0, top: 380, alignItems: "center" };
    default: return { right: 90, top: 300, alignItems: "flex-end" };
  }
};

const stroke = (c = "#000", w = 4) => {
  const s: string[] = []; for (let a = 0; a < 16; a++) { const r = (a / 16) * Math.PI * 2; s.push(`${(Math.cos(r) * w).toFixed(1)}px ${(Math.sin(r) * w).toFixed(1)}px 0 ${c}`); }
  return s.join(",");
};

// —— 观众私信卡（开场钩子）——
const Comment: React.FC<{ o: Overlay; theme: Theme }> = ({ o, theme }) => {
  const { inP, outP, vis } = useLocal(o); if (!vis) return null;
  const left = o.side === "left";
  return (
    <div style={{ position: "absolute", top: o.row ?? (left ? 170 : 520), [left ? "left" : "right"]: 70, width: 620,
                  transform: `translateY(${(1 - inP) * 40}px) scale(${0.85 + inP * 0.15})`, opacity: inP * outP,
                  background: "#fff", borderRadius: 18, padding: "22px 26px", boxShadow: "0 18px 50px rgba(0,0,0,.28)", fontFamily: theme.font }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div style={{ width: 52, height: 52, borderRadius: 26, background: `hsl(${(o.user?.length ?? 3) * 47 % 360} 55% 62%)`, color: "#fff",
                      display: "grid", placeItems: "center", fontSize: 24, fontWeight: 700 }}>{(o.user ?? "?").slice(0, 1)}</div>
        <div style={{ fontSize: 24, color: "#666", fontWeight: 600 }}>{o.user}</div>
        <div style={{ marginLeft: "auto", fontSize: 18, color: "#aaa" }}>{o.time ?? "刚刚"}</div>
      </div>
      <div style={{ fontSize: 31, color: "#1d1d1f", marginTop: 12, lineHeight: 1.45, fontWeight: 500 }}>{o.text}</div>
    </div>
  );
};

// —— 日期贴纸叠加（时间线）——
const DateStack: React.FC<{ o: Overlay; theme: Theme }> = ({ o, theme }) => {
  const f = useCurrentFrame(); const { fps } = useVideoConfig(); const t = f / fps;
  if (t < o.t || (o.t2 != null && t > o.t2)) return null;
  return (
    <div style={{ position: "absolute", left: 60, top: 70, display: "flex", flexDirection: "column", gap: 16, fontFamily: theme.font }}>
      {o.items.map((it: any, n: number) => {
        const lf = Math.round((t - it.t) * fps); if (lf < 0) return null;
        const p = spring({ frame: lf, fps, config: { damping: 13, stiffness: 200 } });
        const strikeP = it.strike ? interpolate(lf, [12, 22], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 0;
        return (
          <div key={n} style={{ display: "flex", alignItems: "center", gap: 0, transform: `translateX(${(1 - p) * -260}px)`, opacity: p, position: "relative" }}>
            <div style={{ background: it.strike ? theme.hot : theme.accent, color: "#fff", fontWeight: 800, fontSize: 26, padding: "10px 16px", borderRadius: "10px 0 0 10px" }}>{it.date}</div>
            <div style={{ background: "#fff", color: "#222", fontWeight: 700, fontSize: 30, padding: "8px 20px", borderRadius: "0 10px 10px 0", boxShadow: "0 8px 24px rgba(0,0,0,.25)" }}>{it.label}</div>
            {it.strike ? <div style={{ position: "absolute", left: 0, top: "50%", height: 6, width: `${strikeP * 100}%`, background: theme.hot, borderRadius: 3 }} /> : null}
          </div>
        );
      })}
    </div>
  );
};

// —— 大数字滚动 ——
const fmt = (v: number, money?: boolean) => money ? Math.round(v).toLocaleString("en-US") : (v >= 100 ? Math.round(v).toLocaleString("en-US") : v.toFixed(v % 1 ? 1 : 0));
const Counter: React.FC<{ o: Overlay; theme: Theme }> = ({ o, theme }) => {
  const { lf, fps, inP, outP, vis } = useLocal(o); if (!vis) return null;
  const p = interpolate(lf, [0, Math.round(fps * (o.dur ?? 1.1))], [0, 1], { extrapolateRight: "clamp", easing: (x) => 1 - Math.pow(1 - x, 3) });
  const v = (o.from ?? 0) + ((o.to ?? 0) - (o.from ?? 0)) * p;
  return (
    <AbsoluteFill style={{ ...posStyle(o.pos), position: "absolute", display: "flex", flexDirection: "column", justifyContent: "flex-start", opacity: outP }}>
      <div style={{ fontFamily: theme.mono, fontWeight: 800, fontSize: o.money ? 132 : 220, color: "#fff", letterSpacing: -2,
                    textShadow: `0 6px 30px rgba(0,0,0,.6), ${stroke("rgba(0,0,0,.55)", 3)}`, transform: `scale(${0.8 + inP * 0.2})` }}>
        {o.prefix ?? ""}{fmt(v, o.money)}<span style={{ fontSize: o.money ? 70 : 96, fontFamily: theme.display, marginLeft: 10 }}>{o.suffix ?? ""}</span>
      </div>
    </AbsoluteFill>
  );
};

// —— 大字（big / stamp / stamp-red / brush）——
const Kinetic: React.FC<{ o: Overlay; theme: Theme }> = ({ o, theme }) => {
  const { inP, outP, vis, lf } = useLocal(o); if (!vis) return null;
  const st = o.style ?? "big";
  const stamp = st.startsWith("stamp");
  const scale = stamp ? interpolate(inP, [0, 1], [2.2, 1]) : 0.7 + inP * 0.3;
  const color = st === "stamp-red" ? "#FF4D3D" : st === "brush" ? "#FFFFFF" : stamp ? "#FFE15A" : "#FFFFFF";
  const font = st === "brush" ? theme.brush : theme.display;
  const size = o.size ?? (st === "brush" ? 210 : stamp ? (o.text.length <= 2 ? 200 : 110) : (o.text.length > 6 ? 84 : 110));
  const rot = stamp ? -6 : 0;
  return (
    <AbsoluteFill style={{ ...posStyle(o.pos), position: "absolute", display: "flex", flexDirection: "column", justifyContent: "flex-start", opacity: Math.min(inP * 1.4, 1) * outP }}>
      <div style={{ fontFamily: font, fontWeight: 900, fontSize: size, color, lineHeight: 1.1, transform: `scale(${scale}) rotate(${rot}deg)`,
                    textShadow: `${stroke("#111", st === "brush" ? 3 : 6)}, 0 10px 30px rgba(0,0,0,.5)`, whiteSpace: "pre", maxWidth: 900, textAlign: o.pos === "lower" || o.pos === "center" ? "center" : "right" }}>{o.text}</div>
      {o.sub ? <div style={{ marginTop: 14, fontFamily: theme.font, fontWeight: 700, fontSize: 38, color: "#fff", background: "rgba(0,0,0,.55)", padding: "6px 18px", borderRadius: 8,
                             opacity: interpolate(lf, [6, 14], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) }}>{o.sub}</div> : null}
    </AbsoluteFill>
  );
};

// —— 术语卡（英文原词 + 中文解释）——
const Term: React.FC<{ o: Overlay; theme: Theme }> = ({ o, theme }) => {
  const { inP, outP, vis } = useLocal(o); if (!vis) return null;
  return (
    <AbsoluteFill style={{ ...posStyle(o.pos), position: "absolute", display: "flex", flexDirection: "column", justifyContent: "flex-start" }}>
      <div style={{ background: theme.termBg, border: "1.5px solid rgba(255,255,255,.25)", borderRadius: 20, padding: "26px 36px", maxWidth: 720,
                    transform: `translateY(${(1 - inP) * 30}px)`, opacity: inP * outP, boxShadow: "0 18px 50px rgba(0,0,0,.4)", fontFamily: theme.font }}>
        <div style={{ fontFamily: theme.display, fontSize: 66, fontWeight: 800, color: theme.termInk, letterSpacing: .5 }}>{o.en}</div>
        <div style={{ fontSize: 36, color: theme.name === "v2-glass" ? theme.accent : "#FFD86B", marginTop: 6, fontWeight: 600 }}>{o.zh}</div>
      </div>
    </AbsoluteFill>
  );
};

// —— 反应梗（生成的短片或图，全屏插入 + 大字）——
const Meme: React.FC<{ o: Overlay; theme: Theme; tl: Timeline }> = ({ o, theme, tl }) => {
  const { inP, vis } = useLocal(o); if (!vis) return null;
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <Asset id={o.src} tl={tl} kb />
      <div style={{ position: "absolute", left: 0, right: 0, bottom: 170, textAlign: "center", fontFamily: theme.display, fontWeight: 900, fontSize: 96,
                    color: "#fff", textShadow: stroke("#000", 6), transform: `scale(${0.8 + inP * 0.2})` }}>{o.caption}</div>
    </AbsoluteFill>
  );
};

// —— 卡通贴纸 + 气泡台词（拟人独白）——
const Sticker: React.FC<{ o: Overlay; theme: Theme; tl: Timeline }> = ({ o, theme, tl }) => {
  const f = useCurrentFrame(); const { fps } = useVideoConfig(); const t = f / fps;
  const { inP, vis } = useLocal(o); if (!vis) return null;
  const bob = Math.sin(f / 7) * 6;
  const lines = (o.bubble ?? []).filter((b: any) => t >= b.t);
  const cur = lines[lines.length - 1];
  return (
    <div style={{ position: "absolute", right: 110, bottom: 60, width: 560, height: 700, transform: `translateY(${(1 - inP) * 300 + bob}px)` }}>
      <div style={{ position: "absolute", inset: 0 }}><Asset id={o.src} tl={tl} contain /></div>
      {cur ? <div key={cur.text} style={{ position: "absolute", left: -260, top: 30, background: "#fff", borderRadius: 30, padding: "18px 28px", fontFamily: theme.font,
                    fontWeight: 800, fontSize: 44, color: "#222", boxShadow: "0 12px 30px rgba(0,0,0,.3)", whiteSpace: "nowrap" }}>{cur.text}</div> : null}
    </div>
  );
};

// —— 要点清单（右侧逐条出现）——
const List: React.FC<{ o: Overlay; theme: Theme }> = ({ o, theme }) => {
  const f = useCurrentFrame(); const { fps } = useVideoConfig(); const t = f / fps;
  if (t < o.t || (o.t2 != null && t > o.t2)) return null;
  return (
    <div style={{ position: "absolute", right: 80, top: 300, width: 640, display: "flex", flexDirection: "column", gap: 22, fontFamily: theme.font }}>
      {o.items.map((it: any, n: number) => {
        const lf = Math.round((t - it.t) * fps); if (lf < 0) return null;
        const p = spring({ frame: lf, fps, config: { damping: 14, stiffness: 190 } });
        const c = it.tone === "bad" ? theme.bad : it.tone === "good" ? theme.good : theme.accent;
        return <div key={n} style={{ background: "rgba(12,16,22,.86)", borderLeft: `10px solid ${c}`, borderRadius: 14, padding: "22px 28px", color: "#fff",
                                     fontSize: 42, fontWeight: 800, transform: `translateX(${(1 - p) * 200}px)`, opacity: p }}>{it.text}</div>;
      })}
    </div>
  );
};

// —— 素材渲染：gen:/stock: id → timeline.assets；没有素材时画占位，方便先出样片 ——
export const Asset: React.FC<{ id?: string; tl: Timeline; kb?: boolean; contain?: boolean }> = ({ id, tl, kb, contain }) => {
  const f = useCurrentFrame(); const { durationInFrames } = useVideoConfig();
  const key = (id ?? "").replace(/^(gen|stock|wiki):/, "");
  const a = tl.assets[key];
  const style: React.CSSProperties = { width: "100%", height: "100%", objectFit: contain ? "contain" : "cover",
                                       transform: kb ? `scale(${1.02 + 0.06 * (f % 300) / 300})` : undefined };
  if (!a || !a.src) {
    return (
      <AbsoluteFill style={{ background: "repeating-linear-gradient(135deg,#2A3240 0 40px,#262D3A 40px 80px)", display: "grid", placeItems: "center" }}>
        <div style={{ color: "#9FB0C8", fontFamily: "Menlo,monospace", fontSize: 34, textAlign: "center" }}>素材待生成<br />{id}<br /><span style={{ fontSize: 24 }}>{a?.label ?? ""}</span></div>
      </AbsoluteFill>
    );
  }
  return a.kind === "video"
    ? <OffthreadVideo src={staticFile(a.src)} muted style={style} />
    : <Img src={staticFile(a.src)} style={style} />;
};

export const OverlayLayer: React.FC<{ tl: Timeline; theme: Theme; filter?: (o: Overlay) => boolean }> = ({ tl, theme, filter }) => {
  const f = useCurrentFrame(); const { fps } = useVideoConfig(); const t = f / fps;
  const out: React.ReactNode[] = [];
  tl.beats.forEach((b) => {
    if (t < b.start - 0.1 || t > b.end + 0.1) return;
    b.overlays.forEach((o, n) => {
      if (filter && !filter(o)) return;
      const k = `${b.id}-${n}`;
      const C: any = { comment: Comment, datestack: DateStack, counter: Counter, kinetic: Kinetic, term: Term, meme: Meme, sticker: Sticker, list: List }[o.type];
      if (C) out.push(<C key={k} o={o} theme={theme} tl={tl} />);
    });
  });
  return <AbsoluteFill style={{ pointerEvents: "none" }}>{out}</AbsoluteFill>;
};
